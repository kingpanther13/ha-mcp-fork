"""First-Call Docs middleware for progressive disclosure of tool documentation.

Compresses tool descriptions in tools/list responses and enforces mandatory
documentation delivery on the first call to each tool per session. Tools
cannot execute until the LLM has received their full documentation.

Tools with short descriptions (below a configurable character threshold)
are exempt and execute immediately without a documentation gate.

For transports with unstable sessions (e.g., proxied/tunneled connections
where each HTTP request gets a new session_id), the middleware uses a
token-based acknowledgment mechanism: docs include a short token that the
LLM must echo back via a ``_docs_ack`` argument to prove it received the
documentation. This is analogous to the ATIS pattern in aviation — pilots
report the ATIS code to prove they have current information.
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from collections import OrderedDict
from collections.abc import Sequence
from typing import Any, override

import mcp.types as mt
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import Tool, ToolResult

logger = logging.getLogger(__name__)

# Argument name used by LLMs to acknowledge documentation receipt.
_DOCS_ACK_PARAM = "_docs_ack"

# Maximum number of outstanding ack tokens per tool before oldest are pruned.
_MAX_TOKENS_PER_TOOL = 100

# Token time-to-live in seconds (4 hours).
_TOKEN_TTL_SECONDS = 4 * 60 * 60


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

    Two mechanisms are used to track docs delivery:

    1. **Session-based** (primary): For transports with stable session IDs,
       the middleware tracks which tools have received docs per session.
       Second call from the same session executes without a token.

    2. **Token-based** (fallback): For transports with unstable sessions
       (e.g., each HTTP request gets a new session_id through a proxy),
       the docs response includes a short acknowledgment token. The LLM
       echoes it back via a ``_docs_ack`` argument to prove it read the
       docs. The middleware strips ``_docs_ack`` before forwarding to the
       actual tool handler.

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
        # tool_name -> {token: monotonic_timestamp} (for unstable-session fallback)
        self._ack_tokens: dict[str, dict[str, float]] = {}

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

    def _generate_ack_token(self, tool_name: str) -> str:
        """Generate and store an ack token for a tool.

        Returns a short hex token. Tokens expire after ``_TOKEN_TTL_SECONDS``.
        Prunes oldest tokens if the per-tool limit is exceeded.
        """
        now = time.monotonic()
        token = secrets.token_hex(3)  # 6-char hex string
        tokens = self._ack_tokens.setdefault(tool_name, {})
        # Prune expired tokens first
        tokens = {t: ts for t, ts in tokens.items() if now - ts < _TOKEN_TTL_SECONDS}
        # Prune if still at capacity (clear and start fresh)
        if len(tokens) >= _MAX_TOKENS_PER_TOOL:
            tokens.clear()
        tokens[token] = now
        self._ack_tokens[tool_name] = tokens
        return token

    def _validate_ack_token(self, tool_name: str, token: str) -> bool:
        """Check if an ack token is valid and not expired for a tool."""
        tokens = self._ack_tokens.get(tool_name)
        if tokens is None or token not in tokens:
            return False
        # Check expiry
        if time.monotonic() - tokens[token] >= _TOKEN_TTL_SECONDS:
            del tokens[token]
            return False
        return True

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

                # Inject _docs_ack into schema so clients can pass it back,
                # and allow additional properties so transports that
                # enforce schema validation won't reject it.
                params = dict(tool.parameters) if tool.parameters else {}
                props = dict(params.get("properties", {}))
                props[_DOCS_ACK_PARAM] = {
                    "type": "string",
                    "description": "Documentation acknowledgment token.",
                }
                params["properties"] = props
                params["additionalProperties"] = True

                modified = tool.model_copy(update={
                    "description": compressed_desc,
                    "parameters": params,
                })
                result.append(modified)

                # Debug: log one example to verify schema injection
                if tool.name == "ha_list_services":
                    logger.warning(
                        "SCHEMA DEBUG ha_list_services — "
                        "additionalProperties=%s, properties_keys=%s, "
                        "full_params=%s",
                        modified.parameters.get("additionalProperties"),
                        list(modified.parameters.get("properties", {}).keys()),
                        modified.parameters,
                    )
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

        # Check for ack token in arguments (strip before forwarding)
        args = context.message.arguments or {}
        ack_token = args.pop(_DOCS_ACK_PARAM, None)

        # Session-based tracking (works for stable sessions)
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

        # Path 1: Session already has docs for this tool — execute
        if tool_name in seen:
            return await call_next(context)

        # Path 2: Valid ack token — docs were received (unstable session fallback)
        if ack_token and self._validate_ack_token(tool_name, str(ack_token)):
            seen.add(tool_name)
            logger.debug(
                "Ack token validated for %s (session=%s)",
                tool_name,
                session_key[:12],
            )
            return await call_next(context)

        # Path 3: No proof of docs delivery — deliver docs with ack token
        seen.add(tool_name)
        token = self._generate_ack_token(tool_name)
        docs = self._full_descriptions[tool_name]
        logger.debug(
            "Delivering first-call docs for %s (session=%s, ack=%s)",
            tool_name,
            session_key[:12],
            token,
        )
        # Embed the ack token directly in the documentation text so it
        # survives any field-level filtering by MCP transports/clients.
        docs_with_ack = (
            f"{docs}\n\n"
            f"ACTION REQUIRED: Call {tool_name} again with your intended "
            f"arguments. You MUST include _docs_ack: {token} to confirm "
            f"you have read this documentation."
        )
        docs_text = (
            f"REQUIRED DOCUMENTATION \u2014 {tool_name}\n"
            f"{'=' * 50}\n\n"
            f"{docs_with_ack}\n"
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
                "documentation": docs_with_ack,
            },
            meta={},
        )
