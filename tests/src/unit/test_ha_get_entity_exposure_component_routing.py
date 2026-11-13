"""``ha_get_entity_exposure`` through the component, on a real ``hass``.

Core's ``homeassistant/expose_entity/list`` gives a bare ``{entity_id:
{assistant: True}}`` map. When the component advertises ``exposure`` (with
``device_registry_child_semantics``), one ``ha_mcp_tools/exposure`` frame gives
the same map plus ``entity_info`` (friendly name, domain, area, floor, labels,
state). Without it, or when that frame fails, the tool reads Core's list and
answers with the map alone. Registries and exposure settings are Core's own.
"""

from __future__ import annotations

import uuid
from typing import NamedTuple
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components import websocket_api
from homeassistant.components.homeassistant.exposed_entities import (
    async_expose_entity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr
from homeassistant.setup import async_setup_component

from ha_mcp.client.rest_client import HomeAssistantClient, HomeAssistantConnectionError
from ha_mcp.tools import tools_voice_assistant
from ha_mcp.tools.component_api import get_component_caps, invalidate_caps
from ha_mcp.tools.tools_voice_assistant import WS_EXPOSURE, VoiceAssistantTools

from ._component_frames import held, observed, older_component

LEGACY_LIST = "homeassistant/expose_entity/list"
ENRICHMENT_KEYS = ("friendly_name", "domain", "area", "floor", "labels", "state")


class Home(NamedTuple):
    """A lamp exposed to Assist and a fan exposed to Alexa."""

    lamp: str
    fan: str
    area: str
    floor: str
    label: str


@pytest.fixture
async def client(
    hass: HomeAssistant, component_core_client: HomeAssistantClient
) -> HomeAssistantClient:
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "config", {})
    return component_core_client


@pytest.fixture
async def home(hass: HomeAssistant, client: HomeAssistantClient) -> Home:
    suffix = uuid.uuid4().hex[:8]
    floor = fr.async_get(hass).async_create(f"Main {suffix}")
    area = ar.async_get(hass).async_create(f"Kitchen {suffix}", floor_id=floor.floor_id)
    label = lr.async_get(hass).async_create(f"Favorites {suffix}")
    registry = er.async_get(hass)
    lamp = registry.async_get_or_create("light", "test", "lamp").entity_id
    fan = registry.async_get_or_create("fan", "test", "fan").entity_id
    registry.async_update_entity(lamp, area_id=area.id, labels={label.label_id})
    hass.states.async_set(lamp, "on", {"friendly_name": "Lamp A"})
    hass.states.async_set(fan, "off", {"friendly_name": "Fan"})
    async_expose_entity(hass, "conversation", lamp, True)
    async_expose_entity(hass, "cloud.alexa", fan, True)
    return Home(lamp, fan, area.name, floor.name, label.name)


def _legacy_map(home: Home) -> dict[str, dict[str, bool]]:
    return {home.lamp: {"conversation": True}, home.fan: {"cloud.alexa": True}}


async def test_one_entity_keeps_its_assistant_map_and_gains_its_name_and_place(
    hass: HomeAssistant, client: HomeAssistantClient, home: Home
) -> None:
    with (
        observed(hass, WS_EXPOSURE) as frames,
        observed(hass, LEGACY_LIST) as legacy,
    ):
        resp = await VoiceAssistantTools(client).ha_get_entity_exposure(
            entity_id=home.lamp
        )

    assert resp["exposed_to"] == {
        "conversation": True,
        "cloud.alexa": False,
        "cloud.google_assistant": False,
    }
    assert (resp["is_exposed_anywhere"], resp["has_custom_settings"]) == (True, True)
    assert {key: resp[key] for key in ENRICHMENT_KEYS} == {
        "friendly_name": "Lamp A",
        "domain": "light",
        "area": home.area,
        "floor": home.floor,
        "labels": [home.label],
        "state": "on",
    }
    assert [frame["entity_id"] for frame in frames] == [home.lamp]
    assert legacy == []


async def test_the_list_is_cores_map_with_entity_info_for_each_entry(
    hass: HomeAssistant, client: HomeAssistantClient, home: Home
) -> None:
    with (
        observed(hass, WS_EXPOSURE) as frames,
        observed(hass, LEGACY_LIST) as legacy,
    ):
        resp = await VoiceAssistantTools(client).ha_get_entity_exposure()

    assert resp["exposed_entities"] == _legacy_map(home)
    assert resp["count"] == 2
    assert set(resp["entity_info"]) == {home.lamp, home.fan}
    assert resp["entity_info"][home.lamp]["area"] == home.area
    (frame,) = frames
    assert "entity_id" not in frame
    assert legacy == []


async def test_entity_info_covers_only_the_entities_the_assistant_filter_keeps(
    client: HomeAssistantClient, home: Home
) -> None:
    resp = await VoiceAssistantTools(client).ha_get_entity_exposure(
        assistant="cloud.alexa"
    )

    assert set(resp["exposed_entities"]) == {home.fan}
    assert set(resp["entity_info"]) == {home.fan}


@pytest.mark.parametrize(
    "missing",
    [
        pytest.param("exposure", id="no-exposure-command"),
        # Placement from a component without it can be stale for a child device.
        pytest.param("device_registry_child_semantics", id="no-child-semantics"),
    ],
)
async def test_an_older_component_serves_cores_list_unchanged(
    hass: HomeAssistant,
    client: HomeAssistantClient,
    home: Home,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    older_component(monkeypatch, missing)

    with (
        observed(hass, WS_EXPOSURE) as frames,
        observed(hass, LEGACY_LIST) as legacy,
    ):
        resp = await VoiceAssistantTools(client).ha_get_entity_exposure()

    assert resp["exposed_entities"] == _legacy_map(home)
    assert "entity_info" not in resp
    assert (len(frames), len(legacy)) == (0, 1)


async def test_a_downgraded_component_serves_cores_list_and_is_probed_again(
    hass: HomeAssistant,
    client: HomeAssistantClient,
    home: Home,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert await get_component_caps(client) is not None
    monkeypatch.delitem(hass.data[websocket_api.DOMAIN], WS_EXPOSURE)

    with (
        patch.object(
            tools_voice_assistant, "invalidate_caps", wraps=invalidate_caps
        ) as drop,
        observed(hass, LEGACY_LIST) as legacy,
    ):
        resp = await VoiceAssistantTools(client).ha_get_entity_exposure(
            entity_id=home.lamp
        )

    assert resp["exposed_to"]["conversation"] is True
    assert "friendly_name" not in resp
    assert len(legacy) == 1
    drop.assert_called_once_with(client)


async def test_an_unanswered_exposure_frame_serves_cores_list(
    hass: HomeAssistant,
    client: HomeAssistantClient,
    home: Home,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with (
        patch.object(
            tools_voice_assistant, "invalidate_caps", wraps=invalidate_caps
        ) as drop,
        observed(hass, LEGACY_LIST) as legacy,
    ):
        async with held(hass, WS_EXPOSURE, monkeypatch):
            resp = await VoiceAssistantTools(client).ha_get_entity_exposure()

    assert resp["exposed_entities"] == _legacy_map(home)
    assert "entity_info" not in resp
    assert len(legacy) == 1
    drop.assert_not_called()


async def test_an_exposure_read_with_no_socket_serves_cores_list(
    hass: HomeAssistant, client: HomeAssistantClient, home: Home
) -> None:
    """Core's list rides the WS bridge, which connects again on its own."""
    with (
        patch.object(
            tools_voice_assistant,
            "get_websocket_client",
            AsyncMock(side_effect=HomeAssistantConnectionError("Failed to connect")),
        ),
        observed(hass, LEGACY_LIST) as legacy,
    ):
        resp = await VoiceAssistantTools(client).ha_get_entity_exposure()

    assert resp["exposed_entities"] == _legacy_map(home)
    assert "entity_info" not in resp
    assert len(legacy) == 1


async def test_an_entity_without_a_state_gets_its_place_but_no_state_fields(
    hass: HomeAssistant, client: HomeAssistantClient, home: Home
) -> None:
    """An entity with a registry entry and no state (disabled, or not set up):
    its name and state are left out rather than sent as null."""
    hass.states.async_remove(home.lamp)

    resp = await VoiceAssistantTools(client).ha_get_entity_exposure(entity_id=home.lamp)

    assert (resp["domain"], resp["area"], resp["floor"]) == (
        "light",
        home.area,
        home.floor,
    )
    assert "friendly_name" not in resp
    assert "state" not in resp
