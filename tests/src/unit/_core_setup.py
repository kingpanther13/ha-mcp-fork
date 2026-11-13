"""Set Core up for a unit test: an integration from YAML, a config entry."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry


async def set_up_domain(hass: HomeAssistant, domain: str, config: Any) -> None:
    """Set ``domain`` up from ``config`` and wait for what it starts."""
    assert await async_setup_component(hass, domain, {domain: config})
    await hass.async_block_till_done()


def add_test_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A config entry of the ``test`` domain that devices and entities belong to."""
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    return entry
