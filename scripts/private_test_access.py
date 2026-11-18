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


class _ClassValues(binding_rules.ScopeValues):
    """Class namespace values, updated in statement order rather than prebound."""

    def __init__(self, values: dict[str, binding_rules.Value]) -> None:
        super().__init__(values)
        self.observed_values = binding_rules.ScopeValues(values)
        self.observed_aliases: binding_rules.Aliases = {}
        self.has_alias_snapshot = False

    def observe(self, aliases: binding_rules.Aliases) -> None:
        binding_rules.union_values(self.observed_values, self)
        if not self.has_alias_snapshot:
            self.observed_aliases.update(aliases)
            self.has_alias_snapshot = True
            return
        # A missing name in an actual earlier/later snapshot uses its ordinary
        # lookup, but a shadowed name in the first snapshot never did.
        for name in self.observed_aliases.keys() | aliases.keys():
            fallback = frozenset({name})
            previous = self.observed_aliases.get(name, fallback)
            self.observed_aliases[name] = previous | aliases.get(name, fallback)


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


def _argument(call: ast.Call, index: int, name: str) -> ast.expr | None:
    if len(call.args) > index:
        return call.args[index]
    return next((item.value for item in call.keywords if item.arg == name), None)


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
    if operation == "patch":
        return binding_rules.strings(target, values)
    if name.endswith("patch.dict"):
        return binding_rules.strings(_argument(call, 0, "in_dict"), values)
    if name.endswith("patch.multiple"):
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
        return binding_rules.strings(target, values) | (
            set() if _own_object(target) else keys
        )
    if name.endswith("patch.object"):
        return binding_rules.strings(target, values) | (
            set()
            if _own_object(target)
            else binding_rules.strings(_argument(call, 1, "attribute"), values)
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
            dotted = binding_rules.strings(target, values)
            if dotted:
                return dotted
        return binding_rules.strings(_argument(call, 1, "name"), values)
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
            return {
                first.target.id: binding_rules.strings(first.iter, values)
                | binding_rules.mapping_keys(first.iter, values)
            }
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


def _scan_class_block(
    body: list[ast.stmt],
    values: _ClassValues,
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
) -> Iterator[tuple[int, str]]:
    nodes = _nodes(
        ast.Module(body=body, type_ignores=[]),
        descend_assignments=False,
        descend_branches=False,
    )
    yield from _scan_nodes(nodes, values, aliases, closure, set())


def _join_class_paths(
    values: _ClassValues,
    aliases: binding_rules.Aliases,
    paths: list[tuple[_ClassValues, binding_rules.Aliases]],
) -> None:
    joined: dict[str, binding_rules.Value] = {}
    names = set().union(*(path_aliases.keys() for _, path_aliases in paths))
    joined_aliases: binding_rules.Aliases = {}
    for path_values, path_aliases in paths:
        binding_rules.union_values(joined, path_values)
        binding_rules.union_values(values.observed_values, path_values.observed_values)
        binding_rules.union_aliases(
            values.observed_aliases, path_values.observed_aliases
        )
        # An unbound alias still resolves by its original name, e.g. getattr.
        binding_rules.union_aliases(
            joined_aliases,
            {name: path_aliases.get(name, frozenset({name})) for name in names},
        )
    values.clear()
    values.update(joined)
    aliases.clear()
    aliases.update(joined_aliases)


def _scan_class_choices(
    bodies: list[list[ast.stmt]],
    values: _ClassValues,
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
) -> Iterator[tuple[int, str]]:
    paths: list[tuple[_ClassValues, binding_rules.Aliases]] = []
    for body in bodies:
        branch_values, branch_aliases = _ClassValues(values), dict(aliases)
        yield from _scan_class_block(body, branch_values, branch_aliases, closure)
        paths.append((branch_values, branch_aliases))
    _join_class_paths(values, aliases, paths)


def _loop_has_break(body: list[ast.stmt]) -> bool:
    pending: list[ast.AST] = list(body)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Break):
            return True
        if not isinstance(node, (*_SCOPES, ast.For, ast.AsyncFor, ast.While)):
            pending.extend(ast.iter_child_nodes(node))
    return False


def _loop_literal_items(node: ast.expr) -> list[ast.expr] | None:
    if isinstance(node, (ast.List, ast.Tuple)) and not any(
        isinstance(item, ast.Starred) for item in node.elts
    ):
        return list(node.elts)
    return None


def _scan_loop_iteration(
    node: ast.For | ast.AsyncFor | ast.While,
    values: _ClassValues,
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
    bindings: list[tuple[ast.expr, frozenset[str] | None, binding_rules.Value]],
) -> Iterator[tuple[int, str]]:
    if isinstance(node, ast.While):
        yield from _scan_expression(node.test, values, aliases, closure)
    for target, resolved, assigned_value in bindings:
        yield from _scan_assignment_target(
            target, resolved, values, aliases, closure, assigned_value
        )
    yield from _scan_class_block(node.body, values, aliases, closure)


def _loop_may_skip(node: ast.For | ast.AsyncFor | ast.While) -> bool:
    if isinstance(node, ast.While):
        return True
    if isinstance(node.iter, (ast.List, ast.Tuple, ast.Set)):
        return not any(not isinstance(item, ast.Starred) for item in node.iter.elts)
    if isinstance(node.iter, ast.Dict):
        return not any(key is not None for key in node.iter.keys)
    return True


def _scan_repeated_loop(
    node: ast.For | ast.AsyncFor | ast.While,
    values: _ClassValues,
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
    bindings: list[tuple[ast.expr, frozenset[str] | None, binding_rules.Value]],
    break_paths: list[tuple[_ClassValues, binding_rules.Aliases]],
) -> Iterator[tuple[int, str]]:
    current, current_aliases = _ClassValues(values), dict(aliases)
    exits = [(current, current_aliases)] if _loop_may_skip(node) else []
    has_break = _loop_has_break(node.body)
    # Revisit with carried state: imports become visible only on later trips.
    for _ in range(256):
        iteration, iteration_aliases = _ClassValues(current), dict(current_aliases)
        yield from _scan_loop_iteration(
            node, iteration, iteration_aliases, closure, bindings
        )
        exits.append((iteration, iteration_aliases))
        if has_break:
            break_paths.append(
                (
                    _ClassValues(iteration.observed_values),
                    dict(iteration.observed_aliases),
                )
            )
        joined, joined_aliases = _ClassValues(current), dict(current_aliases)
        _join_class_paths(
            joined,
            joined_aliases,
            [(current, current_aliases), (iteration, iteration_aliases)],
        )
        if joined == current and joined_aliases == current_aliases:
            break
        current, current_aliases = joined, joined_aliases
    else:
        raise ValueError("too many static alternatives in a loop")
    _join_class_paths(values, aliases, exits)


def _scan_class_loop(
    node: ast.For | ast.AsyncFor | ast.While,
    values: _ClassValues,
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
) -> Iterator[tuple[int, str]]:
    expression = node.test if isinstance(node, ast.While) else node.iter
    yield from _scan_expression(expression, values, aliases, closure)
    items = None if isinstance(node, ast.While) else _loop_literal_items(node.iter)
    current, current_aliases = _ClassValues(values), dict(aliases)
    break_paths: list[tuple[_ClassValues, binding_rules.Aliases]] = []
    has_break = _loop_has_break(node.body)
    bindings: list[tuple[ast.expr, frozenset[str] | None, binding_rules.Value]] = []
    if isinstance(node, (ast.For, ast.AsyncFor)):
        bindings = binding_rules.prepared_assignment(
            [node.target], ast.Constant(value=None), values, aliases
        )
        if isinstance(node.target, ast.Name):
            bindings = [
                (
                    node.target,
                    frozenset({""}),
                    binding_rules.Value(
                        frozenset(
                            binding_rules.strings(node.iter, values)
                            | binding_rules.mapping_keys(node.iter, values)
                        )
                    ),
                )
            ]
    if items is not None and isinstance(node, (ast.For, ast.AsyncFor)):
        # Python evaluates the entire iterable before the first iteration.
        iterations = [
            binding_rules.prepared_assignment([node.target], item, values, aliases)
            for item in items
        ]
        for bindings in iterations:
            iteration = _ClassValues(current)
            yield from _scan_loop_iteration(
                node, iteration, current_aliases, closure, bindings
            )
            current = iteration
            if has_break:
                break_paths.append(
                    (
                        _ClassValues(current.observed_values),
                        dict(current.observed_aliases),
                    )
                )
    else:
        yield from _scan_repeated_loop(
            node, current, current_aliases, closure, bindings, break_paths
        )
    if isinstance(node, ast.While):
        yield from _scan_expression(node.test, current, current_aliases, closure)
    yield from _scan_class_block(node.orelse, current, current_aliases, closure)
    _join_class_paths(values, aliases, [(current, current_aliases), *break_paths])


def _restore_class_fallback(
    name: str,
    values: _ClassValues,
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
) -> None:
    values.pop(name, None)
    aliases.pop(name, None)
    if name in values.class_nonlocals:
        # Deleting a closure cell leaves it unbound, not a module lookup.
        aliases[name] = frozenset({""})
        return
    if name in values.class_globals:
        return
    outer_values, outer_aliases = binding_rules.module_bindings(*closure)
    if name in outer_values:
        values[name] = outer_values[name]
    if name in outer_aliases:
        aliases[name] = outer_aliases[name]


def _scan_class_try(
    node: ast.Try | ast.TryStar,
    values: _ClassValues,
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
) -> Iterator[tuple[int, str]]:
    body_values, body_aliases = _ClassValues(values), dict(aliases)
    yield from _scan_class_block(node.body, body_values, body_aliases, closure)
    # An exception can arise between assignments, not only at the body's end.
    interrupted_values = binding_rules.ScopeValues(body_values.observed_values)
    interrupted_aliases = dict(body_values.observed_aliases)
    handler_start_values = binding_rules.ScopeValues(interrupted_values)
    handler_start_aliases = dict(interrupted_aliases)
    paths: list[tuple[_ClassValues, binding_rules.Aliases]] = []
    if not node.body or not isinstance(node.body[-1], ast.Raise):
        yield from _scan_class_block(node.orelse, body_values, body_aliases, closure)
        binding_rules.union_values(interrupted_values, body_values.observed_values)
        binding_rules.union_aliases(interrupted_aliases, body_values.observed_aliases)
        paths.append((body_values, body_aliases))
    for handler in node.handlers:
        caught_values = _ClassValues(handler_start_values)
        caught_aliases = dict(handler_start_aliases)
        if handler.type is not None:
            yield from _scan_expression(
                handler.type, caught_values, caught_aliases, closure
            )
        if handler.name is not None:
            caught_values[handler.name] = binding_rules.Value()
            caught_aliases[handler.name] = frozenset({""})
        yield from _scan_class_block(
            handler.body, caught_values, caught_aliases, closure
        )
        if handler.name is not None:
            _restore_class_fallback(
                handler.name, caught_values, caught_aliases, closure
            )
        paths.append((caught_values, caught_aliases))
        interrupted_handler = _ClassValues(caught_values.observed_values)
        interrupted_handler_aliases = dict(caught_values.observed_aliases)
        if handler.name is not None:
            _restore_class_fallback(
                handler.name, interrupted_handler, interrupted_handler_aliases, closure
            )
        binding_rules.union_values(interrupted_values, interrupted_handler)
        binding_rules.union_aliases(interrupted_aliases, interrupted_handler_aliases)
        if isinstance(node, ast.TryStar):
            binding_rules.union_values(handler_start_values, caught_values)
            binding_rules.union_aliases(handler_start_aliases, caught_aliases)
            binding_rules.union_values(handler_start_values, interrupted_handler)
            binding_rules.union_aliases(
                handler_start_aliases, interrupted_handler_aliases
            )
    identical_finally_input = False
    if paths:
        _join_class_paths(values, aliases, paths)
        identical_finally_input = (
            values == interrupted_values and aliases == interrupted_aliases
        )
        yield from _scan_class_block(node.finalbody, values, aliases, closure)
    # A nonmatching or handler-raised exception still executes finally, but
    # cannot reach the statements after try. Scan that path without joining it.
    if not identical_finally_input:
        yield from _scan_class_block(
            node.finalbody,
            _ClassValues(interrupted_values),
            interrupted_aliases,
            closure,
        )


def _scan_class_match(
    node: ast.Match,
    values: _ClassValues,
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
) -> Iterator[tuple[int, str]]:
    yield from _scan_expression(node.subject, values, aliases, closure)
    subject_value = binding_rules.Value(
        frozenset(binding_rules.strings(node.subject, values)),
        frozenset(binding_rules.mapping_keys(node.subject, values)),
    )
    subject_aliases = binding_rules.dotted(node.subject, aliases)
    paths: list[tuple[_ClassValues, binding_rules.Aliases]] = []
    remaining = [(_ClassValues(values), dict(aliases))]
    for case in node.cases:
        if not remaining:
            break
        pending_values, pending_aliases = _ClassValues(values), dict(aliases)
        _join_class_paths(pending_values, pending_aliases, remaining)
        branch_values, branch_aliases = (
            _ClassValues(pending_values),
            dict(pending_aliases),
        )
        for name in _pattern_names(case.pattern):
            branch_values[name] = binding_rules.Value()
            branch_aliases[name] = frozenset({""})
        if isinstance(case.pattern, ast.MatchAs) and case.pattern.name is not None:
            branch_values[case.pattern.name] = subject_value
            branch_aliases[case.pattern.name] = subject_aliases
        if case.guard is not None:
            yield from _scan_expression(
                case.guard, branch_values, branch_aliases, closure
            )
        irrefutable = (
            isinstance(case.pattern, ast.MatchAs) and case.pattern.pattern is None
        )
        guard = case.guard.value if isinstance(case.guard, ast.Constant) else None
        remaining = [] if irrefutable else [(pending_values, pending_aliases)]
        if case.guard is not None and guard is not True:
            remaining.append((_ClassValues(branch_values), dict(branch_aliases)))
        if guard is not False:
            yield from _scan_class_block(
                case.body, branch_values, branch_aliases, closure
            )
            paths.append((branch_values, branch_aliases))
    _join_class_paths(values, aliases, paths + remaining)


def _pattern_names(pattern: ast.pattern) -> Iterator[str]:
    for node in ast.walk(pattern):
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name is not None:
            yield node.name
        elif isinstance(node, ast.MatchMapping) and node.rest is not None:
            yield node.rest


def _scan_class_with(
    node: ast.With | ast.AsyncWith,
    values: _ClassValues,
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
) -> Iterator[tuple[int, str]]:
    item, *remaining = node.items
    yield from _scan_expression(item.context_expr, values, aliases, closure)
    if item.optional_vars is not None:
        yield from _scan_assignment_target(
            item.optional_vars, frozenset({""}), values, aliases, closure
        )
    body = node.body
    if remaining:
        # Earlier managers can suppress a later manager's entry failure, before
        # its optional target is bound. Nested blocks retain that exit state.
        body = [type(node)(items=remaining, body=body, type_comment=None)]
    body_values, body_aliases = _ClassValues(values), dict(aliases)
    yield from _scan_class_block(body, body_values, body_aliases, closure)
    _join_class_paths(
        values,
        aliases,
        [
            (body_values, body_aliases),
            (_ClassValues(body_values.observed_values), body_values.observed_aliases),
        ],
    )


def _scan_class_delete(
    targets: list[ast.expr],
    values: _ClassValues,
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
) -> Iterator[tuple[int, str]]:
    for target in targets:
        if isinstance(target, ast.Name):
            _restore_class_fallback(target.id, values, aliases, closure)
        elif isinstance(target, (ast.Tuple, ast.List)):
            yield from _scan_class_delete(target.elts, values, aliases, closure)
        else:
            yield from _scan_expression(target, values, aliases, closure)


def _scan_class_branch(
    node: ast.AST,
    values: _ClassValues,
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
) -> Iterator[tuple[int, str]]:
    if isinstance(node, ast.If):
        yield from _scan_expression(node.test, values, aliases, closure)
        yield from _scan_class_choices(
            [node.body, node.orelse], values, aliases, closure
        )
    elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
        yield from _scan_class_loop(node, values, aliases, closure)
    elif isinstance(node, (ast.Try, ast.TryStar)):
        yield from _scan_class_try(node, values, aliases, closure)
    elif isinstance(node, ast.Match):
        yield from _scan_class_match(node, values, aliases, closure)
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        yield from _scan_class_with(node, values, aliases, closure)
    elif isinstance(node, ast.Delete):
        yield from _scan_class_delete(node.targets, values, aliases, closure)


def _scan_node(
    node: ast.AST,
    values: dict[str, binding_rules.Value],
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
    assigned_names: set[str],
) -> Iterator[tuple[int, str]]:
    if isinstance(values, _ClassValues) and isinstance(node, _BRANCHES):
        yield from _scan_class_branch(node, values, aliases, closure)
    elif isinstance(node, (ast.Assign, ast.AnnAssign)):
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


def _scan_nodes(
    nodes: Iterator[ast.AST],
    values: dict[str, binding_rules.Value],
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
    assigned_names: set[str],
) -> Iterator[tuple[int, str]]:
    for node in nodes:
        if isinstance(values, _ClassValues):
            values.observe(aliases)
        yield from _scan_node(node, values, aliases, closure, assigned_names)
    if isinstance(values, _ClassValues):
        values.observe(aliases)


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
    if isinstance(scope, ast.Module) and isinstance(values, binding_rules.ScopeValues):
        values.module_bindings = (values, aliases)
    assigned_names = {
        name for name, _, _ in binding_rules.assignments(list(_nodes(scope)))
    }
    if not isinstance(scope, ast.ClassDef):
        closure = (values, aliases)
    yield from _scan_nodes(
        _nodes(
            scope,
            descend_assignments=False,
            descend_branches=not isinstance(values, _ClassValues),
        ),
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
