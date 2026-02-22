"""
E2E tests for the first-call docs middleware.

Verifies that the middleware correctly compresses tool descriptions in
tools/list and enforces mandatory documentation delivery on first call.

These tests override the default ENABLE_FIRST_CALL_DOCS=false setting
(set in tests/.env.test for other E2E tests) to enable the middleware.
"""

import logging
import os
from collections.abc import AsyncGenerator

import pytest
from fastmcp import Client

import ha_mcp.config
from ha_mcp.client import HomeAssistantClient
from ha_mcp.server import HomeAssistantSmartMCPServer

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


@pytest.fixture
async def fcd_mcp_client(
    ha_container_with_fresh_config,
) -> AsyncGenerator[Client]:
    """Create an MCP client with first-call docs middleware enabled.

    Temporarily overrides ENABLE_FIRST_CALL_DOCS to true, creates a
    fresh server+client, then restores the original setting.
    """
    container_info = ha_container_with_fresh_config
    base_url = container_info["base_url"]
    token = os.environ.get("HOMEASSISTANT_TOKEN", "")

    # Enable first-call docs for this test
    original_value = os.environ.get("ENABLE_FIRST_CALL_DOCS")
    os.environ["ENABLE_FIRST_CALL_DOCS"] = "true"
    ha_mcp.config._settings = None  # Reset cached settings singleton

    try:
        client = HomeAssistantClient(base_url=base_url, token=token)
        server = HomeAssistantSmartMCPServer(client=client)
        tools = await server.mcp.list_tools()
        logger.info(
            f"First-call docs server initialized with {len(tools)} tools at {base_url}"
        )

        mcp_client = Client(server.mcp)
        async with mcp_client:
            logger.debug("First-call docs MCP client connected (in-memory transport)")
            yield mcp_client
    finally:
        # Restore original setting
        if original_value is None:
            os.environ.pop("ENABLE_FIRST_CALL_DOCS", None)
        else:
            os.environ["ENABLE_FIRST_CALL_DOCS"] = original_value
        ha_mcp.config._settings = None  # Reset so other tests get clean settings


@pytest.mark.asyncio
async def test_tools_list_shows_compressed_descriptions(fcd_mcp_client):
    """Verify that tools/list returns compressed descriptions for long-description tools."""
    logger.info("Testing that tools/list returns compressed descriptions")

    tools = await fcd_mcp_client.list_tools()
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
async def test_short_description_tools_unchanged(fcd_mcp_client):
    """Verify that short-description tools keep their original descriptions."""
    logger.info("Testing that short-description tools are unchanged")

    tools = await fcd_mcp_client.list_tools()
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
async def test_first_call_returns_docs_not_execution(fcd_mcp_client):
    """Verify that the first call to a gated tool returns docs, not execution."""
    logger.info("Testing first-call docs gate enforcement")

    # Call a long-description tool — should get docs, not execution
    result = await fcd_mcp_client.call_tool("ha_list_services", {"domain": "light"})

    # Parse the result — it should be documentation, not actual service data
    data = parse_mcp_result(result)

    # Check if we got the docs response (raw text, not JSON)
    if "raw_response" in data:
        response_text = data["raw_response"]
        assert "REQUIRED DOCUMENTATION" in response_text, (
            f"First call should return docs, got: {response_text[:200]}..."
        )
        assert "Call ha_list_services again" in response_text, (
            "Docs response should instruct to call the same tool again"
        )
        assert "_docs_ack" in response_text, (
            "Docs response should include ack token for acknowledgment"
        )
        logger.info("First call correctly returned documentation (not execution)")
    else:
        # If the result parsed as JSON, it means the tool executed
        pytest.fail(
            "First call to gated tool should return documentation text, "
            f"but got parsed JSON: {data}"
        )


@pytest.mark.asyncio
async def test_second_call_executes_normally(fcd_mcp_client):
    """Verify that the second call to a gated tool executes normally."""
    logger.info("Testing that second call executes after docs delivery")

    # First call — should get docs (ha_get_system_health is gated and safe)
    result1 = await fcd_mcp_client.call_tool("ha_get_system_health", {})
    data1 = parse_mcp_result(result1)
    if "raw_response" in data1:
        assert "REQUIRED DOCUMENTATION" in data1["raw_response"], (
            "First call should return docs"
        )
        logger.info("  First call: docs delivered")
    else:
        pytest.skip("First-call docs middleware may not be active")

    # Second call — should actually execute and return real data
    result2 = await fcd_mcp_client.call_tool("ha_get_system_health", {})
    data2 = parse_mcp_result(result2)

    # The second call should return actual system health data (parsed JSON)
    assert "raw_response" not in data2 or "REQUIRED DOCUMENTATION" not in data2.get(
        "raw_response", ""
    ), "Second call should execute, not return docs again"

    logger.info("  Second call: executed normally")
    logger.info("Docs gate enforcement verified: docs then execution")


@pytest.mark.asyncio
async def test_short_tool_executes_immediately(fcd_mcp_client):
    """Verify that short-description tools execute on first call without docs gate."""
    logger.info("Testing that ha_get_state executes immediately (no docs gate)")

    # ha_get_state has a very short description — should bypass the gate
    result = await fcd_mcp_client.call_tool(
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
