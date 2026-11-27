"""The 2026.12 bridge reads a since-start app log through Core's own client."""

from typing import Any
from unittest.mock import AsyncMock, patch

import aiohasupervisor.models
import pytest
from homeassistant.components import websocket_api
from homeassistant.const import KEY_DATA_LOGGING
from homeassistant.core import HomeAssistant

from ha_mcp.client.rest_client import HomeAssistantClient

from ._error_log_core import call_logs
from ._tools_addons_core import Supervisor
from ._tools_addons_core import core_supervisor as core_supervisor
from ._tools_addons_core import supervisor as supervisor
from ._tools_addons_core import target_app as target_app


@pytest.mark.skipif(
    not hasattr(aiohasupervisor.models, "LogsOptions"),
    reason="Core 2026.11's Supervisor client does not implement native journal reads",
)
async def test_latest_app_uses_core_supervisor_client(
    component_core_client: HomeAssistantClient, core_supervisor: Supervisor
) -> None:
    path = "/addons/core_ssh/logs/latest"
    core_supervisor.reply("GET", path, text="startup banner\nSSH started\n")
    with patch.object(
        component_core_client,
        "guarded_request",
        AsyncMock(wraps=component_core_client.guarded_request),
    ) as rest:
        result = await call_logs(
            component_core_client,
            source="supervisor",
            slug="core_ssh",
            latest=True,
            order="oldest",
            limit=2,
        )
    assert result["log"].splitlines() == ["startup banner", "SSH started"]
    assert not result.get("warnings")
    assert core_supervisor.requests == [("GET", path)]
    assert not any("/hassio/" in call.args[1] for call in rest.await_args_list)


@pytest.mark.skipif(
    not hasattr(aiohasupervisor.models, "LogsOptions"),
    reason="Core 2026.11's Supervisor client does not implement native journal reads",
)
async def test_large_journal_search_retries_a_smaller_native_range(
    hass: HomeAssistant,
    component_core_client: HomeAssistantClient,
    core_supervisor: Supervisor,
) -> None:
    hass.data.pop(KEY_DATA_LOGGING, None)
    path = "/core/logs"
    recent = "2026-11-27 18:00:00.000+00:00 WARNING (MainThread) [logger] done"
    core_supervisor.reply("GET", path, text=("x" * 1024 + "\n") * 1000 + recent + "\n")
    core_supervisor.reply("GET", path, text=recent + "\n")
    with patch.object(
        component_core_client,
        "get_error_log",
        AsyncMock(wraps=component_core_client.get_error_log),
    ) as fallback:
        result = await call_logs(
            component_core_client, source="error_log", search="done", limit=1
        )
    fallback.assert_not_awaited()
    assert not any("Marketplace" in warning for warning in result["warnings"])
    assert result["log"] == recent
    assert core_supervisor.requests == [("GET", path), ("GET", path)]
    assert any("byte limit" in warning for warning in result["warnings"])


@pytest.mark.parametrize("command", ["supervisor_log", "log_time_window"])
async def test_failed_advertised_journal_command_labels_the_proxy(
    hass: HomeAssistant,
    component_core_client: HomeAssistantClient,
    core_supervisor: Supervisor,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    monkeypatch.delitem(hass.data[websocket_api.DOMAIN], "ha_mcp_tools/" + command)
    args: dict[str, Any] = {}
    if command == "supervisor_log":
        path = "/core/logs/boots/-1"
        args["boot"] = -1
    else:
        path = "/core/logs"
        args.update(start_time="2026-11-27T18:00:00Z", end_time="2026-11-27T18:05:00Z")
    core_supervisor.reply("GET", path, text="startup banner\n")
    core_supervisor.reply("GET", path, text="")
    with patch.object(
        component_core_client,
        "guarded_request",
        AsyncMock(wraps=component_core_client.guarded_request),
    ) as rest:
        result = await call_logs(
            component_core_client, source="error_log", limit=1, **args
        )
    assert result["log"] == "startup banner"
    assert any("Marketplace" in message for message in result["warnings"])
    assert any("/hassio/core/logs" in call.args[1] for call in rest.await_args_list)
