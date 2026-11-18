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
from itertools import product
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TREES = ("tests/src/unit", "tests/addon")
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _nodes(scope: ast.AST) -> Iterator[ast.AST]:
    """Nodes in one lexical scope, leaving nested bodies to their own scope."""
    children: Iterator[ast.AST]
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        children = iter(scope.body)
    elif isinstance(scope, ast.Lambda):
        children = iter([scope.body])
    else:
        children = ast.iter_child_nodes(scope)
    for child in children:
        yield child
        if not isinstance(child, _SCOPES):
            yield from _nodes(child)


def _headers(scope: ast.AST) -> Iterator[ast.expr]:
    """Decorators, bases and defaults execute before entering the new scope."""
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        yield from scope.decorator_list
    if isinstance(scope, ast.ClassDef):
        yield from scope.bases
        yield from (keyword.value for keyword in scope.keywords)
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        yield from scope.args.defaults
        yield from (value for value in scope.args.kw_defaults if value is not None)
        yield from (
            node.annotation for node in ast.walk(scope.args)
            if isinstance(node, ast.arg) and node.annotation is not None
        )
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)) and scope.returns is not None:
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


def _strings(node: ast.expr | None, values: dict[str, set[str]]) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name):
        return values.get(node.id, set())
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
    if isinstance(node, ast.Dict):
        return set().union(
            *(
                _strings(value if key is None else key, values)
                for key, value in zip(node.keys, node.values, strict=True)
            )
        )
    return set()


def _bindings(
    scope: ast.AST, parent_values: dict[str, set[str]], parent_aliases: dict[str, str]
) -> tuple[dict[str, set[str]], dict[str, str]]:
    values = {name: set(items) for name, items in parent_values.items()}
    aliases = dict(parent_aliases)
    nodes = list(_nodes(scope))
    local_names = {
        node.id
        for node in nodes
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        parameters = [*scope.args.posonlyargs, *scope.args.args, *scope.args.kwonlyargs,
                      scope.args.vararg, scope.args.kwarg]
        local_names.update(parameter.arg for parameter in parameters if parameter is not None)
    for name in local_names:
        values.pop(name, None)
        aliases.pop(name, None)
    _import_aliases(nodes, aliases)
    _extend_values(list(_assignments(nodes)), values, aliases)
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
    assignments: list[tuple[str, ast.expr]],
    values: dict[str, set[str]],
    aliases: dict[str, str],
) -> None:
    # A lexical constant can refer to one declared later; union alternatives
    # rather than letting a later assignment hide an earlier private target.
    for _ in range(len(assignments) + 1):
        changed = False
        for target, expression in assignments:
            previous = values.get(target, set())
            current = previous | _strings(expression, values)
            changed |= current != previous
            values[target] = current
            if isinstance(expression, (ast.Name, ast.Attribute)):
                resolved = _dotted(expression, aliases)
                changed |= aliases.get(target) != resolved
                aliases[target] = resolved
        if not changed:
            break


def _assignments(nodes: list[ast.AST]) -> Iterator[tuple[str, ast.expr]]:
    for node in nodes:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    yield target.id, node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            yield node.target.id, node.value
        elif isinstance(node, (ast.For, ast.AsyncFor)) and isinstance(
            node.target, ast.Name
        ):
            yield node.target.id, node.iter


def _argument(call: ast.Call, index: int, name: str) -> ast.expr | None:
    if len(call.args) > index:
        return call.args[index]
    return next((item.value for item in call.keywords if item.arg == name), None)


def _own_object(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Name) and node.id in {"self", "cls"}


def _targets(
    call: ast.Call, aliases: dict[str, str], values: dict[str, set[str]]
) -> set[str]:
    name = _dotted(call.func, aliases)
    operation = name.rsplit(".", 1)[-1]
    target = _argument(call, 0, "target")
    if operation == "patch":
        return _strings(target, values)
    if name.endswith("patch.multiple"):
        keys = {item.arg for item in call.keywords if item.arg is not None}
        keys.update(
            set().union(
                *(
                    _strings(item.value, values)
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
    parts = target.split(".")
    for index, part in enumerate(parts):
        if not part.startswith("_") or (
            len(part) > 4 and part.startswith("__") and part.endswith("__")
        ):
            continue
        if index == 1 and parts[0] == "ha_mcp" and part == "_vendor":
            continue
        return True
    return False


def _scan_scope(
    scope: ast.AST, values: dict[str, set[str]], aliases: dict[str, str]
) -> Iterator[tuple[int, str]]:
    values, aliases = _bindings(scope, values, aliases)
    for node in _nodes(scope):
        if isinstance(node, _SCOPES):
            for header in _headers(node):
                yield from _scan_scope(ast.Expression(body=header), values, aliases)
            yield from _scan_scope(node, values, aliases)
        elif isinstance(node, ast.Call):
            for target in sorted(_targets(node, aliases, values)):
                if _private(target):
                    yield node.lineno, target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--repo", type=Path, default=REPO)
    args = parser.parse_args(argv)
    violations = []
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
