"""Issue #2479: simple helpers through `config`, component writes, Core schemas.

Runs against the live devenv HA: every simple type is created and updated with
its fields in `config`, read back, then removed. Exit status 1 on any failure.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import ha_mcp.config
from ha_mcp._vendor.fastmcp import Client
from ha_mcp._vendor.fastmcp.exceptions import ToolError
from ha_mcp.client import HomeAssistantClient
from ha_mcp.server import HomeAssistantSmartMCPServer
from ha_mcp.tools.component_api import get_component_caps

HA = HomeAssistantClient(base_url=os.environ["HA_URL"], token=os.environ["HA_TOKEN"])
FAILURES: list[str] = []

# helper_type -> (create config, update config, key to read back after update)
CASES: dict[str, tuple[dict[str, Any], dict[str, Any], str]] = {
    "input_number": ({"min": 0, "max": 10, "step": 2}, {"max": 20}, "max"),
    "input_text": ({"max": 20, "mode": "password"}, {"max": 30}, "max"),
    "input_select": ({"options": ["a", "b"]}, {"options": ["a", "b", "c"]}, "options"),
    "input_boolean": ({"initial": True}, {"initial": False}, "initial"),
    "input_datetime": ({"has_date": True}, {"has_time": True}, "has_time"),
    "input_button": ({}, {"icon": "mdi:bell"}, "icon"),
    "counter": ({"minimum": 0, "maximum": 5}, {"maximum": 9}, "maximum"),
    "timer": ({"duration": "0:01:00"}, {"duration": "0:02:00"}, "duration"),
    "schedule": (
        {"monday": [{"from": "07:00", "to": "08:00"}]},
        {"tuesday": [{"from": "09:00", "to": "10:00"}]},
        "tuesday",
    ),
    "zone": ({"latitude": 1.5, "longitude": 2.5}, {"radius": 250}, "radius"),
    "person": ({}, {"picture": "/local/p.png"}, "picture"),
    "tag": ({"description": "first"}, {"description": "second"}, "description"),
}


def check(label: str, ok: bool, detail: Any = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'} {label} {detail if not ok else ''}".rstrip())
    if not ok:
        FAILURES.append(label)


def payload(result: Any) -> dict[str, Any]:
    if getattr(result, "structured_content", None):
        return result.structured_content  # type: ignore[no-any-return]
    return json.loads(result.content[0].text)  # type: ignore[no-any-return]


async def call(client: Client, tool: str, **args: Any) -> dict[str, Any]:
    return payload(await client.call_tool(tool, args))


async def catalog(client: Client) -> None:
    tool = next(t for t in await client.list_tools() if t.name == "ha_config_set_helper")
    props = tool.input_schema["properties"]
    check("catalog: flat fields hidden", "min_value" not in props, list(props))
    check("catalog: wait dropped with component", "wait" not in props, list(props))
    desc = props["config"]["description"]
    check("catalog: Core keys rendered", "input_number: min (" in desc, desc[:600])


async def lifecycle(client: Client, helper_type: str) -> None:
    create, update, key = CASES[helper_type]
    name = f"Devenv {helper_type.replace('_', ' ')}"
    try:
        created = await call(client, "ha_config_set_helper", helper_type=helper_type,
                             name=name, action="create", config=create)  # fmt: skip
    except ToolError as err:
        check(f"{helper_type}: create", False, err)
        return
    data, entity_id = created.get("data", {}), created.get("entity_id")
    check(f"{helper_type}: create", created.get("success") is True, created)
    for field, value in create.items():
        stored = data.get(field)
        check(f"{helper_type}: create stored {field}", stored is not None, data)
    if helper_type != "tag":
        state = await HA.get_entity_state(entity_id)
        check(f"{helper_type}: entity exists at once", bool(state), entity_id)
    helper_id = data.get("id") if helper_type == "tag" else entity_id
    try:
        updated = await call(client, "ha_config_set_helper", helper_type=helper_type,
                             helper_id=helper_id, action="update", config=update)  # fmt: skip
        got = updated.get("data", {}).get(key)
        want = update[key]
        check(f"{helper_type}: update {key}", got not in (None, []), (got, want))
        # Fields not re-passed survive the update.
        for field in create:
            if field not in update:
                check(f"{helper_type}: update kept {field}",
                      updated["data"].get(field) is not None, updated["data"])  # fmt: skip
    except ToolError as err:
        check(f"{helper_type}: update", False, err)
    finally:
        try:
            await call(client, "ha_remove_helpers_integrations", target=helper_id,
                       helper_type=helper_type, confirm=True)  # fmt: skip
        except ToolError as err:
            check(f"{helper_type}: remove", False, err)


async def core_schema_errors(client: Client) -> None:
    try:
        await call(client, "ha_config_set_helper", helper_type="input_number",
                   name="Devenv bad", action="create", config={"step": 1})  # fmt: skip
        check("error: missing min/max rejected", False, "created")
    except ToolError as err:
        text = str(err)
        check("error: missing min/max rejected", "data_schema" in text, text[:400])
        check("error: data_schema is Core's (min, not min_value)",
              '"min"' in text and "pattern" not in text, text[:800])  # fmt: skip
    try:
        await call(client, "ha_config_set_helper", helper_type="input_number",
                   name="Devenv bad2", action="create", config={"minn": 1})  # fmt: skip
        check("error: unknown config key rejected", False, "created")
    except ToolError as err:
        check("error: unknown config key rejected", "minn" in str(err), str(err)[:300])


async def main() -> int:
    ha_mcp.config._settings = None
    caps = await get_component_caps(HA)
    names = sorted(caps.capabilities) if caps else []
    check("component advertises helper commands",
          {"helper_schemas", "helper_item", "helper_write"} <= set(names), names)  # fmt: skip
    server = HomeAssistantSmartMCPServer(client=HA)
    async with Client(server.mcp) as client:
        await catalog(client)
        for helper_type in CASES:
            await lifecycle(client, helper_type)
        await core_schema_errors(client)
    print(f"\n{len(FAILURES)} failure(s): {FAILURES}" if FAILURES else "\nall passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
