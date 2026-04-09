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
    parameters: dict[str, Any] | None = None

    def model_copy(self, *, update: dict[str, Any] | None = None) -> FakeTool:
        if not update:
            return FakeTool(name=self.name, description=self.description, parameters=self.parameters)
        return FakeTool(
            name=update.get("name", self.name),
            description=update.get("description", self.description),
            parameters=update.get("parameters", self.parameters),
        )


def _make_context(
    tool_name: str = "",
    arguments: dict | None = None,
) -> MagicMock:
    """Create a mock MiddlewareContext for on_call_tool."""
    ctx = MagicMock()
    ctx.message = MagicMock()
    ctx.message.name = tool_name
    ctx.message.arguments = arguments or {}
    ctx.fastmcp_context = None
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
    async def test_gated_tools_schema_unchanged(self):
        """Gated tools keep their original schema (only description changes)."""
        middleware = FirstCallDocsMiddleware(min_description_length=50)
        long_desc = "Create automation. " + "x" * 500
        tool = FakeTool(
            name="ha_config_set_automation",
            description=long_desc,
            parameters={
                "type": "object",
                "properties": {"config": {"type": "object"}},
                "required": ["config"],
                "additionalProperties": False,
            },
        )

        call_next = AsyncMock(return_value=[tool])
        result = await middleware.on_list_tools(_make_list_context(), call_next)

        params = result[0].parameters
        # Schema should be unchanged — no extra properties injected
        assert params["properties"] == {"config": {"type": "object"}}
        assert params["required"] == ["config"]

    @pytest.mark.asyncio
    async def test_short_tools_schema_unchanged(self):
        """Short-description tools keep their original schema."""
        middleware = FirstCallDocsMiddleware(min_description_length=500)
        tool = FakeTool(
            name="ha_get_state",
            description="Get entity state.",
            parameters={
                "type": "object",
                "properties": {"entity_id": {"type": "string"}},
                "additionalProperties": False,
            },
        )

        call_next = AsyncMock(return_value=[tool])
        result = await middleware.on_list_tools(_make_list_context(), call_next)

        assert result[0].parameters["properties"] == {"entity_id": {"type": "string"}}

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
        assert "Call ha_config_set_automation again" in content

    @pytest.mark.asyncio
    async def test_second_call_executes_within_expiry(self):
        """Second call to a gated tool (within expiry) executes normally."""
        middleware = FirstCallDocsMiddleware(min_description_length=50, docs_expiry_seconds=60)
        middleware._full_descriptions["ha_config_set_automation"] = "docs " * 100

        call_next = AsyncMock(return_value="execution result")
        context = _make_context(tool_name="ha_config_set_automation")

        # First call — docs delivery
        await middleware.on_call_tool(context, call_next)
        call_next.assert_not_awaited()

        # Second call — should execute (within expiry window)
        call_next.reset_mock()
        result = await middleware.on_call_tool(context, call_next)

        call_next.assert_awaited_once()
        assert result == "execution result"

    @pytest.mark.asyncio
    async def test_expired_docs_redeliver(self):
        """After expiry, docs are delivered again."""
        middleware = FirstCallDocsMiddleware(min_description_length=50, docs_expiry_seconds=60)
        middleware._full_descriptions["ha_tool"] = "docs " * 100

        call_next = AsyncMock(return_value="executed")

        # First call — gets docs
        ctx = _make_context(tool_name="ha_tool")
        result1 = await middleware.on_call_tool(ctx, call_next)
        call_next.assert_not_awaited()
        assert "REQUIRED DOCUMENTATION" in result1.content[0].text

        # Simulate expiry by backdating the timestamp
        middleware._docs_delivered_at["ha_tool"] -= 61

        # Next call — docs expired, should get docs again
        call_next.reset_mock()
        result2 = await middleware.on_call_tool(ctx, call_next)
        call_next.assert_not_awaited()
        assert "REQUIRED DOCUMENTATION" in result2.content[0].text

    @pytest.mark.asyncio
    async def test_per_tool_independent_tracking(self):
        """Each tool has its own independent expiry timestamp."""
        middleware = FirstCallDocsMiddleware(min_description_length=50, docs_expiry_seconds=60)
        middleware._full_descriptions["ha_tool_a"] = "docs A " * 100
        middleware._full_descriptions["ha_tool_b"] = "docs B " * 100

        call_next = AsyncMock(return_value="executed")

        # Tool A — first call gets docs
        ctx_a = _make_context(tool_name="ha_tool_a")
        result_a1 = await middleware.on_call_tool(ctx_a, call_next)
        call_next.assert_not_awaited()
        assert "REQUIRED DOCUMENTATION" in result_a1.content[0].text

        # Tool A — second call executes
        call_next.reset_mock()
        result_a2 = await middleware.on_call_tool(ctx_a, call_next)
        call_next.assert_awaited_once()
        assert result_a2 == "executed"

        # Tool B — first call still gets docs (independent of Tool A)
        call_next.reset_mock()
        ctx_b = _make_context(tool_name="ha_tool_b")
        result_b1 = await middleware.on_call_tool(ctx_b, call_next)
        call_next.assert_not_awaited()
        assert "REQUIRED DOCUMENTATION" in result_b1.content[0].text

    @pytest.mark.asyncio
    async def test_excluded_tool_executes_immediately(self):
        """Excluded tools bypass the gate even with captured docs."""
        middleware = FirstCallDocsMiddleware(
            min_description_length=50,
            exclude_tools={"ha_special"},
        )

        call_next = AsyncMock(return_value="executed")
        context = _make_context(tool_name="ha_special")

        result = await middleware.on_call_tool(context, call_next)

        call_next.assert_awaited_once()
        assert result == "executed"

    @pytest.mark.asyncio
    async def test_docs_response_instructs_to_call_same_tool(self):
        """The docs response explicitly tells the LLM to call the same tool."""
        middleware = FirstCallDocsMiddleware(min_description_length=50)
        middleware._full_descriptions["ha_my_tool"] = "Full documentation here."

        call_next = AsyncMock()
        context = _make_context(tool_name="ha_my_tool")

        result = await middleware.on_call_tool(context, call_next)
        content = result.content[0].text

        assert "Call ha_my_tool again" in content
        assert "ACTION REQUIRED" in content

    @pytest.mark.asyncio
    async def test_docs_response_includes_structured_content(self):
        """Docs response includes structured_content for outputSchema validation."""
        middleware = FirstCallDocsMiddleware(min_description_length=50)
        middleware._full_descriptions["ha_my_tool"] = "Full documentation here."

        call_next = AsyncMock()
        context = _make_context(tool_name="ha_my_tool")

        result = await middleware.on_call_tool(context, call_next)

        assert result.structured_content is not None
        assert result.structured_content["status"] == "documentation_required"
        assert "Full documentation here." in result.structured_content["documentation"]

    @pytest.mark.asyncio
    async def test_docs_response_has_meta_for_schema_bypass(self):
        """Docs response sets meta={} so to_mcp_result() returns CallToolResult."""
        middleware = FirstCallDocsMiddleware(min_description_length=50)
        middleware._full_descriptions["ha_my_tool"] = "Docs here."

        call_next = AsyncMock()
        context = _make_context(tool_name="ha_my_tool")

        result = await middleware.on_call_tool(context, call_next)

        # meta={} causes ToolResult.to_mcp_result() to return CallToolResult,
        # which bypasses outputSchema validation in the MCP low-level server
        assert result.meta is not None
        assert isinstance(result.meta, dict)

    @pytest.mark.asyncio
    async def test_zero_expiry_always_delivers_docs(self):
        """With expiry=0, docs are delivered on every call."""
        middleware = FirstCallDocsMiddleware(min_description_length=50, docs_expiry_seconds=0)
        middleware._full_descriptions["ha_tool"] = "docs " * 100

        call_next = AsyncMock(return_value="executed")

        # First call — docs
        ctx = _make_context(tool_name="ha_tool")
        result1 = await middleware.on_call_tool(ctx, call_next)
        call_next.assert_not_awaited()
        assert "REQUIRED DOCUMENTATION" in result1.content[0].text

        # Second call — still docs (expiry=0 means always expired)
        call_next.reset_mock()
        result2 = await middleware.on_call_tool(ctx, call_next)
        call_next.assert_not_awaited()
        assert "REQUIRED DOCUMENTATION" in result2.content[0].text
