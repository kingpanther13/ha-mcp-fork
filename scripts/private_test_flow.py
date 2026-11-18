"""Scope state and deferred execution for the private-access guard."""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts import private_test_bindings as binding_rules
else:
    import private_test_bindings as binding_rules

Value = binding_rules.Value
Aliases = binding_rules.Aliases


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
        self.scope_kind: str = (
            values.scope_kind if isinstance(values, ScopeValues) else "module"
        )
        self.lexical_names: frozenset[str] = (
            values.lexical_names if isinstance(values, ScopeValues) else frozenset()
        )
        self.scope_id: ast.AST | None = (
            values.scope_id if isinstance(values, ScopeValues) else None
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
    values.class_globals = frozenset(binding_rules.declared_names(nodes, ast.Global))
    values.class_nonlocals = frozenset(
        binding_rules.declared_names(nodes, ast.Nonlocal)
    )
    values.class_locals = (
        frozenset(binding_rules.bound_names(nodes, class_scope=True))
        - values.class_globals
    )
    aliases = dict(parent_aliases)
    outer_values, outer_aliases = module_bindings(parent_values, parent_aliases)
    # Class-bound names use LOAD_NAME's module fallback until assigned;
    # only free names capture an enclosing function's lexical bindings.
    for name in binding_rules.bound_names(nodes, class_scope=True):
        values.pop(name, None)
        aliases.pop(name, None)
        if name in outer_values:
            values[name] = outer_values[name]
        if name in outer_aliases:
            aliases[name] = outer_aliases[name]
    return values, aliases


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
    binding_rules.union_values(target[0], source[0])
    for name in target[1].keys() | source[1].keys():
        target[1][name] = binding_rules.alias_options(
            name, target[1]
        ) | binding_rules.alias_options(name, source[1])


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
            self.values.scope_id,
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
            binding_rules.union_values(
                self.values, {name: self.values.local_values[name]}
            )
        if name in self.values.local_aliases:
            fallback = frozenset() if name in self.values.bound else frozenset({name})
            self.aliases[name] = (
                self.aliases.get(name, fallback) | self.values.local_aliases[name]
            )

    def refresh_lexical_name(self, name: str, module: Env) -> None:
        owner = (
            self.closure
            if self.values.scope_kind == "function"
            and name in self.values.lexical_names
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
        binding_rules.union_values(self.values.local_values, other.values.local_values)
        binding_rules.union_aliases(
            self.values.local_aliases, other.values.local_aliases
        )
        self.values.bound &= other.values.bound


@dataclass(frozen=True)
class Function:
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
    owner: ast.AST | None
    defaults: tuple[tuple[str, Value], ...]
    kind: str


@dataclass(frozen=True)
class Invocation:
    token: str
    parameters: tuple[tuple[str, Value], ...]


@dataclass
class Analysis:
    findings: tuple[tuple[int, str], ...]
    outcomes: dict[str, State]


@dataclass
class Effects:
    raised: list[Env]
    normal: bool = True


class Interrupted(Exception):
    """A known call has no normal continuation."""


def body_nodes(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
) -> Iterator[ast.AST]:
    pending: list[ast.AST] = (
        [node.body] if isinstance(node, ast.Lambda) else list(node.body)
    )
    while pending:
        child = pending.pop()
        yield child
        if not isinstance(
            child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        ):
            pending.extend(ast.iter_child_nodes(child))


def mapping_delta[T](
    target: dict[str, T], before: dict[str, T], after: dict[str, T]
) -> None:
    for name in before.keys() | after.keys():
        if name not in after:
            target.pop(name, None)
        elif name not in before or before[name] != after[name]:
            target[name] = after[name]


def apply_delta(target: Env, before: Env, after: Env) -> None:
    mapping_delta(target[0], before[0], after[0])
    mapping_delta(target[1], before[1], after[1])


def apply_effects(
    outcomes: dict[str, State], before: State, effects: Effects
) -> dict[str, State]:
    if not effects.normal:
        outcomes.pop("normal", None)
        outcomes.pop("return", None)
    for closure in effects.raised:
        raised = before.copy()
        publish_context(raised.closure, closure)
        raised.refresh()
        if "raise" in outcomes:
            outcomes["raise"].merge(raised)
        else:
            outcomes["raise"] = raised
    return outcomes


class Deferred:
    def __init__(
        self,
        scan_scope: Callable[..., Iterator[tuple[int, str]]],
        scan_expression: Callable[..., Iterator[tuple[int, str]]],
    ) -> None:
        self.scan_scope = scan_scope
        self.scan_expression = scan_expression
        self.scopes: dict[str, Function] = {}
        self.frames: dict[ast.AST | None, Env] = {}
        self.pending: list[set[str]] = []
        self.active: set[ast.AST] = set()
        self.cache: dict[tuple, Analysis] = {}
        self.immediate: set[ast.Lambda] = set()
        self.suspended: dict[str, Invocation] = {}
        self.effects: list[Effects] = []
        self.awaited: set[ast.Call] = set()

    def register(
        self,
        node: ast.AST,
        closure: Env,
        env: Env | None = None,
        *,
        method: bool = False,
    ) -> str:
        assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        env = closure if env is None else env
        token = f"@function:{id(node)}"
        owner = closure[0].scope_id if isinstance(closure[0], ScopeValues) else None
        positional = [*node.args.posonlyargs, *node.args.args]
        defaults = list(
            zip(
                positional[len(positional) - len(node.args.defaults) :],
                node.args.defaults,
                strict=True,
            )
        )
        defaults.extend(
            (arg, value)
            for arg, value in zip(
                node.args.kwonlyargs, node.args.kw_defaults, strict=True
            )
            if value is not None
        )
        captured = tuple((arg.arg, self.capture(value, env)) for arg, value in defaults)
        kind = "async" if isinstance(node, ast.AsyncFunctionDef) else "sync"
        if kind == "sync" and any(
            isinstance(child, (ast.Yield, ast.YieldFrom)) for child in body_nodes(node)
        ):
            kind = "generator"
        self.scopes[token] = Function(node, owner, captured, kind)
        self.pending[-1].add(token)
        if method and not isinstance(node, ast.Lambda):
            decorators = set().union(
                *(binding_rules.dotted(item, env[1]) for item in node.decorator_list)
            )
            if decorators & {"staticmethod", "builtins.staticmethod"}:
                return f"@static:{token}"
            if decorators & {"classmethod", "builtins.classmethod"}:
                return f"@bound:{token}"
        return token

    def capture(self, node: ast.expr, env: Env) -> Value:
        return replace(
            binding_rules.value_of(node, env[0]),
            callables=binding_rules.dotted(node, env[1]),
        )

    def remember(self, closure: Env) -> None:
        if isinstance(closure[0], ScopeValues):
            self.frames[closure[0].scope_id] = closure

    def bind_alias(
        self, name: str, aliases: frozenset[str], env: Env, closure: Env
    ) -> None:
        env[1][name] = aliases
        module_bindings(*closure)[1][name] = aliases

    def allocate(
        self, call: ast.Call, classes: frozenset[str], env: Env, closure: Env
    ) -> None:
        key = f"@allocation:{id(call)}"
        counts = env[1].get(key, frozenset({"0"}))
        following = frozenset(
            str(min(int(value) + 1, 3)) if value in {"0", "1", "2"} else "3"
            for value in counts
        )
        self.bind_alias(key, following, env, closure)
        aliases = frozenset(
            f"{name}/{id(call)}/{count if count != '3' else '*'}"
            for name in classes
            for count in following
        )
        self.bind_alias(f"@call:{id(call)}", aliases, env, closure)

    def parameters(
        self,
        token: str,
        arguments: binding_rules.Arguments | None,
        *,
        bound: bool = False,
    ) -> tuple[tuple[str, Value], ...]:
        function = self.scopes[token]
        args = function.node.args
        names = [*args.posonlyargs, *args.args]
        values = dict(function.defaults)
        if bound and names:
            receiver = names.pop(0).arg
            values[receiver] = Value(
                object_kinds=frozenset(
                    {receiver if receiver in {"self", "cls"} else ""}
                )
            )
        if arguments is not None:
            for index, parameter in enumerate(names):
                argument = arguments.argument(index, parameter.arg)
                if argument is not None:
                    values[parameter.arg] = argument.value()
            for parameter in args.kwonlyargs:
                argument = dict(arguments.keywords).get(parameter.arg)
                if argument is not None:
                    values[parameter.arg] = argument.value()
        return tuple(sorted(values.items()))

    def invocation(
        self,
        name: str,
        arguments: binding_rules.Arguments | None,
        *,
        constructor: bool = False,
    ) -> Invocation | None:
        bound = name.startswith("@bound:") or constructor
        token = name.removeprefix("@bound:").removeprefix("@static:")
        if token not in self.scopes:
            return None
        return Invocation(token, self.parameters(token, arguments, bound=bound))

    def suspend(self, invocation: Invocation) -> str:
        for name, existing in self.suspended.items():
            if existing == invocation:
                return name
        if len(self.suspended) >= 16384:
            raise ValueError("too many suspended call states")
        name = f"@suspended:{len(self.suspended)}"
        self.suspended[name] = invocation
        return name

    def call(
        self, call: ast.Call, env: Env, closure: Env, *, awaited: bool = False
    ) -> Iterator[tuple[int, str]]:
        if not awaited and call in self.awaited:
            self.awaited.remove(call)
            return
        if awaited:
            self.awaited.add(call)
        names = binding_rules.dotted(call.func, env[1])
        if isinstance(call.func, ast.Lambda):
            for header in binding_rules.scope_headers(call.func):
                yield from self.scan_expression(header, *env, closure, self)
            token = self.register(call.func, closure, env)
            self.pending[-1].discard(token)
            self.immediate.add(call.func)
            names = frozenset({token})
        if names & {"next", "builtins.next"} and call.args:
            yield from self.resume(call.args[0], env, closure)
            return
        classes = binding_rules.class_aliases(call.func, env[1])
        if classes:
            self.allocate(call, classes, env, closure)
            for constructor in ("__new__", "__init__"):
                targets = frozenset().union(
                    *(
                        env[1].get(f"{name}.{constructor}", frozenset())
                        for name in classes
                    )
                )
                yield from self.invoke_targets(
                    targets, call, env, closure, constructor=True
                )
            return
        yield from self.invoke_targets(names, call, env, closure, awaited=awaited)

    def invoke_targets(
        self,
        names: frozenset[str],
        call: ast.Call,
        env: Env,
        closure: Env,
        *,
        constructor: bool = False,
        awaited: bool = False,
    ) -> Iterator[tuple[int, str]]:
        known = frozenset(
            name
            for name in names
            if name.removeprefix("@bound:").removeprefix("@static:") in self.scopes
        )
        if not known:
            return
        runs: list[Invocation] = []
        suspended: set[str] = set()
        for arguments in binding_rules.call_arguments(call, env[0]):
            for name in known:
                invocation = self.invocation(name, arguments, constructor=constructor)
                if invocation is None:
                    continue
                kind = self.scopes[invocation.token].kind
                if kind == "sync" or awaited:
                    runs.append(invocation)
                else:
                    suspended.add(self.suspend(invocation))
        if suspended:
            self.bind_alias(f"@call:{id(call)}", frozenset(suspended), env, closure)
        yield from self.execute(
            runs, closure, unknown=known != names or bool(suspended)
        )

    def await_expression(
        self, node: ast.Await, env: Env, closure: Env
    ) -> Iterator[tuple[int, str]]:
        if isinstance(node.value, ast.Call):
            yield from self.call(node.value, env, closure, awaited=True)
        else:
            yield from self.resume(node.value, env, closure)

    def resume(
        self, expression: ast.expr, env: Env, closure: Env
    ) -> Iterator[tuple[int, str]]:
        names = binding_rules.dotted(expression, env[1])
        yield from self.execute(
            [self.suspended[name] for name in names if name in self.suspended], closure
        )

    def execute(
        self, invocations: list[Invocation], closure: Env, *, unknown: bool = False
    ) -> Iterator[tuple[int, str]]:
        if not invocations:
            return
        before = copy_context(closure)
        continuations: list[Env] = [copy_context(before)] if unknown else []
        failures: list[Env] = []
        for invocation in invocations:
            result = self.analyze(invocation, before)
            yield from result.findings
            for kind, state in result.outcomes.items():
                changed = copy_context(before)
                apply_delta(
                    module_bindings(*changed),
                    module_bindings(*before),
                    module_bindings(*state.closure),
                )
                if kind in {"normal", "return"}:
                    continuations.append(changed)
                elif kind == "raise":
                    failures.append(changed)
        if self.effects:
            self.effects[-1].raised.extend(failures)
            self.effects[-1].normal &= bool(continuations)
        if continuations:
            combined, *remaining = continuations
            for alternative in remaining:
                merge_namespace(combined, alternative)
                merge_namespace(
                    module_bindings(*combined), module_bindings(*alternative)
                )
            publish_context(closure, combined)

    def scan(self, token: str, closure: Env) -> Iterator[tuple[int, str]]:
        invocation = self.invocation(token, None)
        if invocation is not None:
            yield from self.analyze(invocation, closure).findings

    def analyze(self, invocation: Invocation, closure: Env) -> Analysis:
        function = self.scopes[invocation.token]
        module = module_bindings(*closure)
        closure = copy_context(self.frames.get(function.owner, closure))
        publish_context(module_bindings(*closure), module)
        key = (
            function.node,
            namespace_key(closure),
            namespace_key(module_bindings(*closure)),
            invocation.parameters,
        )
        if key in self.cache:
            return self.cache[key]
        if function.node in self.active:
            state = State(
                ClassValues(closure[0]), dict(closure[1]), copy_context(closure)
            )
            return Analysis((), {"normal": state})
        if len(self.cache) >= 16384:
            raise ValueError("too many distinct deferred body states")
        self.active.add(function.node)
        outcomes: dict[str, State] = {}
        try:
            findings = tuple(
                self.scan_scope(
                    function.node,
                    *closure,
                    dict(invocation.parameters),
                    context=self,
                    on_outcomes=lambda result: outcomes.update(
                        {kind: state.copy() for kind, state in result.items()}
                    ),
                )
            )
        finally:
            self.active.remove(function.node)
        result = Analysis(findings, outcomes)
        self.cache[key] = result
        return result
