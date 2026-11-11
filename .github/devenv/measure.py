"""Throwaway: print the files each test rule matches with its list emptied."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

RULES = (
    "no-hand-made-core-replies",
    "no-shared-fake-clients",
    "no-hand-made-hass",
    "no-stand-in-suppressions",
    "no-untyped-core-fixtures",
    "no-shared-core-fakes",
    "no-fake-core-objects",
)

for rule in RULES:
    config = yaml.safe_load(Path(f"rules/{rule}.yml").read_text(encoding="utf-8"))
    listed = set(config.get("ignores") or [])
    config["ignores"] = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{rule}.yml"
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        proc = subprocess.run(
            ["ast-grep", "scan", "--rule", str(path), "--json=compact", "tests"],
            capture_output=True,
            text=True,
            check=False,
        )
    if not proc.stdout.strip():
        print("MEASURE-ERROR", rule, proc.stderr[:2000])
        continue
    rows = json.loads(proc.stdout)
    files = sorted({row["file"] for row in rows})
    for name in files:
        print("MEASURE", rule, name)
    print("MEASURE-COUNT", rule, len(files), "listed", len(listed))
    for name in sorted(set(files) - listed):
        hits = [row for row in rows if row["file"] == name][:3]
        for row in hits:
            text = row["text"].replace("\n", " ")[:110]
            line = row["range"]["start"]["line"] + 1
            print("MEASURE-NEW", rule, f"{name}:{line}", text)
    for name in sorted(listed - set(files)):
        print("MEASURE-GONE", rule, name)
sys.exit(0)
