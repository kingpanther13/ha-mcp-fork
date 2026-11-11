"""Tests for config-entry reconfigure registry reads."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry

from ha_mcp.client.rest_client import HomeAssistantAPIError, HomeAssistantClient


@pytest.fixture
def client() -> HomeAssistantClient:
    """Create a client without opening a network connection."""
    with patch.object(HomeAssistantClient, "__init__", lambda self, **kwargs: None):
        value = HomeAssistantClient()
        value.base_url = "http://test.local:8123"
        value.token = "test-token"
        value.timeout = 30
        value.verify_ssl = True
        return value


async def test_entity_registry_list_returns_cores_rows(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    core_client: HomeAssistantClient,
) -> None:
    """The entity list is the rows Core's registry holds."""
    assert await async_setup_component(hass, "config", {})
    entry = entity_registry.async_get_or_create("light", "demo", "uid-1")

    result = await core_client.list_entity_registry()

    assert [row["entity_id"] for row in result] == [entry.entity_id]


async def test_device_registry_list_returns_cores_rows(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    core_client: HomeAssistantClient,
) -> None:
    """The device list is the rows Core's registry holds."""
    assert await async_setup_component(hass, "config", {})
    config_entry = MockConfigEntry(domain="demo")
    config_entry.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={("demo", "dev-1")}
    )

    result = await core_client.list_device_registry()

    assert [row["id"] for row in result] == [device.id]


@pytest.mark.parametrize(
    "method_name", ["list_entity_registry", "list_device_registry"]
)
async def test_registry_list_rejects_error_envelope(
    core_client: HomeAssistantClient, method_name: str
) -> None:
    """Core's error reply is not silently converted to an empty list."""
    # The "config" integration is not set up, so Core rejects the command.
    with pytest.raises(HomeAssistantAPIError, match="unknown_command"):
        await getattr(core_client, method_name)()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method_name", ["list_entity_registry", "list_device_registry"]
)
async def test_registry_list_rejects_malformed_result(
    client: HomeAssistantClient, method_name: str
) -> None:
    """A malformed result cannot disable identity and duplicate checks."""
    with (
        patch.object(
            client,
            "send_websocket_message",
            new=AsyncMock(return_value={"success": True, "result": ["not-a-row"]}),
        ),
        pytest.raises(HomeAssistantAPIError, match="Unexpected response"),
    ):
        await getattr(client, method_name)()
