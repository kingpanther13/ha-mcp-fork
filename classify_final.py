"""Per call: paths flagged by the PR's check, plus ANY sole-key item dict anywhere (unchecked positions)."""
import json, sys

sys.argv, path = sys.argv[:1], sys.argv[1]
import stub_mcp_lib as lib  # noqa: E402


def walk(v, p, out):
    if isinstance(v, dict):
        if list(v) == ["item"]:
            out.append(p)
        for k, x in v.items():
            walk(x, f"{p}.{k}", out)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            walk(x, f"{p}[{i}]", out)


res = []
try:
    for line in open(path, encoding="utf-8"):
        m = json.loads(line)["params"]
        cfg = m["arguments"].get("config")
        s = isinstance(cfg, str)
        if s:
            try:
                cfg = json.loads(cfg)
            except ValueError:
                res.append({"invalid_json_string": True})
                continue
        anywhere = []
        walk(cfg, "config", anywhere)
        res.append({"flagged": list(lib.malformed(m["name"], cfg)), "item_anywhere": anywhere, "as_string": s})
except FileNotFoundError:
    res = ["no_call"]
print(json.dumps(res))
