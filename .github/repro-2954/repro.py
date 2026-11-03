"""Reproduce issue #2954 through a real stdio MCP client.

Env: HAMCP_CMD (the installed ha-mcp entry point), HA_URL (empty = no HA,
overview calls skipped), CONFIG_DIR (fresh HA_MCP_CONFIG_DIR).
"""

import asyncio
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from ha_mcp._vendor.fastmcp import Client
from ha_mcp._vendor.fastmcp.client.transports import StdioTransport

TOKEN = os.environ["HA_TOKEN"]
HA_URL = os.environ.get("HA_URL", "")
CMD = os.environ["HAMCP_CMD"]
CFG = Path(os.environ["CONFIG_DIR"])
results: dict[str, bool] = {}


def files() -> dict[str, str | None]:
    out = {}
    for name in ("ui.url", "ui.pid", "settings_ui_disabled"):
        p = CFG / name
        out[name] = p.read_text().strip() if p.exists() else None
    return out


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def listening(url: str | None) -> bool:
    if not url:
        return False
    u = urlparse(url)
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex((u.hostname, u.port)) == 0


def sidecar_pids() -> list[int]:
    r = subprocess.run(
        ["pgrep", "-f", "ha_mcp.stdio_settings_sidecar"],
        capture_output=True,
        text=True,
        check=False,
    )
    return [int(x) for x in r.stdout.split()]


def state(label: str) -> dict:
    f = files()
    s = {
        "files": f,
        "sidecar_pids": sidecar_pids(),
        "ui.url_listening": listening(f["ui.url"]),
    }
    print(f"\n--- state {label}: {json.dumps(s, indent=1)}", flush=True)
    return s


async def session(label: str, disabled: bool, kill_mid: bool = False) -> dict | None:
    env = {
        "HOMEASSISTANT_URL": HA_URL or "http://127.0.0.1:9",
        "HOMEASSISTANT_TOKEN": TOKEN,
        "HA_MCP_CONFIG_DIR": str(CFG),
        "HA_MCP_DISABLE_SETTINGS_UI": "1" if disabled else "0",
        "HA_MAX_RETRIES": "1",
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
    }
    print(
        f"\n=== session {label} (HA_MCP_DISABLE_SETTINGS_UI={env['HA_MCP_DISABLE_SETTINGS_UI']})",
        flush=True,
    )
    transport = StdioTransport(command=CMD, args=[], env=env, keep_alive=False)
    overview = None
    async with Client(transport, timeout=60) as client:
        tools = await client.list_tools()
        print(f"tools listed: {len(tools)}")
        if kill_mid:
            for _ in range(50):
                if files()["ui.url"] and listening(files()["ui.url"]):
                    break
                await asyncio.sleep(0.2)
            pid = files()["ui.pid"]
            subprocess.run(["kill", "-KILL", pid], check=False)  # noqa: ASYNC221
            await asyncio.sleep(1)
            ps = subprocess.run(  # noqa: ASYNC221
                ["ps", "-o", "stat=", "-p", pid],
                capture_output=True,
                text=True,
                check=False,
            )
            print(
                f"sidecar pid {pid} after SIGKILL, parent alive: stat={ps.stdout.strip()!r}"
            )
            state("mid-session after SIGKILL")
        if HA_URL:
            r = await client.call_tool(
                "ha_get_overview",
                {"fields": ["success"], "include_notifications": False},
            )
            overview = r.structured_content
            print("ha_get_overview(fields=['success']) ->", json.dumps(overview))
    await asyncio.sleep(2)  # let the parent exit settle
    return overview


def check(name: str, ok: bool, detail: str) -> None:
    results[name] = ok
    print(f"[{'REPRODUCED' if ok else 'not reproduced'}] {name}: {detail}", flush=True)


async def pkill(sig: str) -> None:
    print(f"\n=== pkill -{sig} -f ha_mcp.stdio_settings_sidecar", flush=True)
    subprocess.run(  # noqa: ASYNC221
        ["pkill", f"-{sig}", "-f", "ha_mcp.stdio_settings_sidecar"], check=False
    )
    deadline = time.monotonic() + 15
    while sidecar_pids() and time.monotonic() < deadline:  # noqa: ASYNC110
        await asyncio.sleep(0.2)


async def main() -> None:
    CFG.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240

    ov = await session("A: sidecar enabled", disabled=False)
    a = state("after A")
    print("baseline sidecar up:", bool(a["files"]["ui.url"]) and a["ui.url_listening"])
    if ov is not None:
        print("baseline overview has settings_url:", "settings_url" in ov)

    await pkill("TERM")
    c = state("after SIGTERM")
    check(
        "gap3 ui.url left behind after graceful SIGTERM",
        not c["sidecar_pids"] and c["files"]["ui.url"] is not None,
        f"pids={c['sidecar_pids']} ui.url present={c['files']['ui.url'] is not None}",
    )

    await session("A2: sidecar enabled again", disabled=False)
    state("after A2")
    ov = await session("B: disabled via env", disabled=True)
    b = state("after B")
    check(
        "gap1 running sidecar survives the disable switch",
        bool(b["sidecar_pids"]) and b["ui.url_listening"],
        f"pids={b['sidecar_pids']} listening={b['ui.url_listening']}",
    )
    if ov is not None:
        check(
            "gap2a overview returns settings_url while disabled",
            "settings_url" in ov,
            f"keys={sorted(ov)}",
        )

    await session("A3: sidecar enabled, then crash it", disabled=False)
    await pkill("KILL")
    state("after SIGKILL (stale files expected)")
    ov = await session("C: disabled, no sidecar running", disabled=True)
    d = state("after C")
    if ov is not None:
        check(
            "gap2b overview returns settings_url with nothing listening",
            "settings_url" in ov and not d["ui.url_listening"],
            f"keys={sorted(ov)} listening={d['ui.url_listening']}",
        )

    ov = await session(
        "Z: SIGKILL sidecar while parent lives", disabled=False, kill_mid=True
    )
    z = state("after Z")
    if ov is not None:
        check(
            "gapZ overview returns a SIGKILLed (zombie) sidecar's URL",
            "settings_url" in ov,
            f"keys={sorted(ov)} listening={z['ui.url_listening']}",
        )

    log = CFG / "sidecar.log"
    if log.exists():
        print("\n--- sidecar.log ---\n" + log.read_text()[-6000:])
    print("\nSUMMARY", json.dumps(results, indent=1))


asyncio.run(main())
