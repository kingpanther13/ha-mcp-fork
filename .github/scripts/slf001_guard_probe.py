"""Run only the 17 added guard cases against immutable old/new guard sources."""

import ast
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

PREVIOUS = "8d69144aef38da25569d5ec6a33b09155c86b0cf"
PREVIOUS_REBASED = "18fe48257e013cee99e5521e53a7c7121639e62a"
CURRENT = "992c971257f8297ffa69ffad4772f4513bb24f14"
TEST_PATH = "tests/src/unit/test_private_test_access.py"
GUARD_PATH = "scripts/private_test_access.py"
PREVIOUS_BLOBS = {
    GUARD_PATH: "c0e263121d4bda2f43fc088ceaf5469c95f0e650",
    TEST_PATH: "3fde9416a0d059c88d5f50d19d225c4fcceabce3",
}
TEST_NAMES = {
    "test_reflective_private_access_fails_with_a_source_location",
    "test_public_targets_and_non_target_strings_are_allowed",
}


def source(revision: str, path: str) -> str:
    if revision == PREVIOUS:
        # The stack was rebased. Verify exact original blobs, rather than
        # treating an absent, superseded commit as a regression-test failure.
        revision = PREVIOUS_REBASED
        blob = subprocess.run(
            ["git", "rev-parse", f"{revision}:{path}"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        assert blob == PREVIOUS_BLOBS[path], (path, blob)
    return subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        check=True, capture_output=True, text=True,
    ).stdout


def cases(module: ast.Module) -> dict[str, list[str]]:
    return {
        node.name: ast.literal_eval(node.decorator_list[0].args[1])
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name in TEST_NAMES
    }


def selected_test_source() -> tuple[str, dict[str, list[str]]]:
    previous = cases(ast.parse(source(PREVIOUS, TEST_PATH)))
    module = ast.parse(source(CURRENT, TEST_PATH))
    added = {
        name: [item for item in items if item not in previous[name]]
        for name, items in cases(module).items()
    }
    assert sum(map(len, added.values())) == 17, added
    selected = []
    for node in module.body:
        if isinstance(node, ast.FunctionDef):
            if node.name == "_run":
                selected.append(node)
            elif node.name in TEST_NAMES:
                node = copy.deepcopy(node)
                node.decorator_list[0].args[1] = ast.List(
                    elts=[ast.Constant(value=item) for item in added[node.name]],
                    ctx=ast.Load(),
                )
                selected.append(node)
        else:
            selected.append(node)
    module.body = selected
    return ast.unparse(ast.fix_missing_locations(module)), added


def run_variant(root: Path, label: str, revision: str, test_source: str) -> dict:
    repo = root / label
    (repo / "scripts").mkdir(parents=True)
    tests = repo / "tests/src/unit"
    tests.mkdir(parents=True)
    (repo / GUARD_PATH).write_text(source(revision, GUARD_PATH), encoding="utf-8")
    (repo / TEST_PATH).write_text(test_source, encoding="utf-8")
    junit = root / f"{label}.xml"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-c", "/dev/null",
         "--confcutdir", str(tests), "--maxfail=0", "--tb=short", "-v",
         f"--junitxml={junit}", str(repo / TEST_PATH)],
        cwd=repo, env=os.environ | {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        check=False, capture_output=True, text=True, timeout=120,
    )
    (root / f"{label}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    print(result.stdout, flush=True)
    print(result.stderr, file=sys.stderr, flush=True)
    report = ET.parse(junit).getroot()
    testcases = report.findall(".//testcase")
    failures = report.findall(".//failure")
    errors = report.findall(".//error")
    assert len(testcases) == 17, len(testcases)
    assert not errors, [item.attrib for item in errors]
    assert not report.findall(".//skipped")
    assert all("AssertionError" in (item.text or "") for item in failures)
    expected_failures = 12 if label == "previous" else 0
    assert len(failures) == expected_failures, (label, len(failures))
    assert result.returncode == (1 if expected_failures else 0), result.returncode
    return {
        "revision": revision, "tests": len(testcases), "failures": len(failures),
        "passed": len(testcases) - len(failures), "errors": len(errors),
        "exit_code": result.returncode,
    }


def main() -> None:
    root = Path(os.environ["PROBE_ROOT"])
    root.mkdir(parents=True)
    test_source, added = selected_test_source()
    (root / "selected-cases.json").write_text(json.dumps(added, indent=2), encoding="utf-8")
    results = {
        label: run_variant(root, label, revision, test_source)
        for label, revision in (("previous", PREVIOUS), ("current", CURRENT))
    }
    results["previous"]["rebased_source_commit"] = PREVIOUS_REBASED
    results["previous"]["verified_original_blobs"] = PREVIOUS_BLOBS
    summary = json.dumps(results, indent=2)
    (root / "results.json").write_text(summary, encoding="utf-8")
    print(summary)
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as handle:
        handle.write("Guard regression proof: 17 newly added cases\n\n```json\n" + summary + "\n```\n")


if __name__ == "__main__":
    main()
