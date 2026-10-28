"""The middleware at the outer end of the server's chain.

Registration order is inbound order (first added = outermost), so these run
ahead of every gate and result rewriter the server adds after them.
"""

from __future__ import annotations

from ha_mcp._vendor.fastmcp import FastMCP

from .openai_content_key_middleware import OpenAiContentKeyMiddleware
from .renamed_tool_middleware import RenamedToolAliasMiddleware
from .tool_search_hint_middleware import ToolSearchHintMiddleware
from .validation_middleware import ValidationErrorMiddleware


def add_outer_middleware(mcp: FastMCP) -> None:
    """Register the outermost middleware, in chain order."""
    # Keep the pre-rename tool names callable for clients that still hold
    # an older catalog. First in the chain, so everything after it sees the
    # current name it is keyed on.
    mcp.add_middleware(RenamedToolAliasMiddleware())

    # ChatGPT shows `{}` for a result with a top-level `content` key. Outer to
    # every result-rewriting middleware so it renames the final output.
    mcp.add_middleware(OpenAiContentKeyMiddleware())

    # Convert Pydantic type-validation errors to structured ToolErrors so
    # models get actionable guidance instead of raw Pydantic messages.
    mcp.add_middleware(ValidationErrorMiddleware())

    # Replace the opaque "Unknown tool" FastMCP raises when a client calls
    # ha_search_tools / the ha_call_* proxies while Tool Search is off
    # (stale client tool-list cache) with an actionable refresh hint.
    mcp.add_middleware(ToolSearchHintMiddleware())
