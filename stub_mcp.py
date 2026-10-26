"""Stdio MCP stub for issue 2873: serves ha-mcp's live ha_config_set_automation inputSchema and
mirrors the server's validation order (identifier non-empty check, then config/python_transform
exclusivity), logging every raw tools/call so we can see what the model sends for omitted fields."""
import json, os, sys

LOG = os.environ.get("WIRE_LOG", "wire.log")

PT_SECURITY = (
    "\n\nPYTHON TRANSFORM SECURITY:\n\nALLOWED:\n- Dictionary/list access: config['views'][0]['cards'][1]\n"
    "- Assignment: config['key'] = 'value'\n- Deletion: del config['key'] or config.pop('key')\n"
    "- List methods: append, insert, pop, remove, clear, extend\n- Loops: for, if/else\n\n"
    "FORBIDDEN:\n- Imports, file operations, dunder access, eval/exec, def/class, try/except, while loops"
)


def opt(t, desc, default=None):
    return {"anyOf": [{"type": t}, {"type": "null"}], "default": default, "description": desc}


SET_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "config": {
            "anyOf": [{"additionalProperties": True, "type": "object"}, {"type": "null"}],
            "default": None,
            "description": "Complete automation configuration. Purpose-specific triggers/conditions (HA 2026.7+ default: "
            "'trigger': '<domain>.<name>' with 'target'/'options') are valid config. Mutually exclusive with python_transform.",
        },
        "identifier": opt("string", "Target automation entity_id or HA config 'id' (unique_id). Omit for creation with a "
                          "generated ID. Values such as 'new' are literal IDs, not placeholders."),
        "python_transform": opt("string", "Python expression to transform existing automation config. Mutually exclusive "
                                "with config. WARNING: Expressions with infinite loops will hang the server. Examples: "
                                "Simple: python_transform=\"config['actions'][0]['data']['brightness'] = 255\"" + PT_SECURITY),
        "config_hash": opt("string", "Config hash from ha_config_get_automation for optimistic locking. Required for "
                           "python_transform and when a config update changes an existing automation's alias; otherwise "
                           "optional for config updates (validates before full replacement if provided)."),
        "take_control_of_blueprint": {"type": "boolean", "default": False, "description": "Convert a blueprint-backed "
                                      "automation into an editable standalone one. Requires identifier; mutually exclusive "
                                      "with config and python_transform."},
        "category": opt("string", "Category ID to assign to this automation. Use ha_config_get_category(scope='automation') "
                        "to list available categories, or ha_config_set_category() to create one."),
        "wait": {"type": "boolean", "default": True, "description": "Wait for automation to be queryable before returning. "
                 "Set to False for bulk operations."},
        "enabled": {"anyOf": [{"type": "boolean"}, {"type": "null"}], "default": None, "description": "Turn the automation "
                    "on (True) or off (False) after an optional config update; None leaves it unchanged."},
        "run_actions": {"type": "boolean", "default": False, "description": "Run the automation's actions now, skipping "
                        "its triggers and conditions. Can be used standalone with identifier and no config."},
        "MandatoryBPS": {"type": "boolean", "default": True},
        "BestPracticeKey": opt("string", "Read-receipt for the home-assistant-best-practices skill; required when strict "
                               "best-practices mode is enabled. Pass the attestation phrase verbatim."),
    },
}

SET_DESC = """Create or update a Home Assistant automation.

MODES (pick one; each can also take `enabled`, which alone with
`identifier` turns the automation on or off without touching config):
- python_transform: surgical edits to an existing automation. Requires
  identifier and config_hash from ha_config_get_automation().
- config: new automations or full restructures. Regular automations need
  alias, triggers and actions.
- take_control_of_blueprint: convert a blueprint-backed automation into a
  standalone one. Takes no config of its own.

IDENTITY: omit identifier and config['id'] to create with a generated ID;
an unused raw ID creates that specific ID. Reusing an identifier targets
the same automation even if the alias changes.

EXAMPLES:
- Create: ha_config_set_automation(config={"alias": "Morning Lights", "triggers": [{"trigger": "time", "at": "07:00:00"}], "actions": [{"action": "light.turn_on", "target": {"area_id": "bedroom"}}]})
- Update: current = ha_config_get_automation(identifier="automation.x"); ha_config_set_automation(identifier="automation.x", config_hash=current["config_hash"], config={...})
- Disable: ha_config_set_automation(identifier="automation.x", enabled=False)"""

EXISTING = {
    "id": "1700000000001",
    "alias": "Morning Lights",
    "description": "",
    "triggers": [{"trigger": "time", "at": "07:00:00"}],
    "conditions": [],
    "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.bedroom"}}],
    "mode": "single",
}

TOOLS = [
    {"name": "ha_config_get_automation", "description": "Get an automation's stored config and config_hash.",
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "properties": {"identifier": {"type": "string", "description": "Automation entity_id or unique_id."}},
                     "required": ["identifier"]}},
    {"name": "ha_config_set_automation", "description": SET_DESC, "inputSchema": SET_SCHEMA},
]


def err(message, suggestions, **ctx):
    body = {"success": False, "error": {"code": "VALIDATION_INVALID_PARAMETER", "message": message,
                                        "suggestions": suggestions}, **ctx}
    return {"isError": True, "content": [{"type": "text", "text": json.dumps(body)}]}


def ok(body):
    return {"content": [{"type": "text", "text": json.dumps(body)}]}


def handle_set(a):
    identifier, config, pt = a.get("identifier"), a.get("config"), a.get("python_transform")
    if identifier is not None and not (isinstance(identifier, str) and identifier.strip()):
        return err("identifier must be a non-empty, non-whitespace string",
                   ["Omit identifier to create a new automation",
                    "Or pass a valid automation entity_id / unique_id to update"],
                   parameter="identifier", value=identifier, action="set")
    if config is not None and pt is not None:
        return err("Cannot use both config and python_transform simultaneously",
                   ["Use only ONE of: config or python_transform", "config: Full replacement",
                    "python_transform: Python-based edits (recommended for existing automations)"])
    if isinstance(config, str):
        return err("`config`: expected a JSON object. Pass {\"key\": \"value\"} directly, not a JSON-encoded string.", [])
    if pt is not None and not a.get("config_hash"):
        return err("config_hash is required for python_transform", ["Call ha_config_get_automation first"])
    if config is None and pt is None and a.get("enabled") is None and not a.get("run_actions"):
        return err("Provide config, python_transform, enabled or run_actions", [])
    op = "updated" if identifier else "created"
    return ok({"success": True, "operation": op, "automation_id": identifier or "automation.generated"})


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
        name = msg["params"]["name"]
        args = msg["params"].get("arguments") or {}
        if name == "ha_config_get_automation":
            result = ok({"success": True, "identifier": args.get("identifier"), "config": EXISTING,
                         "config_hash": "abc123def456"})
        else:
            result = handle_set(args)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"name": name, "arguments": args, "isError": bool(result.get("isError")),
                                "error": json.loads(result["content"][0]["text"]).get("error", {}).get("message")}) + "\n")
        send({"jsonrpc": "2.0", "id": mid, "result": result})
    elif method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": msg["params"].get("protocolVersion", "2025-06-18"),
              "capabilities": {"tools": {}}, "serverInfo": {"name": "stub-ha", "version": "0"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
