"""One fork-run experiment. No service or public control endpoint is installed."""
import asyncio
import json
import logging
import os
import runpy
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from ha_mcp._vendor.fastmcp import Client
from ha_mcp._vendor.fastmcp.client.transports.stdio import StdioTransport

ROOT = Path(os.environ["GITHUB_WORKSPACE"])
SRC = ROOT / "src"
MASTER = "7b50b56139f42c8813f26d555c88e84efd0fa7b2"
PR = "ad2b2cfdfd91102c9dfae511f8c34affdc08ef96"
logging.disable(logging.WARNING)


def git(*args):
    return subprocess.check_output(["git", "-C", SRC, *args], text=True).strip()


def ha_container():
    ids = subprocess.check_output(["docker", "ps", "--filter", "publish=8123", "-q"], text=True).split()
    assert len(ids) == 1, "Expected one HA container on 8123"
    return subprocess.check_output(["docker", "inspect", "--format", "{{.Id}} {{.State.StartedAt}}", ids[0]], text=True).strip()


async def snapshot(client, label, expected):
    tools = await client.list_tools()
    dev = next(t for t in tools if t.name == "ha_dev_manage_server")
    marker = "component_server_entry.install_pending" in dev.description
    assert marker == expected, f"{label}: unexpected server code"
    result = await client.call_tool_mcp("ha_get_state", {"entity_id":"zone.home","fields":["context","last_changed"]})
    assert not result.isError, f"{label}: state read failed"
    data = json.loads(result.content[0].text)["data"]
    identity = (data["context"]["id"], data["last_changed"])
    print(f"{label}: PR marker={marker}, zone.home={identity}", flush=True)
    return identity


async def main():
    assert git("rev-parse", "HEAD") == MASTER
    git("fetch", "https://github.com/homeassistant-ai/ha-mcp.git", "refs/pull/3195/head")
    assert git("rev-parse", "FETCH_HEAD") == PR
    container = ha_container()
    print(f"HA container and startup: {container}")
    path = (ROOT / "mcp-path").read_text().strip()
    web_url = (ROOT / "tunnel-mcp.url").read_text().strip() + path
    tunnel_pid = int((ROOT / "tunnel-mcp.pid").read_text())
    # The public DNS record can take a moment to propagate. Any HTTP response
    # other than a gateway error proves the endpoint is already routed.
    deadline = time.monotonic() + 150
    while True:
        try:
            with urllib.request.urlopen(web_url, timeout=5):
                break
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                break
        except OSError:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("Public web route did not become ready")
        await asyncio.sleep(2)
    env = {
        "HOMEASSISTANT_URL":"http://127.0.0.1:8123",
        "HOMEASSISTANT_TOKEN":runpy.run_path(str(SRC / "tests/test_constants.py"))["TEST_TOKEN"],
        "HAMCP_ENABLE_DEV_MODE":"true",
        "HA_MCP_DISABLE_SETTINGS_UI":"true",
        "HA_MCP_CONFIG_DIR":str(ROOT / "stdio-config"),
    }
    stdio = Client(StdioTransport(str(SRC / ".venv/bin/ha-mcp"), args=[], cwd=str(SRC),
                                   env=env, keep_alive=False, log_file=ROOT / "stdio-private.log"), timeout=90)
    async with stdio:
        baseline = await snapshot(stdio, "master stdio", False)
    web = Client(web_url, timeout=60)
    async with web:
        assert await snapshot(web, "master public web", False) == baseline
        print("Web client stays open across restart. Requesting pinned checkout update.")
        (ROOT / "restart-request").touch()
        deadline = time.monotonic() + 170
        while not (ROOT / "restart-result").exists():
            if time.monotonic() > deadline:
                raise RuntimeError("Runner restart timed out")
            await asyncio.sleep(0.5)
        receipt = json.loads((ROOT / "restart-result").read_text())
        print("Runner restart receipt:", receipt)
        assert receipt["sha"] == PR and receipt["old_pid"] != receipt["new_pid"]
        try:
            continued = await snapshot(web, "PR public web, existing client", True)
        except Exception as exc:
            print("Existing web client required reconnect:", type(exc).__name__)
        else:
            assert continued == baseline
    async with stdio:
        assert await snapshot(stdio, "PR stdio, same launch configuration", True) == baseline
    async with Client(web_url, timeout=60) as reconnected:
        assert await snapshot(reconnected, "PR public web, fresh client and original URL", True) == baseline
    assert ha_container() == container
    assert git("rev-parse","HEAD") == PR
    assert int((ROOT / "tunnel-mcp.pid").read_text()) == tunnel_pid
    os.kill(tunnel_pid, 0)
    print("PASS: HA container and start time, tunnel process and public URL preserved.")


if __name__ == "__main__":
    asyncio.run(main())
