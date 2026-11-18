#!/usr/bin/env python3
"""Reject private names reached through strings in unit and app tests.

Ruff checks attribute syntax and the private-import guard checks imports. This
checks reflection and patch targets, including lexical constants, concatenation,
f-strings and patch.multiple keys. It never imports the files it reads and has
no exemption list. Unknown runtime values still require review; a known private
segment in an otherwise dynamic f-string is checked too.
"""

from __future__ import annotations

import argparse
import ast
from collections.abc import Iterator
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts import private_test_bindings as binding_rules
else:
    import private_test_bindings as binding_rules


REPO = Path(__file__).resolve().parent.parent
TREES = ("tests/src/unit", "tests/addon")
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_BRANCHES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.TryStar,
    ast.Match,
    ast.With,
    ast.AsyncWith,
    ast.Delete,
)
_SCOPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
    *_COMPREHENSIONS,
)


_ClassValues = binding_rules.ClassValues
_State = binding_rules.State
_Env = binding_rules.Env


def _comprehension_nodes(
    scope: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
) -> Iterator[ast.AST]:
    """The first iterable belongs to the outer scope, not this body."""
    if isinstance(scope, ast.DictComp):
        yield scope.key
        yield scope.value
    else:
        yield scope.elt
    first, *remaining = scope.generators
    yield first.target
    yield from first.ifs
    yield from remaining


def _nodes(
    scope: ast.AST, *, descend_assignments: bool = True, descend_branches: bool = True
) -> Iterator[ast.AST]:
    """Nodes in one lexical scope, leaving nested bodies to their own scope."""
    children: Iterator[ast.AST]
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        children = iter(scope.body)
    elif isinstance(scope, ast.Lambda):
        children = iter([scope.body])
    elif isinstance(scope, _COMPREHENSIONS):
        children = _comprehension_nodes(scope)
    else:
        children = ast.iter_child_nodes(scope)
    for child in children:
        yield child
        if (
            isinstance(child, _SCOPES)
            or (
                not descend_assignments
                and isinstance(child, (ast.Assign, ast.AnnAssign))
            )
            or (not descend_branches and isinstance(child, _BRANCHES))
        ):
            continue
        yield from _nodes(
            child,
            descend_assignments=descend_assignments,
            descend_branches=descend_branches,
        )


def _headers(scope: ast.AST) -> Iterator[ast.expr]:
    """Decorators, bases and defaults execute before entering the new scope."""
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        yield from scope.decorator_list
    if isinstance(scope, ast.ClassDef):
        yield from scope.bases
        yield from (keyword.value for keyword in scope.keywords)
    if isinstance(scope, _COMPREHENSIONS):
        yield scope.generators[0].iter
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        yield from scope.args.defaults
        yield from (value for value in scope.args.kw_defaults if value is not None)
        yield from (
            node.annotation
            for node in ast.walk(scope.args)
            if isinstance(node, ast.arg) and node.annotation is not None
        )
    if (
        isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
        and scope.returns is not None
    ):
        yield scope.returns


def _bindings(
    scope: ast.AST,
    parent_values: dict[str, binding_rules.Value],
    parent_aliases: binding_rules.Aliases,
    initial_values: dict[str, set[str]] | None = None,
) -> tuple[dict[str, binding_rules.Value], binding_rules.Aliases]:
    nodes = list(_nodes(scope))
    if isinstance(scope, ast.ClassDef):
        values, aliases = binding_rules.class_bindings(
            parent_values, parent_aliases, nodes
        )
        return _ClassValues(values), aliases
    local_names = binding_rules.bound_names(nodes)
    values = binding_rules.ScopeValues(parent_values)
    aliases = dict(parent_aliases)
    imported: binding_rules.Aliases = {}
    binding_rules.import_aliases(nodes, imported)
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        parameters = [
            *scope.args.posonlyargs,
            *scope.args.args,
            *scope.args.kwonlyargs,
            scope.args.vararg,
            scope.args.kwarg,
        ]
        local_names.update(
            parameter.arg for parameter in parameters if parameter is not None
        )
    for name in local_names:
        values.pop(name, None)
        aliases.pop(name, None)
    aliases.update(imported)
    values.update(
        (name, binding_rules.Value(frozenset(items)))
        for name, items in (initial_values or {}).items()
    )
    binding_rules.extend_values(list(binding_rules.assignments(nodes)), values, aliases)
    return values, aliases


def _literal_keyword(mapping: ast.expr, name: str) -> ast.expr | None:
    if not isinstance(mapping, ast.Dict):
        return None
    for key, value in reversed(list(zip(mapping.keys, mapping.values, strict=True))):
        if key is None:
            found = _literal_keyword(value, name)
            if found is not None:
                return found
        elif isinstance(key, ast.Constant) and key.value == name:
            return value
    return None


def _argument(call: ast.Call, index: int, name: str) -> ast.expr | None:
    if len(call.args) > index:
        return call.args[index]
    for item in call.keywords:
        if item.arg == name:
            return item.value
        if item.arg is None:
            found = _literal_keyword(item.value, name)
            if found is not None:
                return found
    return None


def _own_object(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Name) and node.id in {"self", "cls"}


def _targets(
    call: ast.Call,
    aliases: binding_rules.Aliases,
    values: dict[str, binding_rules.Value],
) -> set[str]:
    return set().union(
        *(
            _named_targets(call, name, values)
            for name in binding_rules.dotted(call.func, aliases)
        )
    )


def _named_targets(
    call: ast.Call, name: str, values: dict[str, binding_rules.Value]
) -> set[str]:
    operation = name.rsplit(".", 1)[-1]
    target = _argument(call, 0, "target")
    target_strings = binding_rules.argument_strings(call, 0, "target", values)
    patch_method = name.rsplit(".", 2)[-2:]
    if operation == "patch":
        return target_strings
    if patch_method == ["patch", "dict"]:
        return binding_rules.argument_strings(call, 0, "in_dict", values)
    if patch_method == ["patch", "multiple"]:
        keys = {item.arg for item in call.keywords if item.arg is not None}
        keys.update(
            set().union(
                *(
                    binding_rules.mapping_keys(item.value, values)
                    for item in call.keywords
                    if item.arg is None
                )
            )
        )
        return target_strings | (set() if _own_object(target) else keys)
    if patch_method == ["patch", "object"]:
        return target_strings | (
            set()
            if _own_object(target)
            else binding_rules.argument_strings(call, 1, "attribute", values)
        )
    if operation in {"getattr", "hasattr", "setattr", "delattr"}:
        if _own_object(target):
            return set()
        # pytest.MonkeyPatch also accepts a fully qualified string as its
        # first argument; then its second argument is the replacement value.
        if operation in {"setattr", "delattr"} and name not in {
            operation,
            f"builtins.{operation}",
        }:
            dotted = target_strings
            if dotted:
                return dotted
        return binding_rules.argument_strings(call, 1, "name", values)
    return set()


def _private(target: str) -> bool:
    parts = target.replace(":", ".").split(".")
    for index, part in enumerate(parts):
        if not part.startswith("_") or (
            len(part) > 4 and part.startswith("__") and part.endswith("__")
        ):
            continue
        if index == 1 and parts[0] == "ha_mcp" and part == "_vendor":
            continue
        return True
    return False


def _comprehension_values(
    scope: ast.AST, values: dict[str, binding_rules.Value]
) -> dict[str, set[str]]:
    if isinstance(scope, _COMPREHENSIONS):
        first = scope.generators[0]
        if isinstance(first.target, ast.Name):
            return {first.target.id: binding_rules.iterable_strings(first.iter, values)}
    return {}


def _bind_definition(
    name: str,
    values: dict[str, binding_rules.Value],
    aliases: binding_rules.Aliases,
    assigned_names: set[str],
) -> None:
    aliases[name] = frozenset({""})
    if isinstance(values, _ClassValues) or name not in assigned_names:
        # Clear an inherited value, but retain the scope's assignment
        # alternatives, including assignments after this definition.
        values.pop(name, None)
    if isinstance(values, _ClassValues):
        _store_class_name(values, aliases, name)


def _scan_assignment_target(
    target: ast.expr,
    resolved: frozenset[str] | None,
    values: dict[str, binding_rules.Value],
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
    assigned_value: binding_rules.Value = binding_rules.Value(),
) -> Iterator[tuple[int, str]]:
    if isinstance(target, ast.Name):
        if resolved is not None:
            aliases[target.id] = resolved
            if isinstance(values, _ClassValues):
                values[target.id] = assigned_value
                _store_class_name(values, aliases, target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts:
            # Unpacked elements are unknown, not inherited callable aliases.
            yield from _scan_assignment_target(
                item, frozenset({""}), values, aliases, closure
            )
    elif isinstance(target, ast.Starred):
        yield from _scan_assignment_target(
            target.value, frozenset({""}), values, aliases, closure
        )
    else:
        yield from _scan_expression(target, values, aliases, closure)


def _scan_assignment(
    node: ast.Assign | ast.AnnAssign,
    values: dict[str, binding_rules.Value],
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
) -> Iterator[tuple[int, str]]:
    value = node.value
    if isinstance(value, ast.Lambda):
        for header in _headers(value):
            yield from _scan_expression(header, values, aliases, closure)
    elif value is not None:
        yield from _scan_expression(value, values, aliases, closure)
    # Resolve a chained assignment's RHS once, then evaluate and bind targets
    # left to right: a later target expression can see an earlier new binding.
    targets: list[ast.expr] = (
        node.targets if isinstance(node, ast.Assign) else [node.target]
    )
    for target, resolved, assigned_value in binding_rules.prepared_assignment(
        targets, value, values, aliases
    ):
        yield from _scan_assignment_target(
            target, resolved, values, aliases, closure, assigned_value
        )
    if isinstance(value, ast.Lambda):
        # Defaults ran before assignment. The deferred body sees the new binding,
        # except in a class, whose namespace is not captured by the lambda.
        yield from _scan_scope(value, *closure)
    if isinstance(node, ast.AnnAssign):
        yield from _scan_expression(node.annotation, values, aliases, closure)


def _scan_nested_scope(
    node: ast.AST,
    values: dict[str, binding_rules.Value],
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
    assigned_names: set[str],
) -> Iterator[tuple[int, str]]:
    for header in _headers(node):
        yield from _scan_expression(header, values, aliases, closure)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        _bind_definition(node.name, values, aliases, assigned_names)
    first_values = _comprehension_values(node, values)
    # Methods and comprehension bodies do not capture a class namespace;
    # their decorators/defaults/first iterable still execute in it.
    yield from _scan_scope(node, *closure, first_values)
    if isinstance(node, ast.ClassDef):
        _bind_definition(node.name, values, aliases, assigned_names)


_Outcomes = dict[str, _State]
_Bindings = list[tuple[ast.expr, frozenset[str] | None, binding_rules.Value]]


def _add_outcome(outcomes: _Outcomes, kind: str, state: _State) -> None:
    if kind in outcomes:
        outcomes[kind].merge(state)
    else:
        outcomes[kind] = state.copy()


def _store_class_name(
    values: _ClassValues, aliases: binding_rules.Aliases, name: str
) -> None:
    values.bound |= {name}
    if name in values.class_globals:
        owner = binding_rules.module_bindings(*values.closure)
    elif name in values.class_nonlocals:
        owner = values.closure
    else:
        return
    owner[0].pop(name, None)
    if name in values:
        owner[0][name] = values[name]
    owner[1][name] = aliases.get(name, frozenset({name}))


def _restore_class_fallback(
    name: str, values: _ClassValues, aliases: binding_rules.Aliases, closure: _Env
) -> None:
    values.pop(name, None)
    values.bound -= {name}
    if name in values.class_nonlocals | values.class_globals:
        aliases[name] = frozenset({"" if name in values.class_nonlocals else name})
        _store_class_name(values, aliases, name)
        return
    module = binding_rules.module_bindings(*closure)
    if name in module[0]:
        values[name] = module[0][name]
    aliases[name] = module[1].get(name, frozenset({name}))


class _ClassFlow:
    """Bounded abstract class execution with separate control-flow outcomes."""

    def __init__(self) -> None:
        self.cache: dict[tuple, _Outcomes] = {}
        self.findings: set[tuple[int, str]] = set()

    def expression(self, node: ast.expr, state: _State) -> None:
        self.findings.update(
            _scan_expression(node, state.values, state.aliases, state.closure)
        )

    def block(self, body: list[ast.stmt], incoming: _State) -> _Outcomes:
        key = (tuple(map(id, body)), incoming.key())
        if key in self.cache:
            return {kind: state.copy() for kind, state in self.cache[key].items()}
        if len(self.cache) >= 16384:
            raise ValueError("too many distinct class flow states")
        outcomes = {"normal": incoming.copy()}
        for node in body:
            state = outcomes.pop("normal", None)
            if state is None:
                break
            state.refresh()
            for kind, result in self.statement(node, state).items():
                _add_outcome(outcomes, kind, result)
        self.cache[key] = {kind: state.copy() for kind, state in outcomes.items()}
        return outcomes

    def statement(self, node: ast.stmt, state: _State) -> _Outcomes:
        before = state.copy()
        outcomes = self.dispatch(node, state)
        if isinstance(
            node, (ast.ClassDef, ast.If, ast.For, ast.AsyncFor, ast.While, ast.Match)
        ):
            # Evaluating a class header, condition, iterable or subject can fail
            # before any selected suite runs.
            _add_outcome(outcomes, "raise", before)
        return outcomes

    def dispatch(self, node: ast.stmt, state: _State) -> _Outcomes:
        if isinstance(node, ast.ClassDef):
            return self.definition(node, state)
        if isinstance(node, ast.If):
            self.expression(node.test, state)
            choices = [node.body, node.orelse]
            if isinstance(node.test, ast.Constant):
                choices = [node.body if node.test.value else node.orelse]
            return self.choices(choices, state)
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            return self.loop(node, state)
        if isinstance(node, (ast.Try, ast.TryStar)):
            return self.try_block(node, state)
        if isinstance(node, ast.Match):
            return self.match(node, state)
        if isinstance(node, (ast.With, ast.AsyncWith)):
            return self.with_items(node.items, node.body, state)
        if isinstance(node, (ast.Break, ast.Continue)):
            return {"break" if isinstance(node, ast.Break) else "continue": state}
        return self.simple_statement(node, state)

    def simple_statement(self, node: ast.stmt, state: _State) -> _Outcomes:
        before = state.copy()
        if isinstance(node, ast.Delete):
            self.delete(node.targets, state)
        else:
            nodes: Iterator[ast.AST] = chain(
                [node], _nodes(node, descend_assignments=False)
            )
            if isinstance(node, (*_SCOPES, ast.Assign, ast.AnnAssign)):
                nodes = iter([node])
            self.findings.update(
                _scan_nodes(nodes, state.values, state.aliases, state.closure, set())
            )
        if isinstance(node, ast.Raise):
            return {"raise": state}
        # Retain a conservative interrupted path even for an assignment whose
        # RHS is literal; static analysis does not execute user expressions.
        return (
            {"normal": state, "raise": before}
            if not isinstance(node, (ast.Pass, ast.Global, ast.Nonlocal))
            else {"normal": state}
        )

    def definition(self, node: ast.ClassDef, state: _State) -> _Outcomes:
        for header in _headers(node):
            self.expression(header, state)
        values, aliases = _bindings(node, *state.closure)
        assert isinstance(values, _ClassValues)
        child = _State(values, aliases, state.closure).copy()
        outcomes: _Outcomes = {}
        for kind, result in self.block(node.body, child).items():
            parent = state.copy()
            binding_rules.publish_context(parent.closure, result.closure)
            if kind == "normal":
                _bind_definition(node.name, parent.values, parent.aliases, set())
            _add_outcome(outcomes, kind, parent)
        return outcomes

    def choices(self, bodies: list[list[ast.stmt]], state: _State) -> _Outcomes:
        outcomes: _Outcomes = {}
        for body in bodies:
            for kind, result in self.block(body, state).items():
                _add_outcome(outcomes, kind, result)
        return outcomes

    def delete(self, targets: list[ast.expr], state: _State) -> None:
        for target in targets:
            if isinstance(target, ast.Name):
                _restore_class_fallback(
                    target.id, state.values, state.aliases, state.closure
                )
            elif isinstance(target, (ast.Tuple, ast.List)):
                self.delete(target.elts, state)
            else:
                self.expression(target, state)

    def bind(self, bindings: _Bindings, state: _State) -> None:
        for target, resolved, value in bindings:
            self.findings.update(
                _scan_assignment_target(
                    target, resolved, state.values, state.aliases, state.closure, value
                )
            )

    def loop_plan(
        self, node: ast.For | ast.AsyncFor | ast.While, state: _State
    ) -> tuple[list[_Bindings] | None, _Bindings]:
        expression = node.test if isinstance(node, ast.While) else node.iter
        self.expression(expression, state)
        literal = isinstance(expression, (ast.List, ast.Tuple)) and not any(
            isinstance(item, ast.Starred) for item in expression.elts
        )
        iterations: list[_Bindings] | None = None
        bindings: _Bindings = []
        if not isinstance(node, ast.While):
            bindings = binding_rules.prepared_assignment(
                [node.target], ast.Constant(value=None), state.values, state.aliases
            )
            if isinstance(node.target, ast.Name):
                bindings = [
                    (
                        node.target,
                        frozenset({""}),
                        binding_rules.Value(
                            frozenset(
                                binding_rules.iterable_strings(expression, state.values)
                            )
                        ),
                    )
                ]
            if literal and isinstance(expression, (ast.List, ast.Tuple)):
                iterations = [
                    binding_rules.prepared_assignment(
                        [node.target], item, state.values, state.aliases
                    )
                    for item in expression.elts
                ]
        return iterations, bindings

    def loop_step(
        self,
        node: ast.For | ast.AsyncFor | ast.While,
        current: _State,
        bindings: _Bindings,
        outcomes: _Outcomes,
    ) -> _State | None:
        if isinstance(node, ast.While):
            self.expression(node.test, current)
        self.bind(bindings, current)
        body = self.block(node.body, current)
        for kind in ("raise", "break"):
            if kind in body:
                _add_outcome(
                    outcomes, "normal" if kind == "break" else kind, body[kind]
                )
        carried: _Outcomes = {}
        for kind in ("normal", "continue"):
            if kind in body:
                _add_outcome(carried, "normal", body[kind])
        return carried.get("normal")

    def loop(
        self, node: ast.For | ast.AsyncFor | ast.While, state: _State
    ) -> _Outcomes:
        iterations, bindings = self.loop_plan(node, state)
        outcomes: _Outcomes = {}
        exhaustion: _Outcomes = {}
        current: _State | None = state.copy()
        if iterations is None and self.may_skip(node):
            _add_outcome(exhaustion, "normal", state)
        if (
            isinstance(node, ast.While)
            and isinstance(node.test, ast.Constant)
            and not node.test.value
        ):
            iterations = []
        seen: set[tuple] = set()
        for index in range(len(iterations) if iterations is not None else 256):
            assert current is not None
            current = self.loop_step(
                node,
                current,
                iterations[index] if iterations is not None else bindings,
                outcomes,
            )
            if current is None:
                break
            if iterations is None:
                if not (
                    isinstance(node, ast.While)
                    and isinstance(node.test, ast.Constant)
                    and node.test.value
                ):
                    _add_outcome(exhaustion, "normal", current)
                signature = current.key()
                if signature in seen:
                    break
                seen.add(signature)
        else:
            if iterations is None:
                raise ValueError("too many static alternatives in a loop")
        return self.loop_exits(
            node, outcomes, exhaustion, current, exact=iterations is not None
        )

    def loop_exits(
        self,
        node: ast.For | ast.AsyncFor | ast.While,
        outcomes: _Outcomes,
        exhaustion: _Outcomes,
        current: _State | None,
        *,
        exact: bool,
    ) -> _Outcomes:
        if exact and current is not None:
            _add_outcome(exhaustion, "normal", current)
        if "normal" in exhaustion:
            for kind, result in self.block(node.orelse, exhaustion["normal"]).items():
                _add_outcome(outcomes, kind, result)
        return outcomes

    @staticmethod
    def may_skip(node: ast.For | ast.AsyncFor | ast.While) -> bool:
        if isinstance(node, ast.While):
            return not isinstance(node.test, ast.Constant) or not node.test.value
        if isinstance(node.iter, (ast.List, ast.Tuple, ast.Set)):
            return not any(not isinstance(item, ast.Starred) for item in node.iter.elts)
        return not (
            isinstance(node.iter, ast.Dict)
            and any(key is not None for key in node.iter.keys)
        )

    def handlers(self, node: ast.Try | ast.TryStar, raised: _State) -> _Outcomes:
        outcomes: _Outcomes = {}
        _add_outcome(outcomes, "raise", raised)
        for handler in node.handlers:
            caught = raised.copy()
            if handler.type is not None:
                self.expression(handler.type, caught)
            if handler.name is not None:
                self.bind(
                    [
                        (
                            ast.Name(id=handler.name),
                            frozenset({""}),
                            binding_rules.Value(),
                        )
                    ],
                    caught,
                )
            handled = self.block(handler.body, caught)
            for kind, result in handled.items():
                if handler.name is not None:
                    _restore_class_fallback(
                        handler.name, result.values, result.aliases, result.closure
                    )
                _add_outcome(outcomes, kind, result)
                if isinstance(node, ast.TryStar):
                    raised.merge(result)
        return outcomes

    def try_block(self, node: ast.Try | ast.TryStar, state: _State) -> _Outcomes:
        body = self.block(node.body, state)
        outcomes: _Outcomes = {}
        if "normal" in body:
            outcomes.update(self.block(node.orelse, body["normal"]))
        for kind in ("break", "continue"):
            if kind in body:
                _add_outcome(outcomes, kind, body[kind])
        if "raise" in body:
            for kind, result in self.handlers(node, body["raise"]).items():
                _add_outcome(outcomes, kind, result)
        if not node.finalbody:
            return outcomes
        finished: _Outcomes = {}
        for previous, incoming in outcomes.items():
            for kind, result in self.block(node.finalbody, incoming).items():
                _add_outcome(finished, previous if kind == "normal" else kind, result)
        return finished

    def match_case(
        self,
        case: ast.match_case,
        subject: ast.expr,
        subjects: _State,
        remaining: _State,
    ) -> tuple[_State, _State | None, bool]:
        branch = remaining.copy()
        captures = {
            name: (binding_rules.Value(), frozenset({""}))
            for name in binding_rules.bound_names(list(ast.walk(case.pattern)))
        }
        captures.update(
            binding_rules.pattern_bindings(
                case.pattern, subject, subjects.values, subjects.aliases
            )
        )
        self.bind(
            [
                (ast.Name(id=name), aliases, value)
                for name, (value, aliases) in captures.items()
            ],
            branch,
        )
        if case.guard is not None:
            self.expression(case.guard, branch)
        irrefutable = (
            isinstance(case.pattern, ast.MatchAs) and case.pattern.pattern is None
        )
        guard = bool(case.guard.value) if isinstance(case.guard, ast.Constant) else None
        pending: _Outcomes = {}
        if not irrefutable:
            _add_outcome(pending, "normal", remaining)
        if case.guard is not None and guard is not True:
            _add_outcome(pending, "normal", branch)
        return branch, pending.get("normal"), guard is not False

    def match(self, node: ast.Match, state: _State) -> _Outcomes:
        self.expression(node.subject, state)
        subjects = state.copy()
        remaining: _State | None = state
        outcomes: _Outcomes = {}
        for case in node.cases:
            if remaining is None:
                break
            branch, remaining, accepted = self.match_case(
                case, node.subject, subjects, remaining
            )
            if accepted:
                for kind, result in self.block(case.body, branch).items():
                    _add_outcome(outcomes, kind, result)
        if remaining is not None:
            _add_outcome(outcomes, "normal", remaining)
        return outcomes

    def with_items(
        self, items: list[ast.withitem], body: list[ast.stmt], state: _State
    ) -> _Outcomes:
        if not items:
            return self.block(body, state)
        item, *remaining = items
        self.expression(item.context_expr, state)
        entry = state.copy()
        if item.optional_vars is not None:
            self.bind(
                binding_rules.prepared_assignment(
                    [item.optional_vars],
                    ast.Constant(value=None),
                    state.values,
                    state.aliases,
                ),
                state,
            )
        outcomes = self.with_items(remaining, body, state)
        # A successfully entered manager may suppress an inner exception.
        if "raise" in outcomes:
            _add_outcome(outcomes, "normal", outcomes["raise"])
        _add_outcome(outcomes, "raise", entry)
        return outcomes


def _scan_node(
    node: ast.AST,
    values: dict[str, binding_rules.Value],
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
    assigned_names: set[str],
) -> Iterator[tuple[int, str]]:
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        yield from _scan_assignment(node, values, aliases, closure)
    elif isinstance(node, _SCOPES):
        yield from _scan_nested_scope(node, values, aliases, closure, assigned_names)
    elif isinstance(node, ast.Call):
        for target in sorted(_targets(node, aliases, values)):
            if _private(target):
                yield node.lineno, target
    else:
        imported: binding_rules.Aliases = {}
        binding_rules.import_aliases([node], imported)
        aliases.update(imported)
        if isinstance(values, _ClassValues):
            for name in imported:
                values.pop(name, None)
                _store_class_name(values, aliases, name)


def _scan_nodes(
    nodes: Iterator[ast.AST],
    values: dict[str, binding_rules.Value],
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
    assigned_names: set[str],
) -> Iterator[tuple[int, str]]:
    for node in nodes:
        yield from _scan_node(node, values, aliases, closure, assigned_names)


def _scan_expression(
    expression: ast.expr,
    values: dict[str, binding_rules.Value],
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
) -> Iterator[tuple[int, str]]:
    # Expressions do not create lexical scopes. Only their nested functions or
    # comprehensions do; preserve the surrounding class's closure distinction.
    descendants = (
        iter(())
        if isinstance(expression, _SCOPES)
        else _nodes(expression, descend_assignments=False)
    )
    yield from _scan_nodes(
        chain([expression], descendants), values, aliases, closure, set()
    )


def _scan_scope(
    scope: ast.AST,
    values: dict[str, binding_rules.Value],
    aliases: binding_rules.Aliases,
    initial_values: dict[str, set[str]] | None = None,
) -> Iterator[tuple[int, str]]:
    closure = (values, aliases)
    values, aliases = _bindings(scope, values, aliases, initial_values)
    if isinstance(scope, ast.ClassDef):
        assert isinstance(values, _ClassValues)
        analyzer = _ClassFlow()
        outcomes = analyzer.block(scope.body, _State(values, aliases, closure).copy())
        yield from sorted(analyzer.findings)
        if "normal" in outcomes:
            binding_rules.publish_context(closure, outcomes["normal"].closure)
        return
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        values, aliases = binding_rules.copy_context((values, aliases))
    if isinstance(scope, ast.Module) and isinstance(values, binding_rules.ScopeValues):
        values.module_bindings = (values, aliases)
    assigned_names = {
        name for name, _, _ in binding_rules.assignments(list(_nodes(scope)))
    }
    closure = (values, aliases)
    yield from _scan_nodes(
        _nodes(scope, descend_assignments=False),
        values,
        aliases,
        closure,
        assigned_names,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--repo", type=Path, default=REPO)
    args = parser.parse_args(argv)
    violations: list[str] = []
    for tree in TREES:
        for path in sorted((args.repo / tree).rglob("*.py")):
            try:
                source = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                violations.extend(
                    f"{path.relative_to(args.repo)}:{line}: private string target {target!r}"
                    for line, target in _scan_scope(source, {}, {})
                )
            except (SyntaxError, ValueError) as error:
                violations.append(f"{path.relative_to(args.repo)}: {error}")
    if violations:
        print("\n".join(dict.fromkeys(violations)))
        print("Use the public entry point or real configuration; no test is exempt.")
        return 1
    print("No private string targets in unit or app tests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
