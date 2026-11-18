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
from dataclasses import dataclass
from itertools import chain, product
from pathlib import Path

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


@dataclass(frozen=True)
class _Value:
    strings: frozenset[str] = frozenset()
    mapping_keys: frozenset[str] = frozenset()


class _ClassValues(dict[str, _Value]):
    """Class namespace values, updated in statement order rather than prebound."""

    def __init__(self, values: dict[str, _Value]) -> None:
        super().__init__(values)
        self.observed_values = dict(values)
        self.observed_aliases: _Aliases = {}
        self.has_alias_snapshot = False

    def observe(self, aliases: _Aliases) -> None:
        _union_values(self.observed_values, self)
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


_Aliases = dict[str, frozenset[str]]


def _union_values(target: dict[str, _Value], source: dict[str, _Value]) -> None:
    for name, value in source.items():
        previous = target.get(name, _Value())
        target[name] = _Value(
            previous.strings | value.strings,
            previous.mapping_keys | value.mapping_keys,
        )


def _union_aliases(target: _Aliases, source: _Aliases) -> None:
    for name, value in source.items():
        target[name] = target.get(name, frozenset()) | value


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


def _dotted(node: ast.expr, aliases: _Aliases) -> frozenset[str]:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, frozenset({node.id}))
    if isinstance(node, ast.Attribute):
        return frozenset(f"{name}.{node.attr}" for name in _dotted(node.value, aliases))
    return frozenset({""})


def _joined(parts: list[set[str]]) -> set[str]:
    # Only tiny expressions are useful targets. Bound the cross product of
    # branch alternatives so source input cannot make a CI guard exponential.
    if any(not part for part in parts):
        return set()
    size = 1
    for part in parts:
        size *= len(part)
        if size > 256:
            raise ValueError("too many static alternatives in an attribute target")
    return {"".join(items) for items in product(*parts)}


def _strings(node: ast.expr | None, values: dict[str, _Value]) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name):
        return set(values.get(node.id, _Value()).strings)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _joined(
            [
                _strings(node.left, values) or {"?"},
                _strings(node.right, values) or {"?"},
            ]
        )
    if isinstance(node, ast.JoinedStr):
        return _joined(
            [
                _strings(part.value, values) or {"?"}
                if isinstance(part, ast.FormattedValue)
                else _strings(part, values)
                for part in node.values
            ]
        )
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return set().union(*(_strings(item, values) for item in node.elts))
    return set()


def _mapping_keys(node: ast.expr, values: dict[str, _Value]) -> set[str]:
    if isinstance(node, ast.Name):
        return set(values.get(node.id, _Value()).mapping_keys)
    if isinstance(node, ast.Dict):
        return set().union(
            *(
                _mapping_keys(value, values) if key is None else _strings(key, values)
                for key, value in zip(node.keys, node.values, strict=True)
            )
        )
    return set()


def _bindings(
    scope: ast.AST,
    parent_values: dict[str, _Value],
    parent_aliases: _Aliases,
    initial_values: dict[str, set[str]] | None = None,
) -> tuple[dict[str, _Value], _Aliases]:
    if isinstance(scope, ast.ClassDef):
        return _ClassValues(parent_values), dict(parent_aliases)
    values = dict(parent_values)
    aliases = dict(parent_aliases)
    nodes = list(_nodes(scope))
    local_names = {
        node.id
        for node in nodes
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    local_names.update(
        node.name
        for node in nodes
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    )
    imported: _Aliases = {}
    _import_aliases(nodes, imported)
    local_names.update(imported)
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
        (name, _Value(frozenset(items)))
        for name, items in (initial_values or {}).items()
    )
    _extend_values(list(_assignments(nodes)), values, aliases)
    return values, aliases


def _import_aliases(nodes: list[ast.AST], aliases: _Aliases) -> None:
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = frozenset(
                    {alias.name if alias.asname else alias.name.split(".")[0]}
                )
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                aliases[alias.asname or alias.name] = frozenset(
                    {f"{node.module}.{alias.name}"}
                )


def _extend_values(
    assignments: list[tuple[str, ast.expr, bool]],
    values: dict[str, _Value],
    aliases: _Aliases,
) -> None:
    # A lexical constant can refer to one declared later; union alternatives
    # rather than letting a later assignment hide an earlier private target.
    for _ in range(len(assignments) + 1):
        changed = False
        for target, expression, iterated in assignments:
            previous = values.get(target, _Value())
            strings = _strings(expression, values)
            keys = _mapping_keys(expression, values)
            current = _Value(
                previous.strings | strings | (keys if iterated else set()),
                previous.mapping_keys | (set() if iterated else keys),
            )
            changed |= current != previous
            values[target] = current
            if isinstance(expression, (ast.Name, ast.Attribute)):
                resolved = _dotted(expression, aliases)
                changed |= aliases.get(target) != resolved
                aliases[target] = resolved
        if not changed:
            break


def _assignments(nodes: list[ast.AST]) -> Iterator[tuple[str, ast.expr, bool]]:
    for node in nodes:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    yield target.id, node.value, False
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            yield node.target.id, node.value, False
        elif isinstance(
            node, (ast.For, ast.AsyncFor, ast.comprehension)
        ) and isinstance(node.target, ast.Name):
            yield node.target.id, node.iter, True


def _argument(call: ast.Call, index: int, name: str) -> ast.expr | None:
    if len(call.args) > index:
        return call.args[index]
    return next((item.value for item in call.keywords if item.arg == name), None)


def _own_object(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Name) and node.id in {"self", "cls"}


def _targets(call: ast.Call, aliases: _Aliases, values: dict[str, _Value]) -> set[str]:
    return set().union(
        *(_named_targets(call, name, values) for name in _dotted(call.func, aliases))
    )


def _named_targets(call: ast.Call, name: str, values: dict[str, _Value]) -> set[str]:
    operation = name.rsplit(".", 1)[-1]
    target = _argument(call, 0, "target")
    if operation == "patch":
        return _strings(target, values)
    if name.endswith("patch.dict"):
        return _strings(_argument(call, 0, "in_dict"), values)
    if name.endswith("patch.multiple"):
        keys = {item.arg for item in call.keywords if item.arg is not None}
        keys.update(
            set().union(
                *(
                    _mapping_keys(item.value, values)
                    for item in call.keywords
                    if item.arg is None
                )
            )
        )
        return _strings(target, values) | (set() if _own_object(target) else keys)
    if name.endswith("patch.object"):
        return _strings(target, values) | (
            set()
            if _own_object(target)
            else _strings(_argument(call, 1, "attribute"), values)
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
            dotted = _strings(target, values)
            if dotted:
                return dotted
        return _strings(_argument(call, 1, "name"), values)
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
    scope: ast.AST, values: dict[str, _Value]
) -> dict[str, set[str]]:
    if isinstance(scope, _COMPREHENSIONS):
        first = scope.generators[0]
        if isinstance(first.target, ast.Name):
            return {
                first.target.id: _strings(first.iter, values)
                | _mapping_keys(first.iter, values)
            }
    return {}


def _bind_definition(
    name: str,
    values: dict[str, _Value],
    aliases: _Aliases,
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
    values: dict[str, _Value],
    aliases: _Aliases,
    closure: tuple[dict[str, _Value], _Aliases],
    assigned_value: _Value = _Value(),
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
    values: dict[str, _Value],
    aliases: _Aliases,
    closure: tuple[dict[str, _Value], _Aliases],
) -> Iterator[tuple[int, str]]:
    value = node.value
    if isinstance(value, ast.Lambda):
        for header in _headers(value):
            yield from _scan_expression(header, values, aliases, closure)
    elif value is not None:
        yield from _scan_expression(value, values, aliases, closure)
    # Resolve a chained assignment's RHS once, then evaluate and bind targets
    # left to right: a later target expression can see an earlier new binding.
    resolved = _dotted(value, aliases) if value is not None else None
    assigned_value = _Value(
        frozenset(_strings(value, values)),
        frozenset(_mapping_keys(value, values)) if value is not None else frozenset(),
    )
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
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
    values: dict[str, _Value],
    aliases: _Aliases,
    closure: tuple[dict[str, _Value], _Aliases],
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
    aliases: _Aliases,
    closure: tuple[dict[str, _Value], _Aliases],
) -> Iterator[tuple[int, str]]:
    nodes = _nodes(
        ast.Module(body=body, type_ignores=[]),
        descend_assignments=False,
        descend_branches=False,
    )
    yield from _scan_nodes(nodes, values, aliases, closure, set())


def _join_class_paths(
    values: _ClassValues,
    aliases: _Aliases,
    paths: list[tuple[_ClassValues, _Aliases]],
) -> None:
    joined: dict[str, _Value] = {}
    names = set().union(*(path_aliases.keys() for _, path_aliases in paths))
    joined_aliases: _Aliases = {}
    for path_values, path_aliases in paths:
        _union_values(joined, path_values)
        _union_values(values.observed_values, path_values.observed_values)
        _union_aliases(values.observed_aliases, path_values.observed_aliases)
        # An unbound alias still resolves by its original name, e.g. getattr.
        _union_aliases(
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
    aliases: _Aliases,
    closure: tuple[dict[str, _Value], _Aliases],
) -> Iterator[tuple[int, str]]:
    paths: list[tuple[_ClassValues, _Aliases]] = []
    for body in bodies:
        branch_values, branch_aliases = _ClassValues(values), dict(aliases)
        yield from _scan_class_block(body, branch_values, branch_aliases, closure)
        paths.append((branch_values, branch_aliases))
    _join_class_paths(values, aliases, paths)


def _scan_class_loop(
    node: ast.For | ast.AsyncFor | ast.While,
    values: _ClassValues,
    aliases: _Aliases,
    closure: tuple[dict[str, _Value], _Aliases],
) -> Iterator[tuple[int, str]]:
    expression = node.test if isinstance(node, ast.While) else node.iter
    yield from _scan_expression(expression, values, aliases, closure)
    body_values, body_aliases = _ClassValues(values), dict(aliases)
    # A later iteration can see assignments from the body. Seed alternatives,
    # then let definite assignments replace them in source order within it.
    seeded_aliases = dict(aliases)
    body_nodes = list(_nodes(ast.Module(body=node.body, type_ignores=[])))
    _extend_values(list(_assignments(body_nodes)), body_values, seeded_aliases)
    _union_aliases(body_aliases, seeded_aliases)
    if not isinstance(node, ast.While):
        iterated = _Value(
            frozenset(_strings(node.iter, values) | _mapping_keys(node.iter, values))
        )
        yield from _scan_assignment_target(
            node.target, frozenset({""}), body_values, body_aliases, closure, iterated
        )
    body_values = _ClassValues(body_values)
    yield from _scan_class_block(node.body, body_values, body_aliases, closure)
    paths = [(body_values, body_aliases)]
    if isinstance(node, ast.While) or not _definitely_nonempty(node.iter):
        paths.append((_ClassValues(values), dict(aliases)))
    if any(isinstance(part, (ast.Break, ast.Continue)) for part in body_nodes):
        paths.append(
            (_ClassValues(body_values.observed_values), body_values.observed_aliases)
        )
    _join_class_paths(values, aliases, paths)
    if isinstance(node, ast.While):
        yield from _scan_expression(node.test, values, aliases, closure)
    # Retain the break path as well as the normal-completion else block.
    yield from _scan_class_choices([[], node.orelse], values, aliases, closure)


def _definitely_nonempty(node: ast.expr) -> bool:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return any(not isinstance(item, ast.Starred) for item in node.elts)
    if isinstance(node, ast.Dict):
        return any(key is not None for key in node.keys)
    return False


def _restore_class_fallback(
    name: str,
    values: _ClassValues,
    aliases: _Aliases,
    closure: tuple[dict[str, _Value], _Aliases],
) -> None:
    outer_values, outer_aliases = closure
    values.pop(name, None)
    aliases.pop(name, None)
    if name in outer_values:
        values[name] = outer_values[name]
    if name in outer_aliases:
        aliases[name] = outer_aliases[name]


def _scan_class_try(
    node: ast.Try | ast.TryStar,
    values: _ClassValues,
    aliases: _Aliases,
    closure: tuple[dict[str, _Value], _Aliases],
) -> Iterator[tuple[int, str]]:
    body_values, body_aliases = _ClassValues(values), dict(aliases)
    yield from _scan_class_block(node.body, body_values, body_aliases, closure)
    # An exception can arise between assignments, not only at the body's end.
    interrupted_values = dict(body_values.observed_values)
    interrupted_aliases = dict(body_values.observed_aliases)
    handler_start_values = dict(interrupted_values)
    handler_start_aliases = dict(interrupted_aliases)
    yield from _scan_class_block(node.orelse, body_values, body_aliases, closure)
    _union_values(interrupted_values, body_values.observed_values)
    _union_aliases(interrupted_aliases, body_values.observed_aliases)
    paths: list[tuple[_ClassValues, _Aliases]] = []
    if not node.body or not isinstance(node.body[-1], ast.Raise):
        paths.append((body_values, body_aliases))
    for handler in node.handlers:
        caught_values = _ClassValues(handler_start_values)
        caught_aliases = dict(handler_start_aliases)
        if handler.type is not None:
            yield from _scan_expression(
                handler.type, caught_values, caught_aliases, closure
            )
        if handler.name is not None:
            caught_values[handler.name] = _Value()
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
        _union_values(interrupted_values, interrupted_handler)
        _union_aliases(interrupted_aliases, interrupted_handler_aliases)
    if paths:
        _join_class_paths(values, aliases, paths)
        yield from _scan_class_block(node.finalbody, values, aliases, closure)
    # A nonmatching or handler-raised exception still executes finally, but
    # cannot reach the statements after try. Scan that path without joining it.
    yield from _scan_class_block(
        node.finalbody, _ClassValues(interrupted_values), interrupted_aliases, closure
    )


def _scan_class_match(
    node: ast.Match,
    values: _ClassValues,
    aliases: _Aliases,
    closure: tuple[dict[str, _Value], _Aliases],
) -> Iterator[tuple[int, str]]:
    yield from _scan_expression(node.subject, values, aliases, closure)
    paths: list[tuple[_ClassValues, _Aliases]] = []
    if not any(
        case.guard is None
        and isinstance(case.pattern, ast.MatchAs)
        and case.pattern.pattern is None
        for case in node.cases
    ):
        paths.append((_ClassValues(values), dict(aliases)))
    for case in node.cases:
        branch_values, branch_aliases = _ClassValues(values), dict(aliases)
        for name in _pattern_names(case.pattern):
            branch_values[name] = _Value()
            branch_aliases[name] = frozenset({""})
        if case.guard is not None:
            yield from _scan_expression(
                case.guard, branch_values, branch_aliases, closure
            )
        yield from _scan_class_block(case.body, branch_values, branch_aliases, closure)
        paths.append((branch_values, branch_aliases))
    _join_class_paths(values, aliases, paths)


def _pattern_names(pattern: ast.pattern) -> Iterator[str]:
    for node in ast.walk(pattern):
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name is not None:
            yield node.name
        elif isinstance(node, ast.MatchMapping) and node.rest is not None:
            yield node.rest


def _scan_class_with(
    node: ast.With | ast.AsyncWith,
    values: _ClassValues,
    aliases: _Aliases,
    closure: tuple[dict[str, _Value], _Aliases],
) -> Iterator[tuple[int, str]]:
    for item in node.items:
        yield from _scan_expression(item.context_expr, values, aliases, closure)
        if item.optional_vars is not None:
            yield from _scan_assignment_target(
                item.optional_vars, frozenset({""}), values, aliases, closure
            )
    body_values, body_aliases = _ClassValues(values), dict(aliases)
    yield from _scan_class_block(node.body, body_values, body_aliases, closure)
    # A context manager may suppress an exception between body assignments.
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
    aliases: _Aliases,
    closure: tuple[dict[str, _Value], _Aliases],
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
    aliases: _Aliases,
    closure: tuple[dict[str, _Value], _Aliases],
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
    values: dict[str, _Value],
    aliases: _Aliases,
    closure: tuple[dict[str, _Value], _Aliases],
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
        imported: _Aliases = {}
        _import_aliases([node], imported)
        aliases.update(imported)
        if isinstance(values, _ClassValues):
            for name in imported:
                values.pop(name, None)


def _scan_nodes(
    nodes: Iterator[ast.AST],
    values: dict[str, _Value],
    aliases: _Aliases,
    closure: tuple[dict[str, _Value], _Aliases],
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
    values: dict[str, _Value],
    aliases: _Aliases,
    closure: tuple[dict[str, _Value], _Aliases],
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
    values: dict[str, _Value],
    aliases: _Aliases,
    initial_values: dict[str, set[str]] | None = None,
) -> Iterator[tuple[int, str]]:
    closure = (values, aliases)
    values, aliases = _bindings(scope, values, aliases, initial_values)
    assigned_names = {name for name, _, _ in _assignments(list(_nodes(scope)))}
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
        print("\n".join(violations))
        print("Use the public entry point or real configuration; no test is exempt.")
        return 1
    print("No private string targets in unit or app tests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
