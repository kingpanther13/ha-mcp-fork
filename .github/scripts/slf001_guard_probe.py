"""Prove preserved rule tests, guard regressions, and diagnostic source lines."""

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
GUARD_FLOW = "scripts/private_test_flow.py"
GUARD_EXPRESSIONS = "scripts/private_test_expressions.py"
GUARD_PATHS = (GUARD_PATH, GUARD_SUPPORT, GUARD_FLOW, GUARD_EXPRESSIONS)
FINALLY_NAME = "test_nested_finally_reports_each_source_access_once"
BOUND_NAME = "test_formatted_targets_are_bounded_before_allocation"
PERFORMANCE_NAME = "test_nested_finally_reuses_repeated_states"
OLD_GUARD_TEST = "tests/src/unit/test_private_test_access.py"
GUARD_TEST = "tests/src/unit/test_server_answer_rule.py"
BASELINE_TESTS = (GUARD_TEST,)
GUARD_TESTS = (GUARD_TEST,)
LOCATION_NAME = "test_multiline_private_access_reports_its_call_line"
ERROR_NAME = "test_oversized_targets_still_fail_after_lazy_binding"
SPY_NAME = "test_formatter_spy_delegates_ordinary_target_formatting"
REJECT_NAME = "test_reflective_private_access_fails_with_a_source_location"
ALLOW_NAME = "test_public_targets_and_non_target_strings_are_allowed"
HEADER_NAME = "test_definition_headers_and_unshadowed_aliases_still_reject_private_access"
MAPPING_NAME = "test_patch_dict_keeps_string_alternatives_separate_from_mapping_keys"
BASELINE = "497008f3208c53fa3bcc05749ffc4861438bd442"
LOOP_CASE_ID = "loop-compound-tuple-elements-keep-stable-state-reject"
LOOP_SOURCE_OLD = "ARGS = (mod, '_helper')\nfor item in items:\n    ARGS = (mod, '_helper' + '')\ngetattr(*ARGS)\n"
LOOP_SOURCE_NEW = "ARGS = (mod, 'public')\nfor item in items:\n    ARGS = (mod, '_helper' + '')\ngetattr(*ARGS)\n"
BASELINE_GROUP_COUNTS = {'test_multiline_private_access_reports_its_call_line': 1, 'test_reflective_private_access_fails_with_a_source_location': 298, 'test_public_targets_and_non_target_strings_are_allowed': 274, 'test_local_definitions_shadow_an_imported_reflection_alias': 3, 'test_definition_headers_and_unshadowed_aliases_still_reject_private_access': 11, 'test_patch_dict_checks_only_its_import_target': 32, 'test_patch_dict_keeps_string_alternatives_separate_from_mapping_keys': 12, 'test_nested_finally_reports_each_source_access_once': 2, 'test_formatted_targets_are_bounded_before_allocation': 2, 'test_oversized_targets_still_fail_after_lazy_binding': 3, 'test_formatter_spy_delegates_ordinary_target_formatting': 1, 'test_nested_finally_reuses_repeated_states': 1, 'test_the_guard_covers_every_python_file_in_both_trees': 2, 'test_invalid_source_fails_instead_of_silently_skipping_a_file': 1, 'test_a_suppression_comment_does_not_exempt_private_access': 1, 'test_only_a_typed_in_server_answer_is_refused': 31, 'test_no_test_types_in_a_server_answer': 1}
STRONG_REJECT_SOURCE = 'def test_reflective_private_access_fails_with_a_source_location(tmp_path, source):\n    result = _run(tmp_path, source)\n    assert result.returncode == 1, result.stderr\n    assert re.search(\n        r"^tests/src/unit/test_sample\\.py:[1-9]\\d*: private string target ",\n        result.stdout,\n        re.MULTILINE,\n    ), result.stdout'
LOCATION_TEST_SOURCE = 'def test_multiline_private_access_reports_its_call_line(tmp_path):\n    result = _run(tmp_path, "name = \'_helper\'\\n\\ngetattr(\\n    mod,\\n    name,\\n)\\n")\n    assert result.returncode == 1, result.stdout + result.stderr\n    assert (\n        "tests/src/unit/test_sample.py:3: private string target \'_helper\'"\n        in result.stdout\n    )'
CASE_MANIFEST = json.loads(Path(__file__).with_name("slf001_guard_cases.json").read_text())
CASE_GROUPS = {REJECT_NAME: "reject", ALLOW_NAME: "allow", ERROR_NAME: "error"}
assert set(CASE_MANIFEST) == set(CASE_GROUPS.values())
assert sum(BASELINE_GROUP_COUNTS.values()) == 676
NEW_CASE_COUNTS = {group: len(CASE_MANIFEST[section]) for group, section in CASE_GROUPS.items()}
GROUP_COUNTS = {
    group: count + NEW_CASE_COUNTS.get(group, 0)
    for group, count in BASELINE_GROUP_COUNTS.items()
}
GROUP_COUNTS[LOCATION_NAME] = 1
FAILURE_GROUP_COUNTS = {
    group: sum(case["baseline_fails"] for case in CASE_MANIFEST[section])
    for group, section in CASE_GROUPS.items()
    if any(case["baseline_fails"] for case in CASE_MANIFEST[section])
}
TOTAL_CASES = sum(GROUP_COUNTS.values())
EXPECTED_FAILURE_COUNT = sum(FAILURE_GROUP_COUNTS.values())
assert EXPECTED_FAILURE_COUNT > 0, "Awaiting the trusted predicted-failure manifest"
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
        if isinstance(item, ast.Starred):
            # Accept only the literal source-map generator used by these tables.
            generator = item.value
            assert isinstance(generator, ast.GeneratorExp)
            [clause] = generator.generators
            assert not clause.ifs and not clause.is_async
            assert isinstance(clause.target, ast.Tuple)
            assert [part.id for part in clause.target.elts] == ["case_id", "source"]
            assert ast.dump(generator.elt) == ast.dump(
                ast.parse("pytest.param(source, id=case_id)", mode="eval").body
            )
            iteration = clause.iter
            assert isinstance(iteration, ast.Call) and not iteration.args and not iteration.keywords
            assert isinstance(iteration.func, ast.Attribute) and iteration.func.attr == "items"
            mapping = iteration.func.value
            assert isinstance(mapping, ast.Dict)
            additions = ast.literal_eval(mapping)
            assert len(additions) == len(mapping.keys), "duplicate literal case ID"
            assert all(isinstance(key, str) and isinstance(value, str)
                       for key, value in additions.items())
            assert not cases.keys() & additions.keys(), "duplicate case ID"
            cases.update(additions)
            continue
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


def definitions(texts: list[str]) -> dict[str, ast.AST]:
    nodes = {}
    for text in texts:
        for node in ast.parse(text).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                assert node.name not in nodes, (node.name, "duplicate definition")
                nodes[node.name] = node
    return nodes


def server_answer_cases(node: ast.FunctionDef) -> dict[str, tuple[str, bool]]:
    table = node.decorator_list[0].args[1]
    if isinstance(table, ast.List):
        return {
            next(ast.literal_eval(kw.value) for kw in item.keywords if kw.arg == "id"):
            tuple(ast.literal_eval(arg) for arg in item.args)
            for item in table.elts
        }
    assert isinstance(table, ast.ListComp)
    if len(table.generators) == 2:
        outer, inner = table.generators
        assert not outer.ifs and not outer.is_async and not inner.ifs and not inner.is_async
        assert ast.dump(table.elt) == ast.dump(ast.parse("pytest.param(source, refused, id=case_id)", mode="eval").body)
        assert ast.dump(outer.target) == ast.dump(ast.parse("for refused, cases in rows: pass").body[0].target)
        assert ast.dump(inner.target) == ast.dump(ast.parse("for case_id, source in rows: pass").body[0].target)
        assert ast.dump(inner.iter) == ast.dump(ast.parse("cases.items()", mode="eval").body)
        assert isinstance(outer.iter, ast.Call) and not outer.iter.args and not outer.iter.keywords
        assert isinstance(outer.iter.func, ast.Attribute) and outer.iter.func.attr == "items"
        mapping = outer.iter.func.value
        assert isinstance(mapping, ast.Dict)
        groups = ast.literal_eval(mapping)
        assert set(groups) == {False, True} and len(mapping.keys) == 2
        assert all(isinstance(rows, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in rows.items()) for rows in groups.values())
        assert all(isinstance(value, ast.Dict) and len(ast.literal_eval(value)) == len(value.keys) for value in mapping.values)
        result = {key: (source, refused) for refused, rows in groups.items() for key, source in rows.items()}
        assert len(result) == sum(len(rows) for rows in groups.values()) == 31
        return result
    [clause] = table.generators
    assert not clause.ifs and not clause.is_async
    assert ast.dump(table.elt) == ast.dump(ast.parse("pytest.param(source, refused, id=case_id)", mode="eval").body)
    assert ast.dump(clause.target) == ast.dump(ast.parse("for case_id, (source, refused) in rows: pass").body[0].target)
    assert isinstance(clause.iter, ast.Call) and not clause.iter.args and not clause.iter.keywords
    assert isinstance(clause.iter.func, ast.Attribute) and clause.iter.func.attr == "items"
    mapping = clause.iter.func.value
    assert isinstance(mapping, ast.Dict)
    result = ast.literal_eval(mapping)
    assert len(result) == len(mapping.keys) == 31
    assert all(isinstance(key, str) and isinstance(value, tuple) and len(value) == 2
               and isinstance(value[0], str) and isinstance(value[1], bool)
               for key, value in result.items())
    return result


def expected_failures(baseline: str, candidate: str) -> set[str]:
    old_sources = [source(baseline, path) for path in BASELINE_TESTS]
    new_sources = [source(candidate, path) for path in GUARD_TESTS]
    old_nodes = definitions(old_sources)
    new_nodes = definitions(new_sources)
    assert set(new_nodes) == set(old_nodes) | {LOCATION_NAME}
    assert ast.dump(new_nodes[LOCATION_NAME]) == ast.dump(ast.parse(LOCATION_TEST_SOURCE).body[0])
    # Preserve both old modules' tests and harnesses. Only the explicitly
    # approved diagnostic assertion replacement may differ from the old body.
    for name, node in old_nodes.items():
        new_node = new_nodes[name]
        if name == LOCATION_NAME:
            continue  # The exact strengthened multiline body is checked above.
        if name == "test_only_a_typed_in_server_answer_is_refused":
            assert server_answer_cases(node) == server_answer_cases(new_node)
            assert ast.dump(node.args) == ast.dump(new_node.args)
            assert [ast.dump(item) for item in node.body] == [ast.dump(item) for item in new_node.body]
            assert len(node.decorator_list) == len(new_node.decorator_list) == 1
            old_decorator = ast.parse(ast.unparse(node.decorator_list[0])).body[0].value
            new_decorator = ast.parse(ast.unparse(new_node.decorator_list[0])).body[0].value
            old_decorator.args[1] = new_decorator.args[1] = ast.List(elts=[], ctx=ast.Load())
            assert ast.dump(old_decorator) == ast.dump(new_decorator)
            continue
        if name not in CASE_GROUPS:
            assert ast.dump(node) == ast.dump(new_node), (name, "changed prior definition")
            continue
        assert ast.dump(node.args) == ast.dump(new_node.args), (name, "changed signature")
        expected_node = ast.parse(STRONG_REJECT_SOURCE).body[0] if name == REJECT_NAME else node
        assert [ast.dump(item) for item in expected_node.body] == [
            ast.dump(item) for item in new_node.body
        ], (name, "changed prior test assertions")
        # The source table can grow; its decorator, parameter name and options cannot.
        assert len(node.decorator_list) == len(new_node.decorator_list) == 1
        old_decorator = ast.parse(ast.unparse(node.decorator_list[0])).body[0].value
        new_decorator = ast.parse(ast.unparse(new_node.decorator_list[0])).body[0].value
        old_decorator.args[1] = new_decorator.args[1] = ast.List(elts=[], ctx=ast.Load())
        assert ast.dump(old_decorator) == ast.dump(new_decorator), name

    names = set()
    for group, section in CASE_GROUPS.items():
        old = {}
        for text in old_sources:
            if group in definitions([text]):
                additions = parameter_cases(text, group)
                assert not old.keys() & additions.keys(), (group, "duplicate baseline IDs")
                old.update(additions)
        new = {}
        for text in new_sources:
            if any(isinstance(node, ast.FunctionDef) and node.name == group
                   for node in ast.parse(text).body):
                additions = parameter_cases(text, group)
                assert not new.keys() & additions.keys(), (group, "duplicate IDs")
                new.update(additions)
        assert old.items() <= new.items(), group
        expected_added = {case["id"]: case["source"] for case in CASE_MANIFEST[section]}
        added = {case_id: value for case_id, value in new.items() if case_id not in old}
        assert added == expected_added, (group, set(added) ^ set(expected_added))
        assert len(added) == NEW_CASE_COUNTS[group], (group, added)
        names.update(
            group + "[" + case["id"] + "]"
            for case in CASE_MANIFEST[section] if case["baseline_fails"]
        )
    assert len(names) == EXPECTED_FAILURE_COUNT
    return names


def run_variant(root: Path, label: str, selected: tuple[str, ...] = GUARD_TESTS) -> dict:
    junit = root / f"{label}.xml"
    types_file = root / f"{label}-exception-types.json"
    command = [
        sys.executable, "-m", "pytest", "--maxfail=0", "--tb=short", "-v",
        "-p", "slf001_proof_types", f"--junitxml={junit}", *selected,
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
        assert data["tests"] == TOTAL_CASES, (label, data["tests"])
        assert data["groups"] == GROUP_COUNTS, (label, data["groups"])
        assert not data["errors"], (label, data["errors"])
        assert not data["skips"], (label, data["skips"])
        assert len(set(data["case_names"])) == TOTAL_CASES
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
        expected_after = set()
        assert set(after["failures"]) == expected_after, set(after["failures"]) ^ expected_after
        assert after["exit_code"] == 0, after["exit_code"]


def diagnostic_mutations(root: Path, candidate_source: str) -> dict:
    """Prove the CLI diagnostic's missing and incorrect line both fail the test."""
    marker = ":{line}: private string target "
    assert candidate_source.count(marker) == 1
    selected = (f"{GUARD_TEST}::{LOCATION_NAME}",)
    results = {}
    mutations = {
        "diagnostic-no-line": (": private string target ", "test_sample.py: private string target"),
        "diagnostic-wrong-line": (":{line + 1}: private string target ", "test_sample.py:4: private string target"),
        "diagnostic-argument-line": (":{line + 2}: private string target ", "test_sample.py:5: private string target"),
    }
    for label, (replacement, actual_diagnostic) in mutations.items():
        try:
            mutant = candidate_source.replace(marker, replacement)
            Path(GUARD_PATH).write_text(mutant, encoding="utf-8")
            (root / f"{label}-source.py").write_text(mutant, encoding="utf-8")
            (root / f"{label}.patch").write_text(git("diff", "--", GUARD_PATH), encoding="utf-8")
            data = results[label] = run_variant(root, label, selected)
        finally:
            Path(GUARD_PATH).write_text(candidate_source, encoding="utf-8")
            (root / "diagnostic-results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        assert data["tests"] == 1 and data["case_names"] == [LOCATION_NAME]
        assert not data["errors"] and not data["skips"]
        assert data["exit_code"] == 1 and set(data["failures"]) == {LOCATION_NAME}
        assert data["exception_types"] == {LOCATION_NAME: "AssertionError"}
        failure = data["failures"][LOCATION_NAME]
        assert "tests/src/unit/test_sample.py:3: private string target" in failure
        assert actual_diagnostic in failure
    assert not git("status", "--porcelain", "--untracked-files=no")
    restored = results["diagnostic-restored"] = run_variant(root, "diagnostic-restored", selected)
    (root / "diagnostic-results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    assert restored["tests"] == 1 and restored["case_names"] == [LOCATION_NAME]
    assert restored["exit_code"] == 0
    assert not restored["failures"] and not restored["exception_types"]
    assert not restored["errors"] and not restored["skips"]
    return results


def loop_body_mutations(root: Path, candidate_source: str) -> dict:
    """Prove the stronger loop case detects a checker that skips loop bodies."""
    test_path = Path(GUARD_TEST)
    candidate_tests = test_path.read_text(encoding="utf-8")
    matches = [
        value
        for node in ast.walk(ast.parse(candidate_tests))
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant) and key.value == LOOP_CASE_ID
    ]
    assert len(matches) == 1
    [value] = matches
    assert isinstance(value, ast.Constant) and value.value == LOOP_SOURCE_NEW
    literal = ast.get_source_segment(candidate_tests, value)
    assert literal is not None and candidate_tests.count(literal) == 1
    old_tests = candidate_tests.replace(literal, json.dumps(LOOP_SOURCE_OLD), 1)
    marker = "body = self.block(node.body, current)"
    assert candidate_source.count(marker) == 1
    mutant = candidate_source.replace(marker, 'body = {"normal": current}', 1)
    name = f"{REJECT_NAME}[{LOOP_CASE_ID}]"
    selected = (f"{GUARD_TEST}::{name}",)
    results = {}
    variants = (
        ("loop-body-old-case", old_tests, mutant, False),
        ("loop-body-strong-case", candidate_tests, mutant, True),
        ("loop-body-restored", candidate_tests, candidate_source, False),
    )
    for label, tests, guard, fails in variants:
        try:
            test_path.write_text(tests, encoding="utf-8")
            Path(GUARD_PATH).write_text(guard, encoding="utf-8")
            (root / f"{label}-test-source.py").write_text(tests, encoding="utf-8")
            (root / f"{label}-guard-source.py").write_text(guard, encoding="utf-8")
            (root / f"{label}.patch").write_text(
                git("diff", "--", GUARD_PATH, GUARD_TEST), encoding="utf-8"
            )
            data = results[label] = run_variant(root, label, selected)
        finally:
            Path(GUARD_PATH).write_text(candidate_source, encoding="utf-8")
            test_path.write_text(candidate_tests, encoding="utf-8")
            (root / "loop-body-results.json").write_text(
                json.dumps(results, indent=2), encoding="utf-8"
            )
        assert not git("status", "--porcelain", "--untracked-files=no")
        assert data["tests"] == 1 and data["case_names"] == [name]
        assert not data["errors"] and not data["skips"]
        assert data["exit_code"] == int(fails)
        assert set(data["failures"]) == ({name} if fails else set())
        assert data["exception_types"] == ({name: "AssertionError"} if fails else {})
    return results


def existing_source(revision: str, path: str) -> str | None:
    return source(revision, path) if git("ls-tree", "--name-only", revision, "--", path) else None


def restore_sources(sources: dict[str, str | None]) -> None:
    for path, text in sources.items():
        if text is None:
            Path(path).unlink(missing_ok=True)
        else:
            Path(path).write_text(text, encoding="utf-8")


def boundary_proof(root: Path, revision: str, candidate_guards: dict) -> None:
    """Reproduce targeted regressions against their exact introducing guard."""
    groups: dict[str, dict[str, bool]] = {}
    for group, section in CASE_GROUPS.items():
        for case in CASE_MANIFEST[section]:
            if "boundary_fails" in case:
                reference = case.get("boundary_revision", revision)
                groups.setdefault(reference, {})[group + "[" + case["id"] + "]"] = case["boundary_fails"]
    assert groups
    for reference, cases in groups.items():
        label = "boundary" if reference == revision else f"boundary-{reference[:8]}"
        revision_proof(root, reference, candidate_guards, cases, label)


def revision_proof(root: Path, revision: str, candidate_guards: dict, cases: dict[str, bool], label: str) -> None:
    assert re.fullmatch(r"[0-9a-f]{40}", revision), revision
    reference_guards = {path: existing_source(revision, path) for path in GUARD_PATHS}
    assert cases and any(cases.values()), "No targeted intermediate-regression cases"
    expected = {name for name, fails in cases.items() if fails}
    evidence = {
        "guard_revision": revision,
        "guard_blobs": {path: git("rev-parse", f"{revision}:{path}") for path in GUARD_PATHS},
        "expected_failure_names": sorted(expected),
    }
    (root / f"{label}-sources.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    restore_sources(reference_guards)
    try:
        data = run_variant(root, label, tuple(f"{GUARD_TEST}::{name}" for name in cases))
    finally:
        restore_sources(candidate_guards)
    assert not git("status", "--porcelain", "--untracked-files=no")
    (root / f"{label}-results.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    assert data["tests"] == len(cases) and set(data["case_names"]) == set(cases)
    assert data["exit_code"] == 1 and set(data["failures"]) == expected
    assert data["exception_types"] == {name: "AssertionError" for name in expected}
    assert not data["errors"] and not data["skips"]


def main() -> None:
    baseline_only = os.environ.get("PROBE_BASELINE_ONLY", "false") == "true"
    diagnostic_only = os.environ.get("PROBE_DIAGNOSTIC_LINES_ONLY", "false") == "true"
    boundary_baseline = os.environ.get("PROBE_BOUNDARY_BASELINE", "")
    assert not (baseline_only and diagnostic_only)
    baseline = os.environ["PROBE_BASELINE"]
    assert baseline == BASELINE, (baseline, BASELINE)
    candidate = os.environ["PROBE_CANDIDATE"]
    assert re.fullmatch(r"[0-9a-f]{40}", baseline), baseline
    assert re.fullmatch(r"[0-9a-f]{40}", candidate), candidate
    assert git("rev-parse", "HEAD") == candidate
    assert not git("status", "--porcelain", "--untracked-files=no")
    root = Path(os.environ["PROBE_ROOT"])
    root.mkdir(parents=True, exist_ok=True)
    (root / "slf001_proof_types.py").write_text(EXCEPTION_PLUGIN, encoding="utf-8")
    candidate_guards = {path: existing_source(candidate, path) for path in GUARD_PATHS}
    baseline_guards = {path: existing_source(baseline, path) for path in GUARD_PATHS}
    assert all(candidate_guards[path] is not None and baseline_guards[path] is not None
               for path in (GUARD_PATH, GUARD_SUPPORT))
    assert baseline_guards[GUARD_FLOW] is not None
    assert candidate_guards[GUARD_FLOW] is not None or baseline_only
    assert candidate_guards != baseline_guards or baseline_only
    expected = expected_failures(baseline, candidate)
    evidence = {
        "baseline_only": baseline_only,
        "diagnostic_lines_only": diagnostic_only,
        "boundary_baseline": boundary_baseline,
        "baseline": baseline,
        "candidate": candidate,
        "driver": git("-C", "../proof", "rev-parse", "HEAD"),
        "blobs": {
            revision: {
                path: git("rev-parse", f"{revision}:{path}")
                for path in (*GUARD_PATHS, *BASELINE_TESTS)
                if git("ls-tree", "--name-only", revision, "--", path)
            } for revision in (baseline, candidate)
        },
        "selected_modules": GUARD_TESTS,
        "preserved_baseline_modules": BASELINE_TESTS,
        "preserved_baseline_cases": sum(BASELINE_GROUP_COUNTS.values()),
        "missing_guard_paths": {
            "baseline": [path for path, text in baseline_guards.items() if text is None],
            "candidate": [path for path, text in candidate_guards.items() if text is None],
        },
        "expected_group_counts": GROUP_COUNTS,
        "expected_failure_group_counts": FAILURE_GROUP_COUNTS,
        "expected_baseline_failure_names": sorted(expected),
    }
    (root / "sources.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    for label, revision, paths in (("baseline", baseline, BASELINE_TESTS),
                                   ("candidate", candidate, GUARD_TESTS)):
        for path in paths:
            (root / f"{label}-{Path(path).name}").write_text(source(revision, path), encoding="utf-8")
    for label, guards in (("baseline", baseline_guards), ("candidate", candidate_guards)):
        for path, text in guards.items():
            if text is not None:
                (root / f"{label}-{Path(path).name}").write_text(text, encoding="utf-8")
    results = {}
    try:
        if boundary_baseline:
            boundary_proof(root, boundary_baseline, candidate_guards)
        if not diagnostic_only:
            try:
                restore_sources(baseline_guards)
                (root / "baseline-overlay.patch").write_text(git("diff", "--", *GUARD_PATHS), encoding="utf-8")
                results["baseline"] = run_variant(root, "baseline")
            finally:
                restore_sources(candidate_guards)
            assert not git("status", "--porcelain", "--untracked-files=no")
            if not baseline_only:
                results["candidate"] = run_variant(root, "candidate")
            validate(results, expected, baseline_only)
        if not baseline_only:
            candidate_source = candidate_guards[GUARD_PATH]
            assert isinstance(candidate_source, str)
            results.update(diagnostic_mutations(root, candidate_source))
            if not diagnostic_only:
                results.update(loop_body_mutations(root, candidate_source))
    finally:
        restore_sources(candidate_guards)
        (root / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    assert not git("status", "--porcelain", "--untracked-files=no")
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
        handle.write(f"Regression proof: all {TOTAL_CASES} merged rule cases\n\n```json\n" + rendered + "\n```\n")


if __name__ == "__main__":
    main()
