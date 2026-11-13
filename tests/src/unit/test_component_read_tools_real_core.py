"""Read tools served by the component's commands on a real ``hass``.

Each tool runs on the server's own client against Core with the
``ha_mcp_tools/*`` commands registered, so a drift between what a component
command returns and what the tool makes of it fails here. Registries, states,
storage helpers, config entries, blueprints and exposure settings are Core's
own, set up through Core's public API.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.homeassistant.exposed_entities import (
    async_expose_entity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry

from ha_mcp.client.rest_client import HomeAssistantClient
from ha_mcp.tools.smart_search import SmartSearchTools
from ha_mcp.tools.tools_blueprints import BlueprintTools
from ha_mcp.tools.tools_config_helpers import HelperConfigTools
from ha_mcp.tools.tools_entities import EntityTools
from ha_mcp.tools.tools_search import SearchTools
from ha_mcp.tools.tools_voice_assistant import VoiceAssistantTools

from ._search_tool import _setup_visibility_disabled


@pytest.fixture
async def client(
    hass: HomeAssistant, component_core_client: HomeAssistantClient
) -> HomeAssistantClient:
    """The component-served client on a ``hass`` that answers registry commands."""
    assert await async_setup_component(hass, "config", {})
    return component_core_client


@pytest.fixture
def search_tools(
    client: HomeAssistantClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> SearchTools:
    _setup_visibility_disabled(tmp_path, monkeypatch)
    return SearchTools(client, SmartSearchTools(client=client))


@contextmanager
def _sent(client: HomeAssistantClient) -> Iterator[AsyncMock]:
    """A spy on the WebSocket messages the server sends to Core meanwhile."""
    with patch.object(
        client,
        "send_websocket_message",
        AsyncMock(wraps=client.send_websocket_message),
    ) as spy:
        yield spy


def _types(spy: AsyncMock) -> list[str]:
    return [str(call.args[0].get("type")) for call in spy.call_args_list]


def _office_upstairs(hass: HomeAssistant) -> tuple[str, str]:
    """An Office area on an Upstairs floor; returns (area_id, floor_id)."""
    floor = fr.async_get(hass).async_create(f"Upstairs {uuid.uuid4().hex[:8]}")
    area = ar.async_get(hass).async_create(
        f"Office {uuid.uuid4().hex[:8]}", floor_id=floor.floor_id
    )
    return area.id, floor.floor_id


def _child_device_sensor(hass: HomeAssistant, area_id: str) -> str:
    """A sensor on a child device whose parent alone sits in ``area_id``."""
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    parent = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("test", "parent")}
    )
    devices.async_update_device(parent.id, area_id=area_id)
    child = devices.async_get_or_create_child(
        config_entry_id=entry.entry_id,
        identifiers={("test", "child")},
        parent_device_id=parent.id,
    )
    sensor = er.async_get(hass).async_get_or_create(
        "sensor",
        "test",
        "child_sensor",
        config_entry=entry,
        device_id=child.id,
        original_name="Child Sensor",
    )
    hass.states.async_set(sensor.entity_id, "21", {"friendly_name": "Child Sensor"})
    return sensor.entity_id


def _names(hass: HomeAssistant, area_id: str, floor_id: str) -> tuple[str, str]:
    area = ar.async_get(hass).async_get_area(area_id)
    floor = fr.async_get(hass).async_get_floor(floor_id)
    assert area is not None and floor is not None
    return area.name, floor.name


# --- overview and search -------------------------------------------------------


async def test_overview_counts_every_state_core_holds(
    hass: HomeAssistant, search_tools: SearchTools
) -> None:
    hass.states.async_set("light.lamp", "on", {"friendly_name": "Lamp"})
    hass.states.async_set("sensor.temp", "21", {"friendly_name": "Temp"})

    overview = await search_tools.ha_get_overview()

    assert not any("legacy path" in w for w in overview.get("warnings", []))
    assert overview["system_summary"]["total_entities"] == len(hass.states.async_all())


async def test_overview_counts_a_child_device_entity_in_its_parents_area(
    hass: HomeAssistant, search_tools: SearchTools
) -> None:
    area_id, _ = _office_upstairs(hass)
    _child_device_sensor(hass, area_id)

    overview = await search_tools.ha_get_overview()

    assert overview["area_analysis"][area_id]["count"] == 1


async def test_search_names_a_child_device_entitys_inherited_area_and_floor(
    hass: HomeAssistant, search_tools: SearchTools
) -> None:
    area_id, floor_id = _office_upstairs(hass)
    entity_id = _child_device_sensor(hass, area_id)
    area, floor = _names(hass, area_id, floor_id)

    found = await search_tools.ha_search(
        query="child sensor", result_fields=["entity_id", "area", "floor"]
    )

    assert found["entities"] == [{"entity_id": entity_id, "area": area, "floor": floor}]


# --- helpers ---------------------------------------------------------------------


async def test_a_renamed_storage_helper_lists_its_storage_id_and_current_name(
    hass: HomeAssistant, client: HomeAssistantClient
) -> None:
    """The storage id is what an update needs; the name is the one shown now."""
    assert await async_setup_component(hass, "input_boolean", {})
    created = await client.send_websocket_message(
        {"type": "input_boolean/create", "name": f"Guest {uuid.uuid4().hex[:8]}"}
    )
    storage_id = created["result"]["id"]
    entity_id = er.async_get(hass).async_get_entity_id(
        "input_boolean", "input_boolean", storage_id
    )
    assert entity_id is not None
    er.async_get(hass).async_update_entity(entity_id, name="Current Guest")
    await hass.async_block_till_done()

    with _sent(client) as sent:
        listed = await HelperConfigTools(client).ha_config_list_helpers(
            helper_type="input_boolean"
        )

    (record,) = listed["helpers"]
    assert (record["id"], record["entity_id"], record["name"]) == (
        storage_id,
        entity_id,
        "Current Guest",
    )
    assert "input_boolean/list" not in _types(sent)


async def test_a_flow_helper_lists_its_entry_options_but_never_its_data(
    hass: HomeAssistant, client: HomeAssistantClient
) -> None:
    entry = MockConfigEntry(
        domain="template",
        title="Creation Title",
        options={"state": "{{ is_state('sun.sun', 'above_horizon') }}"},
        data={"api_key": "DATA_SECRET_XYZ"},
    )
    entry.add_to_hass(hass)
    sensor = er.async_get(hass).async_get_or_create(
        "binary_sensor", "template", "sun_up", config_entry=entry
    )
    er.async_get(hass).async_update_entity(sensor.entity_id, name="Sun Is Up")

    listed = await HelperConfigTools(client).ha_config_list_helpers(
        helper_type="template"
    )

    (record,) = listed["helpers"]
    assert record["entry_id"] == entry.entry_id
    assert record["entity_id"] == sensor.entity_id
    assert record["name"] == "Sun Is Up"
    assert record["options"] == {"state": "{{ is_state('sun.sun', 'above_horizon') }}"}
    assert "id" not in record
    assert "DATA_SECRET_XYZ" not in json.dumps(listed)


async def test_tags_come_from_cores_tag_list(
    hass: HomeAssistant, client: HomeAssistantClient
) -> None:
    """The component does not cover tags: an empty component answer must not
    read as "no tags exist"."""
    assert await async_setup_component(hass, "tag", {})
    await client.send_websocket_message({"type": "tag/create", "tag_id": "tag-42"})

    with _sent(client) as sent:
        listed = await HelperConfigTools(client).ha_config_list_helpers(
            helper_type="tag"
        )

    assert [h["id"] for h in listed["helpers"]] == ["tag-42"]
    assert "tag/list" in _types(sent)


# --- states ----------------------------------------------------------------------


async def test_bulk_states_come_from_the_component_with_missing_ids_named(
    hass: HomeAssistant, search_tools: SearchTools, client: HomeAssistantClient
) -> None:
    hass.states.async_set("light.lamp", "on", {"friendly_name": "Lamp"})
    hass.states.async_set("sensor.temp", "21", {"friendly_name": "Temp"})

    with patch.object(
        client, "get_entity_state", AsyncMock(wraps=client.get_entity_state)
    ) as rest_read:
        result = await search_tools.ha_get_state(
            entity_id=["light.lamp", "sensor.temp", "light.ghost"]
        )

    data = result["data"]
    assert set(data["states"]) == {"light.lamp", "sensor.temp"}
    assert data["states"]["light.lamp"]["attributes"]["friendly_name"] == "Lamp"
    assert [(e["entity_id"], e["error"]["code"]) for e in data["errors"]] == [
        ("light.ghost", "ENTITY_NOT_FOUND")
    ]
    rest_read.assert_not_awaited()


async def test_a_single_state_comes_from_the_component(
    hass: HomeAssistant, search_tools: SearchTools, client: HomeAssistantClient
) -> None:
    hass.states.async_set("light.lamp", "on", {"friendly_name": "Lamp"})

    with patch.object(
        client, "get_entity_state", AsyncMock(wraps=client.get_entity_state)
    ) as rest_read:
        result = await search_tools.ha_get_state(entity_id="light.lamp")

    assert (result["data"]["entity_id"], result["data"]["state"]) == (
        "light.lamp",
        "on",
    )
    rest_read.assert_not_awaited()


# --- blueprints ------------------------------------------------------------------

_MOTION_BLUEPRINT = (
    "blueprint:\n"
    "  name: Motion Light\n"
    "  domain: automation\n"
    "  input:\n"
    "    motion_sensor:\n"
    "      name: Motion Sensor\n"
    "triggers:\n"
    "  - trigger: state\n"
    "    entity_id: !input motion_sensor\n"
    "actions:\n"
    "  - action: light.turn_on\n"
)


def _write(hass: HomeAssistant, relative: str, text: str) -> None:
    target = Path(hass.config.path(relative))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


async def test_a_blueprint_get_returns_the_file_as_written(
    hass: HomeAssistant, client: HomeAssistantClient
) -> None:
    """What a caller edits and saves back is the file byte for byte, and the
    parsed body keeps ``!input`` as a marker."""
    _write(hass, "blueprints/automation/user/motion.yaml", _MOTION_BLUEPRINT)
    assert await async_setup_component(hass, "automation", {})

    result = await BlueprintTools(client).ha_manage_blueprints(
        action="get", path="user/motion.yaml", domain="automation"
    )

    data = result["data"]
    assert data["metadata"]["name"] == "Motion Light"
    assert data["config"]["triggers"][0]["entity_id"] == {"__input__": "motion_sensor"}
    assert data["yaml"] == _MOTION_BLUEPRINT
    assert data["yaml_source"] == "component"


async def test_a_blueprint_path_outside_the_blueprints_folder_is_never_read(
    hass: HomeAssistant, client: HomeAssistantClient
) -> None:
    _write(hass, "secrets.yaml", "db_pw: hunter2\n")

    reply = await client.send_websocket_message(
        {
            "type": "ha_mcp_tools/blueprint_get",
            "domain": "automation",
            "path": "../../secrets.yaml",
        }
    )

    assert "hunter2" not in json.dumps(reply)


# --- entity registry enrichment --------------------------------------------------


def _lamp_in_office(hass: HomeAssistant) -> tuple[str, str, str, str]:
    """A lamp with an alias, area and label; returns its id and those names."""
    area_id, floor_id = _office_upstairs(hass)
    label = lr.async_get(hass).async_create(f"Favorites {uuid.uuid4().hex[:8]}")
    lamp = er.async_get(hass).async_get_or_create("light", "test", "lamp")
    er.async_get(hass).async_update_entity(
        lamp.entity_id, aliases={"desk"}, area_id=area_id, labels={label.label_id}
    )
    area, floor = _names(hass, area_id, floor_id)
    return lamp.entity_id, area, floor, label.name


async def test_an_entity_carries_its_area_floor_and_label_names(
    hass: HomeAssistant, client: HomeAssistantClient
) -> None:
    entity_id, area, floor, label = _lamp_in_office(hass)

    result = await EntityTools(client).ha_get_entity(entity_id=entity_id)

    entry = result["entity_entry"]
    assert (entry["area"], entry["floor"], entry["label_names"]) == (
        area,
        floor,
        [label],
    )


async def test_a_bulk_get_enriches_each_entity_on_its_own(
    hass: HomeAssistant, client: HomeAssistantClient
) -> None:
    entity_id, area, _, label = _lamp_in_office(hass)
    plain = er.async_get(hass).async_get_or_create("light", "test", "plain")

    result = await EntityTools(client).ha_get_entity(
        entity_id=[entity_id, plain.entity_id]
    )

    by_id = {e["entity_id"]: e for e in result["entity_entries"]}
    assert (by_id[entity_id]["area"], by_id[entity_id]["label_names"]) == (
        area,
        [label],
    )
    assert (by_id[plain.entity_id]["area"], by_id[plain.entity_id]["label_names"]) == (
        None,
        [],
    )


async def test_a_child_device_entity_gets_its_parents_area_and_floor(
    hass: HomeAssistant, client: HomeAssistantClient
) -> None:
    area_id, floor_id = _office_upstairs(hass)
    entity_id = _child_device_sensor(hass, area_id)

    result = await EntityTools(client).ha_get_entity(entity_id=entity_id)

    entry = result["entity_entry"]
    assert (entry["area"], entry["floor"]) == _names(hass, area_id, floor_id)


# --- voice assistant exposure ----------------------------------------------------


@pytest.fixture
async def exposure(
    hass: HomeAssistant, client: HomeAssistantClient
) -> VoiceAssistantTools:
    assert await async_setup_component(hass, "homeassistant", {})
    return VoiceAssistantTools(client)


async def test_a_single_exposure_keeps_its_assistant_map_and_adds_location(
    hass: HomeAssistant, exposure: VoiceAssistantTools
) -> None:
    area_id, floor_id = _office_upstairs(hass)
    lamp = er.async_get(hass).async_get_or_create("light", "test", "lamp")
    er.async_get(hass).async_update_entity(lamp.entity_id, area_id=area_id)
    hass.states.async_set(lamp.entity_id, "on", {"friendly_name": "Lamp"})
    async_expose_entity(hass, "conversation", lamp.entity_id, True)

    result = await exposure.ha_get_entity_exposure(entity_id=lamp.entity_id)

    assert result["exposed_to"] == {
        "conversation": True,
        "cloud.alexa": False,
        "cloud.google_assistant": False,
    }
    assert result["is_exposed_anywhere"] is True
    assert (result["friendly_name"], result["domain"]) == ("Lamp", "light")
    assert (result["area"], result["floor"]) == _names(hass, area_id, floor_id)


async def test_the_exposure_list_holds_only_exposed_entities(
    hass: HomeAssistant, exposure: VoiceAssistantTools
) -> None:
    area_id, floor_id = _office_upstairs(hass)
    registry = er.async_get(hass)
    lamp = registry.async_get_or_create("light", "test", "lamp")
    attic = registry.async_get_or_create("light", "test", "attic")
    registry.async_update_entity(lamp.entity_id, area_id=area_id)
    hass.states.async_set(lamp.entity_id, "on", {"friendly_name": "Lamp"})
    async_expose_entity(hass, "conversation", lamp.entity_id, True)
    for assistant in ("conversation", "cloud.alexa", "cloud.google_assistant"):
        async_expose_entity(hass, assistant, attic.entity_id, False)

    result = await exposure.ha_get_entity_exposure()

    assert result["exposed_entities"] == {lamp.entity_id: {"conversation": True}}
    assert set(result["entity_info"]) == {lamp.entity_id}
    area, _ = _names(hass, area_id, floor_id)
    assert result["entity_info"][lamp.entity_id]["area"] == area


async def test_a_child_device_entitys_exposure_names_its_parents_area_and_floor(
    hass: HomeAssistant, exposure: VoiceAssistantTools
) -> None:
    area_id, floor_id = _office_upstairs(hass)
    entity_id = _child_device_sensor(hass, area_id)
    async_expose_entity(hass, "conversation", entity_id, True)

    result = await exposure.ha_get_entity_exposure(entity_id=entity_id)

    assert (result["area"], result["floor"]) == _names(hass, area_id, floor_id)
