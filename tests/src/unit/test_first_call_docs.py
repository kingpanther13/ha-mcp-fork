"""Unit tests for the first-call docs middleware."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ha_mcp.middleware.first_call_docs import (
    FirstCallDocsMiddleware,
    _extract_first_line,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeTool:
    """Minimal Tool-like object for testing."""

    name: str
    description: str | None

    def model_copy(self, *, update: dict[str, Any] | None = None) -> FakeTool:
        desc = update.get("description", self.description) if update else self.description
        return FakeTool(name=self.name, description=desc)


def _make_context(
    tool_name: str = "",
    arguments: dict | None = None,
    session_id: str = "test-session-1",
) -> MagicMock:
    """Create a mock MiddlewareContext for on_call_tool."""
    ctx = MagicMock()
    ctx.message = MagicMock()
    ctx.message.name = tool_name
    ctx.message.arguments = arguments or {}

    # Simulate fastmcp_context with session_id
    fastmcp_ctx = MagicMock()
    fastmcp_ctx.request_context = MagicMock()
    fastmcp_ctx.session_id = session_id
    ctx.fastmcp_context = fastmcp_ctx
    return ctx


def _make_list_context() -> MagicMock:
    """Create a mock MiddlewareContext for on_list_tools."""
    ctx = MagicMock()
    ctx.fastmcp_context = None
    return ctx


# ---------------------------------------------------------------------------
# _extract_first_line
# ---------------------------------------------------------------------------


class TestExtractFirstLine:
    def test_single_sentence(self):
        assert _extract_first_line("Get entity state.") == "Get entity state."

    def test_multi_sentence(self):
        result = _extract_first_line("Get entity state. Returns full details.")
        assert result == "Get entity state."

    def test_multi_line(self):
        result = _extract_first_line("Get entity state.\n\nMore details here.")
        assert result == "Get entity state."

    def test_no_period(self):
        result = _extract_first_line("Get entity state")
        assert result == "Get entity state"

    def test_empty_string(self):
        assert _extract_first_line("") == ""

    def test_leading_blank_lines(self):
        result = _extract_first_line("\n\n  Get entity state. More info.")
        assert result == "Get entity state."

    def test_only_whitespace(self):
        assert _extract_first_line("   \n\n  ") == ""

    def test_period_at_end_no_space(self):
        result = _extract_first_line("Get entity state.")
        assert result == "Get entity state."

    def test_long_first_line_with_period(self):
        desc = "Create or update a Home Assistant automation. Full config required."
        result = _extract_first_line(desc)
        assert result == "Create or update a Home Assistant automation."


# ---------------------------------------------------------------------------
# on_list_tools
# ---------------------------------------------------------------------------


class TestOnListTools:
    @pytest.mark.asyncio
    async def test_short_descriptions_unchanged(self):
        """Tools with descriptions under the threshold keep originals."""
        middleware = FirstCallDocsMiddleware(min_description_length=500)
        short_tool = FakeTool(name="ha_get_state", description="Get entity state.")

        call_next = AsyncMock(return_value=[short_tool])
        result = await middleware.on_list_tools(_make_list_context(), call_next)

        assert len(result) == 1
        assert result[0].description == "Get entity state."

    @pytest.mark.asyncio
    async def test_long_descriptions_compressed(self):
        """Tools with descriptions above the threshold get compressed."""
        middleware = FirstCallDocsMiddleware(min_description_length=50)
        long_desc = "Create or update a Home Assistant automation. " + "x" * 500
        long_tool = FakeTool(name="ha_config_set_automation", description=long_desc)

        call_next = AsyncMock(return_value=[long_tool])
        result = await middleware.on_list_tools(_make_list_context(), call_next)

        assert len(result) == 1
        assert "IMPORTANT" in result[0].description
        assert "call this tool once" in result[0].description
        assert result[0].description.startswith(
            "Create or update a Home Assistant automation."
        )

    @pytest.mark.asyncio
    async def test_full_description_captured(self):
        """Full description is captured for later delivery."""
        middleware = FirstCallDocsMiddleware(min_description_length=50)
        full_desc = "Create automation. " + "detailed docs " * 50
        tool = FakeTool(name="ha_config_set_automation", description=full_desc)

        call_next = AsyncMock(return_value=[tool])
        await middleware.on_list_tools(_make_list_context(), call_next)

        assert "ha_config_set_automation" in middleware._full_descriptions
        assert middleware._full_descriptions["ha_config_set_automation"] == full_desc

    @pytest.mark.asyncio
    async def test_excluded_tools_unchanged(self):
        """Explicitly excluded tools keep their original descriptions."""
        middleware = FirstCallDocsMiddleware(
            min_description_length=50,
            exclude_tools={"ha_long_tool"},
        )
        long_desc = "Long tool. " + "x" * 500
        tool = FakeTool(name="ha_long_tool", description=long_desc)

        call_next = AsyncMock(return_value=[tool])
        result = await middleware.on_list_tools(_make_list_context(), call_next)

        assert result[0].description == long_desc
        assert "ha_long_tool" not in middleware._full_descriptions

    @pytest.mark.asyncio
    async def test_none_description_unchanged(self):
        """Tools with None descriptions pass through."""
        middleware = FirstCallDocsMiddleware(min_description_length=50)
        tool = FakeTool(name="ha_no_desc", description=None)

        call_next = AsyncMock(return_value=[tool])
        result = await middleware.on_list_tools(_make_list_context(), call_next)

        assert result[0].description is None

    @pytest.mark.asyncio
    async def test_mixed_tools(self):
        """Mix of short, long, and excluded tools handled correctly."""
        middleware = FirstCallDocsMiddleware(
            min_description_length=100,
            exclude_tools={"ha_excluded"},
        )
        tools = [
            FakeTool(name="ha_short", description="Short."),
            FakeTool(name="ha_long", description="Long tool. " + "x" * 200),
            FakeTool(name="ha_excluded", description="Excluded. " + "x" * 200),
        ]

        call_next = AsyncMock(return_value=tools)
        result = await middleware.on_list_tools(_make_list_context(), call_next)

        assert result[0].description == "Short."  # unchanged
        assert "IMPORTANT" in result[1].description  # compressed
        assert result[2].description == tools[2].description  # excluded, unchanged


# ---------------------------------------------------------------------------
# on_call_tool
# ---------------------------------------------------------------------------


class TestOnCallTool:
    @pytest.mark.asyncio
    async def test_short_tool_executes_immediately(self):
        """Tools not in the docs map execute immediately without a gate."""
        middleware = FirstCallDocsMiddleware(min_description_length=500)
        # No tools/list called, so no docs captured — tool should pass through

        call_next = AsyncMock(return_value="executed")
        context = _make_context(tool_name="ha_get_state")

        result = await middleware.on_call_tool(context, call_next)

        assert result == "executed"
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_first_call_returns_docs_not_execution(self):
        """First call to a gated tool returns docs and does NOT execute."""
        middleware = FirstCallDocsMiddleware(min_description_length=50)
        # Simulate tools/list capturing the full description
        full_docs = "Create automation. " + "detailed docs " * 50
        middleware._full_descriptions["ha_config_set_automation"] = full_docs

        call_next = AsyncMock(return_value="should not be called")
        context = _make_context(
            tool_name="ha_config_set_automation",
            arguments={"config": {"alias": "Test"}},
        )

        result = await middleware.on_call_tool(context, call_next)

        # call_next must NOT have been called — execution is blocked
        call_next.assert_not_awaited()

        # Result should contain the full docs
        content = result.content[0].text
        assert "REQUIRED DOCUMENTATION" in content
        assert "ha_config_set_automation" in content
        assert "detailed docs" in content
        assert "call ha_config_set_automation again" in content
        assert "Do not call a different tool" in content

    @pytest.mark.asyncio
    async def test_second_call_executes(self):
        """Second call to a gated tool (after docs delivered) executes normally."""
        middleware = FirstCallDocsMiddleware(min_description_length=50)
        middleware._full_descriptions["ha_config_set_automation"] = "docs " * 100

        call_next = AsyncMock(return_value="execution result")
        context = _make_context(tool_name="ha_config_set_automation")

        # First call — docs delivery
        await middleware.on_call_tool(context, call_next)
        call_next.assert_not_awaited()

        # Second call — should execute
        call_next.reset_mock()
        result = await middleware.on_call_tool(context, call_next)

        call_next.assert_awaited_once()
        assert result == "execution result"

    @pytest.mark.asyncio
    async def test_different_sessions_independent(self):
        """Two different sessions each need their own docs delivery."""
        middleware = FirstCallDocsMiddleware(min_description_length=50)
        middleware._full_descriptions["ha_tool"] = "docs " * 100

        call_next = AsyncMock(return_value="executed")

        # Session 1 — first call gets docs
        ctx1 = _make_context(tool_name="ha_tool", session_id="session-A")
        result1 = await middleware.on_call_tool(ctx1, call_next)
        call_next.assert_not_awaited()
        assert "REQUIRED DOCUMENTATION" in result1.content[0].text

        # Session 2 — also gets docs (independent)
        call_next.reset_mock()
        ctx2 = _make_context(tool_name="ha_tool", session_id="session-B")
        result2 = await middleware.on_call_tool(ctx2, call_next)
        call_next.assert_not_awaited()
        assert "REQUIRED DOCUMENTATION" in result2.content[0].text

        # Session 1 — second call executes
        call_next.reset_mock()
        result3 = await middleware.on_call_tool(ctx1, call_next)
        call_next.assert_awaited_once()
        assert result3 == "executed"

    @pytest.mark.asyncio
    async def test_excluded_tool_executes_immediately(self):
        """Excluded tools bypass the gate even with captured docs."""
        middleware = FirstCallDocsMiddleware(
            min_description_length=50,
            exclude_tools={"ha_special"},
        )
        # Even if somehow docs are captured, excluded tools should bypass
        # (in practice they won't be captured because on_list_tools checks too)

        call_next = AsyncMock(return_value="executed")
        context = _make_context(tool_name="ha_special")

        result = await middleware.on_call_tool(context, call_next)

        call_next.assert_awaited_once()
        assert result == "executed"

    @pytest.mark.asyncio
    async def test_session_id_fallback(self):
        """When session_id is unavailable, falls back to __default__."""
        middleware = FirstCallDocsMiddleware(min_description_length=50)
        middleware._full_descriptions["ha_tool"] = "docs " * 100

        call_next = AsyncMock()

        # Context with no fastmcp_context
        ctx = MagicMock()
        ctx.message = MagicMock()
        ctx.message.name = "ha_tool"
        ctx.message.arguments = {}
        ctx.fastmcp_context = None

        result = await middleware.on_call_tool(ctx, call_next)

        # Should still work — using __default__ session key
        assert "REQUIRED DOCUMENTATION" in result.content[0].text
        assert "__default__" in middleware._docs_delivered

    @pytest.mark.asyncio
    async def test_docs_response_instructs_to_call_same_tool(self):
        """The docs response explicitly tells the LLM to call the same tool."""
        middleware = FirstCallDocsMiddleware(min_description_length=50)
        middleware._full_descriptions["ha_my_tool"] = "Full documentation here."

        call_next = AsyncMock()
        context = _make_context(tool_name="ha_my_tool")

        result = await middleware.on_call_tool(context, call_next)
        content = result.content[0].text

        assert "call ha_my_tool again" in content
        assert "Do not call a different tool" in content
        assert "ACTION REQUIRED" in content
