"""
Device information tools for Home Assistant.

This module provides read-only device lookup and listing functionality.
For device management (rename, update, remove), see tools_registry.py.
"""

import logging
from typing import Annotated, Any

from pydantic import Field

from .helpers import log_tool_usage

logger = logging.getLogger(__name__)


def register_device_info_tools(mcp: Any, client: Any, **kwargs: Any) -> None:
    """Register read-only device information tools."""

    @mcp.tool(
        annotations={
            "idempotentHint": True,
            "readOnlyHint": True,
            "tags": ["system", "zigbee"],
            "title": "Get Device",
        }
    )
    @log_tool_usage
    async def ha_get_device(
        device_id: Annotated[
            str | None,
            Field(
                description="Device ID to retrieve details for. If omitted, lists devices.",
                default=None,
            ),
        ] = None,
        entity_id: Annotated[
            str | None,
            Field(
                description="Entity ID to find the associated device for (e.g., 'light.living_room')",
                default=None,
            ),
        ] = None,
        integration: Annotated[
            str | None,
            Field(
                description="Filter devices by integration: 'zha', 'zigbee2mqtt', 'mqtt', 'hue', etc.",
                default=None,
            ),
        ] = None,
        area_id: Annotated[
            str | None,
            Field(
                description="Filter devices by area ID (e.g., 'living_room')",
                default=None,
            ),
        ] = None,
        manufacturer: Annotated[
            str | None,
            Field(
                description="Filter devices by manufacturer name (e.g., 'Philips')",
                default=None,
            ),
        ] = None,
    ) -> dict[str, Any]:
        """
        Get device information - list all devices or get details for a specific one.

        Without device_id/entity_id: Lists all devices with optional filters.
        With device_id or entity_id: Returns detailed info for that specific device.

        **List all devices:**
        - All devices: ha_get_device()
        - By area: ha_get_device(area_id="living_room")
        - By manufacturer: ha_get_device(manufacturer="Philips")
        - By integration: ha_get_device(integration="zigbee2mqtt")
        - Combined filters: ha_get_device(integration="zha", area_id="kitchen")

        **Single device lookup:**
        - By device_id: ha_get_device(device_id="abc123")
        - By entity_id: ha_get_device(entity_id="light.living_room")

        **Zigbee automation tips:**
        - ZHA triggers: Use `ieee_address` for zha_event triggers
        - Z2M triggers: Use `friendly_name` for MQTT topics (zigbee2mqtt/{friendly_name}/action)

        **Returns (list mode):**
        - List of devices with device_id, name, manufacturer, model, area_id

        **Returns (single device):**
        - Full device details including integration_type, ieee_address, entities
        """
        try:
            # Get device registry
            list_message: dict[str, Any] = {"type": "config/device_registry/list"}
            list_result = await client.send_websocket_message(list_message)

            if not list_result.get("success"):
                return {
                    "success": False,
                    "error": f"Failed to access device registry: {list_result.get('error', 'Unknown error')}",
                }

            all_devices = list_result.get("result", [])

            # Get entity registry
            entity_message: dict[str, Any] = {"type": "config/entity_registry/list"}
            entity_result = await client.send_websocket_message(entity_message)
            all_entities = (
                entity_result.get("result", []) if entity_result.get("success") else []
            )

            # Build entity -> device_id map
            entity_to_device: dict[str, str] = {}
            device_to_entities: dict[str, list[dict[str, Any]]] = {}
            for e in all_entities:
                eid = e.get("entity_id")
                did = e.get("device_id")
                if eid and did:
                    entity_to_device[eid] = did
                    if did not in device_to_entities:
                        device_to_entities[did] = []
                    device_to_entities[did].append(
                        {
                            "entity_id": eid,
                            "name": e.get("name") or e.get("original_name"),
                            "platform": e.get("platform"),
                        }
                    )

            # If entity_id provided, find the device_id
            if entity_id and not device_id:
                device_id = entity_to_device.get(entity_id)
                if not device_id:
                    return {
                        "success": False,
                        "error": f"Entity '{entity_id}' not found or has no associated device",
                        "suggestion": "Use ha_search_entities() to find valid entity IDs",
                    }

            # Helper function to extract integration info from a device
            def get_device_info(device: dict[str, Any]) -> dict[str, Any]:
                identifiers = device.get("identifiers", [])
                connections = device.get("connections", [])

                # Determine integration type and extract IEEE address
                integration_sources = []
                ieee_address = None
                friendly_name = device.get("name_by_user") or device.get("name")
                is_z2m = False

                for identifier in identifiers:
                    if isinstance(identifier, (list, tuple)) and len(identifier) >= 2:
                        domain = identifier[0]
                        value = str(identifier[1])
                        if domain not in integration_sources:
                            integration_sources.append(domain)

                        # ZHA: identifier is ["zha", "IEEE_ADDRESS"]
                        if domain == "zha":
                            ieee_address = value

                        # Z2M: identifier is ["mqtt", "zigbee2mqtt_0xIEEE"]
                        if domain == "mqtt" and "zigbee2mqtt" in value.lower():
                            is_z2m = True
                            # Extract IEEE from "zigbee2mqtt_0x..." or "zigbee2mqtt_bridge_0x..."
                            if "_0x" in value:
                                ieee_address = "0x" + value.split("_0x")[-1]

                # Also check connections for IEEE
                for connection in connections:
                    if isinstance(connection, (list, tuple)) and len(connection) >= 2:
                        if connection[0] == "ieee" and not ieee_address:
                            ieee_address = connection[1]

                # Determine primary integration type
                if "zha" in integration_sources:
                    integration_type = "zha"
                elif is_z2m:
                    integration_type = "zigbee2mqtt"
                elif "mqtt" in integration_sources:
                    integration_type = "mqtt"
                elif integration_sources:
                    integration_type = integration_sources[0]
                else:
                    integration_type = "unknown"

                device_info: dict[str, Any] = {
                    "device_id": device.get("id"),
                    "name": friendly_name,
                    "manufacturer": device.get("manufacturer"),
                    "model": device.get("model"),
                    "sw_version": device.get("sw_version"),
                    "area_id": device.get("area_id"),
                    "integration_type": integration_type,
                    "integration_sources": integration_sources,
                    "via_device_id": device.get("via_device_id"),
                }

                # Add Zigbee-specific info
                if ieee_address:
                    device_info["ieee_address"] = ieee_address

                if integration_type == "zigbee2mqtt":
                    device_info["friendly_name"] = friendly_name
                    device_info["mqtt_topic_hint"] = f"zigbee2mqtt/{friendly_name}/..."

                if integration_type == "zha" and ieee_address:
                    device_info["zha_trigger_hint"] = (
                        f"Use ieee '{ieee_address}' for zha_event triggers"
                    )

                return device_info

            # Single device lookup mode
            if device_id:
                device = next(
                    (d for d in all_devices if d.get("id") == device_id), None
                )
                if not device:
                    return {
                        "success": False,
                        "error": f"Device not found: {device_id}",
                        "suggestion": "Use ha_get_device() to find valid device IDs",
                    }

                device_info = get_device_info(device)
                device_info["entities"] = device_to_entities.get(device_id, [])

                # Add extra fields for single lookup
                device_info["name_by_user"] = device.get("name_by_user")
                device_info["default_name"] = device.get("name")
                device_info["hw_version"] = device.get("hw_version")
                device_info["serial_number"] = device.get("serial_number")
                device_info["disabled_by"] = device.get("disabled_by")
                device_info["labels"] = device.get("labels", [])
                device_info["config_entries"] = device.get("config_entries", [])
                device_info["connections"] = device.get("connections", [])
                device_info["identifiers"] = device.get("identifiers", [])

                entities = device_info.get("entities", [])
                return {
                    "success": True,
                    "device": device_info,
                    "entities": entities,  # Also at top level for backward compatibility
                    "entity_count": len(entities),
                    "queried_by": "entity_id" if entity_id else "device_id",
                    "queried_entity_id": entity_id,
                }

            # List mode - filter devices by any combination of filters
            matched_devices = []
            integration_lower = integration.lower() if integration else None
            manufacturer_lower = manufacturer.lower() if manufacturer else None

            for device in all_devices:
                # Apply area filter
                if area_id and device.get("area_id") != area_id:
                    continue

                # Apply manufacturer filter
                if manufacturer_lower:
                    device_manufacturer = (device.get("manufacturer") or "").lower()
                    if manufacturer_lower not in device_manufacturer:
                        continue

                device_info = get_device_info(device)

                # Apply integration filter if specified
                if integration_lower:
                    # Match integration
                    if (
                        integration_lower == "zigbee2mqtt"
                        and device_info["integration_type"] != "zigbee2mqtt"
                    ) or (
                        integration_lower == "zha"
                        and device_info["integration_type"] != "zha"
                    ) or (
                        integration_lower not in ["zigbee2mqtt", "zha"]
                        and integration_lower not in device_info.get("integration_sources", [])
                    ):
                        continue

                device_info["entities"] = device_to_entities.get(
                    device.get("id"), []
                )
                matched_devices.append(device_info)

            # Build result
            result: dict[str, Any] = {
                "success": True,
                "count": len(matched_devices),
                "total_devices": len(all_devices),
                "devices": matched_devices,
            }

            # Add filter info
            filters_applied = []
            if integration:
                result["integration_filter"] = integration
                filters_applied.append(f"integration={integration}")
            if area_id:
                result["area_filter"] = area_id
                filters_applied.append(f"area_id={area_id}")
            if manufacturer:
                result["manufacturer_filter"] = manufacturer
                filters_applied.append(f"manufacturer={manufacturer}")

            if filters_applied:
                result["filters"] = filters_applied

            # Find bridge device for Z2M
            if integration_lower == "zigbee2mqtt":
                bridge_info = None
                for d in matched_devices:
                    if (
                        d.get("via_device_id") is None
                        and "bridge" in (d.get("name") or "").lower()
                    ):
                        bridge_info = {
                            "device_id": d.get("device_id"),
                            "name": d.get("name"),
                            "ieee_address": d.get("ieee_address"),
                        }
                        break
                if bridge_info:
                    result["bridge"] = bridge_info
                result["usage_hint"] = (
                    "Use 'friendly_name' for MQTT topics: zigbee2mqtt/{friendly_name}/action"
                )
            elif integration_lower == "zha":
                result["usage_hint"] = (
                    "Use 'ieee_address' for zha_event triggers in automations"
                )

            return result

        except Exception as e:
            logger.error(f"Error getting device: {e}")
            return {
                "success": False,
                "error": f"Failed to get device: {str(e)}",
            }
