"""Summarise one run's wire log: per set-call, how each optional string field arrived, and the outcome."""
import json, sys

FIELDS = ("identifier", "python_transform", "config_hash", "category", "BestPracticeKey")


def shape(args, k):
    if k not in args:
        return "omitted"
    v = args[k]
    if v is None:
        return "null"
    if v == "":
        return "empty"
    if isinstance(v, str) and not v.strip():
        return "blank"
    if isinstance(v, str) and v.lower() in ("null", "none", "undefined"):
        return f"str:{v}"
    return "value"


calls = []
try:
    for line in open(sys.argv[1], encoding="utf-8"):
        rec = json.loads(line)
        if rec["name"] != "ha_config_set_automation":
            continue
        a = rec["arguments"]
        calls.append({"fields": {k: shape(a, k) for k in FIELDS},
                      "config": "omitted" if "config" not in a else type(a["config"]).__name__,
                      "error": rec["error"] if rec["isError"] else None})
except FileNotFoundError:
    pass
print(json.dumps({"calls": calls, "succeeded": bool(calls) and calls[-1]["error"] is None}))
