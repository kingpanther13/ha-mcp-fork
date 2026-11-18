"""Static values and binding snapshots used by the private-access guard."""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from itertools import product


@dataclass(frozen=True)
class Value:
    strings: frozenset[str] = frozenset()
    mapping_keys: frozenset[str] = frozenset()
    iterable_elements: frozenset[str] = frozenset()
    mapping_strings: tuple[tuple[str, frozenset[str]], ...] = ()
    is_iterable: bool = False


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
        self.class_locals: frozenset[str] = (
            values.class_locals if isinstance(values, ScopeValues) else frozenset()
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
    values.class_locals = (
        frozenset(bound_names(nodes, class_scope=True)) - values.class_globals
    )
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
        target[name] = _merged_value(previous, value)


def _merged_value(left: Value, right: Value) -> Value:
    entries = dict(left.mapping_strings)
    for key, strings in right.mapping_strings:
        entries[key] = entries.get(key, frozenset()) | strings
    return Value(
        left.strings | right.strings,
        left.mapping_keys | right.mapping_keys,
        left.iterable_elements | right.iterable_elements,
        tuple(sorted(entries.items())),
        left.is_iterable or right.is_iterable,
    )


def union_aliases(target: Aliases, source: Aliases) -> None:
    for name, value in source.items():
        target[name] = target.get(name, frozenset()) | value


def dotted(node: ast.expr, aliases: Aliases) -> frozenset[str]:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, frozenset({node.id}))
    if isinstance(node, ast.Attribute):
        return frozenset(f"{name}.{node.attr}" for name in dotted(node.value, aliases))
    if isinstance(node, ast.IfExp):
        return frozenset().union(*(dotted(arm, aliases) for arm in _arms(node)))
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


def _arms(node: ast.IfExp) -> tuple[ast.expr, ...]:
    if isinstance(node.test, ast.Constant):
        return (node.body if node.test.value else node.orelse,)
    return node.body, node.orelse


def _is_iterable(node: ast.expr | None, values: dict[str, Value]) -> bool:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
        return True
    if isinstance(node, ast.Name):
        return values.get(node.id, Value()).is_iterable
    if isinstance(node, ast.IfExp):
        return any(_is_iterable(arm, values) for arm in _arms(node))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_iterable(node.left, values) or _is_iterable(node.right, values)
    return False


def _concatenated_strings(node: ast.BinOp, values: dict[str, Value]) -> set[str]:
    parts = [strings(node.left, values), strings(node.right, values)]
    if any(
        not part and _is_iterable(operand, values)
        for part, operand in zip(parts, (node.left, node.right), strict=True)
    ):
        return set()
    return joined([part or {"?"} for part in parts])


def _formatted_field(node: ast.FormattedValue, values: dict[str, Value]) -> set[str]:
    known = _format_candidates(node.value, values)
    if not known:
        return {"?"}
    specs = strings(node.format_spec, values) if node.format_spec is not None else {""}
    if not specs:
        return {"?"}
    if len(known) * len(specs) > 256:
        raise ValueError("too many static alternatives in an attribute target")
    return {
        _format_literal(value, node.conversion, spec)
        for value in known
        for spec in specs
    }


def _format_candidates(node: ast.expr, values: dict[str, Value]) -> list[object]:
    if isinstance(node, ast.Constant):
        return [node.value]
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.UAdd, ast.USub))
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float, complex))
    ):
        value = node.operand.value
        return [-value if isinstance(node.op, ast.USub) else +value]
    return list(strings(node, values))


def _format_literal(value: object, conversion: int, spec: str) -> str:
    # Width and precision can request huge allocations; bound them before format.
    if len(spec) > 4096 or any(
        len(number) > 4 or int(number) > 4096 for number in re.findall(r"\d+", spec)
    ):
        raise ValueError("formatted attribute target exceeds the size limit")
    converters: dict[int, Callable[[object], str]] = {
        ord("s"): str,
        ord("r"): repr,
        ord("a"): ascii,
    }
    if conversion in converters:
        value = converters[conversion](value)
    try:
        result = format(value, spec)
    except TypeError, ValueError:
        return "?"
    if len(result) > 4096:
        raise ValueError("formatted attribute target exceeds the size limit")
    return result


def strings(node: ast.expr | None, values: dict[str, Value]) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name):
        return set(values.get(node.id, Value()).strings)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _concatenated_strings(node, values)
    if isinstance(node, ast.JoinedStr):
        return joined(
            [
                _formatted_field(part, values)
                if isinstance(part, ast.FormattedValue)
                else strings(part, values)
                for part in node.values
            ]
        )
    if isinstance(node, ast.IfExp):
        return set().union(*(strings(arm, values) for arm in _arms(node)))
    return set()


def _iterable_elements(node: ast.expr | None, values: dict[str, Value]) -> set[str]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return set().union(*(strings(item, values) for item in node.elts))
    if isinstance(node, ast.Name):
        return set(values.get(node.id, Value()).iterable_elements)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _iterable_elements(node.left, values) | _iterable_elements(
            node.right, values
        )
    if isinstance(node, ast.IfExp):
        return set().union(*(_iterable_elements(arm, values) for arm in _arms(node)))
    return set()


def iterable_strings(node: ast.expr, values: dict[str, Value]) -> set[str]:
    return (
        _iterable_elements(node, values)
        | mapping_keys(node, values)
        | set().union(*(set(value) for value in strings(node, values)))
    )


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
    if isinstance(node, ast.IfExp):
        return set().union(*(mapping_keys(arm, values) for arm in _arms(node)))
    return set()


def _mapping_strings(
    node: ast.expr, values: dict[str, Value]
) -> dict[str, frozenset[str]]:
    if isinstance(node, ast.Name):
        return dict(values.get(node.id, Value()).mapping_strings)
    entries: dict[str, frozenset[str]] = {}
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None:
                entries.update(_mapping_strings(value, values))
            else:
                entries.update(
                    (name, frozenset(strings(value, values)))
                    for name in strings(key, values)
                )
    elif isinstance(node, ast.IfExp):
        for arm in _arms(node):
            for entry_name, candidates in _mapping_strings(arm, values).items():
                entries[entry_name] = entries.get(entry_name, frozenset()) | candidates
    return entries


def value_of(node: ast.expr | None, values: dict[str, Value]) -> Value:
    return Value(
        frozenset(strings(node, values)),
        frozenset(mapping_keys(node, values)) if node is not None else frozenset(),
        frozenset(_iterable_elements(node, values)),
        tuple(sorted(_mapping_strings(node, values).items()))
        if node is not None
        else (),
        _is_iterable(node, values),
    )


def argument_strings(
    call: ast.Call, index: int, name: str, values: dict[str, Value]
) -> set[str]:
    if len(call.args) > index:
        return strings(call.args[index], values)
    candidates: set[str] = set()
    for keyword in call.keywords:
        if keyword.arg == name:
            candidates.update(strings(keyword.value, values))
        elif keyword.arg is None:
            candidates.update(_mapping_strings(keyword.value, values).get(name, ()))
    return candidates


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
            assigned = (
                Value(frozenset(iterable_strings(expression, values)))
                if iterated
                else value_of(expression, values)
            )
            current = _merged_value(previous, assigned)
            changed |= current != previous
            values[target] = current
            if isinstance(expression, (ast.Name, ast.Attribute, ast.IfExp)):
                resolved = dotted(expression, aliases)
                changed |= aliases.get(target) != resolved
                aliases[target] = resolved
        if not changed:
            break


def assignments(nodes: list[ast.AST]) -> Iterator[tuple[str, ast.expr, bool]]:
    for node in nodes:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                yield from _literal_assignments(target, node.value)
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


def _literal_assignments(
    target: ast.expr, expression: ast.expr
) -> Iterator[tuple[str, ast.expr, bool]]:
    for item, value in _literal_targets(target, expression):
        if isinstance(item, ast.Name) and value is not None:
            yield item.id, value, False


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
                assigned = value_of(value, values)
            bindings.append((item, resolved, assigned))
    return bindings


def pattern_bindings(
    pattern: ast.pattern,
    subject: ast.expr,
    values: dict[str, Value],
    aliases: Aliases,
) -> dict[str, tuple[Value, frozenset[str]]]:
    """Snapshot captures whose corresponding literal subject is statically known.

    Matching may still fail; the caller retains the pre-pattern failure path.
    """
    captured: dict[str, tuple[Value, frozenset[str]]] = {}
    for name, expression in _pattern_subjects(pattern, subject):
        value, resolved = value_of(expression, values), dotted(expression, aliases)
        if name in captured:
            previous_value, previous_aliases = captured[name]
            value = _merged_value(previous_value, value)
            resolved |= previous_aliases
        captured[name] = value, resolved
    return captured


def _pattern_subjects(
    pattern: ast.pattern, subject: ast.expr
) -> Iterator[tuple[str, ast.expr]]:
    if isinstance(pattern, ast.MatchAs):
        if pattern.name is not None:
            yield pattern.name, subject
        if pattern.pattern is not None:
            yield from _pattern_subjects(pattern.pattern, subject)
    elif isinstance(pattern, ast.MatchOr):
        for alternative in pattern.patterns:
            yield from _pattern_subjects(alternative, subject)
    elif isinstance(pattern, ast.MatchSequence):
        for subpattern, element in _literal_pattern_pairs(pattern, subject):
            yield from _pattern_subjects(subpattern, element)


def _literal_pattern_pairs(
    pattern: ast.MatchSequence, subject: ast.expr
) -> list[tuple[ast.pattern, ast.expr]]:
    if (
        isinstance(subject, (ast.Tuple, ast.List))
        and len(pattern.patterns) == len(subject.elts)
        and not any(isinstance(item, ast.Starred) for item in subject.elts)
    ):
        return list(zip(pattern.patterns, subject.elts, strict=True))
    return []


Env = tuple[dict[str, Value], Aliases]


class ClassValues(ScopeValues):
    """Class namespace plus the names actually assigned along this path."""

    def __init__(self, values: dict[str, Value]) -> None:
        super().__init__(values)
        self.bound: frozenset[str] = (
            values.bound if isinstance(values, ClassValues) else frozenset()
        )
        self.closure: Env = (self, {})


def copy_namespace(source: Env) -> tuple[ScopeValues, Aliases]:
    return ScopeValues(source[0]), dict(source[1])


def copy_context(closure: Env) -> tuple[ScopeValues, Aliases]:
    module = module_bindings(*closure)
    copied_module = copy_namespace(module)
    copied_module[0].module_bindings = copied_module
    copied = copied_module if closure[0] is module[0] else copy_namespace(closure)
    copied[0].module_bindings = copied_module
    return copied


def publish_context(target: Env, source: Env) -> None:
    pairs = [(module_bindings(*target), module_bindings(*source)), (target, source)]
    for destination, original in pairs:
        destination[0].clear()
        destination[0].update(original[0])
        destination[1].clear()
        destination[1].update(original[1])


def namespace_key(namespace: Env) -> tuple:
    return tuple(sorted(namespace[0].items())), tuple(sorted(namespace[1].items()))


def merge_namespace(target: Env, source: Env) -> None:
    union_values(target[0], source[0])
    for name in target[1].keys() | source[1].keys():
        fallback = frozenset({name})
        target[1][name] = target[1].get(name, fallback) | source[1].get(name, fallback)


@dataclass
class State:
    values: ClassValues
    aliases: Aliases
    closure: Env

    def copy(self) -> State:
        closure = copy_context(self.closure)
        values = ClassValues(self.values)
        values.module_bindings = module_bindings(*closure)
        values.closure = closure
        return State(values, dict(self.aliases), closure)

    def key(self) -> tuple:
        return (
            namespace_key((self.values, self.aliases)),
            self.values.bound,
            self.values.class_locals,
            self.values.class_globals,
            self.values.class_nonlocals,
            namespace_key(self.closure),
            namespace_key(module_bindings(*self.closure)),
        )

    def refresh(self) -> None:
        module = module_bindings(*self.closure)
        names = (
            self.values.keys()
            | self.aliases.keys()
            | module[0].keys()
            | module[1].keys()
            | self.closure[0].keys()
            | self.closure[1].keys()
        )
        for name in (
            (names - self.values.bound)
            | self.values.class_globals
            | self.values.class_nonlocals
        ):
            owner = (
                module
                if name in self.values.class_locals | self.values.class_globals
                else self.closure
            )
            self.values.pop(name, None)
            self.aliases.pop(name, None)
            if name in owner[0]:
                self.values[name] = owner[0][name]
            if name in owner[1]:
                self.aliases[name] = owner[1][name]

    def merge(self, other: State) -> None:
        merge_namespace((self.values, self.aliases), (other.values, other.aliases))
        merge_namespace(self.closure, other.closure)
        merge_namespace(module_bindings(*self.closure), module_bindings(*other.closure))
        # A mixed bound/unbound path has already joined both lookup alternatives.
        self.values.bound |= other.values.bound
