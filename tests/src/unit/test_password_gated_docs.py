"""Unit tests for the password-gated docs middleware."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ha_mcp.middleware.password_gated_docs import (
    _PASSWORD_PARAM,
    _REQUEST_DOCS_SENTINEL,
    PasswordGatedDocsMiddleware,
    _extract_first_line,
    _password_from_description,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeTool:
    """Minimal Tool-like object for testing."""

    name: str
    description: str | None
    parameters: dict[str, Any] | None = field(default_factory=lambda: {
        "type": "object",
        "properties": {},
    })

    def model_copy(self, *, update: dict[str, Any] | None = None) -> FakeTool:
        if not update:
            return FakeTool(
                name=self.name,
                description=self.description,
                parameters=self.parameters,
            )
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
        assert _extract_first_line("Get entity state") == "Get entity state"

    def test_empty_string(self):
        assert _extract_first_line("") == ""

    def test_leading_blank_lines(self):
        result = _extract_first_line("\n\n  Get entity state. More info.")
        assert result == "Get entity state."

    def test_only_whitespace(self):
        assert _extract_first_line("   \n\n  ") == ""


# ---------------------------------------------------------------------------
# _password_from_description
# ---------------------------------------------------------------------------


class TestPasswordFromDescription:
    def test_deterministic(self):
        """Same description always produces the same password."""
        desc = "Some tool description " * 30
        assert _password_from_description(desc) == _password_from_description(desc)

    def test_different_descriptions_different_passwords(self):
        """Different descriptions produce different passwords."""
        pw_a = _password_from_description("Description A " * 30)
        pw_b = _password_from_description("Description B " * 30)
        assert pw_a != pw_b

    def test_length(self):
        """Password is 16 hex characters."""
        pw = _password_from_description("test")
        assert len(pw) == 16
        assert all(c in "0123456789abcdef" for c in pw)

    def test_changes_on_description_change(self):
        """Password changes when description content changes."""
        pw_v1 = _password_from_description("Create automation. Details v1.")
        pw_v2 = _password_from_description("Create automation. Details v2.")
        assert pw_v1 != pw_v2


# ---------------------------------------------------------------------------
# on_list_tools — description compression and schema injection
# ---------------------------------------------------------------------------


class TestOnListTools:
    @pytest.mark.asyncio
    async def test_short_descriptions_unchanged(self):
        """Tools with descriptions under the threshold keep originals."""
        mw = PasswordGatedDocsMiddleware(min_description_length=500)
        short_tool = FakeTool(name="ha_get_state", description="Get entity state.")

        call_next = AsyncMock(return_value=[short_tool])
        result = await mw.on_list_tools(_make_list_context(), call_next)

        assert len(result) == 1
        assert result[0].description == "Get entity state."
        # Short tools should NOT get the password parameter
        assert _PASSWORD_PARAM not in result[0].parameters.get("properties", {})

    @pytest.mark.asyncio
    async def test_long_descriptions_compressed_with_password(self):
        """Tools above threshold get compressed description + password param."""
        mw = PasswordGatedDocsMiddleware(min_description_length=50)
        long_desc = "Create or update a Home Assistant automation. " + "x" * 500
        tool = FakeTool(name="ha_config_set_automation", description=long_desc)

        call_next = AsyncMock(return_value=[tool])
        result = await mw.on_list_tools(_make_list_context(), call_next)

        assert len(result) == 1
        # Description is compressed
        assert "IMPORTANT" in result[0].description
        assert "_docs_password='REQUEST_DOCS'" in result[0].description
        # Password param is in the schema
        assert _PASSWORD_PARAM in result[0].parameters["properties"]
        assert _PASSWORD_PARAM in result[0].parameters["required"]

    @pytest.mark.asyncio
    async def test_existing_schema_preserved(self):
        """Gated tools keep existing schema properties alongside the password."""
        mw = PasswordGatedDocsMiddleware(min_description_length=50)
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
        result = await mw.on_list_tools(_make_list_context(), call_next)

        params = result[0].parameters
        # Original property preserved
        assert "config" in params["properties"]
        # Password param added
        assert _PASSWORD_PARAM in params["properties"]
        # Both are required
        assert "config" in params["required"]
        assert _PASSWORD_PARAM in params["required"]

    @pytest.mark.asyncio
    async def test_full_description_captured(self):
        """Full description is captured internally for later delivery."""
        mw = PasswordGatedDocsMiddleware(min_description_length=50)
        full_desc = "Create automation. " + "detailed docs " * 50
        tool = FakeTool(name="ha_config_set_automation", description=full_desc)

        call_next = AsyncMock(return_value=[tool])
        await mw.on_list_tools(_make_list_context(), call_next)

        assert "ha_config_set_automation" in mw._full_descriptions
        assert mw._full_descriptions["ha_config_set_automation"] == full_desc

    @pytest.mark.asyncio
    async def test_password_derived_from_description(self):
        """Password cached after on_list_tools matches description hash."""
        mw = PasswordGatedDocsMiddleware(min_description_length=50)
        full_desc = "Create automation. " + "detailed docs " * 50
        tool = FakeTool(name="ha_config_set_automation", description=full_desc)

        call_next = AsyncMock(return_value=[tool])
        await mw.on_list_tools(_make_list_context(), call_next)

        expected = _password_from_description(full_desc)
        assert mw._passwords["ha_config_set_automation"] == expected

    @pytest.mark.asyncio
    async def test_excluded_tools_unchanged(self):
        """Explicitly excluded tools bypass the gate."""
        mw = PasswordGatedDocsMiddleware(
            min_description_length=50,
            exclude_tools={"ha_long_tool"},
        )
        long_desc = "Long tool. " + "x" * 500
        tool = FakeTool(name="ha_long_tool", description=long_desc)

        call_next = AsyncMock(return_value=[tool])
        result = await mw.on_list_tools(_make_list_context(), call_next)

        assert result[0].description == long_desc
        assert "ha_long_tool" not in mw._full_descriptions

    @pytest.mark.asyncio
    async def test_none_description_unchanged(self):
        """Tools with None descriptions pass through."""
        mw = PasswordGatedDocsMiddleware(min_description_length=50)
        tool = FakeTool(name="ha_no_desc", description=None)

        call_next = AsyncMock(return_value=[tool])
        result = await mw.on_list_tools(_make_list_context(), call_next)

        assert result[0].description is None

    @pytest.mark.asyncio
    async def test_mixed_tools(self):
        """Mix of short, long, and excluded tools handled correctly."""
        mw = PasswordGatedDocsMiddleware(
            min_description_length=100,
            exclude_tools={"ha_excluded"},
        )
        tools = [
            FakeTool(name="ha_short", description="Short."),
            FakeTool(name="ha_long", description="Long tool. " + "x" * 200),
            FakeTool(name="ha_excluded", description="Excluded. " + "x" * 200),
        ]

        call_next = AsyncMock(return_value=tools)
        result = await mw.on_list_tools(_make_list_context(), call_next)

        # Short — unchanged, no password param
        assert result[0].description == "Short."
        assert _PASSWORD_PARAM not in result[0].parameters.get("properties", {})
        # Long — compressed, has password param
        assert "IMPORTANT" in result[1].description
        assert _PASSWORD_PARAM in result[1].parameters["properties"]
        # Excluded — unchanged, no password param
        assert result[2].description == tools[2].description


# ---------------------------------------------------------------------------
# on_call_tool — password enforcement
# ---------------------------------------------------------------------------


class TestOnCallTool:
    @pytest.mark.asyncio
    async def test_short_tool_executes_immediately(self):
        """Tools not in the docs map execute immediately."""
        mw = PasswordGatedDocsMiddleware(min_description_length=500)

        call_next = AsyncMock(return_value="executed")
        context = _make_context(tool_name="ha_get_state")

        result = await mw.on_call_tool(context, call_next)

        assert result == "executed"
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_request_docs_returns_docs_and_password(self):
        """Calling with REQUEST_DOCS returns documentation + password."""
        mw = PasswordGatedDocsMiddleware(min_description_length=50)
        full_docs = "Create automation. " + "detailed docs " * 50
        mw._full_descriptions["ha_config_set_automation"] = full_docs
        mw._passwords["ha_config_set_automation"] = _password_from_description(full_docs)

        call_next = AsyncMock(return_value="should not be called")
        context = _make_context(
            tool_name="ha_config_set_automation",
            arguments={_PASSWORD_PARAM: _REQUEST_DOCS_SENTINEL},
        )

        result = await mw.on_call_tool(context, call_next)

        call_next.assert_not_awaited()
        content = result.content[0].text
        assert "REQUIRED DOCUMENTATION" in content
        assert "ha_config_set_automation" in content
        assert "detailed docs" in content
        assert "ACCESS PASSWORD:" in content
        assert "ACTION REQUIRED" in content

    @pytest.mark.asyncio
    async def test_no_password_returns_docs(self):
        """Calling without a password returns docs."""
        mw = PasswordGatedDocsMiddleware(min_description_length=50)
        full_docs = "docs " * 100
        mw._full_descriptions["ha_tool"] = full_docs
        mw._passwords["ha_tool"] = _password_from_description(full_docs)

        call_next = AsyncMock(return_value="should not be called")
        context = _make_context(
            tool_name="ha_tool",
            arguments={"config": {"alias": "Test"}},
        )

        result = await mw.on_call_tool(context, call_next)

        call_next.assert_not_awaited()
        assert "REQUIRED DOCUMENTATION" in result.content[0].text

    @pytest.mark.asyncio
    async def test_wrong_password_returns_docs(self):
        """Calling with an incorrect password returns docs."""
        mw = PasswordGatedDocsMiddleware(min_description_length=50)
        full_docs = "docs " * 100
        mw._full_descriptions["ha_tool"] = full_docs
        mw._passwords["ha_tool"] = _password_from_description(full_docs)

        call_next = AsyncMock(return_value="should not be called")
        context = _make_context(
            tool_name="ha_tool",
            arguments={_PASSWORD_PARAM: "wrong_password_123"},
        )

        result = await mw.on_call_tool(context, call_next)

        call_next.assert_not_awaited()
        assert "REQUIRED DOCUMENTATION" in result.content[0].text

    @pytest.mark.asyncio
    async def test_correct_password_executes(self):
        """Calling with the correct password executes the tool."""
        mw = PasswordGatedDocsMiddleware(min_description_length=50)
        full_docs = "docs " * 100
        mw._full_descriptions["ha_tool"] = full_docs
        mw._passwords["ha_tool"] = _password_from_description(full_docs)

        call_next = AsyncMock(return_value="execution result")
        context = _make_context(
            tool_name="ha_tool",
            arguments={
                "config": {"alias": "Test"},
                _PASSWORD_PARAM: mw._passwords["ha_tool"],
            },
        )

        result = await mw.on_call_tool(context, call_next)

        call_next.assert_awaited_once()
        assert result == "execution result"

    @pytest.mark.asyncio
    async def test_password_stripped_before_execution(self):
        """The _docs_password is removed from arguments before tool execution."""
        mw = PasswordGatedDocsMiddleware(min_description_length=50)
        full_docs = "docs " * 100
        mw._full_descriptions["ha_tool"] = full_docs
        mw._passwords["ha_tool"] = _password_from_description(full_docs)

        call_next = AsyncMock(return_value="ok")
        context = _make_context(
            tool_name="ha_tool",
            arguments={
                "entity_id": "light.kitchen",
                _PASSWORD_PARAM: mw._passwords["ha_tool"],
            },
        )

        await mw.on_call_tool(context, call_next)

        # The arguments passed to the real tool should not contain _docs_password
        assert context.message.arguments == {"entity_id": "light.kitchen"}

    @pytest.mark.asyncio
    async def test_passwords_are_unique_per_tool(self):
        """Different tools (with different descriptions) get different passwords."""
        mw = PasswordGatedDocsMiddleware(min_description_length=50)
        desc_a = "Tool A description. " + "a" * 500
        desc_b = "Tool B description. " + "b" * 500
        mw._full_descriptions["ha_tool_a"] = desc_a
        mw._full_descriptions["ha_tool_b"] = desc_b
        mw._passwords["ha_tool_a"] = _password_from_description(desc_a)
        mw._passwords["ha_tool_b"] = _password_from_description(desc_b)

        assert mw._passwords["ha_tool_a"] != mw._passwords["ha_tool_b"]

    @pytest.mark.asyncio
    async def test_password_stable_across_calls(self):
        """Password stays the same across multiple calls (content-derived)."""
        mw = PasswordGatedDocsMiddleware(min_description_length=50)
        full_docs = "docs " * 100
        mw._full_descriptions["ha_tool"] = full_docs
        mw._passwords["ha_tool"] = _password_from_description(full_docs)

        pw1 = mw._get_password("ha_tool")
        pw2 = mw._get_password("ha_tool")

        assert pw1 == pw2

    @pytest.mark.asyncio
    async def test_per_tool_independent_gating(self):
        """Each tool is gated independently."""
        mw = PasswordGatedDocsMiddleware(min_description_length=50)
        desc_a = "docs A " * 100
        desc_b = "docs B " * 100
        mw._full_descriptions["ha_tool_a"] = desc_a
        mw._full_descriptions["ha_tool_b"] = desc_b
        mw._passwords["ha_tool_a"] = _password_from_description(desc_a)
        mw._passwords["ha_tool_b"] = _password_from_description(desc_b)

        call_next = AsyncMock(return_value="executed")

        # Tool A with correct password — executes
        ctx_a = _make_context(
            tool_name="ha_tool_a",
            arguments={_PASSWORD_PARAM: mw._passwords["ha_tool_a"]},
        )
        result_a = await mw.on_call_tool(ctx_a, call_next)
        call_next.assert_awaited_once()
        assert result_a == "executed"

        # Tool B without password — still gated (independent of A)
        call_next.reset_mock()
        ctx_b = _make_context(
            tool_name="ha_tool_b",
            arguments={_PASSWORD_PARAM: _REQUEST_DOCS_SENTINEL},
        )
        result_b = await mw.on_call_tool(ctx_b, call_next)
        call_next.assert_not_awaited()
        assert "REQUIRED DOCUMENTATION" in result_b.content[0].text

    @pytest.mark.asyncio
    async def test_excluded_tool_executes_immediately(self):
        """Excluded tools bypass the gate even with captured docs."""
        mw = PasswordGatedDocsMiddleware(
            min_description_length=50,
            exclude_tools={"ha_special"},
        )

        call_next = AsyncMock(return_value="executed")
        context = _make_context(tool_name="ha_special")

        result = await mw.on_call_tool(context, call_next)

        call_next.assert_awaited_once()
        assert result == "executed"

    @pytest.mark.asyncio
    async def test_docs_response_includes_structured_content(self):
        """Docs response includes structured_content with password."""
        mw = PasswordGatedDocsMiddleware(min_description_length=50)
        full_docs = "Full documentation here."
        mw._full_descriptions["ha_tool"] = full_docs
        mw._passwords["ha_tool"] = _password_from_description(full_docs)

        call_next = AsyncMock()
        context = _make_context(
            tool_name="ha_tool",
            arguments={_PASSWORD_PARAM: _REQUEST_DOCS_SENTINEL},
        )

        result = await mw.on_call_tool(context, call_next)

        assert result.structured_content is not None
        assert result.structured_content["status"] == "documentation_required"
        assert result.structured_content["password"] == mw._passwords["ha_tool"]
        assert "Full documentation here." in result.structured_content["documentation"]

    @pytest.mark.asyncio
    async def test_docs_response_has_meta_for_schema_bypass(self):
        """Docs response sets meta={} so to_mcp_result() returns CallToolResult."""
        mw = PasswordGatedDocsMiddleware(min_description_length=50)
        full_docs = "Docs here."
        mw._full_descriptions["ha_tool"] = full_docs
        mw._passwords["ha_tool"] = _password_from_description(full_docs)

        call_next = AsyncMock()
        context = _make_context(
            tool_name="ha_tool",
            arguments={_PASSWORD_PARAM: _REQUEST_DOCS_SENTINEL},
        )

        result = await mw.on_call_tool(context, call_next)

        assert result.meta is not None
        assert isinstance(result.meta, dict)

    @pytest.mark.asyncio
    async def test_empty_string_password_returns_docs(self):
        """An empty string password is treated as missing."""
        mw = PasswordGatedDocsMiddleware(min_description_length=50)
        full_docs = "docs " * 100
        mw._full_descriptions["ha_tool"] = full_docs
        mw._passwords["ha_tool"] = _password_from_description(full_docs)

        call_next = AsyncMock(return_value="should not be called")
        context = _make_context(
            tool_name="ha_tool",
            arguments={_PASSWORD_PARAM: ""},
        )

        result = await mw.on_call_tool(context, call_next)

        call_next.assert_not_awaited()
        assert "REQUIRED DOCUMENTATION" in result.content[0].text

    @pytest.mark.asyncio
    async def test_tool_a_password_rejected_for_tool_b(self):
        """A password for tool A does not work for tool B."""
        mw = PasswordGatedDocsMiddleware(min_description_length=50)
        desc_a = "docs A " * 100
        desc_b = "docs B " * 100
        mw._full_descriptions["ha_tool_a"] = desc_a
        mw._full_descriptions["ha_tool_b"] = desc_b
        mw._passwords["ha_tool_a"] = _password_from_description(desc_a)
        mw._passwords["ha_tool_b"] = _password_from_description(desc_b)

        call_next = AsyncMock(return_value="should not execute")
        context = _make_context(
            tool_name="ha_tool_b",
            arguments={_PASSWORD_PARAM: mw._passwords["ha_tool_a"]},
        )

        result = await mw.on_call_tool(context, call_next)

        call_next.assert_not_awaited()
        assert "REQUIRED DOCUMENTATION" in result.content[0].text

    @pytest.mark.asyncio
    async def test_non_gated_tool_password_stripped(self):
        """Non-gated tools have _docs_password stripped to avoid validation errors."""
        mw = PasswordGatedDocsMiddleware(min_description_length=500)
        # No tools gated (nothing in _full_descriptions)

        call_next = AsyncMock(return_value="executed")
        context = _make_context(
            tool_name="ha_get_state",
            arguments={
                "entity_id": "sun.sun",
                _PASSWORD_PARAM: "some_stale_password",
            },
        )

        result = await mw.on_call_tool(context, call_next)

        call_next.assert_awaited_once()
        assert result == "executed"
        # Password should have been stripped
        assert context.message.arguments == {"entity_id": "sun.sun"}
