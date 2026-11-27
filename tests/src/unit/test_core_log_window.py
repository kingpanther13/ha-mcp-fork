"""File-backed Core log reads stay on the component and resume after appends."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components import websocket_api
from homeassistant.const import KEY_DATA_LOGGING_DISABLED_REASON
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from ha_mcp.client.rest_client import HomeAssistantClient

from ._error_log_core import call_logs, numbered_log, serve_log
from ._error_log_core import log_file as log_file
from ._log_tool_calls import set_up_core_logs


@pytest.mark.parametrize(
    ("source", "command"),
    [
        ("system", "system_log_entries"),
        ("error_log", "core_log_window"),
        ("logger", "logger_info"),
    ],
)
async def test_advertised_command_failure_labels_the_fallback(
    log_file: Path,
    hass: HomeAssistant,
    component_core_client: HomeAssistantClient,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    command: str,
) -> None:
    await set_up_core_logs(hass)
    await serve_log(log_file, numbered_log(20))
    monkeypatch.delitem(hass.data[websocket_api.DOMAIN], f"ha_mcp_tools/{command}")
    page = await call_logs(component_core_client, source=source, limit=2)
    assert page["success"]
    assert any("ha_mcp_tools component" in warning for warning in page["warnings"])


async def test_journal_timeline_labels_both_apps_at_the_same_instant(
    component_core_client: HomeAssistantClient,
) -> None:
    first = "2026-11-12 10:00:01.123+00:00"
    second = "2026-11-12 10:00:02.123+00:00"
    logs = {
        "app:one": f"untimestamped banner\n{first} exception\nframe one\n{second} later",
        "app:two": f"{first} other",
    }
    request = {
        "type": "ha_mcp_tools/log_time_window",
        "sources": list(logs),
        "start_time": first,
        "end_time": second,
        "limit": 1,
        "journal_text": logs,
    }
    first_page = (await component_core_client.send_websocket_message(request))["result"]
    assert [(r["source"], r["message"]) for r in first_page["records"]] == [
        ("app:one", "exception\nframe one"),
        ("app:two", "other"),
    ]
    request["start_time"] = first_page["next_start_time"]
    last = (await component_core_client.send_websocket_message(request))["result"]
    assert [(r["source"], r["message"]) for r in last["records"]] == [
        ("app:one", "later")
    ]
    assert not last["has_more"]


async def test_exact_window_pages_keep_timestamp_ties_and_tracebacks(
    log_file: Path, component_core_client: HomeAssistantClient
) -> None:
    first = "2026-11-12 10:00:01.123+00:00"
    second = "2026-11-12 10:00:02.123+00:00"
    await serve_log(
        log_file,
        f"{first} ERROR (MainThread) [a] first\nframe\n"
        f"{first} INFO (MainThread) [b] tied\n{second} INFO (MainThread) [b] second\n",
    )
    args = {"sources": ["core"], "start_time": first, "end_time": second, "limit": 1}
    with patch.object(
        component_core_client,
        "guarded_request",
        AsyncMock(wraps=component_core_client.guarded_request),
    ) as rest:
        page = await call_logs(component_core_client, **args)
        assert [row["source"] for row in page["records"]] == ["core", "core"]
        assert page["records"][0]["message"].endswith("first\nframe")
        args["start_time"] = page["next_start_time"]
        last = await call_logs(component_core_client, **args)
    assert not page.get("warnings") and not last.get("warnings")
    assert not any("/hassio/" in call.args[1] for call in rest.await_args_list)
    assert [row["message"].split()[-1] for row in last["records"]] == ["second"]
    assert not last["has_more"]


async def test_appending_new_records_does_not_shift_the_second_page(
    log_file: Path, hass: HomeAssistant, component_core_client: HomeAssistantClient
) -> None:
    await set_up_core_logs(hass)
    await serve_log(log_file, numbered_log(450))
    with patch.object(
        component_core_client,
        "get_error_log",
        AsyncMock(wraps=component_core_client.get_error_log),
    ) as fallback:
        first = await call_logs(component_core_client, source="error_log", limit=1)
        with log_file.open("a", encoding="utf-8") as output:
            output.write(numbered_log(3))
        second = await call_logs(
            component_core_client,
            source="error_log",
            limit=1,
            offset=first["next_offset"],
        )
    assert first["log"].endswith("issue 449")
    assert second["log"].endswith("issue 448")
    assert not first.get("warnings")
    fallback.assert_not_awaited()


async def test_integration_read_keeps_its_whole_traceback(
    log_file: Path, hass: HomeAssistant, component_core_client: HomeAssistantClient
) -> None:
    await set_up_core_logs(hass)
    text = "2026-11-12 10:00:01.123+00:00 ERROR (MainThread) [homeassistant.components.logger] failed\nValueError: marker"
    await serve_log(log_file, text + "\n" + numbered_log(1))
    result = await call_logs(
        component_core_client, source="error_log", integration="logger"
    )
    assert result["log"] == text


async def test_disabled_reason_reaches_the_tool_without_a_log_file(
    hass: HomeAssistant, core_client: HomeAssistantClient
) -> None:
    hass.data[KEY_DATA_LOGGING_DISABLED_REASON] = "environment"
    result = await call_logs(core_client, source="error_log")
    assert result["log_file_disabled_reason"] == "environment"
    assert result["warnings"]


async def test_summary_groups_a_core_logger_under_its_integration(
    log_file: Path, hass: HomeAssistant, component_core_client: HomeAssistantClient
) -> None:
    await set_up_core_logs(hass)
    await serve_log(
        log_file,
        "2026-11-12 10:00:01.123+00:00 ERROR (MainThread) [homeassistant.components.logger.child] failed\n",
    )
    result = await call_logs(component_core_client, source="error_log", structured=True)
    assert result["by_component"] == {
        "logger": {"total_occurrences": 1, "issue_count": 1}
    }


async def test_search_returns_recent_match_when_window_exceeds_byte_limit(
    log_file: Path, component_core_client: HomeAssistantClient
) -> None:
    line = "2026-11-12 10:00:01.123+00:00 DEBUG (MainThread) [load] " + "x" * 1024
    marker = "2026-11-12 10:00:02.123+00:00 WARNING (MainThread) [load] done"
    await serve_log(log_file, (line + "\n") * 1200 + marker + "\n")
    page = await call_logs(
        component_core_client, source="error_log", search="done", limit=1
    )
    assert page["log"] == marker
    assert page["has_more"]
    assert any("byte limit" in warning for warning in page["warnings"])


async def test_summary_credits_each_owner_of_a_shared_library_logger(
    log_file: Path,
    hass: HomeAssistant,
    component_core_client: HomeAssistantClient,
) -> None:
    domains = ("homeassistant_connect_zbt2", "homeassistant_sky_connect")
    for domain in domains:
        assert await async_setup_component(hass, domain, {})
    await serve_log(
        log_file,
        "2026-11-12 10:00:01.123+00:00 ERROR (MainThread) [bellows.radio] failed\n",
    )
    result = await call_logs(component_core_client, source="error_log", structured=True)
    for domain in domains:
        assert result["by_component"][domain] == {
            "total_occurrences": 1,
            "issue_count": 1,
        }
    assert result["top_issues"][0]["count"] == 1


async def test_exact_file_window_marks_a_startup_banner_without_a_time(
    log_file: Path,
    component_core_client: HomeAssistantClient,
) -> None:
    stamp = "2026-11-12 10:00:01.123+00:00"
    await serve_log(
        log_file, f"startup banner\n{stamp} INFO (MainThread) [a] first\nframe\n"
    )
    result = await call_logs(
        component_core_client, source="error_log", start_time=stamp, end_time=stamp
    )
    assert result["log"].endswith("first\nframe")
    assert any("timestamp" in warning for warning in result["warnings"])
    plain = await call_logs(component_core_client, source="error_log", limit=10)
    assert "startup banner" in plain["log"]


async def test_byte_limited_exact_file_window_resumes_all_lines(
    log_file: Path,
    component_core_client: HomeAssistantClient,
) -> None:
    stamp = "2026-11-12 10:00:01.123+00:00"
    rows = [f"{stamp} INFO (MainThread) [a] {n} " + "x" * 2200 for n in range(480)]
    await serve_log(log_file, "\n".join(rows) + "\n")
    args = {"source": "error_log", "start_time": stamp, "end_time": stamp, "limit": 500}
    page = await call_logs(component_core_client, **args)
    assert page["has_more"]
    next_page = await call_logs(
        component_core_client, offset=page["next_offset"], **args
    )
    assert not next_page["has_more"]
    assert page["log"].splitlines() + next_page["log"].splitlines() == rows
