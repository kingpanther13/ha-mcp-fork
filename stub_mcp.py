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


def _bad(v):
    return v == "" or (isinstance(v, dict) and list(v) == ["item"])


def malformed(name, cfg):
    """Mirror of the PR's checks: returns {path: shape} for flagged list fields."""
    if not isinstance(cfg, dict):
        return {}
    if name == "ha_config_set_dashboard":
        fields = {"views": cfg.get("views")}
        views = cfg.get("views")
        for i, v in enumerate(views if isinstance(views, list) else []):
            if isinstance(v, dict):
                for k in ("cards", "sections", "badges"):
                    fields[f"views[{i}].{k}"] = v.get(k)
                secs = v.get("sections")
                for j, sec in enumerate(secs if isinstance(secs, list) else []):
                    if isinstance(sec, dict):
                        fields[f"views[{i}].sections[{j}].cards"] = sec.get("cards")
    elif name == "ha_config_set_script":
        fields = {"sequence": cfg.get("sequence")}
    else:
        fields = {k: cfg.get(k) for k in ("triggers", "trigger", "conditions", "condition", "actions", "action")}
    return {k: ('{"item": ...}' if isinstance(v, dict) else '""') for k, v in fields.items() if _bad(v)}


def error_for(bad):
    names = ", ".join(f"'{k}'" for k in bad)
    shapes = "; ".join(f"'{k}' as {v}" for k, v in bad.items())
    return {"success": False, "error": {"code": "VALIDATION_INVALID_PARAMETER",
            "message": f"Received config {shapes} instead of a JSON array; nothing was sent to Home Assistant",
            "suggestions": [f'Send {names} as a JSON array, e.g. "{next(iter(bad))}": [{{...}}, {{...}}], or omit an empty one',
                            "If it still arrives malformed, pass the whole config as one JSON-encoded string",
                            "If neither works, check the model or MCP client in use"]}}


ARR = {"type": "array", "items": {"type": "object"}}


def config_schema(desc, list_keys):
    obj = {"additionalProperties": True, "type": "object"}
    if HINTED:
        obj["properties"] = {k: ARR for k in list_keys}
    return {"anyOf": [obj, {"type": "null"}], "default": None, "description": desc}


TOOLS = [
    {
        "name": "ha_config_set_dashboard",
        "description": "Create or update a Home Assistant Lovelace dashboard. config holds views (list), each with cards or sections (lists).",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "url_path": {"type": "string", "description": "Dashboard URL path, hyphenated."},
                "config": {"anyOf": [{"additionalProperties": True, "type": "object"}, {"type": "null"}], "default": None,
                           "description": "Dashboard configuration with views and cards."},
            },
            "required": ["url_path"],
        },
    },
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
        bad = malformed(msg["params"]["name"], cfg)
        if bad and ERRMODE != "none":
            body = error_for(bad)
            send({"jsonrpc": "2.0", "id": mid, "result": {"isError": True, "content": [{"type": "text", "text": json.dumps(body)}]}})
        else:
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": '{"success": true}'}]}})
    elif method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": msg["params"].get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}}, "serverInfo": {"name": "stub-ha", "version": "0"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
