"""Prove the full guard suite before and after the class binding and control-flow fixes."""

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
GUARD_SUPPORT = "scripts/private_test_bindings.py"
FINALLY_NAME = "test_nested_finally_reports_each_source_access_once"
GUARD_TEST = "tests/src/unit/test_private_test_access.py"
REJECT_NAME = "test_reflective_private_access_fails_with_a_source_location"
ALLOW_NAME = "test_public_targets_and_non_target_strings_are_allowed"
HEADER_NAME = "test_definition_headers_and_unshadowed_aliases_still_reject_private_access"
MAPPING_NAME = "test_patch_dict_keeps_string_alternatives_separate_from_mapping_keys"
GROUP_COUNTS = {
    REJECT_NAME: 129,
    ALLOW_NAME: 110,
    "test_local_definitions_shadow_an_imported_reflection_alias": 3,
    HEADER_NAME: 11,
    "test_patch_dict_checks_only_its_import_target": 32,
    MAPPING_NAME: 12,
    FINALLY_NAME: 2,
    "test_the_guard_covers_every_python_file_in_both_trees": 2,
    "test_invalid_source_fails_instead_of_silently_skipping_a_file": 1,
    "test_a_suppression_comment_does_not_exempt_private_access": 1,
}
FAILURE_GROUP_COUNTS = {REJECT_NAME: 15, ALLOW_NAME: 11, FINALLY_NAME: 1}
NEW_CASE_COUNTS = {REJECT_NAME: 22, ALLOW_NAME: 23}
FAILING_CASE_IDS = {
    REJECT_NAME: {
        "class-bound-name-uses-module-fallback", "class-bound-alias-uses-module-fallback",
        "class-literal-unpack-preserves-value", "class-literal-unpack-snapshots-rhs",
        "class-literal-unpack-preserves-alias", "class-loop-carries-import-alias",
        "class-except-star-carries-prior-handler", "class-false-match-guard-keeps-private-capture",
        "class-whole-subject-private-capture", "class-earlier-context-preserves-unbound-target",
        "class-nested-literal-unpack-preserves-value",
        "class-deleted-name-uses-module-not-function-fallback",
        "class-exception-name-uses-module-not-function-fallback",
        "class-global-name-uses-module-without-local-store",
        "class-deleted-global-alias-falls-back-to-builtin",
    },
    ALLOW_NAME: {
        "class-bound-name-ignores-private-function-value", "class-bound-alias-ignores-function-reflection",
        "nonbinding-assignment-does-not-expand-data", "class-single-iteration-value-does-not-run-backwards",
        "class-for-normal-exit-runs-else", "class-while-normal-exit-runs-else",
        "class-raised-try-skips-else-before-finally", "class-false-match-guard-keeps-public-capture",
        "class-global-name-ignores-private-enclosing-value",
        "class-deleted-global-name-does-not-restore-old-value",
        "class-deleted-nonlocal-name-does-not-restore-old-value",
    },
}
EXCEPTION_PLUGIN = '''import json
import os
from pathlib import Path

_failures = {}


def pytest_runtest_makereport(item, call):
    if call.when == "call" and call.excinfo is not None:
        _failures[item.name] = call.excinfo.type.__name__


def pytest_sessionfinish(session, exitstatus):
    Path(os.environ["PROOF_EXCEPTION_TYPES"]).write_text(json.dumps(_failures))
'''


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def source(revision: str, path: str) -> str:
    return subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        check=True, capture_output=True, text=True,
    ).stdout


def parameter_cases(text: str, name: str) -> dict[str, str]:
    [node] = [
        node for node in ast.parse(text).body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    cases = {}
    for item in node.decorator_list[0].args[1].elts:
        if isinstance(item, ast.Call):
            assert isinstance(item.func, ast.Attribute) and item.func.attr == "param"
            value = ast.literal_eval(item.args[0])
            [case_id] = [ast.literal_eval(kw.value) for kw in item.keywords if kw.arg == "id"]
        else:
            value = ast.literal_eval(item)
            case_id = value.encode("unicode_escape").decode("ascii")
        assert case_id not in cases
        cases[case_id] = value
    return cases


def expected_failures(baseline: str, candidate: str) -> set[str]:
    old_source = source(baseline, GUARD_TEST)
    new_source = source(candidate, GUARD_TEST)
    names = set()
    for group, count in NEW_CASE_COUNTS.items():
        old = parameter_cases(old_source, group)
        new = parameter_cases(new_source, group)
        assert old.items() <= new.items(), group
        added = set(new) - set(old)
        assert len(added) == count, (group, added)
        assert FAILING_CASE_IDS[group] <= added
        names.update(group + "[" + case_id + "]" for case_id in FAILING_CASE_IDS[group])
    assert FINALLY_NAME not in old_source
    [finally_test] = [node for node in ast.parse(new_source).body
                      if isinstance(node, ast.FunctionDef) and node.name == FINALLY_NAME]
    decorator = finally_test.decorator_list[0]
    assert ast.literal_eval(decorator.args[1]) == [False, True]
    [ids] = [ast.literal_eval(kw.value) for kw in decorator.keywords if kw.arg == "ids"]
    assert ids == ["public", "private"]
    names.add(FINALLY_NAME + "[private]")
    assert len(names) == 27
    return names


def run_variant(root: Path, label: str) -> dict:
    junit = root / f"{label}.xml"
    types_file = root / f"{label}-exception-types.json"
    command = [
        sys.executable, "-m", "pytest", "--maxfail=0", "--tb=short", "-v",
        "-p", "slf001_proof_types", f"--junitxml={junit}", GUARD_TEST,
    ]
    # Keep the complete project configuration and Core fixture/plugin stack.
    # This additional observer records real exception classes, not log guesses.
    env = os.environ.copy()
    env.pop("UNIT_TEST_SHARD", None)
    env["PROOF_EXCEPTION_TYPES"] = str(types_file)
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        command, env=env, check=False, capture_output=True, text=True, timeout=300
    )
    (root / f"{label}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    print(result.stdout, flush=True)
    print(result.stderr, file=sys.stderr, flush=True)
    report = ET.parse(junit).getroot()
    cases = report.findall(".//testcase")
    names = [case.get("name", "") for case in cases]
    failures = {
        name: failure.get("message", "") + "\n" + (failure.text or "")
        for name, case in zip(names, cases, strict=True)
        if (failure := case.find("failure")) is not None
    }
    return {
        "command": command,
        "exit_code": result.returncode,
        "tests": len(cases),
        "groups": dict(Counter(name.split("[", 1)[0] for name in names)),
        "case_names": names,
        "failures": failures,
        "exception_types": json.loads(types_file.read_text()),
        "errors": [dict(item.attrib) for item in report.findall(".//error")],
        "skips": [dict(item.attrib) for item in report.findall(".//skipped")],
    }


def validate(results: dict, expected: set[str], baseline_only: bool) -> None:
    assert set(results) == ({"baseline"} if baseline_only else {"baseline", "candidate"})
    for label, data in results.items():
        assert data["tests"] == 303, (label, data["tests"])
        assert data["groups"] == GROUP_COUNTS, (label, data["groups"])
        assert not data["errors"], (label, data["errors"])
        assert not data["skips"], (label, data["skips"])
        assert len(set(data["case_names"])) == 303
        assert set(data["exception_types"]) == set(data["failures"])
        assert all(kind == "AssertionError" for kind in data["exception_types"].values())
    before = results["baseline"]
    assert set(before["failures"]) == expected, set(before["failures"]) ^ expected
    failure_groups = Counter(name.split("[", 1)[0] for name in before["failures"])
    assert dict(failure_groups) == FAILURE_GROUP_COUNTS, failure_groups
    assert before["exit_code"] == 1, before["exit_code"]
    if not baseline_only:
        after = results["candidate"]
        assert set(before["case_names"]) == set(after["case_names"])
        assert not after["failures"], after["failures"]
        assert after["exit_code"] == 0, after["exit_code"]


def main() -> None:
    baseline_only = os.environ.get("PROBE_BASELINE_ONLY", "false") == "true"
    baseline = os.environ["PROBE_BASELINE"]
    candidate = os.environ["PROBE_CANDIDATE"]
    assert re.fullmatch(r"[0-9a-f]{40}", baseline), baseline
    assert re.fullmatch(r"[0-9a-f]{40}", candidate), candidate
    assert git("rev-parse", "HEAD") == candidate
    assert not git("status", "--porcelain", "--untracked-files=no")
    root = Path(os.environ["PROBE_ROOT"])
    root.mkdir(parents=True, exist_ok=True)
    (root / "slf001_proof_types.py").write_text(EXCEPTION_PLUGIN, encoding="utf-8")
    candidate_guard = source(candidate, GUARD_PATH)
    baseline_guard = source(baseline, GUARD_PATH)
    assert candidate_guard != baseline_guard or baseline_only
    expected = expected_failures(baseline, candidate)
    evidence = {
        "baseline_only": baseline_only,
        "baseline": baseline,
        "candidate": candidate,
        "driver": git("-C", "../proof", "rev-parse", "HEAD"),
        "blobs": {
            revision: {
                path: git("rev-parse", f"{revision}:{path}")
                for path in (GUARD_PATH, GUARD_SUPPORT, GUARD_TEST)
                if git("ls-tree", "--name-only", revision, "--", path)
            } for revision in (baseline, candidate)
        },
        "selected_module": GUARD_TEST,
        "expected_group_counts": GROUP_COUNTS,
        "expected_failure_group_counts": FAILURE_GROUP_COUNTS,
        "expected_baseline_failure_names": sorted(expected),
    }
    (root / "sources.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    results = {}
    try:
        Path(GUARD_PATH).write_text(baseline_guard, encoding="utf-8")
        (root / "baseline-overlay.patch").write_text(git("diff", "--", GUARD_PATH), encoding="utf-8")
        results["baseline"] = run_variant(root, "baseline")
    finally:
        Path(GUARD_PATH).write_text(candidate_guard, encoding="utf-8")
    assert not git("status", "--porcelain", "--untracked-files=no")
    if not baseline_only:
        results["candidate"] = run_variant(root, "candidate")
    (root / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    validate(results, expected, baseline_only)
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
        handle.write("Regression proof: all 303 guard cases\n\n```json\n" + rendered + "\n```\n")


if __name__ == "__main__":
    main()
