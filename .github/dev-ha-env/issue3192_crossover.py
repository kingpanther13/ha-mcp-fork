"""Run the same two scripts from each location against stock 9.2.0."""
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp
import yaml
from ha_mcp._vendor.fastmcp import Client
from ha_mcp._vendor.fastmcp.client.transports import StdioTransport

sys.path.insert(0, str(Path("tests").resolve()))
from test_constants import TEST_TOKEN

SPEAKERS = "qualification_packaged_speakers"
TARGET = "set_evening_target"
HELPER = "input_number.e2e_pagination_seed"
PACKAGE = "packages/definition_contract.yaml"


@asynccontextmanager
async def stdio(url, read_only):
    with tempfile.TemporaryDirectory() as config_dir:
        env = {"HOMEASSISTANT_URL": url, "HOMEASSISTANT_TOKEN": TEST_TOKEN,
               "HA_MCP_CONFIG_DIR": config_dir, "HA_MCP_DISABLE_SETTINGS_UI": "true",
               "HAMCP_ENV_FILE": "/dev/null", "ENABLE_TOOL_SEARCH": "false",
               "READ_ONLY_MODE": str(read_only).lower()}
        transport = StdioTransport(command=shutil.which("ha-mcp"), args=[], env=env, keep_alive=False)
        async with Client(transport, timeout=60) as client:
            yield client


async def call(client, tool, **params):
    raw = await client.call_tool_mcp(tool, params)
    wire = raw.model_dump(mode="json", by_alias=True)
    body = wire.get("structuredContent")
    if body is None:
        body = json.loads(wire["content"][0]["text"])
    return body


async def service(client, domain, name, **params):
    result = await call(client, "ha_call_service", domain=domain, service=name, **params)
    assert result.get("success") is not False, result


async def helper(client):
    result = await call(client, "ha_get_state", entity_id=HELPER)
    return float(result["data"]["state"])


async def phase(url, location, bodies):
    async with stdio(url, True) as reader, stdio(url, False) as operator:
        await service(operator, "script", "reload")
        # Set up a Core observer before running the reporter's event-only script.
        async with aiohttp.ClientSession() as http, http.ws_connect(url.rstrip("/")+"/api/websocket") as ws:
            assert (await ws.receive_json(timeout=10))["type"] == "auth_required"
            await ws.send_json({"type":"auth","access_token":TEST_TOKEN})
            assert (await ws.receive_json(timeout=10))["type"] == "auth_ok"
            await ws.send_json({"id":1,"type":"subscribe_events","event_type":"qualification_script_only"})
            assert (await ws.receive_json(timeout=10))["success"]
            await service(operator, "script", SPEAKERS)
            observed = (await ws.receive_json(timeout=10))["event"]["event_type"]
            assert observed == "qualification_script_only"
        await service(operator, "input_number", "set_value", entity_id=HELPER, data={"value": 0})
        before = await helper(reader)
        await service(operator, "script", TARGET)
        after = await helper(reader)
        assert (before, after) == (0, 67), (before, after)
        print("EVIDENCE", location, "script.reload succeeded, observed", observed,
              "and service script set helper", before, "->", after, flush=True)
        for slug, definition in bodies.items():
            entity_id = "script."+slug
            result = await call(reader, "ha_config_get_script", script_id=entity_id)
            if location == "scripts.yaml":
                assert result["config"] == definition, result
                print("EVIDENCE", location, entity_id, "returned identical full body", flush=True)
            else:
                assert result["error"]["code"] == "RESOURCE_NOT_FOUND" and slug in result["available_script_ids"], result
                print("EVIDENCE", location, entity_id, result["error"]["code"],
                      "and requested ID in available_script_ids", flush=True)


async def main():
    url = json.loads(Path("../targets.json").read_text())["ha"]
    container, = subprocess.check_output(["docker","ps","--filter","publish=8123","-q"], text=True).splitlines()
    def read(path):
        return subprocess.check_output(["docker","exec",container,"cat","/config/"+path], text=True)
    def write(path, content):
        subprocess.run(["docker","exec","-i",container,"sh","-c",'cat > "$1"', "sh","/config/"+path],
                       input=content, text=True, check=True)
    package, scripts = read(PACKAGE), read("scripts.yaml")
    prefix, block = package.split("\nscript:\n")
    flat = textwrap.dedent(block)
    bodies = yaml.safe_load(flat)
    assert set(bodies) == {TARGET, SPEAKERS}
    print("EVIDENCE scripts moved unchanged; source SHA256:", hashlib.sha256(flat.encode()).hexdigest(), flush=True)
    await phase(url, "package-before", bodies)
    write(PACKAGE, prefix+"\n")
    write("scripts.yaml", scripts.rstrip()+"\n"+flat)
    await phase(url, "scripts.yaml", bodies)
    write("scripts.yaml", scripts)
    write(PACKAGE, package)
    await phase(url, "package-after", bodies)

if __name__ == "__main__":
    asyncio.run(main())
