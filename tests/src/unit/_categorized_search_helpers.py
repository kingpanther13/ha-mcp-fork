"""Unit tests for CategorizedSearchTransform.

Tests the categorization logic, transform_tools output, get_tool resolution,
proxy category validation, dispatch execution, and SearchKeywordsTransform.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ha_mcp._vendor.fastmcp.tools import Tool
from ha_mcp._vendor.mcp.types import ToolAnnotations

from ._search_mode_connection import search_connection as search_connection


@pytest.fixture
def read_only_on(monkeypatch):
    monkeypatch.setattr(
        "ha_mcp.read_only.get_global_settings",
        lambda: SimpleNamespace(read_only_mode=True),
    )


def _make_tool(
    name: str,
    *,
    read_only: bool = False,
    destructive: bool = False,
    idempotent: bool = False,
    description: str = "",
) -> Tool:
    """Create a minimal Tool for testing."""

    async def noop() -> str:
        return "ok"

    annotations = ToolAnnotations(
        read_only_hint=read_only,
        destructive_hint=destructive,
        idempotent_hint=idempotent,
    )
    return Tool.from_function(
        fn=noop, name=name, description=description, annotations=annotations
    )


# ---------------------------------------------------------------------------
# _categorize_tool
# ---------------------------------------------------------------------------


def set_catalog(transform, tools):
    """Supply this transform's MCP catalog; it builds its own dispatch cache."""
    transform.get_tool_catalog = AsyncMock(return_value=tools)


async def proxy_function(transform, category):
    proxy = await transform.get_tool(
        f"ha_call_{category}_tool", AsyncMock(return_value=None)
    )
    assert proxy is not None
    return proxy.fn


async def render_search_results(transform, tools):
    """Read each compact hit through the generated search tool."""
    found = []
    for tool in tools:
        # A singleton query checks formatting independently of BM25 ranking.
        set_catalog(transform, [tool])
        search = await transform.get_tool(
            "ha_search_tools", AsyncMock(return_value=None)
        )
        assert search is not None
        found.extend(await search.fn(query=tool.name, ctx=_make_ctx()))
    return found


def _make_ctx(call_tool_return: Any = "tool_result"):
    """Create a mock Context with fastmcp.call_tool."""
    ctx = MagicMock()
    ctx.fastmcp.call_tool = AsyncMock(return_value=call_tool_return)
    return ctx
