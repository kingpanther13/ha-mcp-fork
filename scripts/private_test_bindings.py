"""Static values and binding snapshots used by the private-access guard."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import product


@dataclass(frozen=True)
class Value:
    strings: frozenset[str] = frozenset()
    mapping_keys: frozenset[str] = frozenset()


Aliases = dict[str, frozenset[str]]


class ScopeValues(dict[str, Value]):
    """Lexical values retaining the module namespace for class name lookup."""

    def __init__(self, values: dict[str, Value]) -> None:
        super().__init__(values)
        self.module_bindings: tuple[dict[str, Value], Aliases] | None = (
            values.module_bindings if isinstance(values, ScopeValues) else None
        )
        self.class_globals: frozenset[str] = (
            values.class_globals if isinstance(values, ScopeValues) else frozenset()
        )
        self.class_nonlocals: frozenset[str] = (
            values.class_nonlocals if isinstance(values, ScopeValues) else frozenset()
        )


def module_bindings(
    values: dict[str, Value], aliases: Aliases
) -> tuple[dict[str, Value], Aliases]:
    if isinstance(values, ScopeValues) and values.module_bindings is not None:
        return values.module_bindings
    return values, aliases


def class_bindings(
    parent_values: dict[str, Value], parent_aliases: Aliases, nodes: list[ast.AST]
) -> tuple[ScopeValues, Aliases]:
    values = ScopeValues(parent_values)
    values.class_globals = frozenset(_declared_names(nodes, ast.Global))
    values.class_nonlocals = frozenset(_declared_names(nodes, ast.Nonlocal))
    aliases = dict(parent_aliases)
    outer_values, outer_aliases = module_bindings(parent_values, parent_aliases)
    # Class-bound names use LOAD_NAME's module fallback until assigned;
    # only free names capture an enclosing function's lexical bindings.
    for name in bound_names(nodes, class_scope=True):
        values.pop(name, None)
        aliases.pop(name, None)
        if name in outer_values:
            values[name] = outer_values[name]
        if name in outer_aliases:
            aliases[name] = outer_aliases[name]
    return values, aliases


def bound_names(nodes: list[ast.AST], *, class_scope: bool = False) -> set[str]:
    """Names local to a scope, including bindings represented without Name nodes."""
    names: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
            if node.name is not None:
                names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest is not None:
            names.add(node.rest)
    imported: Aliases = {}
    import_aliases(nodes, imported)
    names.update(imported)
    if class_scope:
        names.update(_declared_names(nodes, ast.Global))
        names.difference_update(_declared_names(nodes, ast.Nonlocal))
    return names


def _declared_names(
    nodes: list[ast.AST], declaration: type[ast.Global] | type[ast.Nonlocal]
) -> set[str]:
    return {
        name for node in nodes if isinstance(node, declaration) for name in node.names
    }


def union_values(target: dict[str, Value], source: dict[str, Value]) -> None:
    for name, value in source.items():
        previous = target.get(name, Value())
        target[name] = Value(
            previous.strings | value.strings,
            previous.mapping_keys | value.mapping_keys,
        )


def union_aliases(target: Aliases, source: Aliases) -> None:
    for name, value in source.items():
        target[name] = target.get(name, frozenset()) | value


def dotted(node: ast.expr, aliases: Aliases) -> frozenset[str]:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, frozenset({node.id}))
    if isinstance(node, ast.Attribute):
        return frozenset(f"{name}.{node.attr}" for name in dotted(node.value, aliases))
    return frozenset({""})


def joined(parts: list[set[str]]) -> set[str]:
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


def strings(node: ast.expr | None, values: dict[str, Value]) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name):
        return set(values.get(node.id, Value()).strings)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return joined(
            [
                strings(node.left, values) or {"?"},
                strings(node.right, values) or {"?"},
            ]
        )
    if isinstance(node, ast.JoinedStr):
        return joined(
            [
                strings(part.value, values) or {"?"}
                if isinstance(part, ast.FormattedValue)
                else strings(part, values)
                for part in node.values
            ]
        )
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return set().union(*(strings(item, values) for item in node.elts))
    return set()


def mapping_keys(node: ast.expr, values: dict[str, Value]) -> set[str]:
    if isinstance(node, ast.Name):
        return set(values.get(node.id, Value()).mapping_keys)
    if isinstance(node, ast.Dict):
        return set().union(
            *(
                mapping_keys(value, values) if key is None else strings(key, values)
                for key, value in zip(node.keys, node.values, strict=True)
            )
        )
    return set()


def import_aliases(nodes: list[ast.AST], aliases: Aliases) -> None:
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


def extend_values(
    assignments: list[tuple[str, ast.expr, bool]],
    values: dict[str, Value],
    aliases: Aliases,
) -> None:
    # A lexical constant can refer to one declared later; union alternatives
    # rather than letting a later assignment hide an earlier private target.
    for _ in range(len(assignments) + 1):
        changed = False
        for target, expression, iterated in assignments:
            previous = values.get(target, Value())
            alternatives = strings(expression, values)
            keys = mapping_keys(expression, values)
            current = Value(
                previous.strings | alternatives | (keys if iterated else set()),
                previous.mapping_keys | (set() if iterated else keys),
            )
            changed |= current != previous
            values[target] = current
            if isinstance(expression, (ast.Name, ast.Attribute)):
                resolved = dotted(expression, aliases)
                changed |= aliases.get(target) != resolved
                aliases[target] = resolved
        if not changed:
            break


def assignments(nodes: list[ast.AST]) -> Iterator[tuple[str, ast.expr, bool]]:
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


def _literal_targets(
    target: ast.expr, expression: ast.expr | None
) -> Iterator[tuple[ast.expr, ast.expr | None]]:
    if isinstance(target, (ast.Tuple, ast.List)):
        elements: list[ast.expr | None] = [None] * len(target.elts)
        if (
            isinstance(expression, (ast.Tuple, ast.List))
            and len(expression.elts) == len(target.elts)
            and not any(
                isinstance(item, ast.Starred)
                for item in (*target.elts, *expression.elts)
            )
        ):
            elements = list(expression.elts)
        for item, value in zip(target.elts, elements, strict=True):
            yield from _literal_targets(item, value)
    elif isinstance(target, ast.Starred):
        yield from _literal_targets(target.value, None)
    else:
        yield target, expression


def prepared_assignment(
    targets: list[ast.expr],
    expression: ast.expr | None,
    values: dict[str, Value],
    aliases: Aliases,
) -> list[tuple[ast.expr, frozenset[str] | None, Value]]:
    """Snapshot all RHS elements before any left-to-right target evaluation.

    Attribute and subscript stores still need scanning, but cannot contribute
    lexical values. Do not expand their potentially large fixture payloads.
    """
    bindings = []
    for target in targets:
        for item, value in _literal_targets(target, expression):
            resolved = None
            assigned = Value()
            if isinstance(item, ast.Name) and expression is not None:
                resolved = (
                    dotted(value, aliases) if value is not None else frozenset({""})
                )
                assigned = Value(
                    frozenset(strings(value, values)),
                    frozenset(mapping_keys(value, values))
                    if value is not None
                    else frozenset(),
                )
            bindings.append((item, resolved, assigned))
    return bindings
