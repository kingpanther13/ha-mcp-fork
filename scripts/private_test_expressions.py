"""Ordered operand evaluation for the private-access guard."""

from __future__ import annotations

import ast
import copy
from collections.abc import Callable, Generator, Iterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts import private_test_bindings as binding_rules
    from scripts import private_test_flow as flow_rules
else:
    import private_test_bindings as binding_rules
    import private_test_flow as flow_rules


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
            yield from self.scan_node(node, *env, closure, self.context)
            prepared = node
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
        yield from self.context.call(
            node, env, closure, captured=(prepared, self.snapshots)
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
    scope: ast.AST, env: flow_rules.Env, closure: flow_rules.Env, context: flow_rules.Deferred
) -> Iterator[tuple[int, str]]:
    for annotation in flow_rules.function_annotations(scope):
        policy = flow_rules.Deferred(context.scan_scope, context.scan_expression)
        policy.pending.append(set())
        state = flow_rules.State(flow_rules.ClassValues(env[0]), env[1], closure).copy()
        try:
            yield from policy.scan_expression(
                annotation, state.values, state.aliases, state.closure, policy
            )
        except flow_rules.Interrupted:
            continue
        for token in policy.pending.pop():
            yield from policy.scan(token, state.closure)


def scan_comprehension(
    scope: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
    env: flow_rules.Env,
    closure: flow_rules.Env,
    context: flow_rules.Deferred,
    scan_expression: Callable[..., Generator[tuple[int, str], None, tuple[ast.expr, flow_rules.Env]]],
) -> Iterator[tuple[int, str]]:
    values, aliases = env
    for index, generator in enumerate(scope.generators):
        if index:
            iterable, inputs = yield from scan_expression(
                generator.iter, values, aliases, closure, context
            )
            if isinstance(generator.target, ast.Name):
                values[generator.target.id] = binding_rules.iterated_value(iterable, inputs[0])
        for name in binding_rules.bound_names([generator.target]):
            aliases[name] = frozenset({""})
            closure[0][name] = values.get(name, binding_rules.Value())
            closure[1][name] = aliases[name]
        for condition in generator.ifs:
            yield from scan_expression(condition, values, aliases, closure, context)
            if isinstance(condition, ast.Constant) and not condition.value:
                return
    expressions = [scope.key, scope.value] if isinstance(scope, ast.DictComp) else [scope.elt]
    for expression in expressions:
        yield from scan_expression(expression, values, aliases, closure, context)


def scan_body(
    scope: ast.AST,
    closure: flow_rules.Env,
    context: flow_rules.Deferred,
    on_outcomes: Callable[[dict[str, flow_rules.State]], None] | None,
    scan_expression: Callable[..., Generator[tuple[int, str], None, tuple[ast.expr, flow_rules.Env]]],
) -> Iterator[tuple[int, str]]:
    values, aliases = flow_rules.ClassValues(closure[0]), dict(closure[1])
    effects = flow_rules.Effects([])
    context.effects.append(effects)
    state = flow_rules.State(flow_rules.ClassValues(values), aliases, closure).copy()
    try:
        if isinstance(scope, ast.Lambda):
            yield from scan_expression(scope.body, values, aliases, closure, context)
        else:
            assert isinstance(scope, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
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
    for token in context.pending.pop():
        yield from context.scan(token, closure)
