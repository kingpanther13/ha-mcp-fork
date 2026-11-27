"""Throwaway proof for issue 3218. Never merged.

Part A: the ``try/except Exception`` around ``add_middleware`` in
``_apply_tool_security_policies`` cannot be entered.
Part B: which broken policy module lets the server start ungated.
"""

from __future__ import annotations

import ast
import inspect
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
REGISTERED = "Tool security policies middleware registered"
NOT_ACTIVE = "TOOL SECURITY GATING IS NOT ACTIVE"

BASE_ENV = {
    "HOMEASSISTANT_URL": "http://127.0.0.1:9",
    "HOMEASSISTANT_TOKEN": "unused",
    "HA_MCP_DISABLE_SETTINGS_UI": "1",
    "HA_MCP_DISABLE_UPDATE_CHECK": "1",
    "ENABLE_AUTO_BACKUP": "false",
    "PYTHONDONTWRITEBYTECODE": "1",
}

CHILD = r"""
import io, json, logging, sys, traceback
buf = io.StringIO()
logging.basicConfig(stream=buf, level=logging.INFO, force=True)
out = {}
try:
    import ha_mcp
    out["pkg"] = ha_mcp.__file__
    from ha_mcp.server import HomeAssistantSmartMCPServer
    srv = HomeAssistantSmartMCPServer()
    out["constructed"] = True
    out["flag"] = bool(srv.settings.enable_tool_security_policies)
    out["policy_middleware"] = any(
        type(m).__name__ == "PolicyMiddleware" for m in srv.mcp.middleware
    )
except BaseException as exc:
    out["constructed"] = False
    out["exc"] = (type(exc).__name__ + ": " + str(exc))[:200]
    frames = traceback.extract_tb(exc.__traceback__)
    out["where"] = [
        f.filename.split("ha_mcp/", 1)[-1] + ":" + str(f.lineno)
        for f in frames
        if "ha_mcp/" in f.filename and "_vendor" not in f.filename
    ][:8]
log = buf.getvalue()
out["log_not_active"] = "TOOL SECURITY GATING IS NOT ACTIVE" in log
out["log_registered"] = "Tool security policies middleware registered" in log
print("RESULT " + json.dumps(out))
"""

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    CHECKS.append((name, ok, str(detail)))
    print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""))


def find_func(tree: ast.AST, name: str) -> ast.FunctionDef:
    return next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name
    )


def find_class(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name
    )


def body_without_docstring(fn: ast.FunctionDef) -> list[ast.stmt]:
    first = fn.body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return fn.body[1:]
    return fn.body


def part_a() -> None:
    print("\n===== PART A: the add_middleware try block =====")
    os.environ.update(BASE_ENV)
    os.environ["ENABLE_TOOL_SECURITY_POLICIES"] = "true"
    os.environ["HA_MCP_CONFIG_DIR"] = tempfile.mkdtemp(prefix="proofA-")

    # --- static: what the try block contains ---
    server_tree = ast.parse((SRC / "ha_mcp/server.py").read_text())
    fn = find_func(server_tree, "_apply_tool_security_policies")
    tries = [n for n in fn.body if isinstance(n, ast.Try)]
    guarded = tries[-1]
    lo, hi = guarded.body[0].lineno, guarded.body[-1].end_lineno or 0
    print(f"try body: server.py lines {lo}-{hi}")
    for stmt in guarded.body:
        print("  stmt:", ast.unparse(stmt).replace("\n", " "))
    calls = sorted(
        {
            ast.unparse(c.func)
            for stmt in guarded.body
            for c in ast.walk(stmt)
            if isinstance(c, ast.Call)
        }
    )
    attrs = sorted(
        {
            ast.unparse(a)
            for stmt in guarded.body
            for a in ast.walk(stmt)
            if isinstance(a, ast.Attribute)
        }
    )
    print("  attribute reads:", attrs)
    check("try body is two statements", len(guarded.body) == 2, len(guarded.body))
    check(
        "the only calls in the try body",
        calls == ["PolicyMiddleware", "logger.info", "self.mcp.add_middleware"],
        calls,
    )
    handler_types = [ast.unparse(h.type) if h.type else "bare" for h in guarded.handlers]
    check("handler is `except Exception`", handler_types == ["Exception"], handler_types)

    mw_tree = ast.parse((SRC / "ha_mcp/policy/middleware.py").read_text())
    mw_cls = find_class(mw_tree, "PolicyMiddleware")
    init = next(
        n for n in mw_cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"
    )
    init_stmts = body_without_docstring(init)
    for stmt in init_stmts:
        print("  __init__ stmt:", ast.unparse(stmt))
    check(
        "PolicyMiddleware.__init__ is only `self.x = <argument>` assignments",
        all(
            isinstance(s, ast.Assign)
            and len(s.targets) == 1
            and isinstance(s.targets[0], ast.Attribute)
            and isinstance(s.targets[0].value, ast.Name)
            and s.targets[0].value.id == "self"
            and isinstance(s.value, ast.Name)
            for s in init_stmts
        ),
        len(init_stmts),
    )
    check(
        "PolicyMiddleware has no metaclass keyword or class decorator",
        not mw_cls.keywords and not mw_cls.decorator_list,
    )

    vendor_tree = ast.parse(
        (SRC / "ha_mcp/_vendor/fastmcp/server/server.py").read_text()
    )
    add_mw = next(
        n
        for n in find_class(vendor_tree, "FastMCP").body
        if isinstance(n, ast.FunctionDef) and n.name == "add_middleware"
    )
    add_body = [ast.unparse(s) for s in body_without_docstring(add_mw)]
    check(
        "FastMCP.add_middleware body is one list append",
        add_body == ["self.middleware.append(middleware)"] and not add_mw.decorator_list,
        add_body,
    )

    # --- runtime: what the try block actually calls ---
    import ha_mcp.server as server_module
    from ha_mcp._vendor.fastmcp.server.server import FastMCP as VendorFastMCP
    from ha_mcp.policy.middleware import PolicyMiddleware
    from ha_mcp.server import HomeAssistantSmartMCPServer

    original = HomeAssistantSmartMCPServer._apply_tool_security_policies
    target_code = original.__code__
    seen: list[tuple[str, str]] = []

    def profiler(frame, event, arg):  # type: ignore[no-untyped-def]
        if event == "call":
            caller = frame.f_back
            if (
                caller is not None
                and caller.f_code is target_code
                and lo <= caller.f_lineno <= hi
            ):
                seen.append(("py", frame.f_code.co_qualname))
        elif event == "c_call":
            if frame.f_code is target_code and lo <= frame.f_lineno <= hi:
                seen.append(("c", getattr(arg, "__qualname__", repr(arg))))

    def traced(self):  # type: ignore[no-untyped-def]
        sys.setprofile(profiler)
        try:
            return original(self)
        finally:
            sys.setprofile(None)

    HomeAssistantSmartMCPServer._apply_tool_security_policies = traced  # type: ignore[method-assign]
    try:
        srv = HomeAssistantSmartMCPServer()
    finally:
        HomeAssistantSmartMCPServer._apply_tool_security_policies = original  # type: ignore[method-assign]

    print("  direct callees of the try body at runtime:", seen)
    check(
        "runtime: Python functions called directly from the try body",
        [name for kind, name in seen if kind == "py"]
        == ["PolicyMiddleware.__init__", "FastMCP.add_middleware", "Logger.info"],
        seen,
    )
    mcp_type = type(srv.mcp)
    definer = next(k for k in mcp_type.__mro__ if "add_middleware" in vars(k))
    check(
        "runtime: add_middleware resolves to the vendored FastMCP method",
        definer is VendorFastMCP,
        f"{mcp_type.__name__} -> {definer.__module__}.{definer.__name__}",
    )
    check("runtime: mcp.middleware is a plain list", type(srv.mcp.middleware) is list)
    check("runtime: PolicyMiddleware metaclass is type", type(PolicyMiddleware) is type)
    check(
        "runtime: PolicyMiddleware uses object.__new__ and object.__setattr__",
        PolicyMiddleware.__new__ is object.__new__
        and PolicyMiddleware.__setattr__ is object.__setattr__
        and not hasattr(PolicyMiddleware, "__slots__"),
    )
    check(
        "runtime: mcp / approval_queue / approval_response_listener are not properties",
        not any(
            isinstance(inspect.getattr_static(type(srv), name, None), property)
            for name in ("mcp", "approval_queue", "approval_response_listener")
        ),
    )
    check(
        "runtime: ensure_subscribed exists on the listener",
        callable(srv.approval_response_listener.ensure_subscribed),
    )
    check(
        "runtime: baseline registers PolicyMiddleware",
        any(isinstance(m, PolicyMiddleware) for m in srv.mcp.middleware),
    )

    # --- the one statement left: logger.info runs AFTER the append ---
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    capture = Capture()
    server_module.logger.addHandler(capture)
    real_info = server_module.logger.info

    def raising_info(msg, *args, **kwargs):  # type: ignore[no-untyped-def]
        if str(msg).startswith(REGISTERED):
            raise RuntimeError("injected logging failure")
        return real_info(msg, *args, **kwargs)

    server_module.logger.info = raising_info  # type: ignore[method-assign]
    try:
        srv2 = HomeAssistantSmartMCPServer()
    finally:
        server_module.logger.info = real_info  # type: ignore[method-assign]
        server_module.logger.removeHandler(capture)
    said_not_active = any(NOT_ACTIVE in r for r in records)
    is_registered = any(isinstance(m, PolicyMiddleware) for m in srv2.mcp.middleware)
    check(
        "forced entry (logger.info raises): handler says NOT ACTIVE while the "
        "middleware IS registered",
        said_not_active and is_registered,
        f"said_not_active={said_not_active} middleware_registered={is_registered}",
    )


def run_child(src_root: Path, flag: bool) -> dict[str, object]:
    env = dict(os.environ)
    env.update(BASE_ENV)
    env["PYTHONPATH"] = str(src_root)
    env["ENABLE_TOOL_SECURITY_POLICIES"] = "true" if flag else "false"
    env["HA_MCP_CONFIG_DIR"] = tempfile.mkdtemp(prefix="proofB-")
    try:
        proc = subprocess.run(
            [sys.executable, "-c", CHILD],
            env=env,
            capture_output=True,
            text=True,
            timeout=240,
            cwd=env["HA_MCP_CONFIG_DIR"],
        )
    except subprocess.TimeoutExpired:
        return {"outcome": "TIMEOUT"}
    line = next(
        (ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")), None
    )
    if line is None:
        return {"outcome": "NO_RESULT", "stderr": proc.stderr[-400:]}
    out: dict[str, object] = json.loads(line[len("RESULT ") :])
    out["shadowed"] = str(out.get("pkg", "")).startswith(str(src_root))
    if NOT_ACTIVE in proc.stderr:
        out["log_not_active"] = True
    if not out["constructed"]:
        out["outcome"] = "STARTUP_FAILED"
    elif out["policy_middleware"]:
        out["outcome"] = "GATED"
    elif flag:
        out["outcome"] = "RUNNING_UNGATED"
    else:
        out["outcome"] = "RUNNING_FLAG_OFF"
    return out


def scenario(module: str | None, mutation: str | None, flag: bool) -> dict[str, object]:
    root = Path(tempfile.mkdtemp(prefix="proofsrc-"))
    shutil.copytree(
        SRC / "ha_mcp",
        root / "ha_mcp",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    if module and mutation:
        target = root / "ha_mcp" / "policy" / module
        if mutation == "delete":
            target.unlink()
        elif mutation == "empty":
            target.write_text("")
        elif mutation == "syntax":
            target.write_text("def (:\n")
    result = run_child(root, flag)
    shutil.rmtree(root, ignore_errors=True)
    result.update(module=module or "-", mutation=mutation or "none", flag=flag)
    return result


def part_b() -> list[dict[str, object]]:
    print("\n===== PART B: break one policy module at a time, then start =====")
    modules = sorted(p.name for p in (SRC / "ha_mcp/policy").glob("*.py"))
    jobs: list[tuple[str | None, str | None, bool]] = [
        (None, None, True),
        (None, None, False),
    ]
    for module in modules:
        for mutation in ("delete", "empty", "syntax"):
            jobs.append((module, mutation, True))
        jobs.append((module, "delete", False))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda job: scenario(*job), jobs))
    for r in results:
        detail = ""
        if r["outcome"] == "STARTUP_FAILED":
            detail = f"{r.get('exc')} @ {' > '.join(r.get('where', []))}"  # type: ignore[arg-type]
        elif r["outcome"] in ("NO_RESULT", "TIMEOUT"):
            detail = str(r.get("stderr", ""))
        else:
            detail = (
                f"log_not_active={r.get('log_not_active')} "
                f"log_registered={r.get('log_registered')}"
            )
        print(
            f"ROW | {r['module']:<20} | {r['mutation']:<6} | flag={'on ' if r['flag'] else 'off'}"
            f" | {r['outcome']:<16} | shadowed={r.get('shadowed')} | {detail}"
        )
    return results


def main() -> int:
    part_a()
    results = part_b()
    ungated = sorted(
        f"{r['module']}:{r['mutation']}" for r in results if r["outcome"] == "RUNNING_UNGATED"
    )
    failed_on = sorted(
        {
            str(r["module"])
            for r in results
            if r["flag"] and r["outcome"] == "STARTUP_FAILED"
        }
    )
    odd = [r for r in results if r["outcome"] in ("NO_RESULT", "TIMEOUT")]
    print("\n===== SUMMARY =====")
    print("part A failed checks:", [name for name, ok, _ in CHECKS if not ok])
    print("RUNNING_UNGATED scenarios:", ungated)
    print("modules whose breakage stops startup (flag on):", failed_on)
    print("inconclusive scenarios:", len(odd))
    return 1 if any(not ok for _, ok, _ in CHECKS) or odd else 0


if __name__ == "__main__":
    raise SystemExit(main())
