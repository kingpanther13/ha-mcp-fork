"""Unit tests for ``HomeAssistantClient.get_states()``'s response-shape contract.

Every ``/states`` reply Home Assistant itself produces on success is a JSON
array, so ``get_states`` hands it through unchanged; a non-list reply can only
mean the fetch itself failed (see ``get_states``). The client runs against a
real ``hass``, so each reply is Core's own.
"""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant

from ha_mcp.client.rest_client import HomeAssistantClient


async def test_get_states_passes_through_a_real_list(
    hass: HomeAssistant, core_client: HomeAssistantClient
) -> None:
    """The overwhelmingly common, correct case: unchanged."""
    hass.states.async_set("light.one", "on")

    result = await core_client.get_states()

    assert [state["entity_id"] for state in result] == ["light.one"]
    assert result == [json.loads(s.as_dict_json) for s in hass.states.async_all()]


async def test_get_states_passes_through_a_genuinely_empty_list(
    hass: HomeAssistant, core_client: HomeAssistantClient
) -> None:
    """An empty list IS a list -- isinstance(result, list) accepts it as-is.

    The raise in ``get_states`` is about the shape being wrong, not the count
    being zero: a ``hass`` with no entities answers ``[]``.
    """
    assert hass.states.async_all() == []

    result = await core_client.get_states()

    assert result == []


async def test_a_write_reply_that_is_not_json_counts_as_accepted(
    core_client: HomeAssistantClient,
) -> None:
    """The write may have run; reporting it as failed invites a retry that
    repeats it, so the caller gets the empty result and verifies state.

    Core's ``POST /api/template`` answers with the rendered text, here an HTML
    page: ``_request`` returns ``{}`` instead of raising, which
    ``render_template`` reads as no result.
    """
    assert await core_client.render_template("<html>proxy login</html>") == ""
