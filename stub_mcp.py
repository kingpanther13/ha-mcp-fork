"""Stdio MCP stub that logs raw tools/call JSON. SCHEMA=original mirrors ha-mcp's open `config` object;
SCHEMA=hinted additionally declares the list fields as arrays."""
import json, os, sys

LOG = os.environ.get("WIRE_LOG", "wire.log")
HINTED = os.environ.get("SCHEMA") == "hinted"
ARR = {"type": "array", "items": {"type": "object"}}


def config_schema(desc, list_keys):
    obj = {"additionalProperties": True, "type": "object"}
    if HINTED:
        obj["properties"] = {k: ARR for k in list_keys}
    return {"anyOf": [obj, {"type": "null"}], "default": None, "description": desc}


TOOLS = [
    {
        "name": "ha_config_set_automation",
        "description": "Create or update a Home Assistant automation. config needs alias, triggers (list) and actions (list).",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "config": config_schema("Complete automation configuration.", ["triggers", "conditions", "actions"]),
                "identifier": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
            },
        },
    },
    {
        "name": "ha_config_set_script",
        "description": "Create or update a Home Assistant script. config needs alias and sequence (list of actions).",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "script_id": {"type": "string"},
                "config": config_schema("Script configuration dictionary.", ["sequence"]),
            },
            "required": ["script_id"],
        },
    },
]


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    msg = json.loads(raw)
    method, mid = msg.get("method"), msg.get("id")
    if method == "tools/call":
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(raw + "\n")
        send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": '{"success": true}'}]}})
    elif method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": msg["params"].get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}}, "serverInfo": {"name": "stub-ha", "version": "0"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
