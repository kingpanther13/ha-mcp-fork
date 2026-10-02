"""Long-running HA test instance that follows a branch.

Boots HA exactly as the e2e suite does (same config staging and container
builder). Every POLL_S it fetches TRACK_REF and this devenv branch; on a new
commit it reinstalls custom_components/ha_mcp_tools (restarting HA when the
component changed), runs every .github/devenv/scenarios/*.py and the e2e
selection in .github/devenv/e2e.txt against the instance, and posts the
combined output as a check run on the tracked commit.
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
DEVENV = Path(os.environ["DEVENV_DIR"]).resolve()
TRACK_REF = os.environ["TRACK_REF"]
DEVENV_REF = os.environ["DEVENV_REF"]
REPO = os.environ["GITHUB_REPOSITORY"]
POLL_S = 20

sys.path.insert(0, str(SRC / "tests" / "src" / "e2e"))
sys.path.insert(0, str(SRC / "tests"))
os.chdir(SRC)

import conftest as e2e  # noqa: E402
from test_constants import TEST_TOKEN  # noqa: E402

HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def follow(repo: Path, ref: str) -> tuple[str, list[str]]:
    """Fast-forward ``repo`` to ``ref``; return (new sha, changed paths)."""
    old = git(repo, "rev-parse", "HEAD")
    git(repo, "fetch", "-q", "origin", ref)
    new = git(repo, "rev-parse", "FETCH_HEAD")
    if new == old:
        return new, []
    git(repo, "checkout", "-q", "-f", new)
    return new, git(repo, "diff", "--name-only", old, new).splitlines() or ["*"]


def install_component(config: Path) -> None:
    dest = config / "custom_components" / "ha_mcp_tools"
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(SRC / "custom_components" / "ha_mcp_tools", dest)
    e2e._setup_config_permissions(config)


def run_e2e(env: dict[str, str]) -> tuple[bool, str] | None:
    """Run the e2e selection (pytest args, one line each) on the live instance."""
    selection = DEVENV / ".github" / "devenv" / "e2e.txt"
    lines = selection.read_text().splitlines() if selection.exists() else []
    args = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    if not args:
        return None
    proc = subprocess.run(
        ["uv", "run", "--project", str(SRC), "pytest", "-p", "devenv_plugin",
         "-q", "-rfE", "--no-header", "-p", "no:cacheprovider", *args],
        cwd=SRC / "tests", capture_output=True, text=True, timeout=3000,
        env={**env, "PYTHONPATH": str(DEVENV / ".github" / "devenv")},
    )  # fmt: skip
    status = "PASS" if proc.returncode == 0 else f"FAIL ({proc.returncode})"
    output = f"{proc.stdout[-30000:]}{proc.stderr[-6000:]}"
    return proc.returncode == 0, f"## e2e {' '.join(args)}: {status}\n```\n{output}\n```"


def run_scenarios(base_url: str, live: dict[str, str]) -> tuple[bool, str]:
    env = {**os.environ, "HA_URL": base_url, "HA_TOKEN": TEST_TOKEN, **live}
    ok, parts = True, []
    for script in sorted((DEVENV / ".github" / "devenv" / "scenarios").glob("*.py")):
        proc = subprocess.run(
            ["uv", "run", "--project", str(SRC), "python", str(script)],
            cwd=SRC, env=env, capture_output=True, text=True, timeout=900,
        )  # fmt: skip
        ok &= proc.returncode == 0
        status = "PASS" if proc.returncode == 0 else f"FAIL ({proc.returncode})"
        parts.append(f"## {script.name}: {status}\n```\n{proc.stdout[-20000:]}"
                     f"{proc.stderr[-8000:]}\n```")  # fmt: skip
    if (e2e_result := run_e2e(env)) is not None:
        ok &= e2e_result[0]
        parts.append(e2e_result[1])
    return ok, "\n\n".join(parts) or "no scenarios"


def post_check(sha: str, devenv_sha: str, ok: bool, text: str) -> None:
    body = {
        "name": "devenv scenarios",
        "head_sha": sha,
        "status": "completed",
        "conclusion": "success" if ok else "failure",
        "output": {
            "title": f"devenv {devenv_sha[:8]}: {'pass' if ok else 'fail'}",
            "summary": f"HA image {os.environ.get('HA_TEST_IMAGE')}",
            "text": text[-60000:],
        },
    }
    subprocess.run(
        ["gh", "api", "-X", "POST", f"repos/{REPO}/check-runs", "--input", "-"],
        input=json.dumps(body), text=True, check=False,
    )  # fmt: skip
    print("CHECK", sha, devenv_sha, ok, flush=True)


def main() -> None:
    config, _, _, _ = e2e._prepare_testcontainer_config(embedded=False)
    container = e2e._build_ha_testcontainer(config, False, None)
    container.start()
    port = container.get_exposed_port(8123)
    base_url = f"http://localhost:{port}"
    if port_file := os.environ.get("PORT_FILE"):
        Path(port_file).write_text(str(port))
    e2e._wait_for_ha_api_ready(base_url, HEADERS, timeout=600)
    live = {
        "DEVENV_CONFIG": str(config),
        "DEVENV_CONTAINER": container.get_wrapped_container().id,
        "DEVENV_PORT": str(port),
    }
    sha = git(SRC, "rev-parse", "HEAD")
    devenv_sha = git(DEVENV, "rev-parse", "HEAD")
    post_check(sha, devenv_sha, *run_scenarios(base_url, live))

    while True:
        time.sleep(POLL_S)
        try:
            sha, changed = follow(SRC, TRACK_REF)
            devenv_sha, devenv_changed = follow(DEVENV, DEVENV_REF)
        except subprocess.CalledProcessError as err:
            print("fetch failed", err, flush=True)
            continue
        if not changed and not devenv_changed:
            continue
        if any(p == "*" or p.startswith("custom_components/") for p in changed):
            install_component(config)
            container.get_wrapped_container().restart(timeout=60)
            e2e._wait_for_ha_api_ready(base_url, HEADERS, timeout=600)
        post_check(sha, devenv_sha, *run_scenarios(base_url, live))


if __name__ == "__main__":
    main()
