"""Prove the full guard suite before and after the ordered class namespace fixes."""

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
REJECT_NAME = "test_reflective_private_access_fails_with_a_source_location"
ALLOW_NAME = "test_public_targets_and_non_target_strings_are_allowed"
HEADER_NAME = "test_definition_headers_and_unshadowed_aliases_still_reject_private_access"
MAPPING_NAME = "test_patch_dict_keeps_string_alternatives_separate_from_mapping_keys"
GROUP_COUNTS = {
    REJECT_NAME: 105,
    ALLOW_NAME: 87,
    "test_local_definitions_shadow_an_imported_reflection_alias": 3,
    HEADER_NAME: 11,
    "test_patch_dict_checks_only_its_import_target": 32,
    MAPPING_NAME: 12,
    "test_the_guard_covers_every_python_file_in_both_trees": 2,
    "test_invalid_source_fails_instead_of_silently_skipping_a_file": 1,
    "test_a_suppression_comment_does_not_exempt_private_access": 1,
}
FAILURE_GROUP_COUNTS = {REJECT_NAME: 16, ALLOW_NAME: 8}
NEW_CASE_COUNTS = {REJECT_NAME: 23, ALLOW_NAME: 24}
FAILING_CASE_IDS = {
    REJECT_NAME: {
        "class-inherited-rhs", "class-annotated-rhs", "class-read-before-rebind",
        "class-copy-before-rebind", "class-optional-override", "class-mapping-string-join",
        "class-optional-alias", "class-alias-join", "class-loop-zero-iterations",
        "class-handler-inherited-value", "class-match-no-case", "class-mapping-key-join",
        "class-annotation-only", "class-exception-name-fallback",
        "class-deleted-value-fallback", "class-deleted-alias-fallback",
    },
    ALLOW_NAME: {
        "class-definite-public-overwrite", "class-independent-else", "class-match-capture",
        "class-exception-name-public-fallback", "class-deleted-public-fallback",
        "class-deleted-public-alias", "class-independent-handlers",
        "class-loop-definite-public-prefix",
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
    assert len(names) == 24
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


def validate(results: dict, expected: set[str]) -> None:
    for label, data in results.items():
        assert data["tests"] == 254, (label, data["tests"])
        assert data["groups"] == GROUP_COUNTS, (label, data["groups"])
        assert not data["errors"], (label, data["errors"])
        assert not data["skips"], (label, data["skips"])
        assert len(set(data["case_names"])) == 254
        assert set(data["exception_types"]) == set(data["failures"])
        assert all(kind == "AssertionError" for kind in data["exception_types"].values())
    before = results["baseline"]
    after = results["candidate"]
    assert set(before["case_names"]) == set(after["case_names"])
    assert set(before["failures"]) == expected, set(before["failures"]) ^ expected
    failure_groups = Counter(name.split("[", 1)[0] for name in before["failures"])
    assert dict(failure_groups) == FAILURE_GROUP_COUNTS, failure_groups
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
    (root / "slf001_proof_types.py").write_text(EXCEPTION_PLUGIN, encoding="utf-8")
    candidate_guard = source(candidate, GUARD_PATH)
    baseline_guard = source(baseline, GUARD_PATH)
    assert candidate_guard != baseline_guard
    expected = expected_failures(baseline, candidate)
    evidence = {
        "baseline": baseline,
        "candidate": candidate,
        "driver": git("-C", "../proof", "rev-parse", "HEAD"),
        "blobs": {
            revision: {
                path: git("rev-parse", f"{revision}:{path}")
                for path in (GUARD_PATH, GUARD_TEST)
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
    assert not git("diff", "--", GUARD_PATH, GUARD_TEST)
    results["candidate"] = run_variant(root, "candidate")
    (root / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    validate(results, expected)
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
        handle.write("Regression proof: all 254 guard cases\n\n```json\n" + rendered + "\n```\n")


if __name__ == "__main__":
    main()
