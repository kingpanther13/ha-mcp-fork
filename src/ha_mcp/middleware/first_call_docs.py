"""First-Call Docs middleware for progressive disclosure of tool documentation.

Compresses tool descriptions in tools/list responses and enforces mandatory
documentation delivery on the first call to each tool. Tools cannot execute
until the LLM has received their full documentation.

Tools with short descriptions (below a configurable character threshold)
are exempt and execute immediately without a documentation gate.

Tracking uses a global time-based expiry: after docs are delivered for a
tool, the entry expires after a configurable timeout (default 10 minutes).
This ensures new conversations get fresh docs even when the server process
persists across multiple chat sessions, while avoiding redundant docs
delivery within a single conversation.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from typing import override

import mcp.types as mt
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import Tool, ToolResult

logger = logging.getLogger(__name__)

# Default docs expiry in seconds (10 minutes).
_DEFAULT_DOCS_EXPIRY_SECONDS = 10 * 60


def _extract_first_line(description: str) -> str:
    """Extract the first meaningful sentence from a tool description.

    Returns the first non-empty line, truncated at the first sentence
    boundary (period followed by space or newline) if found.
    """
    # Skip leading whitespace and blank lines
    lines = description.strip().splitlines()
    if not lines:
        return ""

    first_line = lines[0].strip()
    if not first_line:
        # First line was blank, try the next non-empty line
        for line in lines[1:]:
            first_line = line.strip()
            if first_line:
                break
        else:
            return ""

    # Truncate at first sentence boundary (period followed by space or end)
    match = re.match(r"^(.+?\.)(?:\s|$)", first_line)
    if match:
        return match.group(1)

    return first_line


class FirstCallDocsMiddleware(Middleware):
    """Middleware that enforces documentation delivery before tool execution.

    On first call to a gated tool, returns full documentation instead of
    executing. The tool executes normally on subsequent calls within the
    expiry window. After the window expires, docs are delivered again
    (for new conversations that start after the previous one ended).

    Tools with short descriptions are exempt and execute immediately.

    Args:
        min_description_length: Character threshold for gating. Tools with
            descriptions shorter than this execute immediately without a
            documentation gate. Default: 500.
        exclude_tools: Optional set of tool names that should always bypass
            the documentation gate regardless of description length.
        docs_expiry_seconds: How long (in seconds) before docs delivery
            tracking expires. After this time, the next call will deliver
            docs again. Default: 600 (10 minutes).
    """

    def __init__(
        self,
        min_description_length: int = 500,
        exclude_tools: set[str] | None = None,
        docs_expiry_seconds: float = _DEFAULT_DOCS_EXPIRY_SECONDS,
    ) -> None:
        self._min_length = min_description_length
        self._exclude_tools = exclude_tools or set()
        self._docs_expiry = docs_expiry_seconds
        # tool_name -> full description text (captured on first tools/list)
        self._full_descriptions: dict[str, str] = {}
        # tool_name -> monotonic timestamp of when docs were delivered
        self._docs_delivered_at: dict[str, float] = {}

        logger.info(
            "FirstCallDocsMiddleware initialized (min_length=%d, expiry=%ds)",
            self._min_length,
            int(self._docs_expiry),
        )

    def _should_gate(self, tool: Tool) -> bool:
        """Check if a tool should be gated behind first-call docs."""
        if tool.name in self._exclude_tools:
            return False
        desc = tool.description or ""
        return len(desc) >= self._min_length

    def _docs_are_fresh(self, tool_name: str) -> bool:
        """Check if docs were delivered recently (within expiry window)."""
        delivered_at = self._docs_delivered_at.get(tool_name)
        if delivered_at is None:
            return False
        return (time.monotonic() - delivered_at) < self._docs_expiry

    @override
    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        """Compress descriptions for gated tools, capture full docs."""
        tools = await call_next(context)
        result: list[Tool] = []

        for tool in tools:
            if self._should_gate(tool):
                # Capture full description (idempotent — first capture wins)
                if tool.name not in self._full_descriptions:
                    self._full_descriptions[tool.name] = tool.description or ""
                    logger.debug(
                        "Captured docs for %s (%d chars)",
                        tool.name,
                        len(self._full_descriptions[tool.name]),
                    )

                # Compress to first line + mandatory hint
                short = _extract_first_line(tool.description or "")
                compressed_desc = (
                    f"{short} "
                    f"(IMPORTANT: You must call this tool once to receive "
                    f"required documentation before it can execute.)"
                )

                modified = tool.model_copy(update={
                    "description": compressed_desc,
                })
                result.append(modified)
            else:
                result.append(tool)

        if self._full_descriptions:
            logger.info(
                "First-call docs active: %d tools gated, %d tools exempt",
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
        """Enforce mandatory docs delivery before tool execution."""
        tool_name = context.message.name

        # Only gate tools we captured during on_list_tools (acts as allow-list)
        if tool_name not in self._full_descriptions:
            return await call_next(context)

        # If docs were delivered recently, execute normally
        if self._docs_are_fresh(tool_name):
            return await call_next(context)

        # First call (or expired) — deliver docs and record timestamp
        self._docs_delivered_at[tool_name] = time.monotonic()
        docs = self._full_descriptions[tool_name]
        logger.debug(
            "Delivering first-call docs for %s (expiry=%ds)",
            tool_name,
            int(self._docs_expiry),
        )

        docs_text = (
            f"REQUIRED DOCUMENTATION \u2014 {tool_name}\n"
            f"{'=' * 50}\n\n"
            f"{docs}\n\n"
            f"ACTION REQUIRED: Call {tool_name} again with your intended "
            f"arguments to execute."
        )
        # meta={} causes to_mcp_result() to return a CallToolResult,
        # which bypasses outputSchema validation in the MCP low-level
        # server. Without this, tools with return type annotations
        # would reject our docs payload as schema-invalid.
        return ToolResult(
            content=docs_text,
            structured_content={
                "status": "documentation_required",
                "tool": tool_name,
                "documentation": docs,
            },
            meta={},
        )
