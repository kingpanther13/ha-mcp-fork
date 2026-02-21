"""
E2E tests for the first-call docs middleware.

Verifies that the middleware correctly compresses tool descriptions in
tools/list and enforces mandatory documentation delivery on first call
when ENABLE_FIRST_CALL_DOCS=true (default).
"""

import logging

import pytest

from ..utilities.assertions import parse_mcp_result

logger = logging.getLogger(__name__)

# Tools known to have short descriptions (<500 chars) — should be exempt
SHORT_DESCRIPTION_TOOLS = ["ha_get_state", "ha_get_overview", "ha_check_config"]

# Tools known to have long descriptions (>500 chars) — should be gated
LONG_DESCRIPTION_TOOLS = [
    "ha_config_set_automation",
    "ha_config_set_dashboard",
    "ha_eval_template",
]


@pytest.mark.asyncio
async def test_tools_list_shows_compressed_descriptions(mcp_client):
    """Verify that tools/list returns compressed descriptions for long-description tools."""
    logger.info("Testing that tools/list returns compressed descriptions")

    tools = await mcp_client.list_tools()
    tool_map = {t.name: t for t in tools}

    # Long-description tools should have the first-call docs hint
    for tool_name in LONG_DESCRIPTION_TOOLS:
        if tool_name in tool_map:
            tool = tool_map[tool_name]
            assert "IMPORTANT" in (tool.description or ""), (
                f"Tool {tool_name} should have compressed description with IMPORTANT hint, "
                f"got: {(tool.description or '')[:100]}..."
            )
            assert "call this tool once" in (tool.description or "").lower(), (
                f"Tool {tool_name} compressed description should mention calling once for docs"
            )
            logger.info(f"  {tool_name}: compressed ({len(tool.description or '')} chars)")

    logger.info("Compressed descriptions verified for long-description tools")


@pytest.mark.asyncio
async def test_short_description_tools_unchanged(mcp_client):
    """Verify that short-description tools keep their original descriptions."""
    logger.info("Testing that short-description tools are unchanged")

    tools = await mcp_client.list_tools()
    tool_map = {t.name: t for t in tools}

    for tool_name in SHORT_DESCRIPTION_TOOLS:
        if tool_name in tool_map:
            tool = tool_map[tool_name]
            assert "IMPORTANT" not in (tool.description or ""), (
                f"Short-description tool {tool_name} should NOT have first-call docs hint"
            )
            logger.info(f"  {tool_name}: original description preserved")

    logger.info("Short-description tools verified unchanged")


@pytest.mark.asyncio
async def test_first_call_returns_docs_not_execution(mcp_client):
    """Verify that the first call to a gated tool returns docs, not execution."""
    logger.info("Testing first-call docs gate enforcement")

    # Call a long-description tool (ha_search_entities has >500 char desc)
    # Use ha_list_services which is read-only and gated
    result = await mcp_client.call_tool("ha_list_services", {"domain": "light"})

    # Parse the result — it should be documentation, not actual service data
    data = parse_mcp_result(result)

    # Check if we got the docs response (raw text, not JSON)
    if "raw_response" in data:
        response_text = data["raw_response"]
        assert "REQUIRED DOCUMENTATION" in response_text, (
            f"First call should return docs, got: {response_text[:200]}..."
        )
        assert "call ha_list_services again" in response_text, (
            "Docs response should instruct to call the same tool again"
        )
        assert "Do not call a different tool" in response_text, (
            "Docs response should warn against calling a different tool"
        )
        logger.info("First call correctly returned documentation (not execution)")
    else:
        # If the result parsed as JSON, it means the tool executed
        # This would be a failure of the docs gate
        pytest.fail(
            "First call to gated tool should return documentation text, "
            f"but got parsed JSON: {data}"
        )


@pytest.mark.asyncio
async def test_second_call_executes_normally(mcp_client):
    """Verify that the second call to a gated tool executes normally."""
    logger.info("Testing that second call executes after docs delivery")

    # First call — should get docs (ha_get_system_health is gated and safe)
    result1 = await mcp_client.call_tool("ha_get_system_health", {})
    data1 = parse_mcp_result(result1)
    if "raw_response" in data1:
        assert "REQUIRED DOCUMENTATION" in data1["raw_response"], (
            "First call should return docs"
        )
        logger.info("  First call: docs delivered")
    else:
        pytest.skip("First-call docs middleware may not be active")

    # Second call — should actually execute and return real data
    result2 = await mcp_client.call_tool("ha_get_system_health", {})
    data2 = parse_mcp_result(result2)

    # The second call should return actual system health data (parsed JSON)
    assert "raw_response" not in data2 or "REQUIRED DOCUMENTATION" not in data2.get(
        "raw_response", ""
    ), "Second call should execute, not return docs again"

    logger.info("  Second call: executed normally")
    logger.info("Docs gate enforcement verified: docs then execution")


@pytest.mark.asyncio
async def test_short_tool_executes_immediately(mcp_client):
    """Verify that short-description tools execute on first call without docs gate."""
    logger.info("Testing that ha_get_state executes immediately (no docs gate)")

    # ha_get_state has a very short description — should bypass the gate
    result = await mcp_client.call_tool(
        "ha_get_state", {"entity_id": "sun.sun"}
    )
    data = parse_mcp_result(result)

    # Should get actual state data, not documentation
    if "raw_response" in data:
        assert "REQUIRED DOCUMENTATION" not in data["raw_response"], (
            "Short-description tool should execute immediately, not return docs"
        )
    else:
        # Got parsed JSON — this is correct behavior (tool executed)
        state_data = data.get("data", data)
        assert "state" in state_data or "entity_id" in state_data, (
            f"Expected state data from ha_get_state, got: {data}"
        )

    logger.info("ha_get_state executed immediately without docs gate")
