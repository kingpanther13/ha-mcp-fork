"""The dev proxy's ha_auth token forward reaches core's own listener (#2950)."""

from __future__ import annotations

import importlib
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from homeassistant.components.http import ApiConfig
from homeassistant.core import HomeAssistant

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPONENT_DIR = REPO_ROOT / "homeassistant-addon-webhook-proxy-dev" / "mcp_proxy_dev"
PACKAGE = "webhook_proxy_dev_forward_tls_test"


class _CoreAnswer:
    status = 200
    content_type = "application/json"

    async def __aenter__(self) -> _CoreAnswer:
        return self

    async def __aexit__(self, *_args: object) -> bool:
        return False

    async def read(self) -> bytes:
        return b'{"access_token":"core"}'


class _RelaySession:
    """Rejects a verified https hop: core's certificate names neither
    127.0.0.1 nor a private-CA host the container trusts."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> _CoreAnswer:
        self.calls.append((url, kwargs))
        if url.startswith("https://") and kwargs.get("ssl") is not False:
            raise aiohttp.ClientError("certificate verify failed")
        return _CoreAnswer()


@pytest.fixture
def autoapprove(monkeypatch: pytest.MonkeyPatch) -> Iterator[types.ModuleType]:
    """The dev proxy's auto-approve module, loaded from its add-on directory."""
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(COMPONENT_DIR)]
    monkeypatch.setitem(sys.modules, PACKAGE, package)
    yield importlib.import_module(f"{PACKAGE}.oauth_autoapprove")
    for name in [n for n in sys.modules if n.startswith(f"{PACKAGE}.")]:
        del sys.modules[name]


@pytest.mark.parametrize(
    ("use_ssl", "host", "target"),
    [
        (False, "0.0.0.0", "http://127.0.0.1:9443"),
        (True, "0.0.0.0", "https://127.0.0.1:9443"),
        (True, "10.0.2.15", "https://10.0.2.15:9443"),
        (True, "::", "https://[::1]:9443"),
    ],
    ids=[
        "plain-http",
        "tls",
        "tls-restricted-listen-address",
        "tls-ipv6-wildcard-listen-address",
    ],
)
async def test_sign_in_reaches_core_listener(
    hass: HomeAssistant,
    autoapprove: types.ModuleType,
    use_ssl: bool,
    host: str,
    target: str,
):
    """With http.ssl_certificate, every ha_auth sign-in answered 503: the
    forward verified a certificate issued for a name it could not trust. A
    listener bound to a LAN address only is reached there, not on loopback."""
    hass.config.api = ApiConfig("192.168.1.2", host, 9443, use_ssl)
    session = _RelaySession()

    result = await autoapprove._forward_to_core(
        hass, {"session": session}, "/auth/token", {"grant_type": "authorization_code"}
    )

    assert isinstance(result, tuple)  # a web.Response here is the user's 503
    url, kwargs = session.calls[-1]
    assert url == f"{target}/auth/token"
    assert (kwargs.get("ssl") is False) == target.startswith("https://")
