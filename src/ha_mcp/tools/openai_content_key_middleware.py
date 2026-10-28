"""FastMCP middleware: rename a top-level ``content`` result key for ChatGPT.

ChatGPT (clientInfo name ``openai-mcp``) shows ``{}`` for any tool result
whose JSON has a top-level ``content`` key, while every other client reads it
normally. That blanks ``ha_get_skill_guide`` and ``ha_read_file``, among
others. For that client alone the key is renamed to ``contentText``; nested
``content`` keys are untouched.

The check uses the client's self-reported name. It only decides a result
field's name, never access, so a client that falsely claims ``openai-mcp``
just gets ``contentText``.
"""

from __future__ import annotations

import json
from typing import Any

from ha_mcp._vendor.fastmcp.server.middleware.middleware import (
    CallNext,
    Middleware,
    MiddlewareContext,
)
from ha_mcp._vendor.fastmcp.tools.base import default_serializer
from ha_mcp._vendor.mcp.types import TextContent

from .tools_bug_report import _extract_client_info

OPENAI_CLIENT_NAME = "openai-mcp"
RENAMED_KEY = "contentText"


class OpenAiContentKeyMiddleware(Middleware):
    """Rename a tool result's top-level ``content`` key for ChatGPT."""

    async def on_call_tool(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Any:
        result = await call_next(context)
        structured = getattr(result, "structured_content", None)
        if not isinstance(structured, dict) or "content" not in structured:
            return result
        client = _extract_client_info(context.fastmcp_context)
        if client.get("name") != OPENAI_CLIENT_NAME:
            return result

        renamed = {
            (RENAMED_KEY if key == "content" else key): value
            for key, value in structured.items()
        }
        result.structured_content = renamed
        # The text block is FastMCP's serialization of the same dict; rewrite
        # only a block that still matches it, leaving custom content alone.
        for block in result.content or []:
            if isinstance(block, TextContent) and _json_equals(block.text, structured):
                block.text = default_serializer(renamed)
        return result


def _json_equals(text: str, value: dict[str, Any]) -> bool:
    try:
        return bool(json.loads(text) == value)
    except ValueError:
        return False
