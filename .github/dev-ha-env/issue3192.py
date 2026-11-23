"""Exercise the release's stdio server against the Docker dev-environment holder."""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path

from ha_mcp._vendor.fastmcp import Client
from ha_mcp._vendor.fastmcp.client.transports import StdioTransport

sys.path.insert(0, str(Path("tests").resolve()))
from test_constants import TEST_TOKEN

AUTO = "automation.qualification_packaged_night"
SCRIPT = "script.qualification_packaged_speakers"
PACKAGE = "packages/definition_contract.yaml"
IDS = {"automation": AUTO, "script": SCRIPT}
MARKERS = ("qualification_automation_only", "qualification_script_only",
           "qualification_automation_on_disk", "qualification_script_on_disk")


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def log(label, value):
    print("PROBE " + label + " " + json.dumps(value, sort_keys=True), flush=True)


async def session(url, fs_enabled, stage):
    with tempfile.TemporaryDirectory() as config_dir:
        env = {"HOMEASSISTANT_URL": url, "HOMEASSISTANT_TOKEN": TEST_TOKEN,
               "HA_MCP_CONFIG_DIR": config_dir, "HA_MCP_DISABLE_SETTINGS_UI": "true",
               "HAMCP_ENV_FILE": "/dev/null", "ENABLE_TOOL_SEARCH": "false",
               "READ_ONLY_MODE": "true", "ENABLE_BETA_FEATURES": str(fs_enabled).lower(), "HAMCP_ENABLE_FILESYSTEM_TOOLS": str(fs_enabled).lower()}
        transport = StdioTransport(command=shutil.which("ha-mcp"), args=[], env=env, keep_alive=False)
        async with Client(transport, timeout=90) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            log(stage + "/catalog", {"search_args": sorted(tools["ha_search"].model_dump(by_alias=True)["inputSchema"]["properties"]),
                "search_description": tools["ha_search"].description.splitlines()[:5],
                "related": sorted(n for n in tools if any(k in n for k in
                    ("automation","script","yaml","_file","trace","eval","call_service","execute","skill","ws_command","tool_search")))})
            async def call(label, tool, **params):
                if tool not in tools:
                    log(stage + "/" + label, {"listed": False})
                    return
                response = await client.call_tool_mcp(tool, params)
                wire = response.model_dump(mode="json", by_alias=True)
                body = wire.get("structuredContent")
                if body is None:
                    body = [getattr(part, "text", "") for part in response.content]
                    if len(body) == 1:
                        try:
                            body = json.loads(body[0])
                        except (ValueError, TypeError):
                            body = body[0]
                text = json.dumps(body, ensure_ascii=False)
                view = {"error": wire.get("isError", False), "markers": [m for m in MARKERS if m in text]}
                if label == "guide":
                    view["available"] = bool(body)
                elif label.startswith("yaml_") or label == "file":
                    view["response"] = text[:1700]
                else:
                    view["response"] = text[:4300]
                log(stage + "/" + label, view)
            if stage == "stock":
                await call("guide", "ha_get_skill_guide")
                await call("managed_automation", "ha_config_get_automation", entity_id="automation.e2e_seed_automation")
                await call("managed_script", "ha_config_get_script", script_id="qualification_editor_control")
                await call("integration", "ha_get_integration", domain="ha_mcp_tools")
                await call("auto_get", "ha_config_get_automation", entity_id=AUTO)
                await call("script_get", "ha_config_get_script", script_id=SCRIPT)
                await call("script_get_bare", "ha_config_get_script", script_id=SCRIPT.split(".")[1])
            if stage == "file_tools":
                await call("list_packages", "ha_list_files", path="packages", pattern="*.yaml")
            for domain, eid in IDS.items():
                search = {"query": eid, "exact_match": True, "limit": 1}
                if stage == "stock":
                    await call(domain+"_state", "ha_get_state", entity_id=eid)
                    await call(domain+"_trace", "ha_get_automation_traces", automation_id=eid, sections="config")
                    await call(domain+"_exact", "ha_search", **search)
                    await call(domain+"_action_search", "ha_search", query=f"qualification_{domain}_only", limit=10)
                    await call(domain+"_exact_wide", "ha_search", **(search | {"limit": 10}))
                if stage != "file_tools":
                    await call(domain+"_compat", "ha_search", **search, include_config=True, search_types=[domain])
                path = domain if domain == "automation" else SCRIPT
                await call("yaml_"+domain, "ha_config_get_yaml", yaml_path=path, file=PACKAGE, include_parsed=True)
                if stage == "file_tools":
                    await call("yaml_glob_"+domain, "ha_config_get_yaml", yaml_path=path, file="packages/*.yaml")
            await call("file", "ha_read_file", path=PACKAGE)


async def main():
    url=json.loads(Path("../targets.json").read_text())["ha"]
    containers = docker("ps","--filter","publish=8123","-q").splitlines()
    assert len(containers) == 1, containers
    container=containers[0]
    def within(*args):
        return docker("exec", container, *args)
    core=within("python","-c","from importlib.metadata import version; print(version('homeassistant'))")
    has_component=within("sh","-c","if test -f /config/custom_components/ha_mcp_tools/manifest.json; then echo yes; else echo no; fi")
    log("versions", {"ha":core,"ha_mcp": version("ha-mcp"),"component_files":has_component,
                     "transport":"ha-mcp subprocess stdio"})
    await session(url, False, "stock")
    await session(url, True, "file_tools")
    # Change only the fixture on disk. No service call, reload or entity execution.
    within("sed","-i","-e","s/qualification_automation_only/qualification_automation_on_disk/",
           "-e","s/qualification_script_only/qualification_script_on_disk/","/config/"+PACKAGE)
    await session(url, True, "after_disk_edit")

asyncio.run(main())
