"""Long-running HA test instance that follows a branch.

Boots HA exactly as the e2e suite does (same config staging and container
builder) and writes its host port to PORT_FILE for the tunnel. Every POLL_S it
fetches TRACK_REF; when custom_components/ha_mcp_tools changed, it reinstalls
the component and restarts HA. Status goes to /config/www/devenv.json, served
at <url>/local/devenv.json.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SRC = Path(os.environ["SRC_DIR"]).resolve()
TRACK_REF = os.environ["TRACK_REF"]
PORT_FILE = Path(os.environ["PORT_FILE"])
POLL_S = 20

# conftest uses package-relative imports, so import it the way pytest does.
sys.path.insert(0, str(SRC / "tests"))
os.chdir(SRC)

from src.e2e._conftest_readiness import _wait_for_ha_api_ready  # noqa: E402
from src.e2e._conftest_testcontainer import (  # noqa: E402
    _build_ha_testcontainer,
    _prepare_testcontainer_config,
)
from test_constants import TEST_TOKEN  # noqa: E402

HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}
MCP_PORT = os.environ.get("MCP_PORT")


def start_server(base_url: str) -> subprocess.Popen | None:
    """The tracked branch's ha-mcp server against this HA, for MCP_PORT's tunnel."""
    if not MCP_PORT:
        return None
    env = os.environ | {
        "HOMEASSISTANT_URL": base_url,
        "HOMEASSISTANT_TOKEN": TEST_TOKEN,
        "MCP_HOST": "127.0.0.1",
    }
    log = open(SRC.parent / "server.log", "ab")  # noqa: SIM115
    return subprocess.Popen(["uv", "run", "ha-mcp-web"], cwd=SRC, env=env, stdout=log, stderr=log)


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(SRC), *args], text=True).strip()


def write_status(config: Path, **status: object) -> None:
    www = config / "www"
    www.mkdir(exist_ok=True)
    (www / "devenv.json").write_text(json.dumps(status))
    print("STATUS", json.dumps(status), flush=True)


def install_component(config: Path) -> None:
    dest = config / "custom_components" / "ha_mcp_tools"
    # HA runs as root in the container, so files it wrote here (__pycache__)
    # are root-owned; the runner's user has passwordless sudo.
    subprocess.run(["sudo", "rm", "-rf", str(dest)], check=True)
    shutil.copytree(SRC / "custom_components" / "ha_mcp_tools", dest)
    subprocess.run(["sudo", "chmod", "-R", "a+rwX", str(config)], check=True)


def main() -> None:
    config, _, _, _ = _prepare_testcontainer_config(embedded=False)
    container = _build_ha_testcontainer(config, False, None)
    container.start()
    port = container.get_exposed_port(8123)
    base_url = f"http://localhost:{port}"
    PORT_FILE.write_text(str(port))
    sha = git("rev-parse", "HEAD")
    ready = _wait_for_ha_api_ready(base_url, HEADERS, timeout=600)
    write_status(config, sha=sha, ready=ready, booted_at=time.time())
    server = start_server(base_url)

    while True:
        time.sleep(POLL_S)
        try:
            git("fetch", "-q", "origin", TRACK_REF)
            head = git("rev-parse", "FETCH_HEAD")
            if head == sha:
                continue
            changed = git("diff", "--name-only", sha, head).splitlines()
            git("checkout", "-q", "-f", head)
            sha = head
            if any(p.startswith("custom_components/ha_mcp_tools/") for p in changed):
                write_status(config, sha=sha, ready=False, restarting=True)
                install_component(config)
                container.get_wrapped_container().restart(timeout=30)
                port = container.get_exposed_port(8123)
                base_url = f"http://localhost:{port}"
                ready = _wait_for_ha_api_ready(base_url, HEADERS, timeout=600)
            if server:
                server.terminate()
                server.wait(30)
                server = start_server(base_url)
            write_status(config, sha=sha, ready=ready, port=port, booted_at=time.time())
        except Exception as err:
            # Publish the failure live; the job log is unreadable until it ends.
            write_status(config, sha=sha, ready=False, error=f"{type(err).__name__}: {err}")


if __name__ == "__main__":
    main()
