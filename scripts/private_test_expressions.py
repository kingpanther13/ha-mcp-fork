"""Ordered operand evaluation for the private-access guard."""

from __future__ import annotations

import ast
import copy
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts import private_test_bindings as binding_rules
    from scripts import private_test_flow as flow_rules
else:
    import private_test_bindings as binding_rules
    import private_test_flow as flow_rules


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


def function_annotations(scope: ast.AST) -> Iterator[ast.expr]:
    """Annotations are policy inputs, not definition-time effects on Python 3.14."""
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        yield from (
            node.annotation
            for node in ast.walk(scope.args)
            if isinstance(node, ast.arg) and node.annotation is not None
        )
        if scope.returns is not None:
            yield scope.returns


class Expression:
    """Evaluate operand effects in order while retaining already captured values."""

    def __init__(
        self,
        context: flow_rules.Deferred,
        scan_node: Callable[..., Iterator[tuple[int, str]]],
        targets: Callable[..., set[str]],
        private: Callable[[str], bool],
        scopes: tuple[type[ast.AST], ...],
    ) -> None:
        self.context = context
        self.scan_node = scan_node
        self.targets = targets
        self.private = private
        self.scopes = scopes
        self.snapshots: flow_rules.Env = ({}, {})

    def snapshot(
        self,
        node: ast.expr,
        env: flow_rules.Env,
        *,
        origin: ast.expr | None = None,
        kind: str = "value",
    ) -> ast.Name:
        source = node if origin is None else origin
        name = f"@operand:{id(source)}:{kind}"
        value = self.context.capture(node, env)
        self.snapshots[0][name] = value
        self.snapshots[1][name] = value.callables
        # Argument/state equality includes AST identity; repeated loop visits
        # must retain the same proxy while each value keeps its own snapshot.
        return self.context.operands.setdefault(
            (source, kind), ast.copy_location(ast.Name(id=name, ctx=ast.Load()), source)
        )

    @staticmethod
    def refresh(env: flow_rules.Env, closure: flow_rules.Env) -> None:
        if isinstance(env[0], flow_rules.ClassValues):
            flow_rules.State(env[0], env[1], closure).refresh()

    def scan(
        self, node: ast.expr, env: flow_rules.Env, closure: flow_rules.Env
    ) -> Generator[tuple[int, str], None, tuple[ast.expr, flow_rules.Env]]:
        prepared = yield from self.evaluate(node, env, closure)
        assert isinstance(prepared, ast.expr)
        return prepared, self.snapshots

    def evaluate(
        self, node: ast.AST, env: flow_rules.Env, closure: flow_rules.Env
    ) -> Generator[tuple[int, str], None, ast.AST]:
        if isinstance(node, ast.Name):
            self.snapshots[1].update(env[1])
            return self.snapshot(node, env)
        if isinstance(node, ast.Constant):
            return node
        if isinstance(node, self.scopes):
            prepared = yield from self.scoped(node, env, closure)
        elif isinstance(node, ast.Call):
            return (yield from self.call(node, env, closure))
        elif isinstance(node, ast.BoolOp):
            return (yield from self.boolean(node.values, node.op, env, closure))
        elif isinstance(node, ast.IfExp):
            return (yield from self.conditional(node, env, closure))
        elif isinstance(node, ast.Await):
            value = yield from self.evaluate(node.value, env, closure)
            assert isinstance(value, ast.expr)
            yield from self.context.resume(value, self.snapshots, closure)
            self.refresh(env, closure)
            prepared = node
        else:
            prepared = yield from self.children(node, env, closure)
        if self.context.effects and not self.context.effects[-1].normal:
            raise flow_rules.Interrupted
        if isinstance(prepared, ast.Attribute):
            self.snapshots[1].update(env[1])
            assert isinstance(node, ast.Attribute)
            return self.snapshot(prepared, self.snapshots, origin=node)
        return prepared

    def scoped(
        self, node: ast.AST, env: flow_rules.Env, closure: flow_rules.Env
    ) -> Generator[tuple[int, str], None, ast.AST]:
        if isinstance(node, ast.GeneratorExp):
            return (yield from self.generator(node, env, closure))
        yield from self.scan_node(node, *env, closure, self.context)
        return node

    def generator(
        self, node: ast.GeneratorExp, env: flow_rules.Env, closure: flow_rules.Env
    ) -> Generator[tuple[int, str], None, ast.Name]:
        iterable = yield from self.evaluate(node.generators[0].iter, env, closure)
        assert isinstance(iterable, ast.expr)
        function, call = generator_scope(node, self.context)
        token = self.context.register(function, closure, env)
        self.context.pending[-1].discard(token)
        prepared = copy.copy(call)
        prepared.args = [iterable]
        yield from self.context.invoke_targets(
            frozenset({token}), call, env, closure,
            captured=(prepared, self.snapshots),
        )
        return self.snapshot(call, env, origin=node)

    def children(
        self, node: ast.AST, env: flow_rules.Env, closure: flow_rules.Env
    ) -> Generator[tuple[int, str], None, ast.AST]:
        # Evaluate every child before interning the rebuilt syntax. Repeated
        # visits keep their effects and fresh values, but stable AST identities.
        prepared = copy.copy(node)
        if isinstance(node, ast.Dict):
            assert isinstance(prepared, ast.Dict)
            keys: list[ast.expr | None] = []
            values: list[ast.expr] = []
            for key, value in zip(node.keys, node.values, strict=True):
                resolved_key = (
                    (yield from self.evaluate(key, env, closure)) if key else None
                )
                resolved_value = yield from self.evaluate(value, env, closure)
                assert resolved_key is None or isinstance(resolved_key, ast.expr)
                assert isinstance(resolved_value, ast.expr)
                keys.append(resolved_key)
                values.append(resolved_value)
            prepared.keys, prepared.values = keys, values
            return self.context.prepared.setdefault(node, prepared)
        for field, value in ast.iter_fields(node):
            if isinstance(value, ast.AST):
                setattr(
                    prepared, field, (yield from self.evaluate(value, env, closure))
                )
            elif isinstance(value, list):
                items = list(value)
                for index, item in enumerate(value):
                    if isinstance(item, ast.AST):
                        items[index] = yield from self.evaluate(item, env, closure)
                setattr(prepared, field, items)
        return self.context.prepared.setdefault(node, prepared)

    def call(
        self, node: ast.Call, env: flow_rules.Env, closure: flow_rules.Env
    ) -> Generator[tuple[int, str], None, ast.Name]:
        prepared = yield from self.children(node, env, closure)
        assert isinstance(prepared, ast.Call)
        for target in sorted(
            self.targets(prepared, self.snapshots[1], self.snapshots[0])
        ):
            if self.private(target):
                yield node.lineno, target
        yield from CallDispatch(self.context, node, prepared, self.snapshots).run(
            env, closure
        )
        self.refresh(env, closure)
        if self.context.effects and not self.context.effects[-1].normal:
            raise flow_rules.Interrupted
        return self.snapshot(node, env)

    def boolean(
        self,
        operands: list[ast.expr],
        operation: ast.boolop,
        env: flow_rules.Env,
        closure: flow_rules.Env,
    ) -> Generator[tuple[int, str], None, ast.AST]:
        first, *remaining = operands
        captured = yield from self.evaluate(first, env, closure)
        if not remaining:
            return captured
        truth = bool(first.value) if isinstance(first, ast.Constant) else None
        if truth is not None:
            if truth == isinstance(operation, ast.Or):
                return captured
            return (yield from self.boolean(remaining, operation, env, closure))
        # An unknown condition may skip the remainder, including a known raise.
        skipped = flow_rules.State(
            flow_rules.ClassValues(env[0]), dict(env[1]), closure
        ).copy()
        branch = skipped.copy()
        effects = flow_rules.Effects([])
        self.context.effects.append(effects)
        result = captured
        try:
            result = yield from self.boolean(
                remaining, operation, (branch.values, branch.aliases), branch.closure
            )
        except flow_rules.Interrupted:
            effects.normal = False
        finally:
            self.context.effects.pop()
        if effects.normal:
            skipped.merge(branch)
        self.publish(skipped, env, closure)
        if self.context.effects:
            self.context.effects[-1].raised.extend(effects.raised)
        assert isinstance(captured, ast.expr) and isinstance(result, ast.expr)
        merged = {"value": self.context.capture(captured, self.snapshots)}
        binding_rules.union_values(
            merged, {"value": self.context.capture(result, self.snapshots)}
        )
        return self.snapshot(
            ast.Name(id="value", ctx=ast.Load()),
            (merged, {"value": merged["value"].callables}),
            origin=first,
            kind="boolean",
        )

    @staticmethod
    def publish(
        state: flow_rules.State, env: flow_rules.Env, closure: flow_rules.Env
    ) -> None:
        flow_rules.publish_context(closure, state.closure)
        env[0].clear()
        env[0].update(state.values)
        env[1].clear()
        env[1].update(state.aliases)

    def conditional(
        self, node: ast.IfExp, env: flow_rules.Env, closure: flow_rules.Env
    ) -> Generator[tuple[int, str], None, ast.AST]:
        yield from self.evaluate(node.test, env, closure)
        if isinstance(node.test, ast.Constant):
            selected = node.body if node.test.value else node.orelse
            return (yield from self.evaluate(selected, env, closure))
        before = flow_rules.State(
            flow_rules.ClassValues(env[0]), dict(env[1]), closure
        ).copy()
        normal: list[flow_rules.State] = []
        raised: list[flow_rules.Env] = []
        merged: dict[str, binding_rules.Value] = {}
        for arm in (node.body, node.orelse):
            branch = before.copy()
            effects = flow_rules.Effects([])
            self.context.effects.append(effects)
            try:
                result = yield from self.evaluate(
                    arm, (branch.values, branch.aliases), branch.closure
                )
                assert isinstance(result, ast.expr)
                value = self.context.capture(result, self.snapshots)
            except flow_rules.Interrupted:
                effects.normal = False
            finally:
                self.context.effects.pop()
            raised.extend(effects.raised)
            if effects.normal:
                normal.append(branch)
                binding_rules.union_values(merged, {"value": value})
        if self.context.effects:
            self.context.effects[-1].raised.extend(raised)
            self.context.effects[-1].normal &= bool(normal)
        if not normal:
            raise flow_rules.Interrupted
        combined, *remaining = normal
        for branch in remaining:
            combined.merge(branch)
        self.publish(combined, env, closure)
        return self.snapshot(
            ast.Name(id="value", ctx=ast.Load()),
            (merged, {"value": merged["value"].callables}),
            origin=node,
            kind="conditional",
        )


@dataclass
class CallDispatch:
    context: flow_rules.Deferred
    node: ast.Call
    prepared: ast.Call
    inputs: flow_rules.Env

    def run(
        self, env: flow_rules.Env, closure: flow_rules.Env
    ) -> Iterator[tuple[int, str]]:
        names = binding_rules.dotted(self.prepared.func, self.inputs[1])
        if isinstance(self.node.func, ast.Lambda):
            token = f"@function:{id(self.node.func)}"
            if token not in self.context.scopes:
                token = self.context.register(self.node.func, closure, env)
            self.context.pending[-1].discard(token)
            names = frozenset({token})
        next_names = names & {"next", "builtins.next"}
        if not next_names:
            yield from self.ordinary(names, env, closure)
            return
        choices: list[tuple[frozenset[str], bool | None]] = [
            (arguments.positional[0].aliases(), len(arguments.positional) > 1)
            for arguments in self.context.capture_arguments(self.prepared, self.inputs)
            if arguments.positional
        ]
        if names - next_names:
            choices.append((names - next_names, None))
        yield from self.alternatives(choices, env, closure)

    def ordinary(
        self, names: frozenset[str], env: flow_rules.Env, closure: flow_rules.Env
    ) -> Iterator[tuple[int, str]]:
        eager = {"tuple", "list", "set", "frozenset", "dict"}
        if names and all(name.removeprefix("builtins.") in eager for name in names):
            targets = frozenset().union(*(
                arguments.positional[0].aliases()
                for arguments in self.context.capture_arguments(self.prepared, self.inputs)
                if arguments.positional
            ))
            yield from consume_generators(
                targets, (dict(self.inputs[0]), dict(self.inputs[1])), closure,
                self.context,
            )
            return
        classes = binding_rules.class_aliases(self.prepared.func, self.inputs[1])
        if classes:
            self.context.allocate(self.node, classes, env, closure)
            for constructor in ("__new__", "__init__"):
                targets = frozenset().union(
                    *(
                        env[1].get(f"{name}.{constructor}", frozenset())
                        for name in classes
                    )
                )
                yield from self.context.invoke_targets(
                    targets,
                    self.node,
                    env,
                    closure,
                    constructor=True,
                    captured=(self.prepared, self.inputs),
                )
            return
        yield from self.context.invoke_targets(
            names, self.node, env, closure, captured=(self.prepared, self.inputs)
        )

    def alternatives(
        self,
        choices: list[tuple[frozenset[str], bool | None]],
        env: flow_rules.Env,
        closure: flow_rules.Env,
    ) -> Iterator[tuple[int, str]]:
        if not choices:
            return
        before = flow_rules.State(
            flow_rules.ClassValues(env[0]), dict(env[1]), closure
        ).copy()
        normal: list[flow_rules.State] = []
        raised: list[flow_rules.Env] = []
        for names, default in choices:
            branch = before.copy()
            effects = flow_rules.Effects([])
            self.context.effects.append(effects)
            try:
                if default is None:
                    yield from self.ordinary(
                        names, (branch.values, branch.aliases), branch.closure
                    )
                else:
                    yield from self.context.resume_targets(
                        names,
                        (dict(self.inputs[0]), dict(self.inputs[1])),
                        branch.closure,
                        defaults=frozenset({default}),
                    )
            except flow_rules.Interrupted:
                effects.normal = False
            finally:
                self.context.effects.pop()
            raised.extend(effects.raised)
            if effects.normal:
                branch.refresh()
                normal.append(branch)
        if self.context.effects:
            self.context.effects[-1].raised.extend(raised)
            self.context.effects[-1].normal &= bool(normal)
        if normal:
            combined, *remaining = normal
            for branch in remaining:
                combined.merge(branch)
            Expression.publish(combined, env, closure)


def comprehension_nodes(
    scope: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
) -> Iterator[ast.AST]:
    """The first iterable belongs to the outer scope, not this body."""
    for index, generator in enumerate(scope.generators):
        if index:
            yield generator.iter
        yield generator.target
        yield from generator.ifs
    if isinstance(scope, ast.DictComp):
        yield scope.key
        yield scope.value
    else:
        yield scope.elt


def comprehension_values(
    scope: ast.AST, values: dict[str, binding_rules.Value]
) -> dict[str, binding_rules.Value]:
    if isinstance(scope, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        first = scope.generators[0]
        if isinstance(first.target, ast.Name):
            return {first.target.id: binding_rules.iterated_value(first.iter, values)}
    return {}


def scan_annotations(
    scope: ast.AST,
    env: flow_rules.Env,
    closure: flow_rules.Env,
    context: flow_rules.Deferred,
) -> Iterator[tuple[int, str]]:
    for annotation in function_annotations(scope):
        policy = flow_rules.Deferred(context.scan_scope, context.scan_expression)
        policy.scopes = dict(context.scopes)
        policy.frames = {
            owner: flow_rules.copy_context(frame)
            for owner, frame in context.frames.items()
        }
        policy.active = set(context.active)
        policy.pending.append(set())
        state = flow_rules.State(flow_rules.ClassValues(env[0]), env[1], closure).copy()
        try:
            yield from policy.scan_expression(
                annotation, state.values, state.aliases, state.closure, policy
            )
        except flow_rules.Interrupted:
            continue
        yield from scan_pending(policy.pending.pop(), state.closure, policy)


def scan_comprehension(
    scope: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
    env: flow_rules.Env,
    closure: flow_rules.Env,
    context: flow_rules.Deferred,
    scan_expression: Callable[
        ..., Generator[tuple[int, str], None, tuple[ast.expr, flow_rules.Env]]
    ],
) -> Iterator[tuple[int, str]]:
    values, aliases = env
    for index, generator in enumerate(scope.generators):
        if index:
            iterable, inputs = yield from scan_expression(
                generator.iter, values, aliases, closure, context
            )
            if isinstance(generator.target, ast.Name):
                values[generator.target.id] = binding_rules.iterated_value(
                    iterable, inputs[0]
                )
        yield from consume_generators(
            generator_names(generator.iter, (values, aliases), context),
            (values, aliases), closure, context,
        )
        for name in binding_rules.bound_names([generator.target]):
            aliases[name] = frozenset({""})
            closure[0][name] = values.get(name, binding_rules.Value())
            closure[1][name] = aliases[name]
        for condition in generator.ifs:
            yield from scan_expression(condition, values, aliases, closure, context)
            if isinstance(condition, ast.Constant) and not condition.value:
                return
    expressions = (
        [scope.key, scope.value] if isinstance(scope, ast.DictComp) else [scope.elt]
    )
    for expression in expressions:
        yield from scan_expression(expression, values, aliases, closure, context)


def scan_body(
    scope: ast.AST,
    closure: flow_rules.Env,
    context: flow_rules.Deferred,
    on_outcomes: Callable[[dict[str, flow_rules.State]], None] | None,
    scan_expression: Callable[
        ..., Generator[tuple[int, str], None, tuple[ast.expr, flow_rules.Env]]
    ],
) -> Iterator[tuple[int, str]]:
    values, aliases = flow_rules.ClassValues(closure[0]), dict(closure[1])
    effects = flow_rules.Effects([])
    context.effects.append(effects)
    state = flow_rules.State(flow_rules.ClassValues(values), aliases, closure).copy()
    try:
        if isinstance(scope, ast.Lambda):
            yield from scan_expression(scope.body, values, aliases, closure, context)
        else:
            assert isinstance(
                scope, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
            )
            yield from scan_comprehension(
                scope, (values, aliases), closure, context, scan_expression
            )
    except flow_rules.Interrupted:
        effects.normal = False
    finally:
        context.effects.pop()
    final = flow_rules.State(flow_rules.ClassValues(values), aliases, closure).copy()
    outcomes = flow_rules.apply_effects({"return": final}, state, effects)
    if on_outcomes is not None:
        on_outcomes(outcomes)
    context.remember(closure)
    yield from scan_pending(context.pending.pop(), closure, context)


def scan_pending(
    tokens: set[str], closure: flow_rules.Env, context: flow_rules.Deferred
) -> Iterator[tuple[int, str]]:
    for token in tokens:
        invocation = context.invocation(token, None)
        if invocation is not None:
            yield from context.analyze(invocation, closure).findings


_GENEXPR_ITER = "@generator-expression-iterable"


def generator_scope(
    node: ast.GeneratorExp, context: flow_rules.Deferred
) -> tuple[ast.FunctionDef, ast.Call]:
    cached = context.prepared.get(node)
    if cached is None:
        body: list[ast.stmt] = [ast.Expr(value=ast.Yield(value=node.elt))]
        for index in range(len(node.generators) - 1, -1, -1):
            clause = node.generators[index]
            for condition in reversed(clause.ifs):
                body = [ast.copy_location(ast.If(test=condition, body=body, orelse=[]), condition)]
            iterable = clause.iter if index else ast.Name(id=_GENEXPR_ITER, ctx=ast.Load())
            body = [ast.copy_location(ast.For(target=clause.target, iter=iterable, body=body, orelse=[], type_comment=None), clause.iter)]
        cached = ast.fix_missing_locations(ast.copy_location(ast.FunctionDef(
            name="@generator-expression",
            args=ast.arguments(posonlyargs=[ast.arg(arg=_GENEXPR_ITER)], args=[], vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[]),
            body=body, decorator_list=[], returns=None, type_comment=None, type_params=[],
        ), node))
        context.prepared[node] = cached
    assert isinstance(cached, ast.FunctionDef)
    call = context.prepared.get(cached)
    if call is None:
        call = ast.copy_location(ast.Call(func=ast.Name(id="@generator-expression", ctx=ast.Load()), args=[], keywords=[]), node)
        context.prepared[cached] = call
    assert isinstance(call, ast.Call)
    return cached, call


def generator_names(
    expression: ast.expr, env: flow_rules.Env, context: flow_rules.Deferred
) -> frozenset[str]:
    if isinstance(expression, ast.GeneratorExp):
        _, expression = generator_scope(expression, context)
    return frozenset(
        name for name in binding_rules.dotted(expression, env[1])
        if name in context.suspended and context.scopes[context.suspended[name].token].kind == "generator"
    )


def generator_has_items(names: frozenset[str], env: flow_rules.Env) -> bool:
    return any(env[1].get(f"@resume:{name}", frozenset({"start"})) - {"done"} for name in names)


def advance_generators(
    names: frozenset[str], env: flow_rules.Env, closure: flow_rules.Env,
    context: flow_rules.Deferred,
) -> Iterator[tuple[int, str]]:
    yield from context.resume_targets(names, env, closure, defaults=frozenset({True}))
    Expression.refresh(env, closure)
    if context.effects and not context.effects[-1].normal:
        raise flow_rules.Interrupted


def consume_generators(
    names: frozenset[str], env: flow_rules.Env, closure: flow_rules.Env,
    context: flow_rules.Deferred,
) -> Iterator[tuple[int, str]]:
    names = frozenset(name for name in names if name in context.suspended and context.scopes[context.suspended[name].token].kind == "generator")
    if not names:
        return
    seen: set[tuple] = set()
    for _ in range(256):
        yield from advance_generators(names, env, closure, context)
        if not generator_has_items(names, env):
            return
        signature = iteration_signature(names, flow_rules.State(flow_rules.ClassValues(env[0]), env[1], closure), context)
        if signature in seen:
            if finish_generators(names, env, closure, context):
                return
            if context.effects:
                context.effects[-1].normal = False
            raise flow_rules.Interrupted
        seen.add(signature)
    raise ValueError("too many static generator iterations")


def finish_generators(names: frozenset[str], env: flow_rules.Env, closure: flow_rules.Env, context: flow_rules.Deferred) -> bool:
    cursors = flow_rules.module_bindings(*closure)[1]
    if not any("done" in cursors.get(f"@resume:{name}", env[1].get(f"@resume:{name}", frozenset())) for name in names):
        return False
    for name in names:
        context.bind_alias(f"@resume:{name}", frozenset({"done"}), env, closure)
    return True


def iteration_signature(names: frozenset[str], state: flow_rules.State, context: flow_rules.Deferred) -> tuple:
    def state_key(source: flow_rules.State) -> tuple:
        copied = source.copy()
        for aliases in (copied.aliases, copied.closure[1], flow_rules.module_bindings(*copied.closure)[1]):
            for name in names:
                aliases.pop(f"@resume:{name}", None)
        return copied.key()

    positions: list[tuple[object, ...]] = []
    for name in sorted(names):
        stages = state.aliases.get(f"@resume:{name}", frozenset({"start"}))
        for stage in sorted(stages):
            continuation = context.continuations.get(stage)
            if continuation is None:
                positions.append((name, stage))
                continue
            steps = tuple(continuation_position(step) for step in continuation.steps)
            positions.append((name, steps, state_key(continuation.state)))
    return state_key(state), frozenset(positions)


def continuation_position(step: Callable[..., object]) -> tuple:
    def syntax(value: object) -> tuple:
        if isinstance(value, ast.AST):
            return (id(value),)
        if isinstance(value, (list, tuple)):
            return tuple(syntax(item) for item in value)
        return ()

    if isinstance(step, partial):
        keywords = step.keywords
        start = keywords.get("start")
        if getattr(step.func, "__name__", "") == "resume_loop" and keywords.get("iterations") is None:
            start = None
        return (getattr(step.func, "__func__", step.func), syntax(step.args), syntax(keywords.get("body")), start)
    cells = getattr(step, "__closure__", None) or ()
    return (getattr(step, "__code__", type(step)), tuple(syntax(cell.cell_contents) for cell in cells))


def clear_iteration(node: ast.AST, outcomes: dict[str, flow_rules.State], context: flow_rules.Deferred) -> None:
    key = f"@iteration:{id(node)}"
    for kind, state in outcomes.items():
        if kind in context.continuations:
            continue
        for aliases in (state.aliases, state.closure[1], flow_rules.module_bindings(*state.closure)[1]):
            aliases.pop(key, None)


LoopBindings = list[tuple[ast.expr, frozenset[str] | None, binding_rules.Value]]


def loop_bindings(
    node: ast.For | ast.AsyncFor | ast.While,
    values: dict[str, binding_rules.Value], aliases: binding_rules.Aliases,
) -> tuple[list[LoopBindings] | None, LoopBindings]:
    expression = node.test if isinstance(node, ast.While) else node.iter
    literal = binding_rules.literal_iterations(expression, aliases)
    iterations: list[LoopBindings] | None = None
    bindings: LoopBindings = []
    if not isinstance(node, ast.While):
        bindings = binding_rules.prepared_assignment([node.target], ast.Constant(value=None), values, aliases)
        if isinstance(node.target, ast.Name):
            bindings = [(node.target, frozenset({""}), binding_rules.iterated_value(expression, values))]
        if literal is not None:
            iterations = [binding_rules.prepared_assignment([node.target], item, values, aliases) for item in literal]
        if isinstance(expression, ast.Name) and expression.id == _GENEXPR_ITER:
            positions = values.get(expression.id, binding_rules.Value()).positions
            if len(positions) == 1 and all(item is not None for item in positions[0]):
                iterations = []
                for item in positions[0]:
                    assert item is not None
                    if isinstance(node.target, ast.Name):
                        iterations.append([(node.target, item.aliases() or frozenset({""}), item.value())])
                    else:
                        inputs = dict(item.bindings)
                        iterations.append(binding_rules.prepared_assignment([node.target], item.expression, inputs, {name: value.callables for name, value in inputs.items()}))
    return iterations, bindings


def prepare_loop(node: ast.For | ast.AsyncFor | ast.While, state: flow_rules.State, context: flow_rules.Deferred) -> tuple[list[LoopBindings] | None, LoopBindings]:
    names = frozenset() if isinstance(node, ast.While) else generator_names(node.iter, (state.values, state.aliases), context)
    if names:
        context.bind_alias(f"@iteration:{id(node)}", names, (state.values, state.aliases), state.closure)
    return loop_bindings(node, state.values, state.aliases)


def advance_iteration(names: frozenset[str], state: flow_rules.State, context: flow_rules.Deferred, findings: set[tuple[int, str]]) -> bool:
    findings.update(advance_generators(names, (state.values, state.aliases), state.closure, context))
    return generator_has_items(names, (state.values, state.aliases))


def converged_iteration(names: frozenset[str], state: flow_rules.State, context: flow_rules.Deferred, exhaustion: dict[str, flow_rules.State]) -> None:
    if names and finish_generators(names, (state.values, state.aliases), state.closure, context):
        flow_rules.add_outcome(exhaustion, "normal", state)
