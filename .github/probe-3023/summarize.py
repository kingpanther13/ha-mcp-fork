"""Print the current and historical findings, including the known misses."""
import json
from pathlib import Path

report = json.loads(Path("probe-data/report.json").read_text())
findings = {(r["path"], r["start"]["line"]) for r in report["results"]}
paths = report["paths"]["scanned"]
print("scanned:", len(paths), "errors:", len(report["errors"]))
current = sorted((p, n) for p,n in findings if p.startswith("tests/"))
print("current:", len(current), "distinct files:", len({p for p,n in current}))
for p,n in current:
    print(f"{p}:{n}  {Path(p).read_text().splitlines()[n-1].strip()[:115]}")
for path in sorted(Path("probe-data/replay").glob("*.py")):
    for n,line in enumerate(path.read_text().splitlines(),1):
        if (str(path),n) in findings or "# expect:" in line or "write_text(_FRONTEND" in line:
            print(path.name,n, "HIT" if (str(path),n) in findings else "CLEAR",line.strip())
if report["errors"]:
    raise SystemExit(report["errors"])

