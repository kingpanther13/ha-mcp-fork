"""A WebSocket command whose own handler Core holds, or that drops the connection.

Each re-registers the command with its registered schema. ``held`` keeps the
command's own handler waiting until the block ends, then lets it answer, so
the client's wait runs out first; ``drop_connection_on`` cuts the client's
connection when the command arrives, which never answers.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from ha_mcp.client import websocket_client
from ha_mcp.client.rest_client import HomeAssistantClient
from ha_mcp.client.websocket_client import HomeAssistantWebSocketClient

from ._ws_transport import abort_transport


def drop_connection_on(
    hass: HomeAssistant, command: str, socket: HomeAssistantWebSocketClient
) -> None:
    """Drop ``socket``'s TCP connection when ``command`` reaches Core."""
    _, schema = hass.data[websocket_api.DOMAIN][command]

    async def drop(
        core: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        abort_transport(socket)

    websocket_api.async_register_command(
        hass, command, websocket_api.async_response(drop), schema
    )


@asynccontextmanager
async def held(
    hass: HomeAssistant,
    client: HomeAssistantClient,
    command: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[None]:
    """Hold ``command`` inside Core until the block ends; the client's wait for
    it is cut to a second, so it times out meanwhile. Core's handler then
    answers, and the block waits until that answer is out."""
    core_handler, schema = hass.data[websocket_api.DOMAIN][command]
    release = asyncio.Event()
    answered = asyncio.Event()

    async def hold(
        core: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        await release.wait()
        core_handler(core, connection, msg)
        answered.set()

    websocket_api.async_register_command(
        hass, command, websocket_api.async_response(hold), schema
    )
    # One command opens the client's socket under the normal wait first.
    await client.send_websocket_message({"type": "ping"})
    monkeypatch.setattr(websocket_client, "DEFAULT_COMMAND_WAIT_TIMEOUT", 1.0)
    try:
        yield
    finally:
        release.set()
        async with asyncio.timeout(5):
            await answered.wait()
        # Core writes replies in order: the pong means the held reply is out.
        await client.send_websocket_message({"type": "ping"})
        await hass.async_block_till_done()
