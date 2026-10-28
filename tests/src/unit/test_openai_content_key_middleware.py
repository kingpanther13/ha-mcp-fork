"""Unit tests for OpenAiContentKeyMiddleware.

ChatGPT (clientInfo name ``openai-mcp``) shows ``{}`` for any tool result whose
JSON has a top-level ``content`` key, so that client alone gets the key renamed
to ``contentText``; every other client keeps ``content``.
"""

import json

import pytest

from ha_mcp._vendor.fastmcp import Client, FastMCP
from ha_mcp._vendor.mcp.types import Implementation
from ha_mcp.tools.openai_content_key_middleware import OpenAiContentKeyMiddleware


def _make_mcp() -> FastMCP:
    mcp = FastMCP("test")
    mcp.add_middleware(OpenAiContentKeyMiddleware())

    @mcp.tool()
    async def read_text() -> dict:
        return {"success": True, "content": "## Rooms", "nested": {"content": "x"}}

    return mcp


async def _call(client_name: str | None) -> tuple[dict, dict]:
    """Return (structured_content, parsed text block) for one read_text call."""
    info = Implementation(name=client_name, version="1.0.0") if client_name else None
    async with Client(_make_mcp(), client_info=info) as client:
        result = await client.call_tool("read_text", {})
    return result.structured_content, json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_chatgpt_gets_text_it_can_render_instead_of_empty_result():
    structured, text = await _call("openai-mcp")

    for payload in (structured, text):
        assert "content" not in payload
        assert payload["contentText"] == "## Rooms"
        assert payload["success"] is True
        # Only the top-level key is renamed.
        assert payload["nested"] == {"content": "x"}


@pytest.mark.asyncio
@pytest.mark.parametrize("client_name", ["claude-code", None])
async def test_other_clients_keep_the_content_key_they_already_read(client_name):
    structured, text = await _call(client_name)

    for payload in (structured, text):
        assert payload["content"] == "## Rooms"
        assert "contentText" not in payload
