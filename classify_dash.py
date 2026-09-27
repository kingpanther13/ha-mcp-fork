"""List every path holding a sole-key {"item": ...} dict, per dashboard call."""
import json, sys


def walk(v, path, out):
    if isinstance(v, dict):
        if list(v) == ["item"]:
            out.append(path)
        for k, x in v.items():
            walk(x, f"{path}.{k}", out)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            walk(x, f"{path}[{i}]", out)


calls = []
try:
    for line in open(sys.argv[1], encoding="utf-8"):
        cfg = json.loads(line)["params"]["arguments"].get("config")
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except ValueError:
                calls.append("string:invalid_json")
                continue
        out = []
        walk(cfg, "config", out)
        calls.append(out)
except FileNotFoundError:
    calls = ["no_call"]
print(json.dumps(calls))
