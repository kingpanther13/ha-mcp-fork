"""Live-iteration harness for ha-mcp.

Spawns a fresh `uv run ha-mcp` subprocess on every invocation, talking to it
over stdio JSON-RPC (the same protocol Claude Code uses). Because each call
starts a new subprocess, any edit under `src/ha_mcp/...` is picked up on the
next call — no MCP reconnect, no daemon, no caching. Pair with a long-lived
test HA container so you can iterate on tool source and verify against real
HA state in seconds.

Run from the repo root:
    uv run python scripts/dev_harness.py <command> [args...]

Commands:
    tools                              list every tool the server exposes
    call <tool_name> [k=v ...]         call any tool; values are JSON-decoded
                                       when possible (limit=3 -> int,
                                       wait=false -> bool, tags=[\"a\"] -> list)
    state <entity_id>                  HA entity state via direct WebSocket
    helper-config <type> <helper_id>   storage entry for a simple helper via WS
    smoke                              quick end-to-end sanity check

Environment overrides:
    HA_TEST_HOST          default: localhost
    HA_TEST_PORT          default: 32769
    HOMEASSISTANT_TOKEN   default: the public TEST_TOKEN from tests/test_constants.py

See docs/dev-harness.md for setup details.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import websockets

REPO = Path(__file__).resolve().parents[1]
HA_HOST = os.environ.get("HA_TEST_HOST", "localhost")
HA_PORT = int(os.environ.get("HA_TEST_PORT", "32769"))
HA_URL_HTTP = f"http://{HA_HOST}:{HA_PORT}"
HA_URL_WS = f"ws://{HA_HOST}:{HA_PORT}/api/websocket"
# Public test token from tests/test_constants.py (embedded in the test HA
# container's auth storage, expires 2035). Override via HOMEASSISTANT_TOKEN.
HA_TOKEN = os.environ.get(
    "HOMEASSISTANT_TOKEN",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiIxOTE5ZTZlMTVkYjI0Mzk2YTQ4YjFiZTI1MDM1YmU2YSIsImlhdCI6MTc1NzI4OTc5NiwiZXhwIjoyMDcyNjQ5Nzk2fQ.Yp9SSAjm2gvl9Xcu96FFxS8SapHxWAVzaI0E3cD9xac",
)


# ---------- direct HA WS verification helpers -----------------------------


async def ws_call(payload: dict[str, Any]) -> dict[str, Any]:
    async with websockets.connect(HA_URL_WS) as ws:
        hello = json.loads(await ws.recv())
        assert hello["type"] == "auth_required"
        await ws.send(json.dumps({"type": "auth", "access_token": HA_TOKEN}))
        ok = json.loads(await ws.recv())
        assert ok["type"] == "auth_ok", ok
        await ws.send(json.dumps({"id": 1, **payload}))
        return json.loads(await ws.recv())


async def ws_state(entity_id: str) -> dict[str, Any] | None:
    r = await ws_call({"type": "get_states"})
    if r.get("type") != "result" or not r.get("success"):
        return None
    for s in r.get("result", []):
        if s.get("entity_id") == entity_id:
            return s
    return None


async def ws_helper_config(helper_type: str, helper_id: str) -> dict[str, Any] | None:
    r = await ws_call({"type": f"{helper_type}/list"})
    if r.get("type") != "result" or not r.get("success"):
        return None
    items = r.get("result", [])
    if isinstance(items, dict):
        items = items.get("storage", []) or []
    for item in items:
        if isinstance(item, dict) and item.get("id") == helper_id:
            return item
    return None


# ---------- MCP subprocess driver -----------------------------------------


class MCPClient:
    """Drives a fresh ha-mcp subprocess via JSON-RPC over stdio."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self._next_id = 1

    async def __aenter__(self) -> "MCPClient":
        env = os.environ.copy()
        env["HOMEASSISTANT_URL"] = HA_URL_HTTP
        env["HOMEASSISTANT_TOKEN"] = HA_TOKEN
        self.proc = subprocess.Popen(
            ["uv", "run", "ha-mcp"],
            cwd=REPO,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            bufsize=1,
        )
        await self._send(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "dev-harness", "version": "0.1"},
            },
        )
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    async def __aexit__(self, *exc) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def _write(self, msg: dict[str, Any]) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    async def _send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        rid = self._next_id
        self._next_id += 1
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        assert self.proc and self.proc.stdout
        for _ in range(500):
            line = await asyncio.get_event_loop().run_in_executor(
                None, self.proc.stdout.readline
            )
            if not line:
                err = self.proc.stderr.read() if self.proc.stderr else ""
                raise RuntimeError(f"MCP server closed stdout. stderr:\n{err}")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == rid:
                return msg
        raise RuntimeError(f"Timed out waiting for response to {method}")

    async def list_tools(self) -> list[dict[str, Any]]:
        resp = await self._send("tools/list")
        return resp.get("result", {}).get("tools", [])

    async def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return await self._send("tools/call", {"name": name, "arguments": args})


# ---------- commands ------------------------------------------------------


def _parse_kv(args: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for a in args:
        if "=" not in a:
            raise SystemExit(f"expected key=value, got {a!r}")
        k, v = a.split("=", 1)
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def _print_tool_result(resp: dict[str, Any]) -> None:
    result = resp.get("result", resp)
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list) and content and content[0].get("type") == "text":
        text = content[0].get("text", "")
        try:
            print(json.dumps(json.loads(text), indent=2))
            return
        except json.JSONDecodeError:
            print(text)
            return
    print(json.dumps(resp, indent=2))


async def cmd_tools() -> None:
    async with MCPClient() as mcp:
        tools = await mcp.list_tools()
    print(f"{len(tools)} tools registered")
    for t in sorted(tools, key=lambda x: x.get("name", "")):
        print(f"  {t.get('name')}")


async def cmd_call(tool_name: str, kv: list[str]) -> None:
    args = _parse_kv(kv)
    async with MCPClient() as mcp:
        resp = await mcp.call_tool(tool_name, args)
    _print_tool_result(resp)


async def cmd_state(entity_id: str) -> None:
    state = await ws_state(entity_id)
    print(json.dumps(state, indent=2))


async def cmd_helper_config(helper_type: str, helper_id: str) -> None:
    cfg = await ws_helper_config(helper_type, helper_id)
    print(json.dumps(cfg, indent=2))


async def cmd_smoke() -> None:
    async with MCPClient() as mcp:
        tools = await mcp.list_tools()
        print(f"tools: {len(tools)} loaded")
        resp = await mcp.call_tool("ha_search_entities", {"query": "bed_light", "limit": 1})
    _print_tool_result(resp)


async def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    rest = sys.argv[2:]
    if cmd == "tools":
        await cmd_tools()
    elif cmd == "call":
        if not rest:
            raise SystemExit("usage: call <tool_name> [k=v ...]")
        await cmd_call(rest[0], rest[1:])
    elif cmd == "state":
        await cmd_state(rest[0])
    elif cmd == "helper-config":
        await cmd_helper_config(rest[0], rest[1])
    elif cmd == "smoke":
        await cmd_smoke()
    else:
        print(f"unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
