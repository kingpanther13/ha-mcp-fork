"""Attribute Core's stored system-log entries using integration manifests."""

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.loader import IntegrationNotFound, async_get_integration
from homeassistant.setup import async_get_loaded_integrations


async def integration_loggers(
    hass: HomeAssistant, selected: str | None = None
) -> dict[str, set[str]]:
    domains = {selected} if selected else set(async_get_loaded_integrations(hass))
    prefixes: dict[str, set[str]] = {}
    for domain in domains:
        integration = await async_get_integration(hass, domain)
        for name in (integration.pkg_path, *(integration.loggers or ())):
            prefixes.setdefault(name, set()).add(domain)
    return prefixes


def logger_domains(name: str, prefixes: dict[str, set[str]]) -> list[str]:
    matches: set[str] = set()
    while name:
        matches.update(prefixes.get(name, ()))
        name = name.rpartition(".")[0]
    return sorted(matches)


async def _entries(hass: HomeAssistant, msg: dict[str, Any]) -> dict[str, Any]:
    from homeassistant.components.system_log.const import DOMAIN

    rows = hass.data[DOMAIN].records.to_list()
    selected = msg.get("integration")
    try:
        prefixes = await integration_loggers(hass, selected)
    except IntegrationNotFound as exc:
        return {"error": str(exc)}
    for row in rows:
        row["integrations"] = logger_domains(row["name"], prefixes)
    if selected:
        rows = [row for row in rows if selected in row["integrations"]]
    return {"entries": rows}


def command_specs(vol: Any) -> list[tuple[dict[Any, Any], Any, Any]]:
    return [
        (
            {
                vol.Required("type"): "ha_mcp_tools/system_log_entries",
                vol.Optional("integration"): str,
            },
            lambda hass, msg, **result: result,
            _entries,
        )
    ]
