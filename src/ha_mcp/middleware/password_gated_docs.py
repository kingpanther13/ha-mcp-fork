"""Password-gated first-call docs middleware for progressive disclosure.

Compresses tool descriptions in tools/list responses and enforces password
verification before tool execution. Each gated tool gets a unique password
that rotates every 30 minutes.

Flow:
1. tools/list returns compressed one-liner descriptions with a _docs_password
   parameter added to each gated tool's schema.
2. LLM calls a gated tool with _docs_password='REQUEST_DOCS' (or any
   invalid password) — middleware returns full documentation + the current
   password for that tool.
3. LLM calls the tool again with the correct password + real arguments —
   middleware strips _docs_password and forwards the call for execution.

Tools with short descriptions (below a configurable character threshold)
are exempt and execute immediately without a password gate.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import time
from collections.abc import Sequence
from copy import deepcopy
from typing import Any, override

import mcp.types as mt
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import Tool, ToolResult

logger = logging.getLogger(__name__)

# Default password rotation in seconds (30 minutes).
_DEFAULT_ROTATION_SECONDS = 30 * 60

# Sentinel value that LLMs use on first call to request docs.
_REQUEST_DOCS_SENTINEL = "REQUEST_DOCS"

# Name of the password parameter added to gated tools' schemas.
_PASSWORD_PARAM = "_docs_password"


def _extract_first_line(description: str) -> str:
    """Extract the first meaningful sentence from a tool description.

    Returns the first non-empty line, truncated at the first sentence
    boundary (period followed by space or newline) if found.
    """
    lines = description.strip().splitlines()
    if not lines:
        return ""

    first_line = lines[0].strip()
    if not first_line:
        for line in lines[1:]:
            first_line = line.strip()
            if first_line:
                break
        else:
            return ""

    match = re.match(r"^(.+?\.)(?:\s|$)", first_line)
    if match:
        return match.group(1)

    return first_line


class PasswordGatedDocsMiddleware(Middleware):
    """Middleware that enforces password verification before tool execution.

    Each gated tool requires a unique password that changes at a
    configurable interval (default 30 minutes). On first call or with a
    wrong/missing password, the middleware returns the tool's full
    documentation together with the current password. Execution only
    proceeds when the correct password is supplied.

    The password is a declared parameter (_docs_password) in each gated
    tool's inputSchema, so it is not stripped by clients that enforce
    additionalProperties: false (e.g. Claude Code).

    Args:
        min_description_length: Character threshold for gating. Tools with
            descriptions shorter than this execute immediately without a
            password gate. Default: 500.
        exclude_tools: Optional set of tool names that should always bypass
            the password gate regardless of description length.
        password_rotation_seconds: How long (in seconds) before the password
            for each tool rotates. Default: 1800 (30 minutes).
    """

    def __init__(
        self,
        min_description_length: int = 500,
        exclude_tools: set[str] | None = None,
        password_rotation_seconds: float = _DEFAULT_ROTATION_SECONDS,
    ) -> None:
        self._min_length = min_description_length
        self._exclude_tools = exclude_tools or set()
        self._rotation_seconds = password_rotation_seconds
        # Random secret generated once per server instance.
        self._secret = os.urandom(32)
        # tool_name -> full description text (captured on first tools/list).
        self._full_descriptions: dict[str, str] = {}

        logger.info(
            "PasswordGatedDocsMiddleware initialized "
            "(min_length=%d, rotation=%ds)",
            self._min_length,
            int(self._rotation_seconds),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _should_gate(self, tool: Tool) -> bool:
        """Check if a tool should be gated behind password verification."""
        if tool.name in self._exclude_tools:
            return False
        desc = tool.description or ""
        return len(desc) >= self._min_length

    def _generate_password(self, tool_name: str) -> str:
        """Generate a unique password for a tool based on current time bucket."""
        if self._rotation_seconds > 0:
            time_bucket = int(time.time()) // int(self._rotation_seconds)
        else:
            # rotation=0 means password never stabilizes (always changes).
            time_bucket = int(time.monotonic() * 1000)
        payload = f"{tool_name}:{time_bucket}".encode()
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()[:16]

    def _verify_password(self, tool_name: str, password: str) -> bool:
        """Check if the given password matches the current password."""
        expected = self._generate_password(tool_name)
        return hmac.compare_digest(password, expected)

    @staticmethod
    def _add_password_param(tool: Tool, description: str) -> Tool:
        """Return a copy of *tool* with _docs_password added to its schema."""
        params: dict[str, Any] = deepcopy(tool.parameters) if tool.parameters else {
            "type": "object",
            "properties": {},
        }
        params.setdefault("properties", {})
        params["properties"][_PASSWORD_PARAM] = {
            "type": "string",
            "description": (
                "Documentation access password. On first use of this tool, "
                "pass 'REQUEST_DOCS' to receive required documentation and "
                "your unique access password."
            ),
        }
        # Make the password required so the LLM always provides it.
        required = list(params.get("required", []))
        if _PASSWORD_PARAM not in required:
            required.append(_PASSWORD_PARAM)
        params["required"] = required

        return tool.model_copy(update={
            "description": description,
            "parameters": params,
        })

    # ------------------------------------------------------------------
    # Middleware hooks
    # ------------------------------------------------------------------

    @override
    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        """Compress descriptions for gated tools and add password param."""
        tools = await call_next(context)
        result: list[Tool] = []

        for tool in tools:
            if self._should_gate(tool):
                # Capture full description (first capture wins).
                if tool.name not in self._full_descriptions:
                    self._full_descriptions[tool.name] = tool.description or ""
                    logger.debug(
                        "Captured docs for %s (%d chars)",
                        tool.name,
                        len(self._full_descriptions[tool.name]),
                    )

                short = _extract_first_line(tool.description or "")
                compressed_desc = (
                    f"{short} "
                    f"(IMPORTANT: Call with _docs_password='REQUEST_DOCS' "
                    f"to receive required documentation and your access "
                    f"password before use.)"
                )
                result.append(self._add_password_param(tool, compressed_desc))
            else:
                result.append(tool)

        if self._full_descriptions:
            logger.info(
                "Password-gated docs active: %d tools gated, %d tools exempt",
                len(self._full_descriptions),
                len(tools) - len(self._full_descriptions),
            )

        return result

    @override
    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Enforce password verification before tool execution."""
        tool_name = context.message.name

        # Non-gated tools execute immediately (strip _docs_password if present
        # to prevent unexpected keyword argument errors).
        if tool_name not in self._full_descriptions:
            arguments = context.message.arguments or {}
            if _PASSWORD_PARAM in arguments:
                context.message.arguments = {
                    k: v for k, v in arguments.items() if k != _PASSWORD_PARAM
                }
            return await call_next(context)

        # Extract password from arguments.
        arguments = context.message.arguments or {}
        password = str(arguments.get(_PASSWORD_PARAM, ""))

        # Valid password — strip it from arguments and execute.
        if (
            password
            and password != _REQUEST_DOCS_SENTINEL
            and self._verify_password(tool_name, password)
        ):
            clean_args = {
                k: v for k, v in arguments.items() if k != _PASSWORD_PARAM
            }
            context.message.arguments = clean_args
            return await call_next(context)

        # Invalid / missing / REQUEST_DOCS — deliver docs + password.
        current_password = self._generate_password(tool_name)
        docs = self._full_descriptions[tool_name]

        logger.debug("Delivering docs + password for %s", tool_name)

        docs_text = (
            f"REQUIRED DOCUMENTATION \u2014 {tool_name}\n"
            f"{'=' * 50}\n\n"
            f"{docs}\n\n"
            f"{'=' * 50}\n"
            f"ACCESS PASSWORD: {current_password}\n"
            f"{'=' * 50}\n\n"
            f"ACTION REQUIRED: Call {tool_name} again with your intended "
            f"arguments AND _docs_password='{current_password}' to execute."
        )

        return ToolResult(
            content=docs_text,
            structured_content={
                "status": "documentation_required",
                "tool": tool_name,
                "documentation": docs,
                "password": current_password,
            },
            meta={},
        )
