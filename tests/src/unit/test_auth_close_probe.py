"""Fork-only controls for auth-rejection cleanup with default server heartbeats.

Run against upstream master without the SLF001 migration or heartbeat override.
Core's existing verify_cleanup fixture checks for any abandoned timer or task.
"""

import pytest
from aiohttp import WSMsgType
from homeassistant.components.websocket_api.auth import (
    TYPE_AUTH,
    TYPE_AUTH_INVALID,
    TYPE_AUTH_REQUIRED,
)
from homeassistant.components.websocket_api.const import URL
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant.typing import ClientSessionGenerator

from ha_mcp.client.rest_client import HomeAssistantAuthError, HomeAssistantClient
from ha_mcp.client.websocket_client import websocket_manager


async def test_ha_mcp_invalid_token_with_default_heartbeat(
    core_client: HomeAssistantClient,
) -> None:
    """The unchanged production client receives Core's real token rejection."""
    with pytest.raises(HomeAssistantAuthError):
        await websocket_manager.get_client(
            url=core_client.base_url, token="invalid-token", verify_ssl=False
        )


async def test_core_only_invalid_token_then_server_close(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    """Core and aiohttp alone reject auth and complete the server's close."""
    assert await async_setup_component(hass, "websocket_api", {})
    await hass.async_block_till_done()
    client = await hass_client_no_auth()
    try:
        async with client.ws_connect(URL) as websocket:
            required = await websocket.receive_json()
            assert required["type"] == TYPE_AUTH_REQUIRED
            await websocket.send_json(
                {"type": TYPE_AUTH, "access_token": "invalid-token"}
            )
            rejected = await websocket.receive_json()
            assert rejected["type"] == TYPE_AUTH_INVALID
            # Unlike exiting immediately after auth_invalid, this waits for
            # Core to initiate closure, as the production-client trace did.
            closed = await websocket.receive()
            assert closed.type is WSMsgType.CLOSE
    finally:
        await client.close()
        await hass.async_block_till_done()


async def test_ha_mcp_valid_token_connection_and_cleanup(
    core_client: HomeAssistantClient,
) -> None:
    """The same real client and default server heartbeat work with valid auth."""
    websocket = await websocket_manager.get_client(
        url=core_client.base_url, token=core_client.token, verify_ssl=False
    )
    try:
        assert await websocket.ping()
    finally:
        await websocket_manager.disconnect()
    assert not websocket.is_connected
