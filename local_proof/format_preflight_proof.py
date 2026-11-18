"""GitHub-only mutation proof; the test's formatter spy prevents large allocations."""
import ast
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
path = Path("scripts/private_test_bindings.py")
test_path = Path("tests/src/unit/test_private_test_access.py")
original = path.read_text()
test_source = test_path.read_text()
sentinel = "oversized format reached formatter"
assert 'raise AssertionError("' + sentinel + '")' in test_source
assert 'result = _run(tmp_path, source, formatter_spy=True)' in test_source
assert 'test_formatter_spy_delegates_ordinary_target_formatting' in test_source
[formatter] = [node for node in ast.parse(original).body
               if isinstance(node, ast.FunctionDef) and node.name == "_format_literal"]
[preflight] = [node for node in formatter.body if isinstance(node, ast.If)
              and any(isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                      and child.func.attr == "findall" for child in ast.walk(node.test))]
assert any(isinstance(child, ast.Raise) for child in ast.walk(preflight))
lines = original.splitlines(keepends=True)
mutant = "".join(lines[:preflight.lineno - 1] + lines[preflight.end_lineno:])
evidence = Path("format-proof")
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
        (evidence / f"{label}-bindings.py").write_text(source)
        (evidence / f"{label}.patch").write_bytes(subprocess.check_output(["git", "diff", "--", str(path)]))
        with (evidence / f"{label}.log").open("w") as log:
            result = subprocess.run([
                "uv", "run", "pytest",
                str(test_path) + "::test_formatted_targets_are_bounded_before_allocation",
                str(test_path) + "::test_formatter_spy_delegates_ordinary_target_formatting",
                "-n", "0", "--tb=short", "--maxfail=0", "-v", "-s", "-rA",
                f"--junitxml={evidence / f'{label}.xml'}",
            ], stdout=log, stderr=subprocess.STDOUT, check=False)
        results[label] = result.returncode
finally:
    path.write_text(original)
(evidence / "exit-codes.json").write_text(json.dumps(results, indent=2))
assert results == {"mutant": 1, "candidate": 0}, results
expected_failures = {
    "test_formatted_targets_are_bounded_before_allocation[formatted-width-bounded-before-allocation]",
    "test_formatted_targets_are_bounded_before_allocation[formatted-precision-bounded-before-allocation]",
}
expected_names = expected_failures | {"test_formatter_spy_delegates_ordinary_target_formatting"}
cases = {}
for label in ("mutant", "candidate"):
    cases[label] = ET.parse(evidence / f"{label}.xml").findall(".//testcase")
    assert len(cases[label]) == 3, (label, len(cases[label]))
    assert {case.get("name") for case in cases[label]} == expected_names
    for case in cases[label]:
        assert case.find("error") is None, ET.tostring(case)
        assert case.find("skipped") is None, ET.tostring(case)
        failure = case.find("failure")
        if label == "mutant" and case.get("name") in expected_failures:
            assert failure is not None, ET.tostring(case)
            assert "AssertionError" in failure.get("message", ""), ET.tostring(case)
            assert sentinel in (failure.text or ""), ET.tostring(case)
        else:
            assert failure is None, ET.tostring(case)
assert {case.get("name") for case in cases["mutant"]} == {case.get("name") for case in cases["candidate"]}
assert path.read_text() == original
print("Removing only the formatting preflight fails both spy-backed bounds with the sentinel; ordinary formatting passes and restoring the guard passes all three.")
