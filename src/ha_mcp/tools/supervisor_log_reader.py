"""Fetch one bounded journal range through Core's Supervisor client."""

import re
from datetime import datetime
from math import floor
from typing import Any

from .._version import is_running_in_addon
from ..client.error_log_journald import JournaldPage
from ..client.exceptions import HomeAssistantAPIError
from ..client.journal_window import fetch_range
from ..errors import ErrorCode, create_error_response
from .component_api import (
    COMPONENT_VIA_MARKETPLACE,
    ask_component,
    component_supports,
    component_unusable_warning,
    get_component_caps,
)
from .helpers import exception_to_structured_error, raise_tool_error
from .log_common import (
    DEFAULT_LOG_LIMIT,
    SUPERVISOR_SEARCH_WINDOW_LINES,
    _coerce_limit,
    _validate_log_slug,
)


async def journal_fallback_warning(client: Any, action: str) -> str:
    caps = await get_component_caps(client)
    if component_supports(caps, "supervisor_log"):
        return (
            f"The native journal read was unavailable, so {action}. "
            "It requires Core 2026.12 or later and a working HA-MCP journal command. "
            f"Install component updates {COMPONENT_VIA_MARKETPLACE}"
        )
    return component_unusable_warning(caps, action)


async def _proxy_window(
    client: Any, names: list[str], args: dict[str, Any], read_lines: int | None
) -> dict[str, Any]:
    warning = await journal_fallback_warning(
        client,
        "the exact journal window uses the Supervisor API",
    )
    if not is_running_in_addon() and not await client._is_supervised_install():
        raise_tool_error(
            create_error_response(
                ErrorCode.SERVICE_CALL_FAILED,
                "Reading exact file-log windows requires the component.",
                suggestions=[warning],
            )
        )
    try:
        start, end = (
            datetime.fromisoformat(args[k]) for k in ("start_time", "end_time")
        )
        if not start.tzinfo or not end.tzinfo or end < start:
            raise ValueError
    except ValueError:
        raise_tool_error(
            create_error_response(
                ErrorCode.VALIDATION_INVALID_PARAMETER,
                "Supply ordered start_time and end_time with UTC offsets.",
            )
        )
    received = {}
    for name in names:
        path = name.replace("app:", "addons/", 1) if name.startswith("app:") else name
        span = f"realtime={start.timestamp():.6f}:"
        if args["raw"]:
            span += f"{end.timestamp():.6f}"
            count, skip = args["limit"], args["offset"]
            page = await fetch_range(client, path, f"{span}:{skip}:{count}")
            probe = await fetch_range(client, path, f"{span}:{skip + count}:1")
            lines = [
                line
                for line in page.text.splitlines()
                if args["search"].casefold() in line.casefold()
            ]
            return {
                "log": "\n".join(lines),
                "returned_lines": len(lines),
                "has_more": bool(probe.first_cursor or probe.text),
                "warnings": [warning],
            }
        if read_lines is None:
            break
        page = await fetch_range(
            client,
            path,
            f"{span}{floor(end.timestamp()) + 1}:0:{read_lines}",
            "plain" if name in {"core", "supervisor"} else "verbose",
        )
        received[name] = page.text
    parsed = (
        await ask_component(client, "log_time_window", journal_text=received, **args)
        if received
        else None
    )
    if parsed is None:
        raise_tool_error(
            create_error_response(
                ErrorCode.SERVICE_CALL_FAILED,
                "Merging timeline records requires the HA-MCP component.",
                suggestions=[warning],
            )
        )
    return {**parsed, "warnings": [warning]}


async def exact_log_window(
    client: Any,
    source: str,
    slug: str | None,
    sources: list[str] | None,
    start: str | None,
    end: str | None,
    limit: int | None,
    offset: int | str,
    search: str | None,
    order: str | None,
) -> dict[str, Any]:
    raw = sources is None
    if (
        not start
        or not end
        or not isinstance(offset, int)
        or offset < 0
        or (not raw and offset)
    ):
        raise_tool_error(
            create_error_response(
                ErrorCode.VALIDATION_INVALID_PARAMETER,
                "Supply start_time and end_time with UTC offsets. Raw reads accept a non-negative offset; continue a timeline with next_start_time.",
            )
        )
    if raw:
        if source not in {"error_log", "supervisor", "system_service"}:
            raise_tool_error(
                create_error_response(
                    ErrorCode.VALIDATION_INVALID_PARAMETER,
                    "Choose error_log, supervisor or system_service for a timed raw read.",
                )
            )
        _validate_log_slug(source, slug)
        sources = [
            "core"
            if source == "error_log"
            else f"app:{slug}"
            if source == "supervisor"
            else str(slug)
        ]
    effective = _coerce_limit(limit, default=DEFAULT_LOG_LIMIT)
    if (
        not sources
        or len(sources) > 12
        or any(
            not re.fullmatch(r"(?:app:)?[a-zA-Z0-9_][a-zA-Z0-9_.-]*", name)
            for name in sources
        )
    ):
        raise_tool_error(
            create_error_response(
                ErrorCode.VALIDATION_INVALID_PARAMETER,
                "Supply one to twelve journal source names.",
            )
        )
    args = {
        "sources": sources,
        "start_time": start,
        "end_time": end,
        "raw": raw,
        "limit": effective,
        "offset": offset,
        "search": search or "",
    }
    reply = await ask_component(client, "log_time_window", **args)
    if reply is None or reply.get("unavailable"):
        reply = await _proxy_window(
            client, sources, args, reply.get("read_lines") if reply else None
        )
    if "error" in reply:
        if status := reply.get("status"):
            exception_to_structured_error(
                HomeAssistantAPIError(reply["error"], status),
                context={"sources": sources, "status": status},
            )
        raise_tool_error(
            create_error_response(ErrorCode.SERVICE_CALL_FAILED, reply["error"])
        )
    if raw and reply["has_more"]:
        reply.setdefault("next_offset", offset + effective)
    if order == "newest":
        reply.setdefault("warnings", []).append(
            "Exact windows are served oldest first."
        )
    return {
        "success": True,
        "source": source if raw else "timeline",
        "sources": sources,
        "start_time": start,
        "end_time": end,
        **reply,
    }


async def component_journal(
    client: Any, source: str, **params: Any
) -> dict[str, Any] | None:
    result = await ask_component(client, "supervisor_log", source=source, **params)
    if result is None or result.get("unavailable"):
        return None
    if "error" in result:
        raise HomeAssistantAPIError(result["error"], result.get("status"), result)
    return result


async def component_journal_page(
    client: Any, source: str, **params: Any
) -> JournaldPage | None:
    result = await component_journal(client, source, **params)
    if result is None:
        return None
    return JournaldPage(result["text"], result.get("first_cursor"))


async def _proxy_selection(
    client: Any, source: str, args: dict[str, Any]
) -> dict[str, Any]:
    if listing := args.get("listing"):
        reply = await client.send_websocket_message(
            {
                "type": "supervisor/api",
                "method": "get",
                "endpoint": f"/host/logs/{listing}",
            }
        )
        if not reply.get("success"):
            raise HomeAssistantAPIError(str(reply.get("error")))
        result = reply["result"]
        payload = result.get("data", result) if isinstance(result, dict) else None
        if not isinstance(payload, dict) or listing not in payload:
            raise HomeAssistantAPIError(
                "Supervisor returned an unexpected journal listing."
            )
        return payload
    if args.get("identifier") or (args.get("latest") and source != "core"):
        raise HomeAssistantAPIError(
            "This journal selector requires the HA-MCP component on Core 2026.12 or later."
        )
    service = "addons/" + source[4:] if source.startswith("app:") else source
    path = f"/hassio/{service}/logs"
    if "boot" in args:
        path += f"/boots/{args['boot']}"
    if args.get("latest"):
        path += "/latest"
    response = await client._raw_request(
        "GET",
        path,
        headers={"Accept": "text/plain"},
        params={"no_colors": "true", "lines": str(args["lines"])},
    )
    return {"text": response.text}


async def selected_journal(
    client: Any,
    source: str,
    slug: str | None,
    *,
    boot: int | str | None,
    latest: bool,
    identifier: str | None,
    listing: str | None,
    limit: int | None,
    search: str | None,
    order: str | None,
) -> dict[str, Any]:
    """Read a selected boot, service start, host identifier, or discovery list."""
    if not listing:
        _validate_log_slug(source, slug)
    journal = (
        "core"
        if source == "error_log"
        else f"app:{slug}"
        if source == "supervisor"
        else slug or "host"
    )
    if source not in {"system_service", "error_log", "supervisor"} or (
        identifier and journal != "host"
    ):
        raise_tool_error(
            create_error_response(
                ErrorCode.VALIDATION_INVALID_PARAMETER,
                "Choose a journal source. Host identifiers require source='system_service', slug='host'.",
            )
        )
    if (boot is not None and latest) or (
        boot is not None and not re.fullmatch(r"-?\d+|[a-fA-F0-9]{32}", str(boot))
    ):
        raise_tool_error(
            create_error_response(
                ErrorCode.VALIDATION_INVALID_PARAMETER,
                "Use a boot offset or a boot ID, or select latest on its own.",
            )
        )
    effective = _coerce_limit(limit, default=DEFAULT_LOG_LIMIT)
    args = {
        k: v
        for k, v in {"boot": boot, "identifier": identifier, "listing": listing}.items()
        if v is not None
    }
    args.update(
        latest=latest, lines=SUPERVISOR_SEARCH_WINDOW_LINES if search else effective
    )
    try:
        reply = await component_journal(client, journal, **args)
        result: dict[str, Any] = {"success": True, "source": source, "slug": slug}
        if reply is None:
            result["warnings"] = [
                await journal_fallback_warning(
                    client,
                    "the journal selection uses Core's Supervisor proxy",
                )
            ]
            reply = await _proxy_selection(client, journal, args)
    except HomeAssistantAPIError as exc:
        exception_to_structured_error(
            exc, context={"source": source, "slug": slug, "status": exc.status_code}
        )
        raise
    if listing:
        return {**result, listing: reply[listing]}
    lines = reply["text"].splitlines()
    matches = [
        line for line in lines if not search or search.casefold() in line.casefold()
    ]
    selected = matches[-effective:]
    if order != "oldest":
        selected.reverse()
    return {
        **result,
        "log": "\n".join(selected),
        "total_lines": len(matches),
        "returned_lines": len(selected),
        "limit": effective,
        "order": order or "newest",
    }
