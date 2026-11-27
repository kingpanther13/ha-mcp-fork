"""Filter Core and journal records by event time and page whole timestamp groups."""

import math
from datetime import UTC, datetime
from itertools import groupby
from typing import Any

from homeassistant.const import KEY_DATA_LOGGING
from homeassistant.core import HomeAssistant

from .supervisor_log import _prepare as read_journal

_MAX_FILE_BYTES = 1_000_000


async def _source(
    hass: HomeAssistant,
    name: str,
    start: datetime,
    end: datetime,
    count: int,
    raw: bool,
    skip: int,
) -> dict[str, Any]:
    if name == "core" and (path := hass.data.get(KEY_DATA_LOGGING)):
        return await hass.async_add_executor_job(
            _file_page, path, start, end, count, skip, raw
        )
    stop = f"{end.timestamp():.6f}" if raw else str(math.floor(end.timestamp()) + 1)
    request = {
        "source": name,
        "range": f"realtime={start.timestamp():.6f}:{stop}:{skip}:{count - 1 if raw else count}",
        **(
            {}
            if raw
            else {"format": "plain" if name in {"core", "supervisor"} else "verbose"}
        ),
    }
    reply = await read_journal(hass, request)
    if raw and "text" in reply:
        request["range"] = (
            f"realtime={start.timestamp():.6f}:{stop}:{skip + count - 1}:1"
        )
        probe = await read_journal(hass, request)
        if "text" not in probe:
            return probe
        return {
            "lines": reply["text"].splitlines(),
            "has_more": bool(probe.get("first_cursor") or probe["text"]),
        }
    if "text" in reply:
        return {"lines": reply["text"].splitlines()}
    return reply


def _merge(
    batches: list[tuple[str, list[str], bool]],
    start: datetime,
    end: datetime,
    limit: int,
    search: str,
) -> dict[str, Any]:
    from homeassistant.components.system_log.llm import (
        _LOG_QUERY_ORDER_MARGIN,
        _parse_log_records,
    )

    rows: list[tuple[datetime, str, str]] = []
    complete_end = None
    for name, lines, limited in batches:
        records = _parse_log_records(lines)
        if limited:
            if not records:
                return {
                    "error": f"No timestamp in the bounded {name} read. Narrow the time window."
                }
            boundary = min(
                records[-1].time,
                max(row.time for row in records) - _LOG_QUERY_ORDER_MARGIN,
            )
            if boundary <= start:
                return {
                    "error": f"The {name} journal's first timestamp group exceeds the read bound."
                }
            complete_end = min(complete_end or boundary, boundary)
        rows.extend(
            (row.time, name, row.message)
            for row in records
            if start <= row.time <= end and search.casefold() in row.message.casefold()
        )
    rows.sort(key=lambda row: row[0])
    selected: list[dict[str, Any]] = []
    next_time = complete_end
    for moment, group in groupby(rows, key=lambda row: row[0]):
        if complete_end and moment >= complete_end:
            break
        if len(selected) >= limit:
            next_time = moment
            break
        selected.extend(
            {"source": name, "time": stamp.isoformat(), "message": message}
            for stamp, name, message in group
        )
    result: dict[str, Any] = {"records": selected, "returned_records": len(selected)}
    if next_time and next_time <= end:
        result["next_start_time"] = next_time.isoformat()
    result["has_more"] = "next_start_time" in result
    return result


def _file_page(
    path: str, start: datetime, end: datetime, count: int, skip: int, raw: bool
) -> dict[str, Any]:
    from homeassistant.components.system_log.llm import _RECORD_START

    selected: list[str] = []
    size = 0
    inside: bool | None = None
    warnings: list[str] = []
    with open(path, "rb") as file:
        while block := file.readline(_MAX_FILE_BYTES + 1):
            if len(block) > _MAX_FILE_BYTES:
                return {"error": "A file log line exceeds HA-MCP's byte limit."}
            line = block.decode(errors="replace").rstrip("\r\n")
            if match := _RECORD_START.match(line):
                stamp = datetime.fromisoformat(match[1])
                inside = stamp.tzinfo is not None and start <= stamp <= end
            elif inside is None and raw and not warnings:
                warnings.append(
                    "File text before its first timestamp cannot be placed in an exact time window. An ordinary raw read includes it."
                )
            if not inside:
                continue
            if skip:
                skip -= 1
                continue
            size += len(block)
            if size > _MAX_FILE_BYTES:
                warnings.append("HA-MCP's byte limit shortened this file window.")
                return {"lines": selected, "byte_limited": True, "warnings": warnings}
            selected.append(line)
            if len(selected) == count:
                break
    return {"lines": selected, "warnings": warnings}


async def _prepare(hass: HomeAssistant, msg: dict[str, Any]) -> dict[str, Any]:
    from homeassistant.components.system_log.llm import LOG_QUERY_READ_LINES

    try:
        start, end = (
            datetime.fromisoformat(msg[key]) for key in ("start_time", "end_time")
        )
        if not start.tzinfo or not end.tzinfo or end < start:
            raise ValueError
    except ValueError:
        return {"error": "Use ordered ISO timestamps with UTC offsets."}
    raw = msg["raw"]
    names = msg["sources"]
    if raw and len(names) != 1:
        return {"error": "Single-source raw reads require one source."}
    count = msg["limit"] + 1 if raw else LOG_QUERY_READ_LINES
    batches = []
    for name in names:
        if name in msg.get("journal_text", {}):
            batch = {"lines": msg["journal_text"][name].splitlines()}
        else:
            batch = await _source(
                hass,
                name,
                start.astimezone(UTC),
                end.astimezone(UTC),
                count,
                raw,
                msg["offset"],
            )
        if "lines" not in batch:
            return {**batch, "read_lines": count}
        lines = batch["lines"]
        if raw:
            selected = [
                line
                for line in (lines if "has_more" in batch else lines[: msg["limit"]])
                if msg["search"].casefold() in line.casefold()
            ]
            result = {
                "log": "\n".join(selected),
                "returned_lines": len(selected),
                "has_more": batch.get("has_more", len(lines) > msg["limit"]),
            }
            if batch.get("byte_limited"):
                result.update(has_more=True, next_offset=msg["offset"] + len(lines))
            if batch.get("warnings"):
                result["warnings"] = batch["warnings"]
            return result
        batches.append(
            (name, lines, bool(batch.get("byte_limited")) or len(lines) >= count)
        )
    return _merge(batches, start, end, msg["limit"], msg["search"])


def command_specs(vol: Any) -> list[tuple[dict[Any, Any], Any, Any]]:
    return [
        (
            {
                vol.Required("type"): "ha_mcp_tools/log_time_window",
                vol.Required("sources"): vol.All(
                    [vol.Match(r"^(?:app:)?[\w][\w.-]*\Z")], vol.Length(min=1, max=12)
                ),
                vol.Required("start_time"): str,
                vol.Required("end_time"): str,
                vol.Required("limit"): vol.All(int, vol.Range(min=1, max=500)),
                vol.Optional("search", default=""): str,
                vol.Optional("raw", default=False): bool,
                vol.Optional("journal_text"): vol.All(
                    {str: vol.All(str, vol.Length(max=1_000_000))}, vol.Length(max=12)
                ),
                vol.Optional("offset", default=0): vol.All(int, vol.Range(min=0)),
            },
            lambda hass, msg, **result: result,
            _prepare,
        )
    ]
