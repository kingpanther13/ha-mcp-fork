"""ha_manage_logs against real Core: statistics, the system log, a refusal."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from ha_mcp._vendor.fastmcp import Client

from ...utilities.assertions import MCPAssertions, safe_call_tool
from ...utilities.cleanup import CleanupSteps
from ...utilities.wait_helpers import wait_for_tool_result


def _sums(data: dict[str, Any]) -> list[float]:
    entities = data.get("data", data).get("entities") or [{}]
    return [row["sum"] for row in entities[0].get("statistics") or []]


@pytest.mark.asyncio
@pytest.mark.core
async def test_statistic_import_adjust_and_clear(mcp_client: Client) -> None:
    """An imported statistic is listed, an adjustment moves its later sums,
    and a clear removes it."""
    mcp = MCPAssertions(mcp_client)
    statistic_id = f"test:e2e_logs_manage_{uuid4().hex[:8]}"
    start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(
        days=2
    )
    hours = [start + timedelta(hours=i) for i in range(3)]
    history = {
        "source": "statistics",
        "entity_ids": statistic_id,
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(hours=3)).isoformat(),
        "period": "hour",
    }
    try:
        await mcp.call_tool_success(
            "ha_manage_logs",
            {
                "action": "recorder/import_statistics",
                "data": {
                    "metadata": {
                        "statistic_id": statistic_id,
                        "source": "test",
                        "name": "E2E ha_manage_logs",
                        "has_sum": True,
                        "mean_type": 0,
                        "unit_class": "energy",
                        "unit_of_measurement": "kWh",
                    },
                    "stats": [
                        {"start": hour.isoformat(), "state": total, "sum": total}
                        for hour, total in zip(hours, (1, 2, 3), strict=True)
                    ],
                },
            },
        )
        await wait_for_tool_result(
            mcp_client,
            tool_name="ha_manage_logs",
            arguments={"action": "recorder/list_statistic_ids", "limit": 1000},
            predicate=lambda d: any(
                row["statistic_id"] == statistic_id for row in d["result"]
            ),
            description="imported statistic listed",
            timeout=30,
        )
        await wait_for_tool_result(
            mcp_client,
            tool_name="ha_get_history",
            arguments=history,
            predicate=lambda d: _sums(d) == [1, 2, 3],
            description="imported statistic rows readable",
            timeout=30,
        )

        adjusted = await mcp.call_tool_success(
            "ha_manage_logs",
            {
                "action": "recorder/adjust_sum_statistics",
                "data": {
                    "statistic_id": statistic_id,
                    "start_time": hours[1].isoformat(),
                    "adjustment": 10,
                    "adjustment_unit_of_measurement": "kWh",
                },
            },
        )
        assert [row["statistic_id"] for row in adjusted["statistics_metadata"]] == [
            statistic_id
        ]
        await wait_for_tool_result(
            mcp_client,
            tool_name="ha_get_history",
            arguments=history,
            predicate=lambda d: _sums(d) == [1, 12, 13],
            description="adjusted sums readable",
            timeout=30,
        )

        cleared = await mcp.call_tool_success(
            "ha_manage_logs",
            {
                "action": "recorder/clear_statistics",
                "data": {"statistic_ids": [statistic_id]},
            },
        )
        assert cleared["statistics_metadata"] == []
        await wait_for_tool_result(
            mcp_client,
            tool_name="ha_get_history",
            arguments=history,
            predicate=lambda d: _sums(d) == [],
            description="cleared statistic gone",
            timeout=30,
        )
    finally:
        with CleanupSteps() as cleanup, cleanup.step():
            await safe_call_tool(
                mcp_client,
                "ha_manage_logs",
                {
                    "action": "recorder/clear_statistics",
                    "data": {"statistic_ids": [statistic_id]},
                },
            )


@pytest.mark.asyncio
@pytest.mark.core
async def test_a_written_system_log_entry_is_found_by_get_logs(
    mcp_client: Client,
) -> None:
    mcp = MCPAssertions(mcp_client)
    message = f"ha_manage_logs e2e {uuid4().hex}"

    await mcp.call_tool_success(
        "ha_manage_logs",
        {"action": "system_log.write", "data": {"message": message}},
    )

    logs = await mcp.call_tool_success(
        "ha_get_logs", {"source": "system", "search": message}
    )
    entries = logs.get("data", logs)["entries"]
    assert any(message in entry["message"] for entry in entries), entries


@pytest.mark.asyncio
@pytest.mark.core
async def test_cores_refusal_keeps_its_code(mcp_client: Client) -> None:
    mcp = MCPAssertions(mcp_client)

    refusal = await mcp.call_tool_failure(
        "ha_manage_logs",
        {
            "action": "recorder/adjust_sum_statistics",
            "data": {
                "statistic_id": f"test:e2e_missing_{uuid4().hex[:8]}",
                "start_time": datetime.now(UTC).isoformat(),
                "adjustment": 1,
                "adjustment_unit_of_measurement": "kWh",
            },
        },
    )

    assert refusal["ha_error_code"] == "unknown_statistic_id"
    assert refusal["error"]["code"] == "SERVICE_CALL_FAILED"
