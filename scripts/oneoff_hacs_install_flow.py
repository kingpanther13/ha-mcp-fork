"""One-off flow test: HACS-install the component, restart HA, verify it works.

Scratch-branch only — NOT part of the e2e suite. Boots a plain (unsupervised)
HA container, lets HACS download ha_mcp_tools from the mirror repo exactly as
a user would, restarts HA, and asserts the component's bootstrap service
answers. Exits nonzero on any failed step.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests"))
from test_constants import TEST_TOKEN  # noqa: E402

HA_IMAGE = os.environ.get("HA_IMAGE", "ghcr.io/home-assistant/home-assistant:2026.6.4")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
BASE = "http://127.0.0.1:8123"
HDRS = {"Authorization": f"Bearer {TEST_TOKEN}", "Content-Type": "application/json"}


def step(msg):
    print(f"\n=== {msg} ===", flush=True)


def rest(method, path, payload=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data, HDRS, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def wait_api(budget=420):
    """Wait for HA to be fully RUNNING (not just answering HTTP).

    /api/ answers while integrations (incl. HACS) are still loading;
    /api/config carries the core state.
    """
    deadline = time.time() + budget
    while time.time() < deadline:
        try:
            cfg = rest("GET", "/api/config")
            if cfg.get("state") == "RUNNING":
                return
        except Exception:
            pass
        time.sleep(5)
    raise SystemExit(f"HA not RUNNING within {budget}s")


def prep_config() -> Path:
    step("prep config dir from tests/initial_test_state (HACS, NO ha_mcp_tools)")
    config = Path(tempfile.mkdtemp(prefix="oneoff-ha-config-"))
    src = REPO_ROOT / "tests" / "initial_test_state"
    shutil.copytree(src, config, dirs_exist_ok=True)
    assert not (config / "custom_components" / "ha_mcp_tools").exists(), (
        "precondition: component must NOT be pre-installed"
    )

    # HACS frontend (gitignored in the repo copy; HACS refuses to start without it)
    fe_dir = config / "custom_components" / "hacs" / "hacs_frontend"
    if not (fe_dir / "entrypoint.js").exists():
        rel = json.loads(
            urllib.request.urlopen(
                "https://api.github.com/repos/hacs/frontend/releases/latest", timeout=30
            ).read()
        )
        tag = rel["tag_name"]
        url = f"https://github.com/hacs/frontend/releases/download/{tag}/hacs_frontend-{tag}.tar.gz"
        print(f"downloading hacs frontend {tag}")
        buf = io.BytesIO(urllib.request.urlopen(url, timeout=120).read())
        with tarfile.open(fileobj=buf, mode="r:gz") as tf, tempfile.TemporaryDirectory() as td:
            tf.extractall(td, filter="data")
            inner = Path(td) / f"hacs_frontend-{tag}" / "hacs_frontend"
            if not inner.exists():
                inner = Path(td) / "hacs_frontend"
            shutil.rmtree(fe_dir, ignore_errors=True)
            shutil.copytree(inner, fe_dir)

    # GitHub token into the HACS config entry (rate limits / release API)
    ce_path = config / ".storage" / "core.config_entries"
    doc = json.loads(ce_path.read_text())
    for entry in doc["data"]["entries"]:
        if entry.get("domain") == "hacs":
            entry.setdefault("data", {})["token"] = GITHUB_TOKEN
    ce_path.write_text(json.dumps(doc, indent=2))

    for p in config.rglob("*"):
        try:
            p.chmod(0o777)
        except OSError:
            pass
    config.chmod(0o777)
    return config


def hacs_install():
    step("HACS install via ha_install_mcp_tools (real GitHub download)")
    os.environ["HAMCP_ENABLE_CUSTOM_COMPONENT_INTEGRATION"] = "true"
    os.environ["ENABLE_BETA_FEATURES"] = "true"
    os.environ["HOMEASSISTANT_URL"] = BASE
    os.environ["HOMEASSISTANT_TOKEN"] = TEST_TOKEN

    import asyncio

    from fastmcp import Client

    from ha_mcp.client.rest_client import HomeAssistantClient
    from ha_mcp.server import HomeAssistantSmartMCPServer

    async def run():
        ha = HomeAssistantClient(base_url=BASE, token=TEST_TOKEN)
        server = HomeAssistantSmartMCPServer(client=ha)
        async with Client(server.mcp) as c:
            # HACS finishes its own startup (GitHub metadata fetch) after HA
            # reports RUNNING — retry a few times instead of racing it.
            last_exc = None
            for attempt in range(1, 5):
                try:
                    res = await c.call_tool(
                        "ha_install_mcp_tools", {"restart": False}, timeout=600
                    )
                    break
                except Exception as exc:
                    last_exc = exc
                    print(f"install attempt {attempt} failed: {str(exc)[:300]}")
                    await asyncio.sleep(20)
            else:
                raise SystemExit(f"install never succeeded: {last_exc}")
            payload = json.loads(res.content[0].text)
            # Tool responses ride in a {"data": ..., "metadata": ...} envelope.
            payload = payload.get("data", payload)
            print("install result:", json.dumps(payload)[:400])
            assert payload.get("success"), payload
            assert payload.get("installed") or payload.get("already_installed")

    asyncio.run(run())


def main():
    config = prep_config()
    step(f"boot unsupervised HA container ({HA_IMAGE})")
    subprocess.run(
        ["docker", "run", "-d", "--name", "oneoff-ha",
         "-v", f"{config}:/config", "-p", "8123:8123", HA_IMAGE],
        check=True,
    )
    try:
        wait_api()
        print("HA is up")

        hacs_install()

        step("verify HACS actually wrote the component to disk")
        manifest = config / "custom_components" / "ha_mcp_tools" / "manifest.json"
        assert manifest.exists(), "HACS download did not land on disk"
        version = json.loads(manifest.read_text())["version"]
        print(f"component on disk: {version}")

        step("restart HA (user step)")
        try:
            rest("POST", "/api/services/homeassistant/restart", {}, timeout=15)
        except Exception as exc:
            print(f"restart fired (connection drop ok): {type(exc).__name__}")
        time.sleep(20)
        wait_api()
        print("HA back up")

        step("add the integration (user step: Settings > Add integration)")
        # HACS only installs files; services register when the user adds the
        # integration. Drive the config flow exactly as the UI would:
        # menu -> 'tools' -> confirm -> create_entry.
        flow = rest(
            "POST", "/api/config/config_entries/flow", {"handler": "ha_mcp_tools"}
        )
        assert flow.get("type") == "menu", flow
        flow = rest(
            "POST",
            f"/api/config/config_entries/flow/{flow['flow_id']}",
            {"next_step_id": "tools"},
        )
        assert flow.get("type") == "form", flow
        flow = rest(
            "POST", f"/api/config/config_entries/flow/{flow['flow_id']}", {}
        )
        assert flow.get("type") == "create_entry", flow
        print("tools config entry created")

        step("verify the HACS-installed component WORKS post-restart")
        deadline = time.time() + 120
        last = None
        while time.time() < deadline:
            try:
                boot = rest(
                    "POST",
                    "/api/services/ha_mcp_tools/get_caller_token?return_response",
                    {},
                )
                sr = boot.get("service_response", boot)
                if sr.get("version"):
                    assert sr["version"] == version, (sr["version"], version)
                    print(f"\nPASS: HACS-installed component {sr['version']} "
                          "is loaded and answering after restart")
                    return
                last = sr
            except Exception as exc:
                last = exc
            time.sleep(5)
        raise SystemExit(f"component never answered post-restart: {last}")
    finally:
        subprocess.run(["docker", "logs", "--tail", "40", "oneoff-ha"], check=False)
        subprocess.run(["docker", "rm", "-f", "oneoff-ha"], check=False)


if __name__ == "__main__":
    main()
