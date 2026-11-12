"""Temporary probe: can an automation have run yet hold no stored traces?"""

from __future__ import annotations

import json
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from ha_mcp.client.rest_client import HomeAssistantClient
from ha_mcp.tools.tools_traces import TraceTools


@pytest.mark.parametrize("trace_config", [{"stored_traces": 0}, {"stored_traces": 1}])
async def test_probe(
    hass: HomeAssistant,
    component_core_client: HomeAssistantClient,
    trace_config: dict[str, Any],
) -> None:
    assert await async_setup_component(hass, "trace", {})
    ok = await async_setup_component(
        hass,
        "automation",
        {
            "automation": [
                {
                    "id": "test",
                    "alias": "Test",
                    "trigger": [{"platform": "state", "entity_id": "light.trigger"}],
                    "action": [{"event": "trace_test"}],
                    "trace": trace_config,
                }
            ]
        },
    )
    await hass.async_block_till_done()
    hass.states.async_set("light.trigger", "on")
    await hass.async_block_till_done()
    state = hass.states.get("automation.test")
    result = await TraceTools(component_core_client).ha_get_automation_traces(
        entity_id="automation.test"
    )
    print(
        "PROBE",
        json.dumps(trace_config),
        "setup_ok",
        ok,
        "state",
        state and state.attributes.get("last_triggered"),
        "result",
        json.dumps(result, default=str)[:600],
    )
