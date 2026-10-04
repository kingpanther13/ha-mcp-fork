"""Hold a live HA test instance up for dev-ha-env.yml, following a branch.

The workflow copies this file into the tracked checkout's tests/src/e2e/ and
runs it with pytest, so the E2E session fixture does the whole bring-up for the
selected backend: the Docker container (standalone or embedded server) or HAOS
(external, embedded or app). It writes the local targets to tunnel to
DEVENV_TARGETS, then every POLL_S fetches TRACK_REF and applies a new commit
the way a user's update would:

- component: replace custom_components/ha_mcp_tools, as HACS does, and restart
  Home Assistant.
- server, standalone: restart the server process on the runner.
- server, embedded: point the server entry at a wheel built from the commit,
  as ha_dev_manage_server(update_source) does; the entry reloads and reinstalls.
- server, app: refresh the app source with a bumped version and run
  Supervisor's app update.

Status lines (STATUS {...}) go to the job log.
"""

from __future__ import annotations

import io
import json
import os
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
from functools import partial
from pathlib import Path

import requests
from haos_runtime import (
    HA_MCP_SERVER_DOMAIN,
    SSH_ADDON_PASSWORD,
    SSH_ADDON_USER,
    SSHPASS_BIN,
    _build_embedded_server_wheel,
    _home_assistant_ws_command,
    _ssh_debug_host_port,
    trigger_dev_addon_update,
    wait_for_addon_mcp_ready,
)

from . import _conftest_embedded
from ._conftest_embedded import _wait_for_embedded_webhook_ready
from ._conftest_readiness import _wait_for_ha_api_ready

SRC = Path(__file__).resolve().parents[3]
TRACK_REF = os.environ["TRACK_REF"]
POLL_S = 20
COMPONENT = Path("custom_components") / HA_MCP_SERVER_DOMAIN
SERVER_PATHS = ("src/ha_mcp/", "pyproject.toml", "uv.lock")
APP_PATHS = (*SERVER_PATHS, "homeassistant-addon-dev/", "homeassistant-addon/start.py")

# Agents meet strict best-practices mode by default; the E2E suite pins it off.
STRICT_BPS = os.environ.get("DEVENV_STRICT_BPS", "true") == "true"
_conftest_embedded._EMBEDDED_FEATURE_FLAGS["enable_strict_mandatory_bps"] = STRICT_BPS


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(SRC), *args], text=True).strip()


def log(**status: object) -> None:
    print("STATUS", json.dumps(status), flush=True)


def haos_shell(command: str, data: bytes | None = None, timeout: float = 300) -> str:
    """Run ``command`` in the HAOS host via the Advanced SSH app, feeding ``data``."""
    proc = subprocess.run(
        [
            SSHPASS_BIN,
            "-e",
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            "-p",
            str(_ssh_debug_host_port()),
            f"{SSH_ADDON_USER}@127.0.0.1",
            command,
        ],
        input=data,
        capture_output=True,
        timeout=timeout,
        env={**os.environ, "SSHPASS": SSH_ADDON_PASSWORD},
        check=False,
    )
    if proc.returncode:
        raise RuntimeError(
            f"{command!r} failed: {proc.stderr.decode(errors='replace')}"
        )
    return proc.stdout.decode()


def supervisor_data(subpath: str, data: bytes) -> None:
    """Untar ``data`` into Supervisor's data dir (``/mnt/data/supervisor``)."""
    target = shlex.quote(f"/data/{subpath}")
    haos_shell(
        f"docker exec -i hassio_supervisor sh -c 'rm -rf {target} && mkdir -p "
        f"$(dirname {target}) && tar -x -C $(dirname {target})'",
        data,
    )


def tar_dir(path: Path, arcname: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.add(path, arcname=arcname)
    return buf.getvalue()


class Instance:
    def __init__(self, env: dict) -> None:
        self.env = env
        self.backend = env["backend"]
        self.haos = self.backend.startswith("haos")
        self.base_url = env["base_url"]
        self.headers = {"Authorization": f"Bearer {env['token']}"}
        self.server: subprocess.Popen | None = None

    @property
    def standalone(self) -> bool:
        return self.backend in ("container", "haos")

    @property
    def embedded(self) -> bool:
        return self.backend in ("embedded", "haos_embedded")

    def targets(self) -> dict[str, str]:
        if self.standalone:
            mcp = f"http://127.0.0.1:{os.environ['MCP_PORT']}"
            path = os.environ["MCP_SECRET_PATH"]
        elif self.embedded:
            mcp, _, rest = self.env["embedded_webhook_url"].partition("/api/webhook/")
            path = f"/api/webhook/{rest}"
        else:
            url = self.env["addon_mcp_url"]
            mcp = url[: url.index("/", len("http://"))]
            path = url[len(mcp) :]
        return {"ha": self.base_url, "mcp": mcp, "mcp_path": path}

    def start_server(self) -> None:
        if not self.standalone:
            return
        if self.server:
            self.server.terminate()
            self.server.wait(30)
        env = os.environ | {
            "HOMEASSISTANT_URL": self.base_url,
            "HOMEASSISTANT_TOKEN": self.env["token"],
            "MCP_HOST": "127.0.0.1",
            "ENABLE_STRICT_MANDATORY_BPS": str(STRICT_BPS).lower(),
        }
        out = open(SRC.parent / "server.log", "ab")  # noqa: SIM115
        self.server = subprocess.Popen(
            ["uv", "run", "ha-mcp-web"], cwd=SRC, env=env, stdout=out, stderr=out
        )

    def update_component(self) -> None:
        if self.haos:
            supervisor_data(
                f"homeassistant/{COMPONENT.as_posix()}",
                tar_dir(SRC / COMPONENT, COMPONENT.name),
            )
            try:
                requests.post(
                    f"{self.base_url}/api/services/homeassistant/restart",
                    headers=self.headers,
                    timeout=30,
                )
            except requests.ConnectionError:
                pass  # Core can drop the connection as it goes down.
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                try:
                    requests.get(
                        f"{self.base_url}/api/", headers=self.headers, timeout=5
                    )
                except requests.RequestException:
                    break
                time.sleep(2)
        else:
            config = Path(self.env["config_path"])
            dest = config / COMPONENT
            # HA runs as root in the container, so files it wrote here
            # (__pycache__) are root-owned; the runner's user has passwordless sudo.
            subprocess.run(["sudo", "rm", "-rf", str(dest)], check=True)
            shutil.copytree(SRC / COMPONENT, dest)
            subprocess.run(["sudo", "chmod", "-R", "a+rwX", str(config)], check=True)
            self.env["container"].get_wrapped_container().restart(timeout=30)
        _wait_for_ha_api_ready(self.base_url, self.headers, timeout=600)

    def update_server(self, sha: str) -> None:
        if self.standalone:
            self.start_server()
        elif self.embedded:
            with tempfile.TemporaryDirectory() as tmp:
                wheel = _build_embedded_server_wheel(Path(tmp))
                rel = f"devenv/{sha[:12]}/{wheel.name}"
                if self.haos:
                    supervisor_data(
                        f"homeassistant/devenv/{sha[:12]}",
                        tar_dir(wheel.parent, sha[:12]),
                    )
                else:
                    dest = Path(self.env["config_path"]) / rel
                    subprocess.run(
                        ["sudo", "mkdir", "-p", "-m", "777", str(dest.parent)],
                        check=True,
                    )
                    shutil.copy(wheel, dest)
            _home_assistant_ws_command(
                self.base_url,
                self.env["token"],
                {
                    "type": f"{HA_MCP_SERVER_DOMAIN}/server_entry_update",
                    "pip_spec": f"ha-mcp @ file:///config/{rel}",
                },
            )
            time.sleep(5)
            self.wait_server()
        else:
            # Branches from before this workflow lack it; only the app needs it.
            from haos_runtime import build_dev_addon_source_tar

            with tempfile.TemporaryDirectory() as tmp:
                tar = build_dev_addon_source_tar(Path(tmp), sha[:7])
                store = haos_shell(
                    "docker exec hassio_supervisor sh -c "
                    "'test -d /data/apps/local && echo apps || echo addons'"
                ).strip()
                supervisor_data(f"{store}/local/ha_mcp_dev", tar.read_bytes())
            trigger_dev_addon_update(self.base_url, self.env["token"], timeout=900)
            self.wait_server()

    def wait_server(self) -> None:
        if self.embedded and not _wait_for_embedded_webhook_ready(
            self.env["embedded_webhook_url"], timeout=300
        ):
            raise RuntimeError("embedded server did not come back")
        if self.backend == "haos_inaddon":
            wait_for_addon_mcp_ready(timeout=300)


def test_hold_dev_ha_env(ha_container_with_fresh_config) -> None:
    inst = Instance(dict(ha_container_with_fresh_config))
    inst.start_server()
    Path(os.environ["DEVENV_TARGETS"]).write_text(json.dumps(inst.targets()))
    sha = git("rev-parse", "HEAD")
    log(sha=sha, backend=inst.backend, ready=True)
    end = time.monotonic() + 60 * int(os.environ.get("DEVENV_MINUTES", "340"))
    while time.monotonic() < end:
        time.sleep(POLL_S)
        try:
            git("fetch", "-q", "origin", TRACK_REF)
            head = git("rev-parse", "FETCH_HEAD")
            if head == sha:
                continue
            changed = git("diff", "--name-only", sha, head).splitlines()
            git("checkout", "-q", "-f", head)
        except subprocess.CalledProcessError as err:
            log(sha=sha, error=f"git: {err}")
            continue
        sha = head
        log(sha=sha, ready=False, updating=True)
        server_paths = APP_PATHS if inst.backend == "haos_inaddon" else SERVER_PATHS
        updates = []
        if any(p.startswith(server_paths) for p in changed):
            updates.append(("server", partial(inst.update_server, sha)))
        if any(p.startswith(f"{COMPONENT.as_posix()}/") for p in changed) and (
            inst.haos or (Path(inst.env["config_path"]) / COMPONENT).exists()
        ):
            updates.append(("component", inst.update_component))
        errors = {}
        for name, update in updates:
            try:
                update()
            except Exception as err:  # noqa: BLE001
                # Keep the instance up and apply the other update anyway.
                errors[name] = f"{type(err).__name__}: {err}"
        log(sha=sha, ready=not errors, **({"errors": errors} if errors else {}))
