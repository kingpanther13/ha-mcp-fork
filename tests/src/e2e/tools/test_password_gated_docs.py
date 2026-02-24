"""
E2E tests for the password-gated docs middleware.

Verifies that the middleware correctly compresses tool descriptions,
injects the _docs_password parameter, and enforces password verification
before tool execution.

These tests override the default ENABLE_PASSWORD_GATED_DOCS=false setting
(set in tests/.env.test for other E2E tests) to enable the middleware.
"""

import logging
import os
from collections.abc import AsyncGenerator

import pytest
from fastmcp import Client

import ha_mcp.config
from ha_mcp.client import HomeAssistantClient
from ha_mcp.middleware.password_gated_docs import _PASSWORD_PARAM, _REQUEST_DOCS_SENTINEL
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
async def pgd_mcp_client(
    ha_container_with_fresh_config,
) -> AsyncGenerator[Client]:
    """Create an MCP client with password-gated docs middleware enabled.

    Temporarily overrides ENABLE_PASSWORD_GATED_DOCS to true, creates a
    fresh server+client, then restores the original setting.
    """
    container_info = ha_container_with_fresh_config
    base_url = container_info["base_url"]
    token = os.environ.get("HOMEASSISTANT_TOKEN", "")

    original_value = os.environ.get("ENABLE_PASSWORD_GATED_DOCS")
    os.environ["ENABLE_PASSWORD_GATED_DOCS"] = "true"
    ha_mcp.config._settings = None  # Reset cached settings singleton

    try:
        client = HomeAssistantClient(base_url=base_url, token=token)
        server = HomeAssistantSmartMCPServer(client=client)
        tools = await server.mcp.list_tools()
        logger.info(
            f"Password-gated docs server initialized with {len(tools)} tools at {base_url}"
        )

        mcp_client = Client(server.mcp)
        async with mcp_client:
            logger.debug("Password-gated docs MCP client connected (in-memory transport)")
            yield mcp_client
    finally:
        if original_value is None:
            os.environ.pop("ENABLE_PASSWORD_GATED_DOCS", None)
        else:
            os.environ["ENABLE_PASSWORD_GATED_DOCS"] = original_value
        ha_mcp.config._settings = None


@pytest.mark.asyncio
async def test_tools_list_shows_compressed_descriptions_with_password(pgd_mcp_client):
    """Verify that tools/list returns compressed descriptions with _docs_password param."""
    logger.info("Testing that tools/list returns compressed descriptions with password param")

    tools = await pgd_mcp_client.list_tools()
    tool_map = {t.name: t for t in tools}

    for tool_name in LONG_DESCRIPTION_TOOLS:
        if tool_name in tool_map:
            tool = tool_map[tool_name]
            assert "IMPORTANT" in (tool.description or ""), (
                f"Tool {tool_name} should have compressed description"
            )
            assert "_docs_password" in (tool.description or ""), (
                f"Tool {tool_name} should mention _docs_password"
            )
            # Check _docs_password is in the input schema
            schema = tool.inputSchema or {}
            props = schema.get("properties", {})
            assert _PASSWORD_PARAM in props, (
                f"Tool {tool_name} should have {_PASSWORD_PARAM} in inputSchema"
            )
            logger.info(f"  {tool_name}: compressed with password param")


@pytest.mark.asyncio
async def test_short_description_tools_no_password(pgd_mcp_client):
    """Verify that short-description tools don't have the password param."""
    logger.info("Testing that short-description tools are unchanged")

    tools = await pgd_mcp_client.list_tools()
    tool_map = {t.name: t for t in tools}

    for tool_name in SHORT_DESCRIPTION_TOOLS:
        if tool_name in tool_map:
            tool = tool_map[tool_name]
            assert "IMPORTANT" not in (tool.description or ""), (
                f"Short-description tool {tool_name} should NOT have docs hint"
            )
            schema = tool.inputSchema or {}
            props = schema.get("properties", {})
            assert _PASSWORD_PARAM not in props, (
                f"Short-description tool {tool_name} should NOT have {_PASSWORD_PARAM}"
            )
            logger.info(f"  {tool_name}: no password param (correct)")


@pytest.mark.asyncio
async def test_request_docs_returns_docs_and_password(pgd_mcp_client):
    """Verify that calling with REQUEST_DOCS returns docs + a password."""
    logger.info("Testing REQUEST_DOCS flow")

    result = await pgd_mcp_client.call_tool(
        "ha_list_services",
        {"domain": "light", _PASSWORD_PARAM: _REQUEST_DOCS_SENTINEL},
    )

    data = parse_mcp_result(result)

    if "raw_response" in data:
        response_text = data["raw_response"]
        assert "REQUIRED DOCUMENTATION" in response_text, (
            f"Should return docs, got: {response_text[:200]}..."
        )
        assert "ACCESS PASSWORD:" in response_text, (
            "Should include the access password"
        )
        assert "ACTION REQUIRED" in response_text
        logger.info("REQUEST_DOCS correctly returned docs + password")
    else:
        pytest.fail(
            "REQUEST_DOCS should return documentation text, "
            f"but got parsed JSON: {data}"
        )


@pytest.mark.asyncio
async def test_correct_password_executes(pgd_mcp_client):
    """Verify that calling with the correct password executes the tool."""
    logger.info("Testing correct password execution flow")

    # Step 1: Request docs to get the password
    result1 = await pgd_mcp_client.call_tool(
        "ha_get_system_health",
        {_PASSWORD_PARAM: _REQUEST_DOCS_SENTINEL},
    )
    data1 = parse_mcp_result(result1)
    if "raw_response" not in data1:
        pytest.skip("Password-gated docs middleware may not be active")

    response_text = data1["raw_response"]
    assert "ACCESS PASSWORD:" in response_text

    # Extract the password from the response
    for line in response_text.split("\n"):
        if "ACCESS PASSWORD:" in line:
            password = line.split("ACCESS PASSWORD:")[1].strip()
            break
    else:
        pytest.fail("Could not find ACCESS PASSWORD in docs response")

    logger.info(f"  Got password: {password[:4]}...")

    # Step 2: Call again with the correct password
    result2 = await pgd_mcp_client.call_tool(
        "ha_get_system_health",
        {_PASSWORD_PARAM: password},
    )
    data2 = parse_mcp_result(result2)

    # Should get real execution data, not docs
    assert "raw_response" not in data2 or "REQUIRED DOCUMENTATION" not in data2.get(
        "raw_response", ""
    ), "Correct password should execute the tool, not return docs again"

    logger.info("  Correct password executed the tool successfully")


@pytest.mark.asyncio
async def test_short_tool_executes_without_password(pgd_mcp_client):
    """Verify that short-description tools execute without needing a password."""
    logger.info("Testing that ha_get_state executes immediately (no password needed)")

    result = await pgd_mcp_client.call_tool(
        "ha_get_state", {"entity_id": "sun.sun"}
    )
    data = parse_mcp_result(result)

    if "raw_response" in data:
        assert "REQUIRED DOCUMENTATION" not in data["raw_response"], (
            "Short-description tool should execute immediately"
        )
    else:
        state_data = data.get("data", data)
        assert "state" in state_data or "entity_id" in state_data, (
            f"Expected state data from ha_get_state, got: {data}"
        )

    logger.info("ha_get_state executed immediately without password")
