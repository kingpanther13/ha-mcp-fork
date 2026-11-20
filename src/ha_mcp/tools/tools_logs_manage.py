"""Home Assistant's own write operations on the recorder, its statistics and the logs."""

import asyncio
import logging
from typing import Annotated, Any, Literal, NoReturn

from pydantic import Field

from ha_mcp._vendor.fastmcp.exceptions import ToolError
from ha_mcp._vendor.fastmcp.tools import tool

from ..errors import ErrorCode, create_error_response
from .coercion import JSON_STRING_COERCION
from .core_contract import command_payload, core_contract
from .helpers import (
    core_error_message,
    exception_to_structured_error,
    extract_tool_error_message,
    log_tool_usage,
    raise_tool_error,
    register_tool_methods,
)
from .response_helpers import build_pagination_metadata
from .tool_hints import write_hints
from .tools_services import _fetch_catalog_and_translations, _process_services

logger = logging.getLogger(__name__)

# The tool's permission boundary: Core's own names, a dot for a service and a
# slash for a WebSocket command.
LogsAction = Literal[
    "recorder.purge",
    "recorder.purge_entities",
    "recorder.enable",
    "recorder.disable",
    "system_log.clear",
    "system_log.write",
    "logger.set_level",
    "logger.set_default_level",
    "logbook.log",
    "recorder/list_statistic_ids",
    "recorder/validate_statistics",
    "recorder/update_statistics_issues",
    "recorder/clear_statistics",
    "recorder/update_statistics_metadata",
    "recorder/change_statistics_unit",
    "recorder/adjust_sum_statistics",
    "recorder/import_statistics",
]

# How long a recorder action waits for Core's recorder queue to empty.
RECORDER_DRAIN_SECONDS = 30.0
_DRAIN_POLL_SECONDS = 0.2
_PAGED_ACTION = "recorder/list_statistic_ids"


def _raise_core_refusal(action: str, reply: dict[str, Any]) -> NoReturn:
    code = reply.get("error_code")
    raise_tool_error(
        create_error_response(
            ErrorCode.VALIDATION_INVALID_PARAMETER
            if str(code).startswith("invalid_")
            else ErrorCode.SERVICE_CALL_FAILED,
            f"{action} failed: {core_error_message(reply.get('error', 'Unknown error'))}",
            context={"action": action, "ha_error_code": code},
            suggestions=[
                f"Call ha_manage_logs(action='{action}', describe=True) for the "
                "fields Home Assistant accepts"
            ],
        )
    )


def _statistic_ids(data: dict[str, Any]) -> list[str]:
    """The statistic ids a statistics command names, as Core accepted them."""
    metadata = data.get("metadata")
    named = (
        data.get("statistic_id"),
        metadata.get("statistic_id") if isinstance(metadata, dict) else None,
    )
    return [
        *(data.get("statistic_ids") or []),
        *(i for i in named if isinstance(i, str)),
    ]


class LogsManageTools:
    """Core's recorder, statistics and log write operations."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def _send(self, action: str, data: dict[str, Any]) -> Any:
        if "/" in action:
            message = command_payload(action, data)
        else:
            command_payload("call_service", data)
            domain, service = action.split(".", 1)
            message = {
                "type": "call_service",
                "domain": domain,
                "service": service,
                "service_data": data,
            }
        reply = await self._client.send_websocket_message(message)
        if not reply.get("success"):
            _raise_core_refusal(action, reply)
        return reply.get("result")

    async def _describe(self, action: str, warnings: list[str]) -> dict[str, Any]:
        if "/" in action:
            try:
                description = dict(await core_contract(self._client, action))
            except ToolError as err:
                # An older component's bridge refuses commands added since.
                description = {
                    "status": "unavailable",
                    "reason": f"The component could not describe {action}; "
                    "update the HA-MCP component. Core still validates the "
                    f"call itself. ({extract_tool_error_message(err)})",
                }
            warnings.extend(description.pop("warnings", []))
            if description.get("status") == "unavailable":
                warnings.append(description["reason"])
            return description
        domain = action.split(".", 1)[0]
        services, translations = await _fetch_catalog_and_translations(
            self._client, domain, warnings
        )
        catalog = _process_services(
            services,
            translations,
            domain_filter=domain,
            query_filter=action,
            detail_level="full",
            warnings=warnings,
        )
        entry: dict[str, Any] | None = catalog["services"].get(action)
        if entry is None:
            raise_tool_error(
                create_error_response(
                    ErrorCode.SERVICE_NOT_FOUND,
                    f"Home Assistant has no {action} service; its integration "
                    "is not loaded",
                    context={"action": action},
                )
            )
        return entry

    async def _recorder_drain_warning(self) -> str | None:
        """Wait until Core's recorder reports an empty queue; a warning when it
        does not within the bound or cannot be asked."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + RECORDER_DRAIN_SECONDS
        while True:
            reply = await self._client.send_websocket_message({"type": "recorder/info"})
            if not reply.get("success"):
                return (
                    "Could not read recorder/info to wait for the recorder: "
                    f"{core_error_message(reply.get('error', 'Unknown error'))}"
                )
            if reply["result"]["backlog"] == 0:
                return None
            if loop.time() >= deadline:
                return (
                    "The recorder was still working through its queue "
                    f"({reply['result']['backlog']} items) after "
                    f"{RECORDER_DRAIN_SECONDS:g} s; the operation may not have "
                    "finished yet"
                )
            await asyncio.sleep(_DRAIN_POLL_SECONDS)

    async def _statistics_metadata(
        self, statistic_ids: list[str], warnings: list[str]
    ) -> list[dict[str, Any]] | None:
        reply = await self._client.send_websocket_message(
            {"type": "recorder/get_statistics_metadata", "statistic_ids": statistic_ids}
        )
        if reply.get("success"):
            rows: list[dict[str, Any]] = reply["result"]
            return rows
        warnings.append(
            "Could not read the statistics metadata back: "
            f"{core_error_message(reply.get('error', 'Unknown error'))}"
        )
        return None

    async def _run(
        self, action: str, data: dict[str, Any], limit: int, offset: int
    ) -> dict[str, Any]:
        result = await self._send(action, data)
        response: dict[str, Any] = {"success": True, "action": action}
        warnings: list[str] = []
        if action.startswith("recorder"):
            if warning := await self._recorder_drain_warning():
                warnings.append(warning)
        if action.startswith("recorder/") and (ids := _statistic_ids(data)):
            metadata = await self._statistics_metadata(ids, warnings)
            if metadata is not None:
                response["statistics_metadata"] = metadata
        if action == _PAGED_ACTION and isinstance(result, list):
            page = result[offset : offset + limit]
            response.update(
                build_pagination_metadata(len(result), offset, limit, len(page))
            )
            result = page
        response["result"] = result
        if warnings:
            response["warnings"] = warnings
        return response

    @tool(
        name="ha_manage_logs",
        tags={"History & Statistics"},
        annotations=write_hints(
            "Manage Recorder, Statistics and Logs",
            destructive=True,
            idempotent=False,
            open_world=False,
        ),
    )
    @log_tool_usage
    async def ha_manage_logs(
        self,
        action: Annotated[
            LogsAction,
            Field(
                description=(
                    "The Home Assistant operation, named as Core names it: a dot "
                    "is a service, a slash a WebSocket command (Developer Tools > "
                    "Statistics)."
                )
            ),
        ],
        data: Annotated[
            dict[str, Any] | None,
            JSON_STRING_COERCION,
            Field(
                description=(
                    "The operation's fields, sent to Core unchanged: a service's "
                    "service data, or the fields of the WebSocket command. 'id' "
                    "and 'type' are refused."
                )
            ),
        ] = None,
        describe: Annotated[
            bool,
            Field(
                description=(
                    "Send nothing and return Core's own description of the "
                    "action's fields instead."
                )
            ),
        ] = False,
        limit: Annotated[
            int,
            Field(
                ge=1,
                le=1000,
                description="Page size for 'recorder/list_statistic_ids' only.",
            ),
        ] = 100,
        offset: Annotated[
            int,
            Field(
                ge=0, description="Page start for 'recorder/list_statistic_ids' only."
            ),
        ] = 0,
    ) -> dict[str, Any]:
        """Manage Home Assistant's recorder, its long-term statistics and its logs.

        Not for reading recorded states or statistics values (ha_get_history),
        reading log entries (ha_get_logs), or turning on one integration's debug
        logging (ha_set_integration with log_level).

        Each call runs one Core operation; Core validates its fields and its
        refusal comes back with its own error code. Call
        with describe=True first when the fields are not known. A recorder
        operation returns once Core's recorder queue is empty, and a statistics
        command that names statistic ids returns Core's metadata for them as
        `statistics_metadata` (empty for a statistic that no longer exists).
        Purges and statistics clears cannot be undone.

        EXAMPLES:
        - ha_manage_logs(action="recorder/adjust_sum_statistics", describe=True)
        - ha_manage_logs(action="recorder/clear_statistics", data={"statistic_ids": ["sensor.old_meter"]})
        - ha_manage_logs(action="system_log.write", data={"message": "checkpoint"})
        """
        try:
            if describe:
                warnings: list[str] = []
                response: dict[str, Any] = {
                    "success": True,
                    "action": action,
                    "result": await self._describe(action, warnings),
                }
                if warnings:
                    response["warnings"] = warnings
                return response
            return await self._run(action, data or {}, limit, offset)
        except ToolError:
            raise
        except Exception as e:  # noqa: BLE001
            exception_to_structured_error(e, context={"action": action})
            return {}  # unreachable: exception_to_structured_error always raises


def register_logs_manage_tools(mcp: Any, client: Any, **kwargs: Any) -> None:
    """Register the recorder, statistics and log management tool."""
    register_tool_methods(mcp, LogsManageTools(client))
