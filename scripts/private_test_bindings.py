"""Static values and binding snapshots used by the private-access guard."""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from itertools import product


@dataclass(frozen=True)
class Argument:
    """An unevaluated call value with its assignment-time scalar bindings."""

    expression: ast.expr
    bindings: tuple[tuple[str, Value], ...]

    def strings(self) -> set[str]:
        return strings(self.expression, dict(self.bindings))

    def value(self) -> Value:
        return value_of(self.expression, dict(self.bindings))

    def aliases(self) -> frozenset[str]:
        return self.value().callables

    def object_kinds(self) -> frozenset[str]:
        return _object_kinds(self.expression, dict(self.bindings))

    def own_object(self) -> bool:
        kinds = self.object_kinds()
        return bool(kinds) and kinds <= {"self", "cls"} and not self.strings()


@dataclass(frozen=True)
class Arguments:
    positional: tuple[Argument, ...]
    keywords: tuple[tuple[str, Argument], ...]

    def argument(self, index: int, name: str) -> Argument | None:
        if len(self.positional) > index:
            return self.positional[index]
        return dict(self.keywords).get(name)


@dataclass(frozen=True)
class Value:
    strings: frozenset[str] = frozenset()
    mapping_keys: frozenset[str] = frozenset()
    iterable_elements: frozenset[str] = frozenset()
    mapping_alternatives: tuple[tuple[tuple[str, Argument], ...], ...] = ()
    is_iterable: bool = False
    object_kinds: frozenset[str] = frozenset()
    errors: frozenset[str] = frozenset()
    mapping_errors: frozenset[str] = frozenset()
    iteration_errors: frozenset[str] = frozenset()
    positions: tuple[tuple[Argument | None, ...], ...] = ()
    position_errors: frozenset[str] = frozenset()
    target_mappings: tuple[tuple[tuple[str, Argument], ...], ...] = ()
    target_errors: frozenset[str] = frozenset()
    target_key_options: frozenset[bool] = frozenset()
    callables: frozenset[str] = frozenset()


Aliases = dict[str, frozenset[str]]


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
        names.update(declared_names(nodes, ast.Global))
        names.difference_update(declared_names(nodes, ast.Nonlocal))
    return names


def declared_names(
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
    empty_positions: tuple[Positions, ...] = ()
    empty_mappings: tuple[Mapping, ...] = ()
    positions, position_errors = _deferred(
        lambda: _bounded_positions(iter(left.positions + right.positions)),
        empty_positions,
    )
    targets, target_errors = _deferred(
        lambda: _bounded_mappings(iter(left.target_mappings + right.target_mappings)),
        empty_mappings,
    )
    return Value(
        left.strings | right.strings,
        left.mapping_keys | right.mapping_keys,
        left.iterable_elements | right.iterable_elements,
        tuple(dict.fromkeys(left.mapping_alternatives + right.mapping_alternatives)),
        left.is_iterable or right.is_iterable,
        left.object_kinds | right.object_kinds,
        left.errors | right.errors,
        left.mapping_errors | right.mapping_errors,
        left.iteration_errors | right.iteration_errors,
        positions,
        left.position_errors | right.position_errors | position_errors,
        targets,
        left.target_errors | right.target_errors | target_errors,
        left.target_key_options | right.target_key_options,
        left.callables | right.callables,
    )


def union_aliases(target: Aliases, source: Aliases) -> None:
    for name, value in source.items():
        target[name] = target.get(name, frozenset()) | value


def dotted(node: ast.expr, aliases: Aliases) -> frozenset[str]:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, frozenset({node.id}))
    if isinstance(node, ast.Attribute):
        return frozenset().union(
            *(
                _member_aliases(name, node.attr, aliases)
                for name in dotted(node.value, aliases)
            )
        )
    if isinstance(node, ast.Call):
        if (result := aliases.get(f"@call:{id(node)}")) is not None:
            return result
        classes = class_aliases(node.func, aliases)
        return frozenset(f"{name}/{id(node)}" for name in classes) or frozenset({""})
    if isinstance(node, ast.IfExp):
        return frozenset().union(*(dotted(arm, aliases) for arm in _arms(node)))
    return frozenset({""})


def _member_aliases(owner: str, member: str, aliases: Aliases) -> frozenset[str]:
    resolved = alias_value(_bounded_alias(f"{owner}.{member}"), aliases)
    instance = owner.startswith("@class:") and "/" in owner and "." not in owner
    return frozenset(
        f"@bound:{name}" if instance and name.startswith("@function:") else name
        for name in resolved
    )


def class_aliases(node: ast.expr, aliases: Aliases) -> frozenset[str]:
    return frozenset(
        name
        for name in dotted(node, aliases)
        if name.startswith("@class:") and "." not in name and "/" not in name
    )


def alias_value(name: str, aliases: Aliases) -> frozenset[str]:
    values = alias_options(name, aliases)
    if "@inherit" in values:
        owner, _, member = name.partition(".")
        fallback = f"{owner.split('/')[0]}.{member}"
        return (values - {"@inherit"}) | aliases.get(fallback, frozenset({fallback}))
    return values


def alias_options(name: str, aliases: Aliases) -> frozenset[str]:
    """Keep inheritance live through conditional stores and namespace joins."""
    if name in aliases:
        return aliases[name]
    inherited = name.startswith("@class:") and "/" in name and "." in name
    return frozenset({"@inherit" if inherited else name})


def _bounded_alias(name: str) -> str:
    # Consumers inspect the callable suffix. A marker retains the distinction
    # between a long path ending in builtins.setattr and the exact builtin.
    parts = name.split(".")
    return name if len(parts) <= 4 else "*." + ".".join(parts[-3:])


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
        value = values.get(node.id, Value())
        if value.errors:
            raise ValueError(sorted(value.errors)[0])
        return set(value.strings)
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
        value = values.get(node.id, Value())
        if value.iteration_errors:
            raise ValueError(sorted(value.iteration_errors)[0])
        return set(value.iterable_elements)
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
        value = values.get(node.id, Value())
        if value.mapping_errors:
            raise ValueError(sorted(value.mapping_errors)[0])
        return set(value.mapping_keys)
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


def _capture_argument(node: ast.expr, values: dict[str, Value]) -> Argument:
    names = {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}
    # A requested keyword needs scalar expression inputs, not recursively nested
    # mapping payloads. This also prevents self-referencing data from growing
    # snapshots during fixed-point analysis.
    snapshot = tuple(
        (
            name,
            Value(
                value.strings,
                value.mapping_keys,
                value.iterable_elements,
                (),
                value.is_iterable,
                value.object_kinds,
                value.errors,
                value.mapping_errors,
                value.iteration_errors,
                callables=value.callables,
            ),
        )
        for name in sorted(names)
        if (value := values.get(name)) is not None
    )
    return Argument(node, snapshot)


Mapping = tuple[tuple[str, Argument], ...]


def _bounded_mappings(options: Iterator[Mapping]) -> tuple[Mapping, ...]:
    unique: dict[Mapping, None] = {}
    for option in options:
        unique[option] = None
        if len(unique) > 256:
            raise ValueError("too many static alternatives in keyword arguments")
    return tuple(unique)


def _overlay_mappings(
    left: tuple[Mapping, ...], right: tuple[Mapping, ...]
) -> tuple[Mapping, ...]:
    return _bounded_mappings(
        tuple(sorted((dict(before) | dict(after)).items()))
        for before in left
        for after in right
    )


def _mapping_alternatives(
    node: ast.expr, values: dict[str, Value], *, target: str | None = None
) -> tuple[Mapping, ...]:
    if isinstance(node, ast.Name):
        stored = values.get(node.id, Value())
        errors = stored.target_errors if target else stored.mapping_errors
        if errors:
            raise ValueError(sorted(errors)[0])
        mappings = stored.target_mappings if target else stored.mapping_alternatives
        return mappings or ((),)
    if isinstance(node, ast.IfExp):
        return _bounded_mappings(
            mapping
            for arm in _arms(node)
            for mapping in _mapping_alternatives(arm, values, target=target)
        )
    if not isinstance(node, ast.Dict):
        return ((),)
    options: tuple[Mapping, ...] = ((),)
    for key, value in zip(node.keys, node.values, strict=True):
        additions = (
            _mapping_alternatives(value, values, target=target)
            if key is None
            else _mapping_item(key, value, values, target)
        )
        options = _overlay_mappings(options, additions or ((),))
    return options


def _mapping_item(
    key: ast.expr, value: ast.expr, values: dict[str, Value], target: str | None
) -> tuple[Mapping, ...]:
    if target is None:
        return tuple(
            ((name, _capture_argument(value, values)),) for name in strings(key, values)
        )
    return tuple(
        ((target, _capture_argument(value, values)),) if matches else ()
        for matches in _target_key(key, values, target)
    )


def _target_key(node: ast.expr, values: dict[str, Value], target: str) -> set[bool]:
    if isinstance(node, ast.Name) and (stored := values.get(node.id)) is not None:
        if stored.target_key_options:
            return set(stored.target_key_options)
    if isinstance(node, ast.IfExp):
        return set().union(*(_target_key(arm, values, target) for arm in _arms(node)))
    return {name == target for name in _key_strings(node, values)} or {False}


def _key_strings(node: ast.expr, values: dict[str, Value]) -> set[str]:
    if isinstance(node, ast.JoinedStr):
        return joined(
            [
                _key_field(part, values)
                if isinstance(part, ast.FormattedValue)
                else _key_strings(part, values)
                for part in node.values
            ]
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return joined(
            [_key_strings(node.left, values), _key_strings(node.right, values)]
        )
    if isinstance(node, ast.IfExp):
        return set().union(*(_key_strings(arm, values) for arm in _arms(node)))
    return strings(node, values)


def _key_field(node: ast.FormattedValue, values: dict[str, Value]) -> set[str]:
    known = _format_candidates(node.value, values)
    specs = strings(node.format_spec, values) if node.format_spec else {""}
    if len(known) * len(specs) > 256:
        raise ValueError("too many static alternatives in an attribute target")
    return {
        _key_format(value, node.conversion, spec) for value in known for spec in specs
    } or {"?"}


def _key_format(value: object, conversion: int, spec: str) -> str:
    spec = re.sub(r"\d+", lambda match: match[0].lstrip("0") or "0", spec)
    # String precision truncates; a huge precision does not enlarge a short key.
    if isinstance(value, str) and conversion in {-1, ord("s")}:
        match = re.search(r"\.(\d+)(?=s?$)", spec)
        if match:
            digits = match[1].lstrip("0") or "0"
            precision = len(value) if len(digits) > 4 else min(int(digits), len(value))
            spec = spec[: match.start()] + f".{precision}" + spec[match.end() :]
    try:
        return _format_literal(value, conversion, spec)
    except ValueError as error:
        if str(error) != "formatted attribute target exceeds the size limit":
            raise
        return "?"


def _object_kinds(node: ast.expr | None, values: dict[str, Value]) -> frozenset[str]:
    if isinstance(node, ast.Name):
        value = values.get(node.id)
        if value is not None:
            return value.object_kinds or (
                frozenset() if value.strings else frozenset({""})
            )
        return frozenset({node.id if node.id in {"self", "cls"} else ""})
    if isinstance(node, ast.IfExp):
        return frozenset().union(*(_object_kinds(arm, values) for arm in _arms(node)))
    if isinstance(node, (ast.JoinedStr, ast.Constant)):
        return (
            frozenset()
            if isinstance(node, ast.JoinedStr) or isinstance(node.value, str)
            else frozenset({""})
        )
    return frozenset() if strings(node, values) else frozenset({""})


def _deferred[T](resolve: Callable[[], T], empty: T) -> tuple[T, frozenset[str]]:
    try:
        return resolve(), frozenset()
    except ValueError as error:
        return empty, frozenset({str(error)})


def _value_aliases(node: ast.expr | None, values: dict[str, Value]) -> frozenset[str]:
    if isinstance(node, ast.Name):
        stored = values.get(node.id)
        return stored.callables if stored is not None else frozenset({node.id})
    if isinstance(node, ast.Attribute):
        return frozenset(
            _bounded_alias(f"{name}.{node.attr}")
            for name in _value_aliases(node.value, values)
        )
    if isinstance(node, ast.IfExp):
        return frozenset().union(*(_value_aliases(arm, values) for arm in _arms(node)))
    return frozenset()


def value_of(node: ast.expr | None, values: dict[str, Value]) -> Value:
    scalar, errors = _deferred(lambda: strings(node, values), set[str]())
    keys, key_errors = _deferred(
        lambda: mapping_keys(node, values) if node is not None else set(), set[str]()
    )
    elements, iteration_errors = _deferred(
        lambda: _iterable_elements(node, values), set[str]()
    )
    empty_mappings: tuple[Mapping, ...] = ()
    mappings, mapping_errors = _deferred(
        lambda: _mapping_alternatives(node, values) if node is not None else (),
        empty_mappings,
    )
    kinds, _ = _deferred(lambda: _object_kinds(node, values), frozenset[str]())
    empty_positions: tuple[Positions, ...] = ()
    positions, position_errors = _deferred(
        lambda: _sequence_positions(node, values), empty_positions
    )
    targets, target_errors = _deferred(
        lambda: (
            _mapping_alternatives(node, values, target="in_dict")
            if node is not None
            else ()
        ),
        empty_mappings,
    )
    target_keys, _ = _deferred(
        lambda: _target_key(node, values, "in_dict") if node is not None else set(),
        set[bool](),
    )
    return Value(
        frozenset(scalar),
        frozenset(keys),
        frozenset(elements),
        mappings,
        _is_iterable(node, values),
        kinds,
        errors,
        key_errors | mapping_errors,
        iteration_errors,
        positions,
        position_errors,
        targets,
        target_errors,
        frozenset(target_keys),
        _value_aliases(node, values),
    )


def iterated_value(node: ast.expr, values: dict[str, Value]) -> Value:
    try:
        return Value(frozenset(iterable_strings(node, values)))
    except ValueError as error:
        return Value(errors=frozenset({str(error)}))


Positions = tuple[Argument | None, ...]


def _bounded_positions(options: Iterator[Positions]) -> tuple[Positions, ...]:
    unique: dict[Positions, None] = {}
    for option in options:
        if len(option) > 256 or (len(unique) >= 256 and option not in unique):
            raise ValueError("too many static alternatives in positional arguments")
        unique[option] = None
    return tuple(unique)


def _sequence_positions(
    node: ast.expr | None, values: dict[str, Value]
) -> tuple[Positions, ...]:
    if isinstance(node, (ast.Tuple, ast.List)):
        return _expanded_positions(node.elts, values)
    if isinstance(node, ast.Name):
        stored = values.get(node.id, Value())
        if stored.position_errors:
            raise ValueError(sorted(stored.position_errors)[0])
        return stored.positions or ((None,),)
    if isinstance(node, ast.IfExp):
        return _bounded_positions(
            option for arm in _arms(node) for option in _sequence_positions(arm, values)
        )
    return ((None,),)


def _expanded_positions(
    expressions: list[ast.expr], values: dict[str, Value]
) -> tuple[Positions, ...]:
    options: tuple[Positions, ...] = ((),)
    for expression in expressions:
        additions = (
            _sequence_positions(expression.value, values)
            if isinstance(expression, ast.Starred)
            else ((_capture_argument(expression, values),),)
        )
        options = _bounded_positions(
            before if None in before else before + after
            for before in options
            for after in additions
        )
    return options


def call_arguments(
    call: ast.Call, values: dict[str, Value], *, keyword_target: str | None = None
) -> list[Arguments]:
    options = (
        option
        for positions in _expanded_positions(call.args, values)
        for option in _keyword_arguments(
            call,
            values,
            tuple(item for item in positions if item is not None),
            keyword_target,
        )
    )
    unique: dict[Arguments, None] = {}
    for option in options:
        unique[option] = None
        if len(unique) > 256:
            raise ValueError("too many static alternatives in call arguments")
    return list(unique)


def _keyword_arguments(
    call: ast.Call,
    values: dict[str, Value],
    positional: tuple[Argument, ...],
    keyword_target: str | None,
) -> list[Arguments]:
    if keyword_target is not None:
        if positional:
            return [Arguments(positional, ())]
        for keyword in call.keywords:
            if keyword.arg == keyword_target:
                return [
                    Arguments(
                        (),
                        ((keyword_target, _capture_argument(keyword.value, values)),),
                    )
                ]
    options: tuple[Mapping, ...] = ((),)
    for keyword in call.keywords:
        additions = (
            _mapping_alternatives(keyword.value, values, target=keyword_target)
            if keyword.arg is None
            else (((keyword.arg, _capture_argument(keyword.value, values)),),)
        )
        options = _overlay_mappings(options, additions)
    return [Arguments(positional, kwargs) for kwargs in options]


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
                iterated_value(expression, values)
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
            if isinstance(item, (ast.Name, ast.Attribute)) and expression is not None:
                resolved = (
                    dotted(value, aliases) if value is not None else frozenset({""})
                )
                if isinstance(item, ast.Name):
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
        yield from _or_pattern_subjects(pattern, subject)
    elif isinstance(pattern, ast.MatchSequence):
        for subpattern, element in _literal_pattern_pairs(pattern, subject):
            yield from _pattern_subjects(subpattern, element)


def _or_pattern_subjects(
    pattern: ast.MatchOr, subject: ast.expr
) -> Iterator[tuple[str, ast.expr]]:
    for alternative in pattern.patterns:
        matched = pattern_matches(alternative, subject)
        if matched is not False:
            yield from _pattern_subjects(alternative, subject)
        if matched is True:
            break


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


def literal_iterations(expression: ast.expr, aliases: Aliases) -> list[ast.expr] | None:
    """Snapshot short literal iterables without executing fixture code."""
    if isinstance(expression, (ast.List, ast.Tuple)) and not any(
        isinstance(item, ast.Starred) for item in expression.elts
    ):
        return list(expression.elts)
    if not isinstance(expression, ast.Call) or expression.keywords:
        return None
    names = dotted(expression.func, aliases)
    if not names or not names <= {"range", "builtins.range"}:
        return None
    try:
        arguments = [ast.literal_eval(item) for item in expression.args]
        if not 1 <= len(arguments) <= 3 or not all(
            isinstance(item, int) for item in arguments
        ):
            return None
        items = range(*arguments)
        if len(items) > 256:
            return None
    except ValueError, TypeError, OverflowError:
        return None
    return [ast.Constant(value=item) for item in items]


def pattern_matches(pattern: ast.pattern, subject: ast.expr) -> bool | None:
    """Reject definite literal mismatches; retain both paths for unknown values."""
    if isinstance(pattern, ast.MatchAs):
        return (
            True
            if pattern.pattern is None
            else pattern_matches(pattern.pattern, subject)
        )
    if isinstance(pattern, ast.MatchOr):
        alternatives = [pattern_matches(item, subject) for item in pattern.patterns]
        return (
            True if True in alternatives else (None if None in alternatives else False)
        )
    if any(isinstance(node, ast.Call) for node in ast.walk(subject)):
        # literal_eval specially accepts set(), but that name can be shadowed.
        return None
    try:
        value = ast.literal_eval(subject)
        if isinstance(pattern, ast.MatchValue):
            return bool(value == ast.literal_eval(pattern.value))
        if isinstance(pattern, ast.MatchSingleton):
            return value is pattern.value
    except ValueError, TypeError:
        return None
    if isinstance(pattern, ast.MatchSequence):
        return _sequence_matches(pattern, value)
    return None


def _sequence_matches(pattern: ast.MatchSequence, value: object) -> bool | None:
    if not isinstance(value, (tuple, list)):
        return False
    if any(isinstance(item, ast.MatchStar) for item in pattern.patterns):
        return None
    if len(value) != len(pattern.patterns):
        return False
    alternatives = [
        pattern_matches(item, ast.Constant(value=element))
        for item, element in zip(pattern.patterns, value, strict=True)
    ]
    return False if False in alternatives else (None if None in alternatives else True)


def scope_headers(scope: ast.AST) -> Iterator[ast.expr]:
    """Decorators, bases and defaults execute before entering the new scope."""
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        yield from scope.decorator_list
    if isinstance(scope, ast.ClassDef):
        yield from scope.bases
        yield from (keyword.value for keyword in scope.keywords)
    if isinstance(scope, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
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


def comprehension_nodes(
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
