"""GitHub-only proof that the real-reply test catches a missing receive guard."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

revision = os.environ["REVISION"]
assert re.fullmatch(r"[0-9a-f]{40}", revision), revision
actual = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
assert actual == revision, (actual, revision)
path = Path("tests/haos_image_build/build_image.py")
test_path = Path("tests/src/unit/test_haos_supervisor_wait.py")
original = path.read_text()
guard = '''                _remaining_deadline_budget(
                    deadline,
                    f"supervisor/api {method} {endpoint} receive",
                )
'''
assert original.count(guard) == 1, original.count(guard)
mutant = original.replace(guard, "")
evidence = Path("deadline-proof")
evidence.mkdir(exist_ok=True)
(evidence / "source-refs.json").write_text(json.dumps({
    "candidate": revision,
    "source_git_blob": subprocess.check_output(["git", "rev-parse", f"HEAD:{path}"], text=True).strip(),
    "test_git_blob": subprocess.check_output(["git", "rev-parse", f"HEAD:{test_path}"], text=True).strip(),
    "original_sha256": hashlib.sha256(original.encode()).hexdigest(),
    "mutant_sha256": hashlib.sha256(mutant.encode()).hexdigest(),
}, indent=2))
(evidence / "tests.py").write_bytes(test_path.read_bytes())
results = {}
try:
    for label, source in (("mutant", mutant), ("candidate", original)):
        path.write_text(source)
        (evidence / f"{label}-build_image.py").write_text(source)
        (evidence / f"{label}.patch").write_bytes(subprocess.check_output(["git", "diff", "--", str(path)]))
        with (evidence / f"{label}.log").open("w") as log:
            result = subprocess.run([
                "uv", "run", "pytest",
                str(test_path) + "::test_the_right_core_version_is_accepted_only_before_the_deadline",
                "-n", "0", "--tb=short", "--maxfail=0", "-v", "-s", "-rA",
                f"--junitxml={evidence / f'{label}.xml'}",
            ], stdout=log, stderr=subprocess.STDOUT, check=False)
        results[label] = result.returncode
finally:
    path.write_text(original)
(evidence / "exit-codes.json").write_text(json.dumps(results, indent=2))
assert results == {"mutant": 1, "candidate": 0}, results
cases = {}
for label in ("mutant", "candidate"):
    cases[label] = ET.parse(evidence / f"{label}.xml").findall(".//testcase")
    assert len(cases[label]) == 2, (label, len(cases[label]))
    for case in cases[label]:
        assert case.find("error") is None, ET.tostring(case)
        assert case.find("skipped") is None, ET.tostring(case)
        failure = case.find("failure")
        if label == "mutant" and case.get("name", "").endswith("[at-deadline]"):
            assert failure is not None, ET.tostring(case)
            assert "DID NOT RAISE" in failure.get("message", ""), ET.tostring(case)
            assert "TimeoutError" in failure.get("message", ""), ET.tostring(case)
        else:
            assert failure is None, ET.tostring(case)
assert {case.get("name") for case in cases["mutant"]} == {case.get("name") for case in cases["candidate"]}
assert sum(case.find("failure") is not None for case in cases["mutant"]) == 1
assert path.read_text() == original
print("Removing only the post-receive guard fails the at-deadline case; restoring it passes both cases.")
