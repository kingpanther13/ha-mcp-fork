"""A component command on a real ``hass``, watched from Core's side.

Each helper re-registers the command's own handler with its own schema: to
record the messages Core received, to hold it until the client stopped
waiting, or to drop the connection once Core has it. Core's handler answers
every message it is handed; nothing here answers for it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import pytest
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import ActiveConnection
from homeassistant.core import HomeAssistant

from custom_components.ha_mcp_tools.websocket_api import system as component_system
from ha_mcp.client import websocket_client
from ha_mcp.client.rest_client import HomeAssistantClient
from ha_mcp.client.websocket_client import (
    HomeAssistantWebSocketClient,
    get_websocket_client,
)

from ._ws_transport import abort_transport


def older_component(monkeypatch: pytest.MonkeyPatch, *missing: str) -> None:
    """The component's ``ha_mcp_tools/info`` advertises all but ``missing``."""
    monkeypatch.setattr(
        component_system,
        "CAPABILITIES",
        [c for c in component_system.CAPABILITIES if c not in missing],
    )


async def pooled(client: HomeAssistantClient) -> HomeAssistantWebSocketClient:
    """The pooled socket the component commands and the WS bridge share."""
    return await get_websocket_client(
        url=client.base_url, token=client.token, verify_ssl=client.verify_ssl
    )


@contextmanager
def observed(hass: HomeAssistant, command: str) -> Iterator[list[dict[str, Any]]]:
    """Every ``command`` message Core receives meanwhile, in order."""
    core_handler, schema = hass.data[websocket_api.DOMAIN][command]
    messages: list[dict[str, Any]] = []

    def record(
        core: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
    ) -> None:
        messages.append(dict(msg))
        core_handler(core, connection, msg)

    websocket_api.async_register_command(hass, command, record, schema)
    try:
        yield messages
    finally:
        websocket_api.async_register_command(hass, command, core_handler, schema)


@asynccontextmanager
async def held(
    hass: HomeAssistant, command: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[None]:
    """Core answers ``command`` only after the client's wait (cut to 1 s) ran out."""
    core_handler, schema = hass.data[websocket_api.DOMAIN][command]
    released = asyncio.Event()

    async def wait_then_answer(
        core: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
    ) -> None:
        await released.wait()
        core_handler(core, connection, msg)

    monkeypatch.setattr(websocket_client, "DEFAULT_COMMAND_WAIT_TIMEOUT", 1.0)
    websocket_api.async_register_command(
        hass, command, websocket_api.async_response(wait_then_answer), schema
    )
    try:
        yield
    finally:
        released.set()
        websocket_api.async_register_command(hass, command, core_handler, schema)
        await hass.async_block_till_done(wait_background_tasks=True)


@contextmanager
def dropped(
    hass: HomeAssistant, command: str, socket: HomeAssistantWebSocketClient
) -> Iterator[None]:
    """``socket`` loses its connection, with no close frame, on ``command``."""
    core_handler, schema = hass.data[websocket_api.DOMAIN][command]

    def drop(
        core: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
    ) -> None:
        abort_transport(socket)

    websocket_api.async_register_command(hass, command, drop, schema)
    try:
        yield
    finally:
        websocket_api.async_register_command(hass, command, core_handler, schema)
