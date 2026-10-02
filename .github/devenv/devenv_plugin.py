"""pytest plugin: run the e2e suite against the devenv's already-running HA.

Load with ``-p devenv_plugin``. The e2e session fixture builds and starts a
container; this swaps its config staging and container builder for the live
instance (``DEVENV_CONFIG`` / ``DEVENV_CONTAINER`` / ``DEVENV_PORT``), so the
existing trees run unchanged. The fixture's ``with container:`` start/stop and
its teardown ``rmtree(temp_dir)`` are no-ops against the live config.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import docker


class LiveContainer:
    """The subset of testcontainers' DockerContainer the e2e conftest uses."""

    def __init__(self, container_id: str, port: str) -> None:
        self._container = docker.from_env().containers.get(container_id)
        self._port = port

    def __enter__(self) -> LiveContainer:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def get_exposed_port(self, _port: int) -> str:
        return self._port

    def get_container_host_ip(self) -> str:
        return "localhost"

    def get_wrapped_container(self) -> Any:
        return self._container

    def get_logs(self) -> tuple[bytes, bytes]:
        return self._container.logs(), b""

    def stop(self, *args: Any, **kwargs: Any) -> None:
        """The devenv owns the container's lifecycle."""


def _e2e_conftest() -> Any:
    for module in list(sys.modules.values()):
        path = str(getattr(module, "__file__", "") or "").replace("\\", "/")
        if path.endswith("src/e2e/conftest.py"):
            return module
    raise RuntimeError("e2e conftest not imported")


def pytest_collection_finish(session: Any) -> None:
    e2e = _e2e_conftest()
    config = Path(os.environ["DEVENV_CONFIG"])
    blueprint = e2e._copy_local_blueprint_to_www(config)
    live = LiveContainer(os.environ["DEVENV_CONTAINER"], os.environ["DEVENV_PORT"])
    # A throwaway temp_dir: the fixture's teardown deletes it.
    e2e._prepare_testcontainer_config = lambda embedded: (
        config, tempfile.mkdtemp(prefix="devenv_"), blueprint, None
    )
    e2e._build_ha_testcontainer = lambda *args: live
