"""Prove 47 guard cases and one repeat-call regression with real Core fixtures."""

import ast
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

GUARD_PATH = "scripts/private_test_access.py"
GUARD_TEST = "tests/src/unit/test_private_test_access.py"
HELPER_TEST = "tests/src/unit/test_tools_addons_array_patch.py"
GROUP_COUNTS = {
    "test_local_definitions_shadow_an_imported_reflection_alias": 3,
    "test_defined_names_do_not_retain_inherited_private_targets": 5,
    "test_definition_headers_and_unshadowed_aliases_still_reject_private_access": 4,
    "test_patch_dict_checks_only_its_import_target": 32,
    "test_patch_dict_mapping_contents_are_data": 3,
    "test_repeated_calls_capture_only_their_own_post": 1,
}
GUARD_NAMES = list(GROUP_COUNTS)[:-1]
HELPER_NAME = list(GROUP_COUNTS)[-1]
NODE_IDS = [f"{GUARD_TEST}::{name}" for name in GUARD_NAMES] + [
    f"{HELPER_TEST}::TestApplyArrayOps::{HELPER_NAME}"
]


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def source(revision: str, path: str) -> str:
    return subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        check=True, capture_output=True, text=True,
    ).stdout


def helper_span(text: str) -> tuple[int, int]:
    nodes = [
        node for node in ast.parse(text).body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_patch_array"
    ]
    assert len(nodes) == 1 and not nodes[0].decorator_list
    node = nodes[0]
    assert node.end_lineno is not None
    return node.lineno - 1, node.end_lineno


def old_helper_in_new_tests(candidate: str, baseline: str) -> str:
    """Replace only the helper's source span, retaining every candidate test."""
    start, end = helper_span(candidate)
    old_start, old_end = helper_span(baseline)
    lines = candidate.splitlines(keepends=True)
    lines[start:end] = baseline.splitlines(keepends=True)[old_start:old_end]
    return "".join(lines)


def run_variant(root: Path, label: str) -> dict:
    junit = root / f"{label}.xml"
    command = [
        sys.executable, "-m", "pytest", "--maxfail=0", "--tb=short", "-v",
        f"--junitxml={junit}", *NODE_IDS,
    ]
    # Use the candidate's complete pytest configuration and Core plugins.
    # No isolated/minimal pytest environment or conftest bypass is used.
    env = os.environ.copy()
    env.pop("UNIT_TEST_SHARD", None)
    result = subprocess.run(
        command, env=env, check=False, capture_output=True, text=True, timeout=300
    )
    (root / f"{label}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    print(result.stdout, flush=True)
    print(result.stderr, file=sys.stderr, flush=True)
    report = ET.parse(junit).getroot()
    cases = report.findall(".//testcase")
    case_ids = [f"{case.get('classname')}::{case.get('name')}" for case in cases]
    failures = {
        case_id: failure.get("message", "") + "\n" + (failure.text or "")
        for case_id, case in zip(case_ids, cases, strict=True)
        if (failure := case.find("failure")) is not None
    }
    return {
        "command": command,
        "exit_code": result.returncode,
        "tests": len(cases),
        "groups": dict(Counter(case.get("name", "").split("[", 1)[0] for case in cases)),
        "case_ids": case_ids,
        "failures": failures,
        "errors": [dict(item.attrib) for item in report.findall(".//error")],
        "skips": [dict(item.attrib) for item in report.findall(".//skipped")],
    }


def expected_failure(case_id: str) -> bool:
    name = case_id.rsplit("::", 1)[1]
    return (
        name.startswith(GUARD_NAMES[0] + "[")
        or name.startswith(GUARD_NAMES[1] + "[")
        # The innermost parametrize decorator is private=[False, True].
        or name.startswith("test_patch_dict_checks_only_its_import_target[True-")
        or name == HELPER_NAME
    )


def validate(results: dict) -> None:
    for label, data in results.items():
        assert data["tests"] == 48, (label, data["tests"])
        assert data["groups"] == GROUP_COUNTS, (label, data["groups"])
        assert not data["errors"], (label, data["errors"])
        assert not data["skips"], (label, data["skips"])
        assert len(set(data["case_ids"])) == 48
    before = results["baseline"]
    after = results["candidate"]
    assert set(before["case_ids"]) == set(after["case_ids"])
    expected = {case for case in before["case_ids"] if expected_failure(case)}
    assert len(expected) == 25, expected
    assert set(before["failures"]) == expected, set(before["failures"]) ^ expected
    for case, failure in before["failures"].items():
        if case.endswith("::" + HELPER_NAME):
            assert "ValueError: too many values to unpack (expected 1, got 2)" in failure, failure
            assert "[body] = bodies" in failure, failure
        else:
            assert "AssertionError" in failure, failure
    assert before["exit_code"] == 1, before["exit_code"]
    assert not after["failures"], after["failures"]
    assert after["exit_code"] == 0, after["exit_code"]


def main() -> None:
    baseline = os.environ["PROBE_BASELINE"]
    candidate = os.environ["PROBE_CANDIDATE"]
    assert re.fullmatch(r"[0-9a-f]{40}", baseline), baseline
    assert re.fullmatch(r"[0-9a-f]{40}", candidate), candidate
    assert git("rev-parse", "HEAD") == candidate
    assert not git("status", "--porcelain", "--untracked-files=no")
    root = Path(os.environ["PROBE_ROOT"])
    root.mkdir(parents=True, exist_ok=True)
    candidate_guard = source(candidate, GUARD_PATH)
    candidate_tests = source(candidate, HELPER_TEST)
    baseline_guard = source(baseline, GUARD_PATH)
    baseline_tests = source(baseline, HELPER_TEST)
    assert candidate_guard != baseline_guard
    replaced_helper = old_helper_in_new_tests(candidate_tests, baseline_tests)
    assert replaced_helper != candidate_tests
    evidence = {
        "baseline": baseline,
        "candidate": candidate,
        "driver": git("-C", "../proof", "rev-parse", "HEAD"),
        "blobs": {
            revision: {
                path: git("rev-parse", f"{revision}:{path}")
                for path in (GUARD_PATH, GUARD_TEST, HELPER_TEST)
            } for revision in (baseline, candidate)
        },
        "selected_nodes": NODE_IDS,
        "expected_group_counts": GROUP_COUNTS,
        "expected_baseline_failures": {"guard_assertions": 24, "helper_value_error": 1},
    }
    (root / "sources.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    results = {}
    try:
        Path(GUARD_PATH).write_text(baseline_guard, encoding="utf-8")
        Path(HELPER_TEST).write_text(replaced_helper, encoding="utf-8")
        (root / "baseline-overlay.patch").write_text(git("diff", "--", GUARD_PATH, HELPER_TEST), encoding="utf-8")
        results["baseline"] = run_variant(root, "baseline")
    finally:
        Path(GUARD_PATH).write_text(candidate_guard, encoding="utf-8")
        Path(HELPER_TEST).write_text(candidate_tests, encoding="utf-8")
    assert not git("diff", "--", GUARD_PATH, GUARD_TEST, HELPER_TEST)
    results["candidate"] = run_variant(root, "candidate")
    (root / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    validate(results)
    summary = {
        label: {"tests": data["tests"], "failures": len(data["failures"]),
                "passed": data["tests"] - len(data["failures"]),
                "errors": len(data["errors"]), "skips": len(data["skips"]),
                "exit_code": data["exit_code"]}
        for label, data in results.items()
    }
    rendered = json.dumps({"sources": evidence, "results": summary}, indent=2)
    print(rendered)
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as handle:
        handle.write("Regression proof: 47 guard cases and one repeated-call helper case\n\n```json\n" + rendered + "\n```\n")


if __name__ == "__main__":
    main()
