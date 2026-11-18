"""Fork-only mutation proof; execute exclusively on a GitHub Actions runner."""

from __future__ import annotations

import difflib
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


BASE = "bc5ecedb801873f41b0390fc13073d740f992b77"
TEST_PATH = Path("tests/src/unit/test_vendor_fastmcp_script.py")
SCRIPT_PATH = Path("scripts/vendor_fastmcp.py")
TEST_NAME = "test_promote_restores_every_tree_when_a_swap_fails"
ORIGINAL = "for target, backup, had_previous in reversed(swapped):"
REGRESSION = "for target, backup, had_previous in reversed(swapped[-1:]):"


def run_case(root: Path, repo: Path, label: str, expected: set[str]) -> dict:
    report = root / f"{label}.xml"
    command = [
        sys.executable, "-m", "pytest", "-c", "/dev/null",
        "--confcutdir", str(repo / "tests/src/unit"),
        "--maxfail=0", "--tb=long", "-vv", f"--junitxml={report}",
        f"{repo / TEST_PATH}::{TEST_NAME}",
    ]
    result = subprocess.run(
        command, cwd=repo, text=True, capture_output=True, check=False,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
             "PYTHONDONTWRITEBYTECODE": "1"},
    )
    output = result.stdout + result.stderr
    (root / f"{label}.log").write_text(output)
    print(output, flush=True)
    cases = ET.parse(report).findall(".//testcase")
    assert len(cases) == 3, (label, len(cases))
    assert not any(case.find("error") is not None for case in cases), label
    assert not any(case.find("skipped") is not None for case in cases), label
    failures = {
        case.attrib["name"]: case.find("failure")
        for case in cases if case.find("failure") is not None
    }
    assert set(failures) == expected, (label, failures)
    for failure in failures.values():
        assert failure is not None
        assert "AssertionError" in (failure.text or ""), failure.text
        assert "} == before" in (failure.text or ""), failure.text
    assert result.returncode == (1 if expected else 0), result.returncode
    return {"phase": label, "passed": 3 - len(failures),
            "failed": sorted(failures), "errors": 0, "skipped": 0,
            "exit_code": result.returncode, "command": command}


def main() -> None:
    assert os.environ.get("GITHUB_ACTIONS") == "true", "GitHub Actions only"
    source = Path(__file__).resolve().parents[2]
    root = Path(os.environ["PROBE_ROOT"])
    repo = root / "isolated-repo"
    for relative in (SCRIPT_PATH, TEST_PATH):
        baseline = subprocess.run(
            ["git", "show", f"{BASE}:{relative}"], cwd=source,
            capture_output=True, check=True,
        ).stdout
        assert (source / relative).read_bytes() == baseline, relative
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, destination)
    script = repo / SCRIPT_PATH
    original = script.read_text()
    assert original.count(ORIGINAL) == 1
    regression = original.replace(ORIGINAL, REGRESSION)
    (root / "vendor_fastmcp.original.py").write_text(original)
    (root / "vendor_fastmcp.regression.py").write_text(regression)
    (root / "regression.patch").write_text("".join(difflib.unified_diff(
        original.splitlines(keepends=True), regression.splitlines(keepends=True),
        fromfile=str(SCRIPT_PATH), tofile=str(SCRIPT_PATH),
    )))
    phases = [run_case(root, repo, "current", set())]
    try:
        script.write_text(regression)
        phases.append(run_case(root, repo, "regression", {
            f"{TEST_NAME}[mcp]", f"{TEST_NAME}[mcp_types]",
        }))
    finally:
        script.write_text(original)
    assert script.read_text() == original
    phases.append(run_case(root, repo, "restored", set()))
    summary = {"tested_pr_head": BASE, "mutation": REGRESSION, "phases": phases}
    (root / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
