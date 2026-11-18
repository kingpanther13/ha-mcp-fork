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


def bindings(
    scope: ast.AST,
    parent_values: dict[str, Value],
    parent_aliases: Aliases,
    nodes: list[ast.AST],
    initial_values: dict[str, Value] | None = None,
) -> tuple[dict[str, Value], Aliases]:
    if isinstance(scope, ast.ClassDef):
        values, aliases = class_bindings(parent_values, parent_aliases, nodes)
        return ClassValues(values), aliases
    local_names = binding_rules.bound_names(nodes)
    global_names = {
        name for node in nodes if isinstance(node, ast.Global) for name in node.names
    }
    nonlocal_names = binding_rules.declared_names(nodes, ast.Nonlocal)
    local_names -= global_names | nonlocal_names
    values = ScopeValues(parent_values)
    aliases = dict(parent_aliases)
    imported: Aliases = {}
    parameter_names: set[str] = set()
    binding_rules.import_aliases(nodes, imported)
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        parameters = [
            *scope.args.posonlyargs,
            *scope.args.args,
            *scope.args.kwonlyargs,
            scope.args.vararg,
            scope.args.kwarg,
        ]
        parameter_names = {
            parameter.arg for parameter in parameters if parameter is not None
        }
        local_names.update(parameter_names)
    values.scope_id = scope
    values.scope_kind = "module" if isinstance(scope, ast.Module) else "function"
    values.cell_owners.update(dict.fromkeys(local_names, scope))
    for name in global_names:
        values.cell_owners.pop(name, None)
    values.lexical_names = (
        frozenset()
        if isinstance(scope, ast.Module)
        else (values.lexical_names | local_names) - global_names
    )
    for name in local_names:
        values.pop(name, None)
        aliases.pop(name, None)
    aliases.update(imported)
    values.update(initial_values or {})
    binding_rules.extend_values(list(binding_rules.assignments(nodes)), values, aliases)
    for name in nonlocal_names:
        values.pop(name, None)
        if name in parent_values:
            values[name] = parent_values[name]
        aliases[name] = parent_aliases.get(name, frozenset({name}))
    for name in parameter_names:
        values.pop(name, None)
        aliases[name] = frozenset({""})
    values.update(initial_values or {})
    for name, value in (initial_values or {}).items():
        aliases[name] = value.callables or frozenset({""})
    return values, aliases


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
        self.cell_owners: dict[str, ast.AST] = (
            dict(values.cell_owners) if isinstance(values, ScopeValues) else {}
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


def add_outcome(outcomes: dict[str, State], kind: str, state: State) -> None:
    if kind in outcomes:
        outcomes[kind].merge(state)
    else:
        outcomes[kind] = state.copy()


def restore_outcome(outcomes: dict[str, State], previous: str) -> dict[str, State]:
    restored: dict[str, State] = {}
    for kind, state in outcomes.items():
        add_outcome(restored, previous if kind == "normal" else kind, state)
    return restored


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
class Continuation:
    state: State
    steps: list[Callable[[dict[str, State]], dict[str, State]]]
    findings: set[tuple[int, str]]


@dataclass
class Effects:
    raised: list[Env]
    normal: bool = True


class Interrupted(Exception):
    """A known call has no normal continuation."""


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


def replay_names[T](
    target: dict[str, T], source: dict[str, T], names: set[str]
) -> None:
    before = {name: target[name] for name in names if name in target}
    after = {name: source[name] for name in names if name in source}
    mapping_delta(target, before, after)


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
        self.suspended: dict[str, Invocation] = {}
        self.suspended_sites: dict[str, ast.Call | None] = {}
        self.effects: list[Effects] = []
        self.operands: dict[tuple[ast.expr, str], ast.Name] = {}
        self.prepared: dict[ast.AST, ast.AST] = {}
        self.generating: list[bool] = []
        self.continuations: dict[str, Continuation] = {}

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
            isinstance(child, (ast.Yield, ast.YieldFrom))
            for child in binding_rules.body_nodes(node)
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

    def suspend(
        self,
        invocation: Invocation,
        site: ast.Call | None = None,
        live: set[str] | None = None,
    ) -> str:
        for name, existing in self.suspended.items():
            if (
                existing == invocation
                and self.suspended_sites[name] is site
                and (live is None or name not in live)
            ):
                return name
        if len(self.suspended) >= 16384:
            raise ValueError("too many suspended call states")
        name = f"@suspended:{len(self.suspended)}"
        self.suspended[name] = invocation
        self.suspended_sites[name] = site
        return name

    def live_suspensions(self, env: Env, closure: Env) -> set[str]:
        sources = [env, closure, module_bindings(*closure)]
        roots = list(sources)
        for values, aliases in self.frames.values():
            names = (
                {
                    name
                    for name, owner in values.cell_owners.items()
                    if owner is values.scope_id
                }
                if isinstance(values, ScopeValues)
                else set()
            )
            roots.append(
                (
                    {name: values[name] for name in names if name in values},
                    {name: aliases[name] for name in names if name in aliases},
                )
            )
        live = set().union(
            *(names for _, aliases in roots for names in aliases.values())
        )
        pending: list[Value | binding_rules.Argument] = [
            value for values, _ in roots for value in values.values()
        ]
        visited: set[int] = set()
        retained: set[str] = set()
        while pending or (live & self.suspended.keys()) - retained:
            if not pending:
                for name in (live & self.suspended.keys()) - retained:
                    retained.add(name)
                    pending.extend(
                        self.suspension_values(name, module_bindings(*closure)[1])
                    )
                continue
            value = pending.pop()
            if id(value) in visited:
                continue
            visited.add(id(value))
            if isinstance(value, Value):
                live.update(value.callables)
            pending.extend(binding_rules.snapshot_children(value))
        for _, aliases in [*sources, *self.frames.values()]:
            for key in list(aliases):
                if (
                    key.startswith("@resume:")
                    and key.removeprefix("@resume:") not in live
                ):
                    aliases.pop(key)
        return live

    def suspension_values(self, name: str, aliases: Aliases) -> Iterator[Value]:
        stages = aliases.get(f"@resume:{name}", frozenset({"start"}))
        if "start" in stages:
            yield from (value for _, value in self.suspended[name].parameters)
        for stage in stages:
            continuation = self.continuations.get(stage)
            if continuation is not None:
                state = continuation.state.copy()
                self.refresh_cells(state)
                values, captured = state.closure
                names = state.values.lexical_names
                yield from (value for name, value in values.items() if name in names)
                callables = frozenset().union(
                    *(captured.get(name, frozenset()) for name in names)
                )
                yield Value(callables=callables)

    def call(
        self,
        call: ast.Call,
        env: Env,
        closure: Env,
        *,
        captured: tuple[ast.Call, Env] | None = None,
    ) -> Iterator[tuple[int, str]]:
        prepared, inputs = captured if captured is not None else (call, env)
        names = binding_rules.dotted(prepared.func, inputs[1])
        if isinstance(call.func, ast.Lambda):
            if captured is None:
                headers = list(call.func.args.defaults)
                headers.extend(
                    value for value in call.func.args.kw_defaults if value is not None
                )
                for header in headers:
                    yield from self.scan_expression(header, *env, closure, self)
            token = f"@function:{id(call.func)}"
            if captured is None or token not in self.scopes:
                token = self.register(call.func, closure, env)
            self.pending[-1].discard(token)
            names = frozenset({token})
        if names & {"next", "builtins.next"}:
            options = [
                arguments
                for arguments in self.capture_arguments(prepared, inputs)
                if arguments.positional
            ]
            targets = frozenset().union(
                *(arguments.positional[0].aliases() for arguments in options)
            )
            defaults = frozenset(len(arguments.positional) > 1 for arguments in options)
            yield from self.resume_targets(targets, inputs, closure, defaults=defaults)
            return
        classes = binding_rules.class_aliases(prepared.func, inputs[1])
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
                    targets,
                    call,
                    env,
                    closure,
                    constructor=True,
                    captured=(prepared, inputs),
                )
            return
        yield from self.invoke_targets(
            names, call, env, closure, captured=(prepared, inputs)
        )

    def invoke_targets(
        self,
        names: frozenset[str],
        call: ast.Call,
        env: Env,
        closure: Env,
        *,
        constructor: bool = False,
        captured: tuple[ast.Call, Env] | None = None,
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
        live: set[str] | None = None
        for arguments in self.capture_arguments(
            *(captured if captured is not None else (call, env))
        ):
            for name in known:
                invocation = self.invocation(name, arguments, constructor=constructor)
                if invocation is None:
                    continue
                kind = self.scopes[invocation.token].kind
                if kind == "sync":
                    runs.append(invocation)
                elif kind == "generator":
                    if live is None:
                        live = self.live_suspensions(env, closure)
                    suspended_name = self.suspend(invocation, call, live)
                    live.add(suspended_name)
                    self.bind_alias(
                        f"@resume:{suspended_name}", frozenset({"start"}), env, closure
                    )
                    suspended.add(suspended_name)
                else:
                    suspended.add(self.suspend(invocation))
        if suspended:
            self.bind_alias(f"@call:{id(call)}", frozenset(suspended), env, closure)
        yield from self.execute(
            runs, closure, unknown=known != names or bool(suspended)
        )

    def capture_arguments(
        self, call: ast.Call, env: Env
    ) -> list[binding_rules.Arguments]:
        roots = [*call.args, *(keyword.value for keyword in call.keywords)]
        expressions = {node for root in roots for node in ast.walk(root)}

        def capture(argument: binding_rules.Argument) -> binding_rules.Argument:
            # Stored container elements retain their assignment-time snapshots.
            if argument.expression not in expressions:
                return argument
            return replace(argument, resolved=self.capture(argument.expression, env))

        return [
            binding_rules.Arguments(
                tuple(capture(argument) for argument in arguments.positional),
                tuple(
                    (name, capture(argument)) for name, argument in arguments.keywords
                ),
            )
            for arguments in binding_rules.call_arguments(call, env[0])
        ]

    def resume(
        self, expression: ast.expr, env: Env, closure: Env
    ) -> Iterator[tuple[int, str]]:
        names = binding_rules.dotted(expression, env[1])
        yield from self.resume_targets(names, env, closure)

    def resume_targets(
        self,
        names: frozenset[str],
        env: Env,
        closure: Env,
        *,
        defaults: frozenset[bool] = frozenset({False}),
    ) -> Iterator[tuple[int, str]]:
        generators = {
            name
            for name in names & self.suspended.keys()
            if self.scopes[self.suspended[name].token].kind == "generator"
        }
        if generators:
            self.live_suspensions(env, closure)
        results: list[tuple[Invocation, Analysis]] = []
        for name in names & self.suspended.keys():
            invocation = self.suspended[name]
            if name in generators:
                results.extend(
                    (invocation, result)
                    for result in self.resume_generator(
                        name, invocation, env, closure, defaults=defaults
                    )
                )
            else:
                results.append((invocation, self.analyze(invocation, closure)))
        yield from self.execute(
            [],
            closure,
            results=results,
            unknown=any(name not in self.suspended for name in names),
        )

    def execute(
        self,
        invocations: list[Invocation],
        closure: Env,
        *,
        unknown: bool = False,
        results: list[tuple[Invocation, Analysis]] | None = None,
    ) -> Iterator[tuple[int, str]]:
        if results is None:
            results = [
                (invocation, self.analyze(invocation, closure))
                for invocation in invocations
            ]
        if not results:
            return
        before = copy_context(closure)
        continuations: list[Env] = [copy_context(before)] if unknown else []
        failures: list[Env] = []
        for invocation, result in results:
            yield from result.findings
            for kind, state in result.outcomes.items():
                changed = copy_context(before)
                apply_delta(
                    module_bindings(*changed),
                    module_bindings(*before),
                    module_bindings(*state.closure),
                )
                self.replay_cells(changed, state.closure, invocation)
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

    def replay_cells(self, target: Env, source: Env, invocation: Invocation) -> None:
        if not isinstance(target[0], ScopeValues) or not isinstance(
            source[0], ScopeValues
        ):
            return
        local_scope = self.scopes[invocation.token].node
        names = {
            name
            for name, owner in source[0].cell_owners.items()
            if owner is not local_scope and target[0].cell_owners.get(name) is owner
        }
        replay_names(target[0], source[0], names)
        replay_names(target[1], source[1], names)

    def scan(self, token: str, closure: Env) -> Iterator[tuple[int, str]]:
        invocation = self.invocation(token, None)
        if invocation is not None:
            yield from self.analyze(invocation, closure).findings

    def analyze(
        self, invocation: Invocation, closure: Env, *, generator: bool = False
    ) -> Analysis:
        function = self.scopes[invocation.token]
        module = module_bindings(*closure)
        closure = copy_context(self.frames.get(function.owner, closure))
        publish_context(module_bindings(*closure), module)
        key = (
            function.node,
            namespace_key(closure),
            namespace_key(module_bindings(*closure)),
            invocation.parameters,
            generator,
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
        self.generating.append(generator)
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
            self.generating.pop()
            self.active.remove(function.node)
        result = Analysis(findings, outcomes)
        self.cache[key] = result
        return result

    def pause(self, state: State, findings: set[tuple[int, str]]) -> dict[str, State]:
        if len(self.continuations) >= 16384:
            raise ValueError("too many generator continuations")
        key = f"yield:{len(self.continuations)}"
        self.continuations[key] = Continuation(state.copy(), [], findings)
        return {key: state}

    def chain(
        self,
        outcomes: dict[str, State],
        step: Callable[[dict[str, State]], dict[str, State]],
    ) -> None:
        for kind in outcomes:
            if kind in self.continuations:
                self.continuations[kind].steps.append(step)

    def continue_generator(self, key: str, closure: Env) -> Analysis:
        continuation = self.continuations[key]
        state = continuation.state.copy()
        publish_context(module_bindings(*state.closure), module_bindings(*closure))
        self.refresh_cells(state)
        state.refresh()
        outcomes = {"normal": state}
        suspended: dict[str, State] = {}
        self.generating.append(True)
        try:
            for index, step in enumerate(continuation.steps):
                outcomes = step(outcomes)
                for kind in list(outcomes):
                    if kind in self.continuations:
                        self.continuations[kind].steps.extend(
                            continuation.steps[index + 1 :]
                        )
                        suspended[kind] = outcomes.pop(kind)
                if not outcomes:
                    break
        finally:
            self.generating.pop()
        return Analysis(tuple(continuation.findings), outcomes | suspended)

    def refresh_cells(self, state: State) -> None:
        values = state.closure[0]
        if not isinstance(values, ScopeValues):
            return
        for owner in set(values.cell_owners.values()) - {values.scope_id}:
            frame = self.frames.get(owner)
            if frame is not None:
                names = {
                    name for name, scope in values.cell_owners.items() if scope is owner
                }
                replay_names(values, frame[0], names)
                replay_names(state.closure[1], frame[1], names)

    def resume_generator(
        self,
        name: str,
        invocation: Invocation,
        env: Env,
        closure: Env,
        *,
        defaults: frozenset[bool] = frozenset({False}),
    ) -> list[Analysis]:
        key = f"@resume:{name}"
        stages = module_bindings(*closure)[1].get(
            key, env[1].get(key, frozenset({"start"}))
        )
        results = [
            self.analyze(invocation, closure, generator=True)
            if stage == "start"
            else self.continue_generator(stage, closure)
            for stage in stages
            if stage != "done"
        ]
        if "done" in stages:
            state = State(
                ClassValues(closure[0]), dict(closure[1]), copy_context(closure)
            )
            results.append(Analysis((), {"return": state}))
        following: set[str] = set()
        resumed: list[Analysis] = []
        for result in results:
            outcomes: dict[str, State] = {}
            for kind, state in result.outcomes.items():
                yielded = kind in self.continuations
                following.add(kind if yielded else "done")
                completed = kind in {"normal", "return"}
                destinations = {
                    "normal" if yielded or (default and completed) else "raise"
                    for default in defaults
                }
                for destination in destinations:
                    add_outcome(outcomes, destination, state)
            resumed.append(Analysis(result.findings, outcomes))
        self.bind_alias(key, frozenset(following or {"done"}), env, closure)
        for result in resumed:
            for state in result.outcomes.values():
                module_bindings(*state.closure)[1][key] = frozenset(
                    following or {"done"}
                )
        return resumed
