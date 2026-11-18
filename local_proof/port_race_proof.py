"""Run only on GitHub: preserve the real 502 assertions with a forced competitor."""
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

BASELINE = "abbdff7aebc5d8ea2566b90cca709bbc18dd2817"
revision = os.environ["REVISION"]
assert re.fullmatch(r"[0-9a-f]{40}", revision), revision
actual = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
assert actual == revision, (actual, revision)
path = Path("tests/addon/webhook_proxy/test_legacy_oauth_webhook.py")
original = path.read_bytes()
overlay = (Path(__file__).parent / "port_race_overlay.py").read_bytes()
baseline = subprocess.check_output(["git", "show", f"{BASELINE}:{path}"])
evidence = Path("port-proof")
evidence.mkdir(exist_ok=True)
(evidence / "source-refs.json").write_text(json.dumps({"baseline": BASELINE, "candidate": revision}, indent=2))
(evidence / "overlay.py").write_bytes(overlay)
results = {}
try:
    for label, source in (("baseline", baseline), ("candidate", original)):
        path.write_bytes(source + overlay)
        (evidence / f"{label}-test-source.py").write_bytes(source + overlay)
        (evidence / f"{label}-overlay.patch").write_bytes(subprocess.check_output(["git", "diff", "--", str(path)]))
        with (evidence / f"{label}.log").open("w") as log:
            result = subprocess.run([
                "uv", "run", "pytest", str(path),
                "-k", "test_upstream_down_502_does_not_suggest_restarting_home_assistant",
                "-n", "0", "--tb=short", "--maxfail=0", "-v", "-s", "-rA",
                f"--junitxml={evidence / f'{label}.xml'}",
            ], stdout=log, stderr=subprocess.STDOUT, check=False)
        results[label] = result.returncode
finally:
    path.write_bytes(original)
(evidence / "exit-codes.json").write_text(json.dumps(results, indent=2))
assert results == {"baseline": 1, "candidate": 0}, results
cases = {}
for label in ("baseline", "candidate"):
    cases[label] = ET.parse(evidence / f"{label}.xml").findall(".//testcase")
    assert len(cases[label]) == 2, (label, len(cases[label]))
    for case in cases[label]:
        assert case.find("error") is None, ET.tostring(case)
        assert case.find("skipped") is None, ET.tostring(case)
        failure = case.find("failure")
        if label == "baseline":
            assert failure is not None, ET.tostring(case)
            assert "AssertionError: assert 404 == 502" in failure.get("message", ""), ET.tostring(case)
        else:
            assert failure is None, ET.tostring(case)
assert {case.get("name") for case in cases["baseline"]} == {case.get("name") for case in cases["candidate"]}
assert "PROOF: released target port was reused by an unrelated server" in (evidence / "baseline.log").read_text()
assert "PROOF: target port remained reserved" in (evidence / "candidate.log").read_text()
print("Proved both stable/dev cases: baseline returned 404; candidate held reservation and returned 502.")
