"""
System information tools for Home Assistant MCP Server.

This module provides read-only system information tools:
- Configuration validation
- System health monitoring

For system operations (restart, reload), see tools_system_ops.py.
"""

import logging
from typing import Any

from fastmcp.exceptions import ToolError

from ..errors import ErrorCode, create_error_response
from .helpers import (
    exception_to_structured_error,
    get_connected_ws_client,
    log_tool_usage,
    raise_tool_error,
)

logger = logging.getLogger(__name__)


def register_system_tools(mcp: Any, client: Any, **kwargs: Any) -> None:
    """Register Home Assistant system information tools."""

    @mcp.tool(annotations={"idempotentHint": True, "readOnlyHint": True, "tags": ["system"], "title": "Check Configuration"})
    @log_tool_usage
    async def ha_check_config() -> dict[str, Any]:
        """
        Check Home Assistant configuration for errors.

        Validates configuration files without applying changes.
        Always run this before ha_restart() to ensure configuration is valid.
        """
        try:
            config_result = await client.check_config()

            # The API returns {"result": "valid"} or {"result": "invalid", "errors": [...]}
            is_valid = config_result.get("result") == "valid"
            errors = config_result.get("errors") or []  # Handle None case

            return {
                "success": True,
                "result": "valid" if is_valid else "invalid",
                "is_valid": is_valid,
                "errors": errors,
                "message": (
                    "Configuration is valid"
                    if is_valid
                    else f"Configuration has {len(errors)} error(s)"
                ),
            }

        except Exception as e:
            exception_to_structured_error(
                e,
                suggestions=[
                    "Ensure Home Assistant is running and accessible",
                    "Check your connection settings",
                ],
            )

    @mcp.tool(annotations={"idempotentHint": True, "readOnlyHint": True, "tags": ["system"], "title": "Get System Health"})
    @log_tool_usage
    async def ha_get_system_health() -> dict[str, Any]:
        """
        Get Home Assistant system health information.

        Returns health check results from integrations, system resources, and connectivity.
        Available information varies by installation type and loaded integrations.
        """
        ws_client = None

        try:
            # Connect to WebSocket for system_health/info
            ws_client, error = await get_connected_ws_client(
                client.base_url, client.token
            )
            if error or ws_client is None:
                raise_tool_error(error or create_error_response(
                    ErrorCode.CONNECTION_FAILED,
                    "Failed to connect to Home Assistant WebSocket",
                ))

            # system_health/info returns a result + follow-up event
            try:
                _, event_response = await ws_client.send_command_with_event(
                    "system_health/info", wait_timeout=10.0
                )
            except TimeoutError:
                raise_tool_error(create_error_response(
                    ErrorCode.SERVICE_CALL_FAILED,
                    "Timeout waiting for system health data",
                ))
            except Exception as e:
                raise_tool_error(create_error_response(
                    ErrorCode.SERVICE_CALL_FAILED,
                    str(e),
                ))

            health_info = event_response.get("event", {})

            return {
                "success": True,
                "health_info": health_info,
                "component_count": len(health_info) if isinstance(health_info, dict) else 0,
                "message": f"Retrieved health info for {len(health_info) if isinstance(health_info, dict) else 0} components",
            }

        except ToolError:
            raise
        except Exception as e:
            exception_to_structured_error(
                e,
                suggestions=[
                    "System health may not be available in all HA installations",
                    "Try ha_get_overview() for basic system information",
                ],
            )
        finally:
            if ws_client:
                try:
                    await ws_client.disconnect()
                except Exception:
                    pass
