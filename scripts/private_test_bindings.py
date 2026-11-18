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
        self.scope_kind = values.scope_kind if isinstance(values, ScopeValues) else "module"
        self.lexical_names: frozenset[str] = (
            values.lexical_names if isinstance(values, ScopeValues) else frozenset()
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
    values.scope_kind = "class"
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
        (name, Value(value.strings, value.mapping_keys, value.iterable_elements,
                     (), value.is_iterable, value.object_kinds, value.errors,
                     value.mapping_errors, value.iteration_errors))
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


def _overlay_mappings(left: tuple[Mapping, ...], right: tuple[Mapping, ...]) -> tuple[Mapping, ...]:
    return _bounded_mappings(
        tuple(sorted((dict(before) | dict(after)).items()))
        for before in left for after in right
    )


def _mapping_alternatives(node: ast.expr, values: dict[str, Value]) -> tuple[Mapping, ...]:
    if isinstance(node, ast.Name):
        value = values.get(node.id, Value())
        if value.mapping_errors:
            raise ValueError(sorted(value.mapping_errors)[0])
        return value.mapping_alternatives or ((),)
    if isinstance(node, ast.IfExp):
        return _bounded_mappings(
            mapping for arm in _arms(node) for mapping in _mapping_alternatives(arm, values)
        )
    if not isinstance(node, ast.Dict):
        return ((),)
    options: tuple[Mapping, ...] = ((),)
    for key, value in zip(node.keys, node.values, strict=True):
        additions = (
            _mapping_alternatives(value, values)
            if key is None
            else tuple(((name, _capture_argument(value, values)),) for name in strings(key, values))
        )
        options = _overlay_mappings(options, additions or ((),))
    return options


def _object_kinds(node: ast.expr | None, values: dict[str, Value]) -> frozenset[str]:
    if isinstance(node, ast.Name):
        value = values.get(node.id)
        if value is not None:
            return value.object_kinds or (frozenset() if value.strings else frozenset({""}))
        return frozenset({node.id if node.id in {"self", "cls"} else ""})
    if isinstance(node, ast.IfExp):
        return frozenset().union(*(_object_kinds(arm, values) for arm in _arms(node)))
    if isinstance(node, (ast.JoinedStr, ast.Constant)):
        return frozenset() if isinstance(node, ast.JoinedStr) or isinstance(node.value, str) else frozenset({""})
    return frozenset() if strings(node, values) else frozenset({""})


def _deferred[T](resolve: Callable[[], T], empty: T) -> tuple[T, frozenset[str]]:
    try:
        return resolve(), frozenset()
    except ValueError as error:
        return empty, frozenset({str(error)})


def value_of(node: ast.expr | None, values: dict[str, Value]) -> Value:
    scalar, errors = _deferred(lambda: strings(node, values), set[str]())
    keys, key_errors = _deferred(
        lambda: mapping_keys(node, values) if node is not None else set(), set[str]()
    )
    elements, iteration_errors = _deferred(
        lambda: _iterable_elements(node, values), set[str]()
    )
    mappings, mapping_errors = _deferred(
        lambda: _mapping_alternatives(node, values) if node is not None else (), ()
    )
    kinds, _ = _deferred(lambda: _object_kinds(node, values), frozenset[str]())
    return Value(
        frozenset(scalar), frozenset(keys), frozenset(elements), mappings,
        _is_iterable(node, values), kinds, errors,
        key_errors | mapping_errors, iteration_errors,
    )


def iterated_value(node: ast.expr, values: dict[str, Value]) -> Value:
    try:
        return Value(frozenset(iterable_strings(node, values)))
    except ValueError as error:
        return Value(errors=frozenset({str(error)}))


def call_arguments(call: ast.Call, values: dict[str, Value]) -> list[Arguments]:
    positional = tuple(_capture_argument(expression, values) for expression in call.args)
    options: tuple[Mapping, ...] = ((),)
    for keyword in call.keywords:
        additions = (
            _mapping_alternatives(keyword.value, values)
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
    """Resolved class lookup plus stored-local alternatives and definite bindings."""

    def __init__(self, values: dict[str, Value]) -> None:
        super().__init__(values)
        self.bound: frozenset[str] = (
            values.bound if isinstance(values, ClassValues) else frozenset()
        )
        self.local_values: dict[str, Value] = (
            dict(values.local_values) if isinstance(values, ClassValues) else {}
        )
        self.local_aliases: Aliases = (
            dict(values.local_aliases) if isinstance(values, ClassValues) else {}
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
            namespace_key((self.values.local_values, self.values.local_aliases)),
            self.values.class_locals,
            self.values.class_globals,
            self.values.class_nonlocals,
            self.values.scope_kind,
            self.values.lexical_names,
            namespace_key(self.closure),
            namespace_key(module_bindings(*self.closure)),
        )

    def refresh(self) -> None:
        module = module_bindings(*self.closure)
        names = (
            self.values.keys()
            | self.aliases.keys()
            | self.values.local_values.keys()
            | self.values.local_aliases.keys()
            | module[0].keys()
            | module[1].keys()
            | self.closure[0].keys()
            | self.closure[1].keys()
        )
        for name in names:
            self.refresh_name(name, module)

    def refresh_name(self, name: str, module: Env) -> None:
        if self.values.scope_kind != "class":
            self.refresh_lexical_name(name, module)
            return
        external = name in self.values.class_globals | self.values.class_nonlocals
        owner = (
            module
            if name in self.values.class_locals | self.values.class_globals
            else self.closure
        )
        self.values.pop(name, None)
        self.aliases.pop(name, None)
        if external or name not in self.values.bound:
            if name in owner[0]:
                self.values[name] = owner[0][name]
            if name in owner[1]:
                self.aliases[name] = owner[1][name]
        if external:
            return
        # Stored-local alternatives exclude the old fallback value, so a later
        # owner write can replace that fallback without erasing a bound path.
        if name in self.values.local_values:
            union_values(self.values, {name: self.values.local_values[name]})
        if name in self.values.local_aliases:
            fallback = frozenset() if name in self.values.bound else frozenset({name})
            self.aliases[name] = (
                self.aliases.get(name, fallback) | self.values.local_aliases[name]
            )

    def refresh_lexical_name(self, name: str, module: Env) -> None:
        owner = (
            self.closure
            if self.values.scope_kind == "function" and name in self.values.lexical_names
            else module
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
        union_values(self.values.local_values, other.values.local_values)
        union_aliases(self.values.local_aliases, other.values.local_aliases)
        self.values.bound &= other.values.bound


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
    except (ValueError, TypeError, OverflowError):
        return None
    return [ast.Constant(value=item) for item in items]


def pattern_matches(pattern: ast.pattern, subject: ast.expr) -> bool | None:
    """Reject definite literal mismatches; retain both paths for unknown values."""
    if isinstance(pattern, ast.MatchAs):
        return True if pattern.pattern is None else pattern_matches(pattern.pattern, subject)
    if isinstance(pattern, ast.MatchOr):
        alternatives = [pattern_matches(item, subject) for item in pattern.patterns]
        return True if True in alternatives else (None if None in alternatives else False)
    try:
        value = ast.literal_eval(subject)
        if isinstance(pattern, ast.MatchValue):
            return value == ast.literal_eval(pattern.value)
        if isinstance(pattern, ast.MatchSingleton):
            return value is pattern.value
    except (ValueError, TypeError):
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
