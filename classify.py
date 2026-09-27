"""Classify one run's wire.log: clean / wrapped / stringified / other / no_call."""
import json, sys

LIST_KEYS = ("triggers", "trigger", "conditions", "condition", "actions", "action", "sequence")


def classify(path):
    try:
        lines = [l for l in open(path, encoding="utf-8") if l.strip()]
    except FileNotFoundError:
        return ["no_call"], {}
    if not lines:
        return ["no_call"], {}
    verdicts, shapes = [], {}
    for l in lines:
        cfg = json.loads(l)["params"]["arguments"].get("config")
        prefix = ""
        if isinstance(cfg, str):
            try:
                cfg, prefix = json.loads(cfg), "string:"
            except ValueError:
                verdicts.append("string:invalid_json")
                continue
        shapes = {}
        if not isinstance(cfg, dict):
            verdicts.append("other")
            continue
        for k in LIST_KEYS:
            if k not in cfg:
                continue
            v = cfg[k]
            if isinstance(v, list):
                shapes[k] = "list"
            elif isinstance(v, dict) and list(v) == ["item"]:
                shapes[k] = "item-wrapped"
            else:
                shapes[k] = type(v).__name__
        verdicts.append(prefix + ("wrapped" if "item-wrapped" in shapes.values() else "clean"))
    return verdicts, shapes


if __name__ == "__main__":
    calls, shapes = classify(sys.argv[1])
    print(json.dumps({"calls": calls, "first": calls[0], "final": calls[-1], "last_shapes": shapes}))
