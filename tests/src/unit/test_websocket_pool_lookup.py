"""``WebSocketManager.pooled_client``: the pool's connection, read without connecting.

The component capability cache compares it with the connection its caps were
probed over (#2967), on every cached read, so it must never open one.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest

from ha_mcp.client.rest_client import HomeAssistantClient
from ha_mcp.client.websocket_client import (
    HomeAssistantWebSocketClient,
    websocket_manager,
)


@pytest.fixture
def connects() -> Iterator[AsyncMock]:
    """Spy on the real ``connect``: it calls through to Core and counts."""
    with patch.object(
        HomeAssistantWebSocketClient,
        "connect",
        autospec=True,
        wraps=HomeAssistantWebSocketClient.connect,
    ) as spy:
        yield spy


async def test_returns_the_pooled_connection_without_connecting(
    core_client: HomeAssistantClient, connects: AsyncMock
) -> None:
    url, token = core_client.base_url, core_client.token
    assert websocket_manager.pooled_client(url, token, False) is None
    assert connects.await_count == 0

    pooled = await websocket_manager.get_client(url=url, token=token, verify_ssl=False)

    found = websocket_manager.pooled_client(url=url, token=token, verify_ssl=False)

    assert found is pooled
    assert found.is_connected
    assert connects.await_count == 1


async def test_a_dropped_connection_is_returned_until_replaced(
    core_client: HomeAssistantClient, connects: AsyncMock
) -> None:
    url, token = core_client.base_url, core_client.token
    pooled = await websocket_manager.get_client(url=url, token=token, verify_ssl=False)
    await pooled.disconnect()
    assert not pooled.is_connected

    assert websocket_manager.pooled_client(url, token, False) is pooled
    assert connects.await_count == 1

    replaced = await websocket_manager.get_client(
        url=url, token=token, verify_ssl=False
    )
    assert replaced is not pooled
    assert replaced.is_connected
    assert connects.await_count == 2
    assert websocket_manager.pooled_client(url, token, False) is replaced


async def test_other_credentials_have_no_connection(
    core_client: HomeAssistantClient, connects: AsyncMock
) -> None:
    url, token = core_client.base_url, core_client.token
    pooled = await websocket_manager.get_client(url=url, token=token, verify_ssl=False)

    assert websocket_manager.pooled_client(url, "other", False) is None
    assert websocket_manager.pooled_client(url, token, True) is None
    assert websocket_manager.pooled_client(url, token, False) is pooled
    assert connects.await_count == 1
