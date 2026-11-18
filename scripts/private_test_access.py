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


def _nodes(scope: ast.AST, *, descend_assignments: bool = True) -> Iterator[ast.AST]:
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
        if isinstance(child, _SCOPES) or (
            not descend_assignments and isinstance(child, (ast.Assign, ast.AnnAssign))
        ):
            continue
        yield from _nodes(child, descend_assignments=descend_assignments)


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


def _dotted(node: ast.expr, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value, aliases)}.{node.attr}"
    return ""


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
    parent_aliases: dict[str, str],
    initial_values: dict[str, set[str]] | None = None,
) -> tuple[dict[str, _Value], dict[str, str]]:
    values = dict(parent_values)
    aliases = dict(parent_aliases)
    nodes = list(_nodes(scope))
    local_names = {
        node.id
        for node in nodes
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    if not isinstance(scope, ast.ClassDef):
        local_names.update(
            node.name
            for node in nodes
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        )
    imported: dict[str, str] = {}
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
    if isinstance(scope, ast.ClassDef):
        # Class bodies resolve aliases in statement order, including defaults
        # evaluated before a method with the same name is bound.
        aliases = dict(parent_aliases)
    return values, aliases


def _import_aliases(nodes: list[ast.AST], aliases: dict[str, str]) -> None:
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"


def _extend_values(
    assignments: list[tuple[str, ast.expr, bool]],
    values: dict[str, _Value],
    aliases: dict[str, str],
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


def _targets(
    call: ast.Call, aliases: dict[str, str], values: dict[str, _Value]
) -> set[str]:
    name = _dotted(call.func, aliases)
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
    aliases: dict[str, str],
    assigned_names: set[str],
) -> None:
    aliases[name] = ""
    if name not in assigned_names:
        # Clear an inherited value, but retain the scope's assignment
        # alternatives, including assignments after this definition.
        values.pop(name, None)


def _scan_assignment_target(
    target: ast.expr,
    resolved: str | None,
    values: dict[str, _Value],
    aliases: dict[str, str],
    closure: tuple[dict[str, _Value], dict[str, str]],
) -> Iterator[tuple[int, str]]:
    if isinstance(target, ast.Name):
        if resolved is not None:
            aliases[target.id] = resolved
    elif isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts:
            # Unpacked elements are unknown, not inherited callable aliases.
            yield from _scan_assignment_target(item, "", values, aliases, closure)
    elif isinstance(target, ast.Starred):
        yield from _scan_assignment_target(target.value, "", values, aliases, closure)
    else:
        yield from _scan_expression(target, values, aliases, closure)


def _scan_assignment(
    node: ast.Assign | ast.AnnAssign,
    values: dict[str, _Value],
    aliases: dict[str, str],
    closure: tuple[dict[str, _Value], dict[str, str]],
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
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        yield from _scan_assignment_target(target, resolved, values, aliases, closure)
    if isinstance(value, ast.Lambda):
        # Defaults ran before assignment. The deferred body sees the new binding,
        # except in a class, whose namespace is not captured by the lambda.
        yield from _scan_scope(value, *closure)
    if isinstance(node, ast.AnnAssign):
        yield from _scan_expression(node.annotation, values, aliases, closure)


def _scan_nested_scope(
    node: ast.AST,
    values: dict[str, _Value],
    aliases: dict[str, str],
    closure: tuple[dict[str, _Value], dict[str, str]],
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


def _scan_nodes(
    nodes: Iterator[ast.AST],
    values: dict[str, _Value],
    aliases: dict[str, str],
    closure: tuple[dict[str, _Value], dict[str, str]],
    assigned_names: set[str],
) -> Iterator[tuple[int, str]]:
    for node in nodes:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            yield from _scan_assignment(node, values, aliases, closure)
        elif isinstance(node, _SCOPES):
            yield from _scan_nested_scope(
                node, values, aliases, closure, assigned_names
            )
        elif isinstance(node, ast.Call):
            for target in sorted(_targets(node, aliases, values)):
                if _private(target):
                    yield node.lineno, target
        else:
            _import_aliases([node], aliases)


def _scan_expression(
    expression: ast.expr,
    values: dict[str, _Value],
    aliases: dict[str, str],
    closure: tuple[dict[str, _Value], dict[str, str]],
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
    aliases: dict[str, str],
    initial_values: dict[str, set[str]] | None = None,
) -> Iterator[tuple[int, str]]:
    closure = (values, aliases)
    values, aliases = _bindings(scope, values, aliases, initial_values)
    assigned_names = {name for name, _, _ in _assignments(list(_nodes(scope)))}
    if not isinstance(scope, ast.ClassDef):
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
        print("\n".join(violations))
        print("Use the public entry point or real configuration; no test is exempt.")
        return 1
    print("No private string targets in unit or app tests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
