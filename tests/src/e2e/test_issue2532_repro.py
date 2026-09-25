"""Throwaway diagnostics: only released containers run the server under test."""

import json
import socket
import time
from pathlib import Path

import docker
import pytest
import requests

from .utilities.streamable_http import parse_mcp_response

IMAGES = ["7.6.0", "8.5.0"]
EVIDENCE = Path("issue2532-evidence")


def save(name, value):
    EVIDENCE.mkdir(exist_ok=True)
    (EVIDENCE / name).write_text(value)
    print(value)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_start(container):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        container.reload()
        logs = container.logs().decode(errors="replace")
        ready = "Uvicorn running on" in logs
        if ready or container.status == "exited":
            return ready, logs
        time.sleep(0.25)
    return False, logs


def rpc(url, method, params, headers, number):
    response = requests.post(
        url,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": number,
            "method": method,
            "params": params,
        },
        timeout=30,
    )
    response.raise_for_status()
    if response.headers.get("Mcp-Session-Id"):
        headers["Mcp-Session-Id"] = response.headers["Mcp-Session-Id"]
    payload = parse_mcp_response(
        response.headers.get("Content-Type", ""), response.content
    )
    assert payload and "result" in payload, payload
    return payload["result"]


def check_mcp(port):
    url = f"http://127.0.0.1:{port}/mcp"
    headers = {"Accept": "application/json, text/event-stream"}
    init = rpc(
        url,
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "issue2532", "version": "1"},
        },
        headers,
        1,
    )
    headers["MCP-Protocol-Version"] = init["protocolVersion"]
    response = requests.post(
        url,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        },
        timeout=10,
    )
    response.raise_for_status()
    catalog = rpc(url, "tools/list", {}, headers, 2)
    assert catalog["tools"]
    result = rpc(
        url,
        "tools/call",
        {
            "name": "ha_get_entity",
            "arguments": {"entity_id": "sun.sun"},
        },
        headers,
        3,
    )
    assert not result.get("isError"), result
    assert "sun.sun" in json.dumps(result), result
    return {
        "initialize": init,
        "tool_count": len(catalog["tools"]),
        "entity_read": result,
    }


@pytest.mark.parametrize("version", IMAGES)
def test_runtime_layout(version):
    client = docker.from_env()
    image = f"ghcr.io/homeassistant-ai/ha-mcp:{version}"
    code = (
        "import importlib.util,importlib.metadata,json; "
        "print(json.dumps({'version':importlib.metadata.version('ha-mcp'),"
        "'shared_websockets':importlib.util.find_spec('websockets') is not None,"
        "'entry_points':{e.name:e.value for e in importlib.metadata.distribution('ha-mcp').entry_points}}))"
    )
    output = client.containers.run(image, ["python", "-c", code], remove=True).decode()
    layout = json.loads(output)
    save(
        f"{version}-layout.json",
        json.dumps(
            {
                "layout": layout,
                "digests": client.images.get(image).attrs["RepoDigests"],
            },
            indent=2,
        ),
    )
    assert layout["version"] == version
    assert layout["shared_websockets"] == (version == "7.6.0")


@pytest.mark.parametrize("version", IMAGES)
@pytest.mark.parametrize(
    "mode",
    [
        "official-http",
        "legacy-command",
        "bare-http",
        "bare-sse",
        "bare-http-none",
        "bare-sse-none",
    ],
)
def test_launcher(version, mode, ha_container_with_fresh_config):
    ha = ha_container_with_fresh_config
    port = free_port()
    image = f"ghcr.io/homeassistant-ai/ha-mcp:{version}"
    env = {
        "HOMEASSISTANT_URL": ha["base_url"],
        "HOMEASSISTANT_TOKEN": ha["token"],
        "MCP_HOST": "127.0.0.1",
        "MCP_PORT": str(port),
        "MCP_SECRET_PATH": "/mcp",
        "HA_MCP_DISABLE_SETTINGS_UI": "true",
        "MCP_HEALTHZ": "true",
        "HA_MCP_CONFIG_DIR": "/tmp/issue2532",
    }
    if mode == "official-http":
        command = ["ha-mcp-web"]
    elif mode == "legacy-command":
        # A shell captures missing-command diagnostics instead of a Docker API error.
        command = ["sh", "-c", "exec ha-mcp-sse"]
    else:
        transport = "sse" if "sse" in mode else "http"
        override = ",uvicorn_config={'ws':'none'}" if mode.endswith("-none") else ""
        command = [
            "python",
            "-c",
            (
                "from ha_mcp.__main__ import mcp; "
                f"mcp.run(transport='{transport}',host='127.0.0.1',port={port},path='/mcp'{override})"
            ),
        ]
    client = docker.from_env()
    container = client.containers.run(
        image, command, environment=env, network_mode="host", detach=True
    )
    try:
        ready, logs = wait_for_start(container)
        outcome = {
            "version": version,
            "mode": mode,
            "ready": ready,
            "status": container.status,
            "exit_code": container.attrs["State"]["ExitCode"],
        }
        if ready and "http" in mode:
            outcome["mcp"] = check_mcp(port)
        if ready and mode == "official-http" and version == "8.5.0":
            # Exercise the private websocket dependency against the real HA API.
            code = """import asyncio,json,os
from ha_mcp._vendor.websockets.asyncio.client import connect
async def probe():
    url=os.environ['HOMEASSISTANT_URL'].replace('http://','ws://')+'/api/websocket'
    async with connect(url) as ws:
        assert json.loads(await ws.recv())['type']=='auth_required'
        await ws.send(json.dumps({'type':'auth','access_token':os.environ['HOMEASSISTANT_TOKEN']}))
        assert json.loads(await ws.recv())['type']=='auth_ok'
        await ws.send(json.dumps({'id':1,'type':'get_config'}))
        result=json.loads(await ws.recv())
        assert result['success'], result
        print(json.dumps({'websocket_success':True,'ha_version':result['result']['version']}))
asyncio.run(probe())"""
            result = container.exec_run(["python", "-c", code])
            assert result.exit_code == 0, result.output.decode()
            outcome["ha_websocket"] = json.loads(result.output)
        save(f"{version}-{mode}.json", json.dumps(outcome, indent=2))
        if mode == "official-http" or mode.endswith("-none"):
            assert ready, logs
        if version == "8.5.0" and mode in ("bare-http", "bare-sse"):
            assert not ready
            assert "websockets_sansio_impl.py" in logs
            assert "No module named 'websockets'" in logs
        if version == "8.5.0" and mode == "legacy-command":
            assert not ready and "ha-mcp-sse: not found" in logs
    finally:
        if container.status == "running":
            container.stop(timeout=3)
        save(f"{version}-{mode}.log", container.logs().decode(errors="replace"))
        container.remove(force=True)
