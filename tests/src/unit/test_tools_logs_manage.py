"""ha_manage_logs runs Core's own recorder, statistics and log operations.

Every reply and refusal comes from Core's recorder, ``system_log``, ``logger``
and ``logbook`` on a real ``hass``, reached through the server's own client.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder import websocket_api as recorder_websocket_api
from homeassistant.components.recorder.history import state_changes_during_period
from homeassistant.components.recorder.models import StatisticData
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import MockModule, mock_integration
from pytest_homeassistant.components.recorder.common import (
    async_wait_purge_done,
    async_wait_recording_done,
)

from ha_mcp._vendor.fastmcp.exceptions import ToolError
from ha_mcp.client.rest_client import HomeAssistantClient
from ha_mcp.tools import tools_logs_manage
from ha_mcp.tools.tools_logs_manage import LogsManageTools
from ha_mcp.tools.tools_services import ServiceDiscoveryTools

from ._recorded_history import import_statistic, record_states

ENERGY = "sensor.energy"
START = datetime(2024, 1, 1, tzinfo=UTC)
END = START + timedelta(hours=3)
HOURS = [START + timedelta(hours=i) for i in range(3)]


def _rows(*sums: float) -> list[StatisticData]:
    return [
        {"start": start, "state": total, "sum": total}
        for start, total in zip(HOURS, sums, strict=True)
    ]


async def _manage(client: HomeAssistantClient, action: str, **kwargs: Any) -> Any:
    return await LogsManageTools(client).ha_manage_logs(action=action, **kwargs)


async def _refusal(client: HomeAssistantClient, action: str, **kwargs: Any) -> Any:
    with pytest.raises(ToolError) as raised:
        await _manage(client, action, **kwargs)
    return json.loads(str(raised.value))


async def _sums(hass: HomeAssistant, recorder: Recorder, statistic_id: str) -> Any:
    """The hourly sums Core's recorder holds for ``statistic_id``, after every
    queued recorder job has run."""
    await async_wait_recording_done(hass)
    rows = await recorder.async_add_executor_job(
        statistics_during_period,
        hass,
        START,
        END,
        {statistic_id},
        "hour",
        None,
        {"sum"},
    )
    return [row["sum"] for row in rows.get(statistic_id, [])]


@pytest.fixture
async def client(
    recorder_mock: Recorder, hass: HomeAssistant, core_client: HomeAssistantClient
) -> HomeAssistantClient:
    """The client on a ``hass`` with the recorder and ``system_log``."""
    assert await async_setup_component(hass, "system_log", {})
    return core_client


# --- Statistics commands ------------------------------------------------------


async def test_a_cleared_statistic_is_gone(
    recorder_mock: Recorder, hass: HomeAssistant, client: HomeAssistantClient
) -> None:
    await import_statistic(hass, ENERGY, "kWh", "energy", _rows(1, 2, 3))

    result = await _manage(
        client, "recorder/clear_statistics", data={"statistic_ids": [ENERGY]}
    )

    assert result["success"] is True
    assert result["statistics_metadata"] == []
    assert await _sums(hass, recorder_mock, ENERGY) == []


async def test_an_adjusted_sum_moves_every_later_row_by_the_adjustment(
    recorder_mock: Recorder, hass: HomeAssistant, client: HomeAssistantClient
) -> None:
    await import_statistic(hass, ENERGY, "kWh", "energy", _rows(1, 2, 3))

    result = await _manage(
        client,
        "recorder/adjust_sum_statistics",
        data={
            "statistic_id": ENERGY,
            "start_time": HOURS[1].isoformat(),
            "adjustment": 10,
            "adjustment_unit_of_measurement": "kWh",
        },
    )

    assert [row["statistic_id"] for row in result["statistics_metadata"]] == [ENERGY]
    assert await _sums(hass, recorder_mock, ENERGY) == [1, 12, 13]


async def test_a_changed_unit_converts_the_stored_values(
    recorder_mock: Recorder, hass: HomeAssistant, client: HomeAssistantClient
) -> None:
    await import_statistic(hass, ENERGY, "kWh", "energy", _rows(1000, 2000, 3000))

    await _manage(
        client,
        "recorder/change_statistics_unit",
        data={
            "statistic_id": ENERGY,
            "old_unit_of_measurement": "kWh",
            "new_unit_of_measurement": "MWh",
        },
    )

    assert await _sums(hass, recorder_mock, ENERGY) == pytest.approx([1, 2, 3])


async def test_updated_statistics_metadata_is_read_back_from_core(
    client: HomeAssistantClient, hass: HomeAssistant
) -> None:
    await import_statistic(hass, ENERGY, "kWh", "energy", _rows(1, 2, 3))

    result = await _manage(
        client,
        "recorder/update_statistics_metadata",
        data={
            "statistic_id": ENERGY,
            "unit_class": None,
            "unit_of_measurement": "Wh",
        },
    )

    (row,) = result["statistics_metadata"]
    assert row["statistics_unit_of_measurement"] == "Wh"


async def test_imported_statistics_are_readable(
    recorder_mock: Recorder, hass: HomeAssistant, client: HomeAssistantClient
) -> None:
    statistic_id = "test:imported_energy"

    result = await _manage(
        client,
        "recorder/import_statistics",
        data={
            "metadata": {
                "statistic_id": statistic_id,
                "source": "test",
                "name": None,
                "has_sum": True,
                "mean_type": 0,
                "unit_class": "energy",
                "unit_of_measurement": "kWh",
            },
            "stats": [
                {"start": start.isoformat(), "sum": total}
                for start, total in zip(HOURS, (4, 5, 6), strict=True)
            ],
        },
    )

    assert await _sums(hass, recorder_mock, statistic_id) == [4, 5, 6]
    assert [row["statistic_id"] for row in result["statistics_metadata"]] == [
        statistic_id
    ]


async def test_the_statistic_listing_pages(
    client: HomeAssistantClient, hass: HomeAssistant
) -> None:
    for name in ("a", "b", "c"):
        await import_statistic(hass, f"sensor.{name}", "kWh", "energy", _rows(1, 2, 3))

    first = await _manage(client, "recorder/list_statistic_ids", limit=2)
    rest = await _manage(client, "recorder/list_statistic_ids", limit=2, offset=2)

    listed = [row["statistic_id"] for row in first["result"] + rest["result"]]
    assert sorted(listed) == ["sensor.a", "sensor.b", "sensor.c"]
    assert (first["total_count"], first["has_more"]) == (3, True)
    assert (len(rest["result"]), rest["has_more"]) == (1, False)


@pytest.mark.parametrize(
    ("action", "core_function"),
    [
        ("recorder/validate_statistics", "validate_statistics"),
        ("recorder/update_statistics_issues", "update_statistics_issues"),
    ],
)
async def test_a_statistics_maintenance_command_runs_cores_job(
    client: HomeAssistantClient, action: str, core_function: str
) -> None:
    real = getattr(recorder_websocket_api, core_function)
    with patch.object(recorder_websocket_api, core_function, wraps=real) as job:
        result = await _manage(client, action)

    assert result["success"] is True
    assert job.call_count == 1


# --- Services ----------------------------------------------------------------


async def test_a_purge_removes_states_older_than_it_keeps(
    recorder_mock: Recorder, hass: HomeAssistant, client: HomeAssistantClient
) -> None:
    now = dt_util.utcnow()
    old = (now - timedelta(days=30)).timestamp()
    await record_states(
        hass, "sensor.door", ("open", None, old), ("closed", None, now.timestamp())
    )

    await _manage(client, "recorder.purge", data={"keep_days": 5})
    await async_wait_purge_done(hass)

    states = await recorder_mock.async_add_executor_job(
        state_changes_during_period,
        hass,
        now - timedelta(days=60),
        None,
        "sensor.door",
    )
    assert [state.state for state in states["sensor.door"]] == ["closed"]


async def _system_log(client: HomeAssistantClient) -> list[dict[str, Any]]:
    reply = await client.send_websocket_message({"type": "system_log/list"})
    entries: list[dict[str, Any]] = reply["result"]
    return entries


async def test_a_written_system_log_entry_is_listed(
    client: HomeAssistantClient,
) -> None:
    await _manage(client, "system_log.write", data={"message": "hamcp checkpoint"})

    assert ["hamcp checkpoint"] in [
        entry["message"] for entry in await _system_log(client)
    ]


async def test_clearing_the_system_log_empties_it(
    client: HomeAssistantClient, hass: HomeAssistant
) -> None:
    await hass.services.async_call(
        "system_log", "write", {"message": "before clear"}, blocking=True
    )
    assert await _system_log(client)

    await _manage(client, "system_log.clear")

    assert await _system_log(client) == []


@pytest.fixture
def logger_level() -> Iterator[logging.Logger]:
    """The ``logger`` integration's own logger, put back to its level after
    the test, since log levels outlive ``hass``."""
    target = logging.getLogger("homeassistant.components.logger")
    level = target.level
    yield target
    logging.Logger.setLevel(target, level)


async def test_a_set_level_changes_the_level_core_reports(
    client: HomeAssistantClient, hass: HomeAssistant, logger_level: logging.Logger
) -> None:
    assert await async_setup_component(hass, "logger", {})

    await _manage(client, "logger.set_level", data={logger_level.name: "debug"})

    reply = await client.send_websocket_message({"type": "logger/log_info"})
    levels = {row["domain"]: row["level"] for row in reply["result"]}
    assert levels["logger"] == logging.DEBUG


async def test_a_logbook_message_becomes_a_logbook_entry(
    client: HomeAssistantClient, hass: HomeAssistant
) -> None:
    # The logbook registers a sidebar panel; the real frontend needs the
    # frontend build, which is not installed with Core.
    mock_integration(hass, MockModule("frontend"))
    assert await async_setup_component(hass, "logbook", {})
    start = dt_util.utcnow()

    await _manage(
        client, "logbook.log", data={"name": "Checkpoint", "message": "reached"}
    )
    await async_wait_recording_done(hass)

    reply = await client.send_websocket_message(
        {"type": "logbook/get_events", "start_time": start.isoformat()}
    )
    assert [(e.get("name"), e.get("message")) for e in reply["result"]] == [
        ("Checkpoint", "reached")
    ]


# --- Core's refusals ---------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "data", "core_code", "code"),
    [
        (
            "recorder/adjust_sum_statistics",
            {
                "statistic_id": "sensor.unknown",
                "start_time": START.isoformat(),
                "adjustment": 1,
                "adjustment_unit_of_measurement": "kWh",
            },
            "unknown_statistic_id",
            "SERVICE_CALL_FAILED",
        ),
        (
            "recorder/adjust_sum_statistics",
            {
                "statistic_id": ENERGY,
                "start_time": START.isoformat(),
                "adjustment": 1,
                "adjustment_unit_of_measurement": "°C",
            },
            "invalid_units",
            "VALIDATION_INVALID_PARAMETER",
        ),
        (
            "recorder/clear_statistics",
            {
                "statistic_ids": [ENERGY],
                "start_time": HOURS[2].isoformat(),
                "end_time": HOURS[0].isoformat(),
            },
            "invalid_time_range",
            "VALIDATION_INVALID_PARAMETER",
        ),
        (
            "recorder/clear_statistics",
            {"statistic_ids": [ENERGY], "unexpected": True},
            "invalid_format",
            "VALIDATION_INVALID_PARAMETER",
        ),
        (
            "system_log.write",
            {"message": "x", "unexpected": True},
            "invalid_format",
            "VALIDATION_INVALID_PARAMETER",
        ),
    ],
)
async def test_cores_refusal_keeps_its_code(
    client: HomeAssistantClient,
    hass: HomeAssistant,
    action: str,
    data: dict[str, Any],
    core_code: str,
    code: str,
) -> None:
    await import_statistic(hass, ENERGY, "kWh", "energy", _rows(1, 2, 3))

    refusal = await _refusal(client, action, data=data)

    assert refusal["ha_error_code"] == core_code
    assert refusal["error"]["code"] == code


@pytest.mark.parametrize(
    ("action", "field"),
    [("recorder/clear_statistics", "id"), ("system_log.write", "type")],
)
async def test_data_cannot_carry_the_websocket_envelope(
    client: HomeAssistantClient, sent: AsyncMock, action: str, field: str
) -> None:
    refusal = await _refusal(client, action, data={field: "x", "message": "x"})

    assert refusal["error"]["code"] == "VALIDATION_FAILED"
    sent.assert_not_called()


# --- describe ----------------------------------------------------------------


async def test_describe_returns_cores_service_description_without_calling_it(
    client: HomeAssistantClient, sent: AsyncMock
) -> None:
    catalog = await ServiceDiscoveryTools(client).ha_list_services(
        domain="system_log", detail_level="full"
    )

    result = await _manage(client, "system_log.write", describe=True)

    assert result["result"] == catalog["services"]["system_log.write"]
    assert all(
        call.args[0].get("type") != "call_service" for call in sent.call_args_list
    )


async def test_describe_returns_cores_command_schema_through_the_component(
    recorder_mock: Recorder, component_core_client: HomeAssistantClient, sent: AsyncMock
) -> None:
    result = await _manage(
        component_core_client, "recorder/adjust_sum_statistics", describe=True
    )

    assert result["result"]["status"] == "available"
    assert "adjustment" in json.dumps(result["result"]["schema"])
    sent.assert_not_called()


async def test_describe_without_the_component_says_why_it_cannot(
    client: HomeAssistantClient, sent: AsyncMock
) -> None:
    result = await _manage(client, "recorder/adjust_sum_statistics", describe=True)

    assert result["result"]["status"] == "unavailable"
    assert result["result"]["reason"] in result["warnings"]
    sent.assert_not_called()


# --- Waiting for the recorder ------------------------------------------------


async def test_a_recorder_action_returns_after_its_queued_job(
    recorder_mock: Recorder, client: HomeAssistantClient
) -> None:
    assert await recorder_mock.lock_database()
    try:
        call = asyncio.create_task(_manage(client, "recorder.purge"))
        # The purge stays queued while the database is locked.
        await asyncio.sleep(1)
        assert not call.done()
    finally:
        recorder_mock.unlock_database()

    assert "warnings" not in await call


async def test_a_recorder_still_busy_is_a_warning_not_a_failure(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    client: HomeAssistantClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Any bound shorter than the lock below shows the wait gives up.
    monkeypatch.setattr(tools_logs_manage, "RECORDER_DRAIN_SECONDS", 0.5)
    assert await recorder_mock.lock_database()
    try:
        result = await _manage(client, "recorder.purge")
    finally:
        recorder_mock.unlock_database()
    await async_wait_recording_done(hass)

    assert result["success"] is True
    assert any("recorder" in warning for warning in result["warnings"])
