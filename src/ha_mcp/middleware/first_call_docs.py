"""First-Call Docs middleware for progressive disclosure of tool documentation.

Compresses tool descriptions in tools/list responses and enforces mandatory
documentation delivery on the first call to each tool per session. Tools
cannot execute until the LLM has received their full documentation.

Tools with short descriptions (below a configurable character threshold)
are exempt and execute immediately without a documentation gate.
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from collections.abc import Sequence
from typing import Any, override

import mcp.types as mt
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import Tool, ToolResult

logger = logging.getLogger(__name__)


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

    On first call to a gated tool in a session, returns full documentation
    instead of executing. The tool CANNOT execute until docs have been
    delivered. Tools with short descriptions are exempt.

    Args:
        min_description_length: Character threshold for gating. Tools with
            descriptions shorter than this execute immediately without a
            documentation gate. Default: 500.
        exclude_tools: Optional set of tool names that should always bypass
            the documentation gate regardless of description length.
    """

    # Maximum number of sessions to track before evicting the oldest.
    # Prevents unbounded memory growth in long-running HTTP servers.
    _MAX_SESSIONS = 1000

    def __init__(
        self,
        min_description_length: int = 500,
        exclude_tools: set[str] | None = None,
    ) -> None:
        self._min_length = min_description_length
        self._exclude_tools = exclude_tools or set()
        # tool_name -> full description text (captured on first tools/list)
        self._full_descriptions: dict[str, str] = {}
        # session_key -> set of tool names that have received docs
        # OrderedDict for LRU eviction of oldest sessions
        self._docs_delivered: OrderedDict[str, set[str]] = OrderedDict()
        # Global fallback: tracks tools that have had docs delivered to ANY
        # session. Handles transports with unstable sessions (e.g., proxied
        # connections where each HTTP request gets a new session_id).
        # Resets on server restart, which is correct (fresh context = re-deliver).
        self._docs_ever_delivered: set[str] = set()

    def _get_session_key(self, context: MiddlewareContext[Any]) -> str:
        """Get a stable session key for tracking docs delivery.

        Uses Context.session_id when available (HTTP transports).
        Falls back to "__default__" for stdio (single concurrent session).
        """
        ctx = context.fastmcp_context
        if ctx is not None and ctx.request_context is not None:
            try:
                return ctx.session_id
            except (RuntimeError, AttributeError):
                pass
        return "__default__"

    def _should_gate(self, tool: Tool) -> bool:
        """Check if a tool should be gated behind first-call docs."""
        if tool.name in self._exclude_tools:
            return False
        desc = tool.description or ""
        return len(desc) >= self._min_length

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
                result.append(tool.model_copy(update={"description": compressed_desc}))
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

        session_key = self._get_session_key(context)
        if session_key in self._docs_delivered:
            # Move to end (most recently used)
            self._docs_delivered.move_to_end(session_key)
            seen = self._docs_delivered[session_key]
        else:
            # Evict oldest session if at capacity
            while len(self._docs_delivered) >= self._MAX_SESSIONS:
                evicted_key, _ = self._docs_delivered.popitem(last=False)
                logger.debug("Evicted oldest session from docs cache: %s", evicted_key[:12])
            seen: set[str] = set()
            self._docs_delivered[session_key] = seen

        # Block execution only if docs have NEVER been delivered for this tool
        # (neither in the current session nor globally). The global fallback
        # handles transports with unstable sessions where each request gets
        # a new session_id (e.g., proxied/tunneled connections).
        if tool_name not in seen and tool_name not in self._docs_ever_delivered:
            seen.add(tool_name)
            self._docs_ever_delivered.add(tool_name)
            docs = self._full_descriptions[tool_name]
            logger.debug(
                "Delivering first-call docs for %s (session=%s)",
                tool_name,
                session_key[:12],
            )
            docs_text = (
                f"REQUIRED DOCUMENTATION \u2014 {tool_name}\n"
                f"{'=' * 50}\n\n"
                f"{docs}\n\n"
                f"{'=' * 50}\n\n"
                f"ACTION REQUIRED: You have received the documentation "
                f"for {tool_name}. You MUST now call {tool_name} again "
                f"with your arguments to execute it. Do not call a "
                f"different tool \u2014 call {tool_name} with the correct "
                f"arguments based on the documentation above."
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
                    "message": (
                        f"Call {tool_name} again with your arguments to execute."
                    ),
                },
                meta={},
            )

        # Docs already delivered — execute normally
        seen.add(tool_name)  # Sync to current session
        return await call_next(context)
