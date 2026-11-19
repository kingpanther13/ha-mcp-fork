"""Record pinned Core's answers alongside the public tools on the stack head."""
import pytest

from ha_mcp.tools.tools_blueprints import BlueprintTools
from ha_mcp.tools.tools_calendar import CalendarTools

from ._blueprint_actions_core import MOTION, set_up_core
from ._calendars import NO_EVENTS, calendar_config_dir, remote_calendar
from ._tool_errors import rejected_call_body

hass_config_dir = calendar_config_dir


async def test_import_collision(hass, core_client, aioclient_mock):
    path = "example.com/motion.yaml"
    url = "https://example.com/motion.yaml"
    aioclient_mock.get(url, text=MOTION)
    await set_up_core(hass, {f"automation/{path}": "blueprint: [unclosed\n"})
    raw = await core_client.send_websocket_message(
        {"type": "blueprint/save", "domain": "automation", "path": path, "yaml": MOTION}
    )
    tool = await rejected_call_body(BlueprintTools(core_client).ha_manage_blueprints(
        action="import", url=url
    ))
    print("BLUEPRINT raw=", raw, "tool=", tool["error"], flush=True)
    assert raw["error_code"] == "already_exists"
    assert tool["error"]["code"] == "RESOURCE_ALREADY_EXISTS"


@pytest.mark.parametrize("entity_id", ["calendar.read_only", "calendar.missing", "calendar.bad id"])
async def test_calendar_core_and_tool(hass, core_client, entity_id):
    await remote_calendar(hass, "Read Only", NO_EVENTS)
    params = {"entity_id": entity_id, "uid": "probe-3023"}
    raw = await core_client.send_websocket_message({"type": "calendar/event/delete", **params})
    tool = await rejected_call_body(CalendarTools(core_client).ha_config_remove_calendar_event(**params))
    print("CALENDAR", entity_id, "raw=", raw, "tool=", tool["error"], flush=True)
    assert not raw["success"] and raw["error_code"]
    assert tool["error"]["code"]
