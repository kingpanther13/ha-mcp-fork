"""Stdio MCP stub that logs raw tools/call JSON. SCHEMA=original mirrors ha-mcp's open `config` object;
SCHEMA=hinted additionally declares the list fields as arrays."""
import json, os, sys

LOG = os.environ.get("WIRE_LOG", "wire.log")
HINTED = os.environ.get("SCHEMA") == "hinted"
ERRMODE = os.environ.get("ERRMODE", "none")  # none | ha | explicit
LIST_KEYS = ("triggers", "trigger", "conditions", "condition", "actions", "action", "sequence")
FIRST_ERR = {"triggers": "required key not provided at 'triggers[0].trigger'", "trigger": "required key not provided at 'triggers[0].trigger'",
             "conditions": "Unexpected value for condition: 'None'. Expected a condition, a list of conditions or a valid template at 'conditions[0]'",
             "condition": "Unexpected value for condition: 'None'. Expected a condition, a list of conditions or a valid template at 'conditions[0]'",
             "actions": "Unable to determine action at 'actions[0]'", "action": "Unable to determine action at 'actions[0]'",
             "sequence": "Unable to determine action at 'sequence[0]'"}


def wrapped_keys(cfg):
    if not isinstance(cfg, dict):
        return []
    return [k for k in LIST_KEYS if isinstance(cfg.get(k), dict) and list(cfg[k]) == ["item"]]


def error_for(name, keys):
    if ERRMODE == "ha":
        prefix = "Invalid automation configuration: " if name == "ha_config_set_automation" else ""
        return {"success": False, "error": {"code": "VALIDATION_FAILED", "message": f"{prefix}API error: 400 - Message malformed: {FIRST_ERR[keys[0]]}",
                "suggestion": "Check automation configuration format"}}
    names = ", ".join(f"'{k}'" for k in keys)
    return {"success": False, "error": {"code": "VALIDATION_INVALID_PARAMETER",
            "message": f'config {names} was received as {{"item": ...}} instead of a JSON array; nothing was sent to Home Assistant',
            "suggestions": [f'Send {names} as a JSON array, e.g. "{keys[0]}": [{{...}}, {{...}}]',
                            'If it still arrives as {"item": ...}, pass the whole config as one JSON-encoded string',
                            "If neither works, check the model or MCP client in use"]}}
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
        cfg = msg["params"].get("arguments", {}).get("config")
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except ValueError:
                pass
        keys = wrapped_keys(cfg)
        if keys and ERRMODE != "none":
            body = error_for(msg["params"]["name"], keys)
            send({"jsonrpc": "2.0", "id": mid, "result": {"isError": True, "content": [{"type": "text", "text": json.dumps(body)}]}})
        else:
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": '{"success": true}'}]}})
    elif method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": msg["params"].get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}}, "serverInfo": {"name": "stub-ha", "version": "0"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
