#!/usr/bin/env python3
"""Reject private names reached through strings in unit and app tests.

Ruff checks attribute syntax and the private-import guard checks imports. This
checks reflection and patch targets, including lexical constants, concatenation,
f-strings and patch.multiple keys. It never imports the files it reads and has
no exemption list. Unknown runtime values still require review; a known private
segment in an otherwise dynamic f-string is checked too.

Run ``uv run python scripts/private_test_access.py`` from the repository root.
Fast Checks runs the same command over both complete test trees. It checks
reflection helpers and patch targets, including patch.object, patch.dict and
patch.multiple, resolving lexical constants, concatenation, f-strings and static
keyword dictionaries.
Real dunder names, the ha_mcp._vendor import-path segment, and the test's own
self/cls are allowed, matching the attribute/import checks. A replacement's
value is data, not a target. Runtime-computed names still need review.
"""

from __future__ import annotations

import argparse
import ast
import copy
from collections.abc import Callable, Generator, Iterator
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts import private_test_bindings as binding_rules
    from scripts import private_test_expressions as expression_rules
    from scripts import private_test_flow as flow_rules
else:
    import private_test_bindings as binding_rules
    import private_test_expressions as expression_rules
    import private_test_flow as flow_rules


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


_ClassValues = flow_rules.ClassValues
_State = flow_rules.State
_Env = flow_rules.Env


_Deferred = flow_rules.Deferred


def _nodes(scope: ast.AST, *, descend_assignments: bool = True) -> Iterator[ast.AST]:
    """Nodes in one lexical scope, leaving nested bodies to their own scope."""
    children: Iterator[ast.AST]
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        children = iter(scope.body)
    elif isinstance(scope, ast.Lambda):
        children = iter([scope.body])
    elif isinstance(scope, _COMPREHENSIONS):
        children = expression_rules.comprehension_nodes(scope)
    else:
        children = ast.iter_child_nodes(scope)
    for child in children:
        yield child
        if isinstance(child, _SCOPES) or (
            not descend_assignments and isinstance(child, (ast.Assign, ast.AnnAssign))
        ):
            continue
        yield from _nodes(
            child,
            descend_assignments=descend_assignments,
        )


_headers = expression_rules.scope_headers


def _bindings(
    scope: ast.AST,
    parent_values: dict[str, binding_rules.Value],
    parent_aliases: binding_rules.Aliases,
    initial_values: dict[str, binding_rules.Value] | None = None,
) -> tuple[dict[str, binding_rules.Value], binding_rules.Aliases]:
    return flow_rules.bindings(
        scope, parent_values, parent_aliases, list(_nodes(scope)), initial_values
    )


def _targets(
    call: ast.Call,
    aliases: binding_rules.Aliases,
    values: dict[str, binding_rules.Value],
) -> set[str]:
    return set().union(
        *(
            _named_targets(call, name, values)
            for name in binding_rules.dotted(call.func, aliases)
        )
    )


def _call_kind(name: str) -> str | None:
    operation = name.rsplit(".", 1)[-1]
    method = ".".join(name.split(".")[-2:])
    if method in {"patch.dict", "patch.object", "patch.multiple"}:
        return method
    if operation in {"setattr", "delattr"} and name not in {
        operation,
        f"builtins.{operation}",
    }:
        return f"monkey.{operation}"
    if operation in {"patch", "getattr", "hasattr", "setattr", "delattr"}:
        return operation
    return None


def _named_targets(
    call: ast.Call, name: str, values: dict[str, binding_rules.Value]
) -> set[str]:
    kind = _call_kind(name)
    if kind is None:
        return set()
    return set().union(
        *(
            _argument_targets(kind, arguments)
            for arguments in binding_rules.call_arguments(
                call, values, keyword_target="in_dict" if kind == "patch.dict" else None
            )
        )
    )


def _argument_strings(argument: binding_rules.Argument | None) -> set[str]:
    return argument.strings() if argument is not None else set()


def _argument_targets(kind: str, arguments: binding_rules.Arguments) -> set[str]:
    if kind == "patch.dict":
        return _argument_strings(arguments.argument(0, "in_dict"))
    target = arguments.argument(0, "target")
    if kind == "patch":
        return _argument_strings(target)
    if kind in {"patch.object", "patch.multiple"}:
        targets = _argument_strings(target)
        if target is not None and target.own_object():
            return targets
        if kind == "patch.multiple":
            return targets | {name for name, _ in arguments.keywords}
        return targets | _argument_strings(arguments.argument(1, "attribute"))
    if target is not None and target.own_object():
        return set()
    if kind in {"monkey.setattr", "monkey.delattr"}:
        signature = (2, "value") if kind == "monkey.setattr" else (1, "name")
        if arguments.argument(*signature) is None:
            return _argument_strings(target)
        return _argument_strings(arguments.argument(1, "name"))
    return _argument_strings(arguments.argument(1, "name"))


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


def _bind_definition(
    name: str,
    values: dict[str, binding_rules.Value],
    aliases: binding_rules.Aliases,
    resolved: frozenset[str] = frozenset({""}),
) -> None:
    aliases[name] = resolved
    values[name] = binding_rules.Value(callables=resolved)
    if isinstance(values, _ClassValues):
        _store_class_name(values, aliases, name)


def _scan_assignment_target(
    target: ast.expr,
    resolved: frozenset[str] | None,
    values: dict[str, binding_rules.Value],
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
    context: _Deferred,
    assigned_value: binding_rules.Value = binding_rules.Value(),
) -> Iterator[tuple[int, str]]:
    if isinstance(target, ast.Name):
        if resolved is not None:
            aliases[target.id] = resolved
            values[target.id] = replace(assigned_value, callables=resolved)
            if isinstance(values, _ClassValues):
                _store_class_name(values, aliases, target.id)
    else:
        yield from _scan_expression(target, values, aliases, closure, context)
        if resolved is not None and isinstance(target, ast.Attribute):
            owners = binding_rules.dotted(target.value, aliases)
            for owner in owners:
                if owner.startswith("@class:") and "." not in owner:
                    name = f"{owner}.{target.attr}"
                    replacement = resolved or frozenset(
                        {"@inherit" if "/" in owner else ""}
                    )
                    stored = replacement
                    if len(owners) > 1 or owner.endswith("/*"):
                        stored |= binding_rules.alias_options(name, aliases)
                    aliases[name] = stored
                    flow_rules.module_bindings(*closure)[1][name] = stored


def _scan_assignment(
    node: ast.Assign | ast.AnnAssign,
    values: dict[str, binding_rules.Value],
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
    context: _Deferred,
    on_error: Callable[[], None] | None = None,
) -> Iterator[tuple[int, str]]:
    value = node.value
    inputs = (values, aliases)
    if isinstance(value, ast.Lambda):
        for header in _headers(value):
            yield from _scan_expression(header, values, aliases, closure, context)
    elif value is not None:
        value, inputs = yield from _scan_expression(
            value, values, aliases, closure, context
        )
    token = (
        context.register(
            value,
            closure,
            (values, aliases),
            method=isinstance(values, _ClassValues) and values.scope_kind == "class",
        )
        if isinstance(value, ast.Lambda)
        else None
    )
    # Resolve a chained assignment's RHS once, then evaluate and bind targets
    # left to right: a later target expression can see an earlier new binding.
    targets: list[ast.expr] = (
        node.targets if isinstance(node, ast.Assign) else [node.target]
    )
    for target, resolved, assigned_value in binding_rules.prepared_assignment(
        targets, value, *inputs
    ):
        if on_error is not None and not isinstance(target, ast.Name):
            on_error()
        yield from _scan_assignment_target(
            target,
            frozenset({token}) if token else resolved,
            values,
            aliases,
            closure,
            context,
            assigned_value,
        )
    if isinstance(node, ast.AnnAssign):
        if on_error is not None:
            on_error()
        yield from _scan_expression(node.annotation, values, aliases, closure, context)


def _scan_nested_scope(
    node: ast.AST,
    values: dict[str, binding_rules.Value],
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
    context: _Deferred,
) -> Iterator[tuple[int, str]]:
    for header in _headers(node):
        yield from _scan_expression(header, values, aliases, closure, context)
    yield from expression_rules.scan_annotations(
        node, (values, aliases), closure, context
    )
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        token = context.register(
            node,
            closure,
            (values, aliases),
            method=isinstance(values, _ClassValues) and values.scope_kind == "class",
        )
        if not isinstance(node, ast.Lambda):
            _bind_definition(node.name, values, aliases, frozenset({token}))
        return
    yield from _scan_scope(
        node,
        *closure,
        expression_rules.comprehension_values(node, values),
        context=context,
    )


_Outcomes = dict[str, _State]
_Bindings = list[tuple[ast.expr, frozenset[str] | None, binding_rules.Value]]


_add_outcome = flow_rules.add_outcome


def _store_class_name(
    values: _ClassValues, aliases: binding_rules.Aliases, name: str
) -> None:
    if values.scope_kind != "class":
        owner = (
            values.closure
            if values.scope_kind == "function" and name in values.lexical_names
            else flow_rules.module_bindings(*values.closure)
        )
    elif name in values.class_globals:
        owner = flow_rules.module_bindings(*values.closure)
    elif name in values.class_nonlocals:
        owner = values.closure
    else:
        values.bound |= {name}
        values.local_values[name] = values.get(name, binding_rules.Value())
        values.local_aliases[name] = aliases.get(name, frozenset({name}))
        return
    owner[0].pop(name, None)
    if name in values:
        owner[0][name] = values[name]
    owner[1][name] = aliases.get(name, frozenset({name}))


def _restore_class_fallback(
    name: str, values: _ClassValues, aliases: binding_rules.Aliases, closure: _Env
) -> None:
    values.pop(name, None)
    values.bound -= {name}
    values.local_values.pop(name, None)
    values.local_aliases.pop(name, None)
    if values.scope_kind != "class":
        aliases[name] = frozenset({"" if name in values.lexical_names else name})
        _store_class_name(values, aliases, name)
        return
    if name in values.class_nonlocals | values.class_globals:
        aliases[name] = frozenset({"" if name in values.class_nonlocals else name})
        _store_class_name(values, aliases, name)
        return
    module = flow_rules.module_bindings(*closure)
    if name in module[0]:
        values[name] = module[0][name]
    aliases[name] = module[1].get(name, frozenset({name}))


class _ClassFlow:
    """Bounded abstract statement execution with separate control-flow outcomes."""

    def __init__(self, context: _Deferred) -> None:
        self.context = context
        self.cache: dict[tuple, _Outcomes] = {}
        self.findings: set[tuple[int, str]] = set()

    def expression(self, node: ast.expr, state: _State) -> None:
        self.findings.update(
            _scan_expression(
                node, state.values, state.aliases, state.closure, self.context
            )
        )

    def block(
        self, body: list[ast.stmt], incoming: _State, start: int = 0
    ) -> _Outcomes:
        key = (id(body), start, incoming.key())
        generating = bool(self.context.generating and self.context.generating[-1])
        if not generating and key in self.cache:
            return {kind: state.copy() for kind, state in self.cache[key].items()}
        if len(self.cache) >= 16384:
            raise ValueError("too many distinct class flow states")
        outcomes = {"normal": incoming.copy()}
        for index in range(start, len(body)):
            node = body[index]
            state = outcomes.pop("normal", None)
            if state is None:
                break
            state.refresh()
            self.context.remember(state.closure)
            results = self.statement(node, state)
            if any(kind in self.context.continuations for kind in results):
                self.context.chain(
                    results, partial(self.follow, body=body, start=index + 1)
                )
            for kind, result in results.items():
                _add_outcome(outcomes, kind, result)
        if not generating:
            self.cache[key] = {kind: state.copy() for kind, state in outcomes.items()}
        return outcomes

    def follow(
        self, outcomes: _Outcomes, body: list[ast.stmt], start: int = 0
    ) -> _Outcomes:
        remaining = dict(outcomes)
        normal = remaining.pop("normal", None)
        if normal is not None:
            for kind, state in self.block(body, normal, start).items():
                _add_outcome(remaining, kind, state)
        return remaining

    def statement(self, node: ast.stmt, state: _State) -> _Outcomes:
        before = state.copy()
        effects = flow_rules.Effects([])
        self.context.effects.append(effects)
        try:
            outcomes = self.dispatch(node, state)
        except flow_rules.Interrupted:
            outcomes = {}
        finally:
            self.context.effects.pop()
        outcomes = flow_rules.apply_effects(outcomes, before, effects)
        if isinstance(
            node, (ast.ClassDef, ast.If, ast.For, ast.AsyncFor, ast.While, ast.Match)
        ):
            # Evaluating a class header, condition, iterable or subject can fail
            # before any selected suite runs.
            _add_outcome(outcomes, "raise", before)
        return outcomes

    def dispatch(self, node: ast.stmt, state: _State) -> _Outcomes:
        if (
            isinstance(node, (ast.Expr, ast.Assign, ast.AnnAssign, ast.Return))
            and isinstance(node.value, ast.Yield)
            and self.context.generating
            and self.context.generating[-1]
        ):
            return self.yield_statement(node, state)
        if isinstance(node, ast.ClassDef):
            return self.definition(node, state)
        if isinstance(node, ast.If):
            self.expression(node.test, state)
            choices = [node.body, node.orelse]
            if isinstance(node.test, ast.Constant):
                choices = [node.body if node.test.value else node.orelse]
            return self.choices(choices, state)
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            return self.loop(node, state)
        if isinstance(node, (ast.Try, ast.TryStar)):
            return self.try_block(node, state)
        if isinstance(node, ast.Match):
            return self.match(node, state)
        if isinstance(node, (ast.With, ast.AsyncWith)):
            return self.with_items(node.items, node.body, state)
        if isinstance(node, (ast.Break, ast.Continue)):
            return {"break" if isinstance(node, ast.Break) else "continue": state}
        return self.simple_statement(node, state)

    def yield_statement(
        self, node: ast.Expr | ast.Assign | ast.AnnAssign | ast.Return, state: _State
    ) -> _Outcomes:
        assert isinstance(node.value, ast.Yield)
        if node.value.value is not None:
            self.expression(node.value.value, state)
        outcomes = self.context.pause(state, self.findings)
        if not isinstance(node, ast.Expr):
            resumed = copy.copy(node)
            # next() resumes the suspended yield expression with None.
            resumed.value = ast.copy_location(ast.Constant(value=None), node.value)
            prepared = self.context.prepared.setdefault(node, resumed)
            assert isinstance(prepared, ast.stmt)
            self.context.chain(outcomes, partial(self.follow, body=[prepared]))
        return outcomes

    def simple_statement(self, node: ast.stmt, state: _State) -> _Outcomes:
        before = state.copy()
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            outcomes = {"raise": before}
            self.findings.update(
                _scan_assignment(
                    node,
                    state.values,
                    state.aliases,
                    state.closure,
                    self.context,
                    lambda: _add_outcome(outcomes, "raise", state),
                )
            )
            outcomes["normal"] = state
            return outcomes
        if isinstance(node, ast.Delete):
            outcomes = {"raise": before}
            self.delete(node.targets, state, outcomes)
            outcomes["normal"] = state
            return outcomes
        self.findings.update(
            _scan_node(node, state.values, state.aliases, state.closure, self.context)
        )
        if not isinstance(node, _SCOPES):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    self.expression(child, state)
        if isinstance(node, ast.Raise):
            return {"raise": state}
        if isinstance(node, ast.Return):
            outcomes = {"return": state}
            if node.value is not None and not isinstance(node.value, ast.Constant):
                outcomes["raise"] = before
            return outcomes
        # Retain a conservative interrupted path even for an assignment whose
        # RHS is literal; static analysis does not execute user expressions.
        return (
            {"normal": state, "raise": before}
            if not isinstance(node, (ast.Pass, ast.Global, ast.Nonlocal))
            else {"normal": state}
        )

    def definition(self, node: ast.ClassDef, state: _State) -> _Outcomes:
        for header in _headers(node):
            self.expression(header, state)
        values, aliases = _bindings(node, *state.closure)
        assert isinstance(values, _ClassValues)
        child = _State(values, aliases, state.closure).copy()
        outcomes: _Outcomes = {}
        for kind, result in self.block(node.body, child).items():
            parent = state.copy()
            flow_rules.publish_context(parent.closure, result.closure)
            if kind == "normal":
                token = f"@class:{id(node)}"
                members = {
                    f"{token}.{name}": resolved
                    for name, resolved in result.values.local_aliases.items()
                }
                parent.aliases.update(members)
                flow_rules.module_bindings(*parent.closure)[1].update(members)
                _bind_definition(
                    node.name, parent.values, parent.aliases, frozenset({token})
                )
            _add_outcome(outcomes, kind, parent)
        return outcomes

    def choices(self, bodies: list[list[ast.stmt]], state: _State) -> _Outcomes:
        outcomes: _Outcomes = {}
        for body in bodies:
            for kind, result in self.block(body, state).items():
                _add_outcome(outcomes, kind, result)
        return outcomes

    def delete(
        self, targets: list[ast.expr], state: _State, outcomes: _Outcomes
    ) -> None:
        for target in targets:
            _add_outcome(outcomes, "raise", state)
            if isinstance(target, ast.Name):
                _restore_class_fallback(
                    target.id, state.values, state.aliases, state.closure
                )
            elif isinstance(target, (ast.Tuple, ast.List)):
                self.delete(target.elts, state, outcomes)
            else:
                self.bind([(target, frozenset(), binding_rules.Value())], state)

    def bind(self, bindings: _Bindings, state: _State) -> None:
        for target, resolved, value in bindings:
            self.findings.update(
                _scan_assignment_target(
                    target,
                    resolved,
                    state.values,
                    state.aliases,
                    state.closure,
                    self.context,
                    value,
                )
            )

    def loop_plan(
        self, node: ast.For | ast.AsyncFor | ast.While, state: _State
    ) -> tuple[list[_Bindings] | None, _Bindings]:
        expression = node.test if isinstance(node, ast.While) else node.iter
        self.expression(expression, state)
        return expression_rules.prepare_loop(node, state, self.context)

    def loop_step(
        self,
        node: ast.For | ast.AsyncFor | ast.While,
        current: _State,
        bindings: _Bindings,
        outcomes: _Outcomes,
        resume: Callable[[_Outcomes], _Outcomes],
    ) -> _State | None:
        _add_outcome(outcomes, "raise", current)
        if isinstance(node, ast.While):
            self.expression(node.test, current)
        self.bind(bindings, current)
        body = self.block(node.body, current)
        self.context.chain(body, resume)
        for kind in body:
            if kind not in {"normal", "continue"}:
                _add_outcome(
                    outcomes, "normal" if kind == "break" else kind, body[kind]
                )
        carried: _Outcomes = {}
        for kind in ("normal", "continue"):
            if kind in body:
                _add_outcome(carried, "normal", body[kind])
        return carried.get("normal")

    def loop(
        self, node: ast.For | ast.AsyncFor | ast.While, state: _State
    ) -> _Outcomes:
        iterations, bindings = self.loop_plan(node, state)
        return self.iterate(node, state, iterations, bindings, 0)

    def iterate(
        self,
        node: ast.For | ast.AsyncFor | ast.While,
        state: _State,
        iterations: list[_Bindings] | None,
        bindings: _Bindings,
        start: int,
    ) -> _Outcomes:
        outcomes: _Outcomes = {}
        exhaustion: _Outcomes = {}
        current: _State | None = state.copy()
        names = state.aliases.get(f"@iteration:{id(node)}", frozenset())
        if not names and iterations is None and binding_rules.loop_may_skip(node):
            _add_outcome(exhaustion, "normal", state)
        if (
            isinstance(node, ast.While)
            and isinstance(node.test, ast.Constant)
            and not node.test.value
        ):
            iterations = []
        seen: set[tuple] = set()
        for index in range(start, len(iterations) if iterations is not None else 256):
            assert current is not None
            if names and not expression_rules.advance_iteration(
                names, current, self.context, self.findings
            ):
                _add_outcome(exhaustion, "normal", current)
                current = None
                break
            current = self.loop_step(
                node,
                current,
                iterations[index] if iterations is not None else bindings,
                outcomes,
                partial(
                    self.resume_loop,
                    node,
                    iterations=iterations,
                    bindings=bindings,
                    start=index + 1,
                ),
            )
            if current is None:
                break
            if iterations is None:
                if not names and not (
                    isinstance(node, ast.While)
                    and isinstance(node.test, ast.Constant)
                    and node.test.value
                ):
                    _add_outcome(exhaustion, "normal", current)
                signature = (
                    expression_rules.iteration_signature(names, current, self.context)
                    if names
                    else current.key()
                )
                if signature in seen:
                    expression_rules.converged_iteration(
                        names, current, self.context, exhaustion
                    )
                    break
                seen.add(signature)
        else:
            if iterations is None:
                raise ValueError("too many static alternatives in a loop")
        return self.loop_exits(
            node, outcomes, exhaustion, current, exact=iterations is not None
        )

    def resume_loop(
        self,
        node: ast.For | ast.AsyncFor | ast.While,
        incoming: _Outcomes,
        iterations: list[_Bindings] | None,
        bindings: _Bindings,
        start: int,
    ) -> _Outcomes:
        outcomes: _Outcomes = {}
        for kind, state in incoming.items():
            if kind in {"normal", "continue"}:
                for result_kind, result in self.iterate(
                    node, state, iterations, bindings, start
                ).items():
                    _add_outcome(outcomes, result_kind, result)
            else:
                _add_outcome(outcomes, "normal" if kind == "break" else kind, state)
        expression_rules.clear_iteration(node, outcomes, self.context)
        return outcomes

    def loop_exits(
        self,
        node: ast.For | ast.AsyncFor | ast.While,
        outcomes: _Outcomes,
        exhaustion: _Outcomes,
        current: _State | None,
        *,
        exact: bool,
    ) -> _Outcomes:
        if exact and current is not None:
            _add_outcome(exhaustion, "normal", current)
        if "normal" in exhaustion:
            for kind, result in self.block(node.orelse, exhaustion["normal"]).items():
                _add_outcome(outcomes, kind, result)
        expression_rules.clear_iteration(node, outcomes, self.context)
        return outcomes

    def handlers(self, node: ast.Try | ast.TryStar, raised: _State) -> _Outcomes:
        outcomes: _Outcomes = {}
        _add_outcome(outcomes, "raise", raised)
        for handler in node.handlers:
            caught = raised.copy()
            if handler.type is not None:
                self.expression(handler.type, caught)
            if handler.name is not None:
                self.bind(
                    [
                        (
                            ast.Name(id=handler.name),
                            frozenset({""}),
                            binding_rules.Value(),
                        )
                    ],
                    caught,
                )
            handled = self.block(handler.body, caught)
            for kind, result in handled.items():
                if handler.name is not None:
                    _restore_class_fallback(
                        handler.name, result.values, result.aliases, result.closure
                    )
                _add_outcome(outcomes, kind, result)
                if isinstance(node, ast.TryStar):
                    raised.merge(result)
        return outcomes

    def try_block(self, node: ast.Try | ast.TryStar, state: _State) -> _Outcomes:
        body = self.block(node.body, state)
        return self.finish_try(node, body)

    def finish_try(self, node: ast.Try | ast.TryStar, body: _Outcomes) -> _Outcomes:
        suspended = {
            kind: state
            for kind, state in body.items()
            if kind in self.context.continuations
        }
        self.context.chain(suspended, lambda result: self.finish_try(node, result))
        outcomes: _Outcomes = {}
        if "normal" in body:
            outcomes.update(self.block(node.orelse, body["normal"]))
        for kind in ("break", "continue", "return"):
            if kind in body:
                _add_outcome(outcomes, kind, body[kind])
        if "raise" in body:
            for kind, result in self.handlers(node, body["raise"]).items():
                _add_outcome(outcomes, kind, result)
        return suspended | self.finish_final(node.finalbody, outcomes)

    def finish_final(self, body: list[ast.stmt], outcomes: _Outcomes) -> _Outcomes:
        if not body:
            return outcomes
        self.context.chain(outcomes, lambda result: self.finish_final(body, result))
        finished: _Outcomes = {}
        for previous, incoming in outcomes.items():
            if previous in self.context.continuations:
                finished[previous] = incoming
                continue
            results = self.block(body, incoming)
            self.context.chain(
                results,
                partial(flow_rules.restore_outcome, previous=previous),
            )
            for kind, result in results.items():
                _add_outcome(finished, previous if kind == "normal" else kind, result)
        return finished

    def match_case(
        self,
        case: ast.match_case,
        subject: ast.expr,
        subjects: _State,
        remaining: _State,
    ) -> tuple[_State, _State | None, bool]:
        matched = binding_rules.pattern_matches(case.pattern, subject)
        if matched is False:
            return remaining, remaining, False
        branch = remaining.copy()
        captures = {
            name: (binding_rules.Value(), frozenset({""}))
            for name in binding_rules.bound_names(list(ast.walk(case.pattern)))
        }
        captures.update(
            binding_rules.pattern_bindings(
                case.pattern, subject, subjects.values, subjects.aliases
            )
        )
        self.bind(
            [
                (ast.Name(id=name), aliases, value)
                for name, (value, aliases) in captures.items()
            ],
            branch,
        )
        if case.guard is not None:
            self.expression(case.guard, branch)
        irrefutable = matched is True
        guard = bool(case.guard.value) if isinstance(case.guard, ast.Constant) else None
        pending: _Outcomes = {}
        if not irrefutable:
            _add_outcome(pending, "normal", remaining)
        if case.guard is not None and guard is not True:
            _add_outcome(pending, "normal", branch)
        return branch, pending.get("normal"), guard is not False

    def match(self, node: ast.Match, state: _State) -> _Outcomes:
        self.expression(node.subject, state)
        subjects = state.copy()
        remaining: _State | None = state
        outcomes: _Outcomes = {}
        for case in node.cases:
            if remaining is None:
                break
            branch, remaining, accepted = self.match_case(
                case, node.subject, subjects, remaining
            )
            if accepted:
                for kind, result in self.block(case.body, branch).items():
                    _add_outcome(outcomes, kind, result)
        if remaining is not None:
            _add_outcome(outcomes, "normal", remaining)
        return outcomes

    def with_items(
        self, items: list[ast.withitem], body: list[ast.stmt], state: _State
    ) -> _Outcomes:
        if not items:
            return self.block(body, state)
        item, *remaining = items
        self.expression(item.context_expr, state)
        entry = state.copy()
        if item.optional_vars is not None:
            self.bind(
                binding_rules.prepared_assignment(
                    [item.optional_vars],
                    ast.Constant(value=None),
                    state.values,
                    state.aliases,
                ),
                state,
            )
        outcomes = self.with_items(remaining, body, state)
        # A manager can suppress a body error, but not its own exit error.
        exit_states = list(outcomes.values())
        if "raise" in outcomes:
            _add_outcome(outcomes, "normal", outcomes["raise"])
        for result in exit_states:
            _add_outcome(outcomes, "raise", result)
        _add_outcome(outcomes, "raise", entry)
        return outcomes


def _scan_node(
    node: ast.AST,
    values: dict[str, binding_rules.Value],
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
    context: _Deferred,
) -> Iterator[tuple[int, str]]:
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        yield from _scan_assignment(node, values, aliases, closure, context)
    elif isinstance(node, _SCOPES):
        yield from _scan_nested_scope(node, values, aliases, closure, context)
    else:
        imported: binding_rules.Aliases = {}
        binding_rules.import_aliases([node], imported)
        aliases.update(imported)
        if isinstance(values, _ClassValues):
            for name in imported:
                values[name] = binding_rules.Value(callables=imported[name])
                _store_class_name(values, aliases, name)
    if context.effects and not context.effects[-1].normal:
        raise flow_rules.Interrupted


def _scan_expression(
    expression: ast.expr,
    values: dict[str, binding_rules.Value],
    aliases: binding_rules.Aliases,
    closure: tuple[dict[str, binding_rules.Value], binding_rules.Aliases],
    context: _Deferred,
) -> Generator[tuple[int, str], None, tuple[ast.expr, _Env]]:
    evaluator = expression_rules.Expression(
        context, _scan_node, _targets, _private, _SCOPES
    )
    return (yield from evaluator.scan(expression, (values, aliases), closure))


def _scan_scope(
    scope: ast.AST,
    values: dict[str, binding_rules.Value],
    aliases: binding_rules.Aliases,
    initial_values: dict[str, binding_rules.Value] | None = None,
    *,
    context: _Deferred | None = None,
    on_outcomes: Callable[[_Outcomes], None] | None = None,
) -> Iterator[tuple[int, str]]:
    context = context or _Deferred(_scan_scope, _scan_expression)
    context.pending.append(set())
    values, aliases = _bindings(scope, values, aliases, initial_values)
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        values, aliases = flow_rules.copy_context((values, aliases))
    if isinstance(scope, ast.Module) and isinstance(values, flow_rules.ScopeValues):
        values.module_bindings = (values, aliases)
    closure = (values, aliases)
    if isinstance(scope, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)):
        analyzer = _ClassFlow(context)
        state = _State(_ClassValues(values), aliases, closure).copy()
        outcomes = analyzer.block(scope.body, state)
        if on_outcomes is not None:
            on_outcomes(outcomes)
        pending = context.pending.pop()
        yield from sorted(analyzer.findings)
        for kind in ("normal", "return"):
            if kind in outcomes:
                context.remember(outcomes[kind].closure)
                yield from expression_rules.scan_pending(
                    pending, outcomes[kind].closure, context
                )
        return
    yield from expression_rules.scan_body(
        scope, closure, context, on_outcomes, _scan_expression
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
                    f"{path.relative_to(args.repo).as_posix()}:{line}: private string target {target!r}"
                    for line, target in _scan_scope(source, {}, {})
                )
            except (SyntaxError, ValueError) as error:
                violations.append(f"{path.relative_to(args.repo).as_posix()}: {error}")
    if violations:
        print("\n".join(dict.fromkeys(violations)))
        print("Use the public entry point or real configuration; no test is exempt.")
        return 1
    print("No private string targets in unit or app tests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
