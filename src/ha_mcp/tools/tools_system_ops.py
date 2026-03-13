"""
System operation tools for Home Assistant MCP Server.

This module provides destructive system operations:
- Home Assistant restart
- Configuration reload (individual components or all)

For read-only system info (config check, health), see tools_system.py.
"""

import logging
from typing import Any

from .helpers import log_tool_usage
from .util_helpers import coerce_bool_param

logger = logging.getLogger(__name__)

# Mapping of reload targets to their service domains and services
RELOAD_TARGETS = {
    "all": None,  # Special case - reload all
    "automations": ("automation", "reload"),
    "scripts": ("script", "reload"),
    "scenes": ("scene", "reload"),
    "groups": ("group", "reload"),
    "input_booleans": ("input_boolean", "reload"),
    "input_numbers": ("input_number", "reload"),
    "input_texts": ("input_text", "reload"),
    "input_selects": ("input_select", "reload"),
    "input_datetimes": ("input_datetime", "reload"),
    "input_buttons": ("input_button", "reload"),
    "timers": ("timer", "reload"),
    "counters": ("counter", "reload"),
    "templates": ("template", "reload"),
    "persons": ("person", "reload"),
    "zones": ("zone", "reload"),
    "core": ("homeassistant", "reload_core_config"),
    "themes": ("frontend", "reload_themes"),
}


def register_system_ops_tools(mcp: Any, client: Any, **kwargs: Any) -> None:
    """Register Home Assistant system operation tools."""

    @mcp.tool(annotations={"destructiveHint": True, "tags": ["system"], "title": "Restart Home Assistant"})
    @log_tool_usage
    async def ha_restart(
        confirm: bool | str = False,
    ) -> dict[str, Any]:
        """
        Restart Home Assistant.

        **WARNING: This will restart the entire Home Assistant instance!**
        All automations will be temporarily unavailable during restart.
        The restart typically takes 1-5 minutes depending on your setup.

        **Parameters:**
        - confirm: Must be set to True to confirm the restart. This is a safety
                   measure to prevent accidental restarts.

        **Best Practices:**
        1. Always run ha_check_config() first to ensure configuration is valid
        2. Notify users before restarting (if applicable)
        3. Schedule restarts during low-activity periods

        **Example Usage:**
        ```python
        # Always check config first
        config = ha_check_config()
        if config["result"] == "valid":
            # Restart with confirmation
            result = ha_restart(confirm=True)
        ```

        **Alternative:** For configuration changes, consider using ha_reload_core()
        instead, which reloads specific components without a full restart.
        """
        # Coerce boolean parameter that may come as string from XML-style calls
        confirm_bool = coerce_bool_param(confirm, "confirm", default=False) or False

        if not confirm_bool:
            return {
                "success": False,
                "error": "Restart not confirmed",
                "message": (
                    "You must set confirm=True to restart Home Assistant. "
                    "This is a safety measure to prevent accidental restarts."
                ),
                "suggestions": [
                    "Run ha_check_config() first to validate configuration",
                    "Call ha_restart(confirm=True) to proceed with restart",
                    "Consider using ha_reload_core() for config-only changes",
                ],
            }

        restart_initiated = False
        try:
            # Check configuration first as a safety measure
            config_result = await client.check_config()
            if config_result.get("result") != "valid":
                errors = config_result.get("errors") or []
                return {
                    "success": False,
                    "error": "Configuration is invalid - restart aborted",
                    "config_errors": errors,
                    "message": (
                        "Home Assistant configuration has errors. "
                        "Fix the errors before restarting."
                    ),
                }

            # Call the restart service - mark as initiated before the call
            # as the connection may be closed before we get a response
            restart_initiated = True
            await client.call_service("homeassistant", "restart", {})

            return {
                "success": True,
                "message": (
                    "Home Assistant restart initiated. "
                    "The system will be unavailable for 1-5 minutes."
                ),
                "warning": (
                    "Connection will be lost during restart. "
                    "Wait for Home Assistant to become available again."
                ),
            }

        except Exception as e:
            error_msg = str(e)
            # Connection errors after restart initiated are expected
            # (HA closes connections during restart)
            if restart_initiated and any(
                pattern in error_msg.lower()
                for pattern in ("connect", "closed", "504")
            ):
                return {
                    "success": True,
                    "message": (
                        "Home Assistant restart initiated. "
                        "Connection was closed as expected during restart."
                    ),
                    "warning": "Wait 1-5 minutes for Home Assistant to restart.",
                }

            logger.error(f"Failed to restart Home Assistant: {e}")
            return {
                "success": False,
                "error": f"Failed to restart Home Assistant: {str(e)}",
            }

    @mcp.tool(annotations={"destructiveHint": True, "tags": ["system"], "title": "Reload Core Components"})
    @log_tool_usage
    async def ha_reload_core(
        target: str = "all",
    ) -> dict[str, Any]:
        """
        Reload Home Assistant configuration without full restart.

        This tool reloads specific configuration components, allowing changes
        to take effect without restarting the entire Home Assistant instance.
        This is much faster than a full restart.

        **Parameters:**
        - target: What to reload. Options:
          - "all": Reload all reloadable components
          - "automations": Reload automation configurations
          - "scripts": Reload script configurations
          - "scenes": Reload scene configurations
          - "groups": Reload group configurations
          - "input_booleans": Reload input_boolean helpers
          - "input_numbers": Reload input_number helpers
          - "input_texts": Reload input_text helpers
          - "input_selects": Reload input_select helpers
          - "input_datetimes": Reload input_datetime helpers
          - "input_buttons": Reload input_button helpers
          - "timers": Reload timer helpers
          - "counters": Reload counter helpers
          - "templates": Reload template sensors/entities
          - "persons": Reload person configurations
          - "zones": Reload zone configurations
          - "core": Reload core configuration (customize, packages)
          - "themes": Reload frontend themes

        **Example Usage:**
        ```python
        # Reload just automations after editing
        ha_reload_core(target="automations")

        # Reload all configurations
        ha_reload_core(target="all")

        # Reload input helpers after adding new ones
        ha_reload_core(target="input_booleans")
        ```

        **When to Use:**
        - After editing automation/script YAML files
        - After adding new input helpers via YAML
        - After modifying customize.yaml
        - After theme changes
        """
        target = target.lower().strip()

        if target not in RELOAD_TARGETS:
            return {
                "success": False,
                "error": f"Invalid reload target: {target}",
                "valid_targets": list(RELOAD_TARGETS.keys()),
                "suggestion": f"Use one of: {', '.join(RELOAD_TARGETS.keys())}",
            }

        try:
            if target == "all":
                # Reload all reloadable components
                results = []
                errors = []

                for reload_target, service_info in RELOAD_TARGETS.items():
                    if service_info is None:  # Skip "all" itself
                        continue

                    domain, service = service_info
                    try:
                        await client.call_service(domain, service, {})
                        results.append(reload_target)
                    except Exception as e:
                        # Some services might not be available in all installations
                        error_msg = str(e)
                        if "not found" not in error_msg.lower():
                            errors.append(f"{reload_target}: {error_msg}")

                return {
                    "success": True,
                    "message": f"Reloaded {len(results)} components",
                    "reloaded": results,
                    "warnings": errors if errors else None,
                }

            else:
                # Reload specific component
                service_info = RELOAD_TARGETS[target]
                if service_info is None:
                    # This shouldn't happen as we check for "all" above
                    return {
                        "success": False,
                        "error": f"Invalid target configuration for: {target}",
                    }
                domain, service = service_info
                await client.call_service(domain, service, {})

                return {
                    "success": True,
                    "message": f"Successfully reloaded {target}",
                    "target": target,
                    "service": f"{domain}.{service}",
                }

        except Exception as e:
            logger.error(f"Failed to reload {target}: {e}")
            return {
                "success": False,
                "error": f"Failed to reload {target}: {str(e)}",
                "suggestions": [
                    f"Ensure the {target} integration is loaded",
                    "Check Home Assistant logs for details",
                ],
            }
