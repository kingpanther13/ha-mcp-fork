"""The private-string guard rejects access syntax, never unrelated text."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "private_test_access.py"


def _run(tmp_path: Path, source: str, tree: str = "tests/src/unit"):
    path = tmp_path / tree / "test_sample.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--repo", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


@pytest.mark.parametrize(
    "source",
    [
        'patch("ha_mcp.module._helper")\n',
        'patch(target="ha_mcp._module.helper")\n',
        'patch.object(mod, "_helper", replacement)\n',
        'patch.object(target=mod, attribute="_helper", new=replacement)\n',
        'monkeypatch.setattr(mod, "_helper", replacement)\n',
        'monkeypatch.setattr("ha_mcp.module._helper", replacement)\n',
        'monkeypatch.delattr(mod, "_helper")\n',
        'getattr(mod, "_helper")\n',
        'hasattr(mod, "_helper")\n',
        'setattr(mod, "_helper", replacement)\n',
        'delattr(mod, "_helper")\n',
        "patch.multiple(mod, _helper=replacement)\n",
        'patch.multiple("ha_mcp._module", helper=replacement)\n',
        'patch.multiple(mod, **{"_helper": replacement})\n',
        'CHANGES = {"_helper": replacement}\npatch.multiple(mod, **CHANGES)\n',
        'BASE = {"_helper": replacement}\nCHANGES = {**BASE}\npatch.multiple(mod, **CHANGES)\n',
        'from unittest.mock import patch as replace\nreplace("ha_mcp.x._helper")\n',
        'import unittest.mock as mocks\nmocks.patch.object(mod, "_helper")\n',
        'from builtins import getattr as read\nread(mod, "_helper")\n',
        'replace = monkeypatch.setattr\nreplace("ha_mcp.x._helper", replacement)\n',
        'read = getattr\nread(mod, "_helper")\nread = public_function\n',
        'from builtins import getattr as read\nread(mod, "_helper")\nread = public_function\n',
        'replace = patch.object\nreplace(mod, "_helper")\nreplace = public_function\n',
        'import unittest.mock as mocks\nreplace = target\ntarget = mocks.patch\nreplace("ha_mcp.x._helper")\n',
        'import unittest.mock as mocks\nreplace = target\ntarget = mocks.patch.object\nreplace(mod, "_helper")\n',
        'NAME = "_helper"\ngetattr(mod, NAME)\n',
        'PREFIX = "ha_mcp.module."\npatch(PREFIX + "_helper")\n',
        'NAME = "_helper"\npatch(f"ha_mcp.module.{NAME}")\n',
        'patch(f"ha_mcp.{runtime_module}._helper")\n',
        'patch(runtime_module + "._helper")\n',
        'for name in ("public", "_helper"):\n    getattr(mod, name)\n',
        '[getattr(mod, name) for name in ("public", "_helper")]\n',
        '{getattr(mod, name) for name in ("_helper",)}\n',
        '{name: getattr(mod, name) for name in ("_helper",)}\n',
        'tuple(getattr(mod, name) for name in ("_helper",))\n',
        'TARGET = "_helper"\n[None for TARGET in getattr(mod, TARGET)]\n',
        'TARGET = "_helper"\n{None for TARGET in getattr(mod, TARGET)}\n',
        'TARGET = "_helper"\n{TARGET: None for TARGET in getattr(mod, TARGET)}\n',
        'TARGET = "_helper"\niterator = (None for TARGET in getattr(mod, TARGET))\n',
        'patch("ha_mcp._vendor.fastmcp._helper")\n',
        'patch("ha_mcp._vendor.fastmcp._internal.helper")\n',
        'getattr(mod, "_looks_like_dunder__")\n',
        'getattr(mod, "__looks_private")\n',
        'getattr(mod, "__")\n',
        'def test_it():\n    target = "_helper"\n    getattr(mod, target)\n',
        'TARGET = "_helper"\ndef test_it():\n    getattr(mod, TARGET)\n',
        '@patch("ha_mcp.module._helper")\ndef test_it(mocked):\n    pass\n',
        'TARGET = "ha_mcp.module._helper"\n@patch(TARGET)\ndef test_it(TARGET):\n    pass\n',
        'TARGET = "_helper"\ndef test_it(TARGET=getattr(mod, TARGET)):\n    pass\n',
        'TARGET = "_helper"\ncallback = lambda TARGET=getattr(mod, TARGET): TARGET\n',
        'TARGET = "_helper"\nclass Example(getattr(mod, TARGET)):\n    TARGET = "public"\n',
        'TARGET = "_helper"\nclass Example:\n    TARGET = "public"\n    def method(self):\n        getattr(mod, TARGET)\n',
        'TARGET = "public"\nclass Example:\n    TARGET = "_helper"\n    def method(self, value=getattr(mod, TARGET)):\n        pass\n',
        'TARGET = "ha_mcp.mod.public"\nclass Example:\n    TARGET = "ha_mcp.mod._helper"\n    @patch(TARGET)\n    def method(self):\n        pass\n',
        'def outer():\n    TARGET = "_helper"\n    class Example:\n        TARGET = "public"\n        def method(self):\n            getattr(mod, TARGET)\n',
        'TARGET = "_helper"\nclass Example:\n    TARGET = "public"\n    values = [getattr(mod, TARGET) for index in (1,)]\n',
        'class Example:\n    NAMES = ("_helper",)\n    values = [getattr(mod, name) for name in NAMES]\n',
        'DATA = {"_helper": 1}\nif flag:\n    DATA = {"public": 2}\nCOPY = DATA\npatch.multiple(mod, **COPY)\n',
        'for name in {"ha_mcp.module._cache": 0}:\n    patch.dict(name, {})\n',
        '[patch.dict(name, {}) for name in {"ha_mcp.module._cache": 0}]\n',
        'NAMES = {"ha_mcp.module._cache": 0}\nCOPY = NAMES\nfor name in COPY:\n    patch.dict(name, {})\n',
        'BASE = {"_helper": 1}\nCOPY = {**BASE}\npatch.multiple(mod, **COPY)\n',
        'def TARGET():\n    pass\nTARGET = "ha_mcp.module._cache"\npatch.dict(TARGET, {})\n',
        'class TARGET:\n    pass\nTARGET = "ha_mcp.module._cache"\npatch.dict(TARGET, {})\n',
        'from builtins import getattr as read\nread = read(mod, "_helper")\n',
        'from builtins import getattr as read\nread: object = read(mod, "_helper")\n',
        'from builtins import getattr as read\nread = other = read(mod, "_helper")\n',
        'from builtins import getattr as read\nread = wrapper(read(mod, "_helper"))\n',
        'from unittest.mock import patch as replace\nreplace = replace("ha_mcp.module._helper")\n',
        'getattr = getattr(mod, "_helper")\n',
        'from builtins import getattr as read\nread, other = read(mod, "_helper")\n',
        'from builtins import getattr as read\n[read, other] = read(mod, "_helper")\n',
        'from builtins import getattr as read\nread = lambda value=read(mod, "_helper"): value\n',
        'from builtins import getattr as read\nread: object = lambda value=read(mod, "_helper"): value\n',
        'from builtins import getattr as read\nclass Example:\n    read = lambda: read(mod, "_helper")\n',
        'from builtins import getattr as read\nread: object\nread(mod, "_helper")\n',
        'from builtins import getattr as read\nholder[read(mod, "_helper")] = read = public_callback\n',
        'import unittest.mock as source\nsource = replace = source.patch\nreplace("ha_mcp.module._helper")\n',
        'from builtins import getattr as read\nholder[read(mod, "_helper")], read = public_values\n',
        'from builtins import getattr as read\nholder[read(mod, "_helper")], *read = public_values\n',
        'TARGET = "_helper"\nclass Example:\n    TARGET = "public"\n    def method(value=[getattr(mod, TARGET) for index in (1,)]):\n        pass\n',
        'TARGET = "_helper"\nclass Example:\n    TARGET = "public"\n    def method(value=lambda: getattr(mod, TARGET)):\n        pass\n',
        pytest.param('TARGET = "_helper"\nclass Example:\n    TARGET = getattr(mod, TARGET)\n', id="class-inherited-rhs"),
        pytest.param('TARGET = "_helper"\nclass Example:\n    TARGET: object = getattr(mod, TARGET)\n', id="class-annotated-rhs"),
        pytest.param('TARGET = "_helper"\nclass Example:\n    getattr(mod, TARGET)\n    TARGET = "public"\n', id="class-read-before-rebind"),
        pytest.param('TARGET = "_helper"\nclass Example:\n    COPY = TARGET\n    TARGET = "public"\n    getattr(mod, COPY)\n', id="class-copy-before-rebind"),
        pytest.param('TARGET = "_helper"\nclass Example:\n    if flag:\n        TARGET = "public"\n    getattr(mod, TARGET)\n', id="class-optional-override"),
        pytest.param('TARGET = "public"\nclass Example:\n    if flag:\n        TARGET = "_helper"\n    else:\n        TARGET = "public"\n    getattr(mod, TARGET)\n', id="class-value-join"),
        pytest.param('TARGET = "ha_mcp.module._cache"\nclass Example:\n    if flag:\n        TARGET = {"_data": 1}\n    patch.dict(TARGET, {})\n', id="class-mapping-string-join"),
        pytest.param('from builtins import getattr as read\nclass Example:\n    if flag:\n        read = public_callback\n    read(mod, "_helper")\n', id="class-optional-alias"),
        pytest.param('read = public_callback\nclass Example:\n    if flag:\n        read = getattr\n    else:\n        read = public_callback\n    read(mod, "_helper")\n', id="class-alias-join"),
        pytest.param('TARGET = "_helper"\nclass Example:\n    for item in items:\n        TARGET = "public"\n    getattr(mod, TARGET)\n', id="class-loop-zero-iterations"),
        pytest.param('TARGET = "public"\nclass Example:\n    while getattr(mod, TARGET):\n        TARGET = "_helper"\n', id="class-loop-later-condition"),
        pytest.param('TARGET = "public"\nclass Example:\n    try:\n        TARGET = "_helper"\n        risky()\n        TARGET = "public"\n    except Exception:\n        getattr(mod, TARGET)\n', id="class-handler-intermediate-value"),
        pytest.param('TARGET = "_helper"\nclass Example:\n    try:\n        TARGET = "public"\n    except Exception:\n        getattr(mod, TARGET)\n', id="class-handler-inherited-value"),
        pytest.param('TARGET = "_helper"\nclass Example:\n    match value:\n        case 0:\n            TARGET = "public"\n    getattr(mod, TARGET)\n', id="class-match-no-case"),
        pytest.param('KEYS = {"_helper": 1}\nclass Example:\n    if flag:\n        KEYS = {"public": 2}\n    patch.multiple(mod, **KEYS)\n', id="class-mapping-key-join"),
        pytest.param('TARGET = "_helper"\nclass Example:\n    TARGET: str\n    getattr(mod, TARGET)\n', id="class-annotation-only"),
        pytest.param('TARGET = "public"\nclass Example:\n    for item in items:\n        TARGET = "_helper"\n        if flag:\n            break\n        TARGET = "public"\n    getattr(mod, TARGET)\n', id="class-loop-break-value"),
        pytest.param('TARGET = "public"\nclass Example:\n    try:\n        TARGET = "_helper"\n        risky()\n        TARGET = "public"\n    except Specific:\n        TARGET = "public"\n    finally:\n        getattr(mod, TARGET)\n', id="class-finally-unhandled-value"),
        pytest.param('TARGET = "public"\nclass Example:\n    with suppress(Exception):\n        TARGET = "_helper"\n        risky()\n        TARGET = "public"\n    getattr(mod, TARGET)\n', id="class-suppressed-intermediate-value"),
        pytest.param('TARGET = "_helper"\nclass Example:\n    TARGET = "public"\n    try:\n        raise Exception()\n    except Exception as TARGET:\n        pass\n    getattr(mod, TARGET)\n', id="class-exception-name-fallback"),
        pytest.param('TARGET = "_helper"\nclass Example:\n    TARGET = "public"\n    del TARGET\n    getattr(mod, TARGET)\n', id="class-deleted-value-fallback"),
        pytest.param('from builtins import getattr as read\nclass Example:\n    read = public_callback\n    del read\n    read(mod, "_helper")\n', id="class-deleted-alias-fallback"),
        pytest.param('TARGET = "public"\nclass Example:\n    try:\n        pass\n    except Specific:\n        pass\n    else:\n        TARGET = "_helper"\n        risky()\n        TARGET = "public"\n    finally:\n        getattr(mod, TARGET)\n', id="class-finally-else-intermediate"),
    ],
)
def test_reflective_private_access_fails_with_a_source_location(tmp_path, source):
    result = _run(tmp_path, source)
    assert result.returncode == 1, result.stderr
    assert "tests/src/unit/test_sample.py:" in result.stdout
    assert "private string target" in result.stdout


@pytest.mark.parametrize(
    "source",
    [
        'patch("ha_mcp.module.public", return_value="_private_data")\n',
        'patch.object(mod, "public", "_private_data")\n',
        'monkeypatch.setattr("ha_mcp.module.public", "_private_data")\n',
        'setattr(mod, "public", "_private_data")\n',
        'getattr(mod, "public", "_private_default")\n',
        'patch.multiple(mod, public="_private_data")\n',
        'patch("ha_mcp._vendor.fastmcp.public")\n',
        'patch.object(mod, "__call__", replacement)\n',
        'getattr(mod, "__name__")\n',
        'getattr(self, "_own_helper")\n',
        'patch.object(cls, "_own_helper")\n',
        'PRIVATE = "_data"\npublic_function(PRIVATE)\n',
        'TARGET = "_helper"\ndef test_it(TARGET):\n    getattr(mod, TARGET)\n',
        'TARGET = "_helper"\ndef test_it():\n    TARGET = "public"\n    getattr(mod, TARGET)\n',
        'def first():\n    target = "_data"\ndef second():\n    target = "public"\n    getattr(mod, target)\n',
        'TARGET = "ha_mcp.module.public"\n@patch(TARGET)\ndef test_it():\n    TARGET = "_data"\n',
        'TARGET = "public"\ndef test_it(value=getattr(mod, TARGET)):\n    TARGET = "_data"\n',
        'read = getattr\nread(mod, "public")\nread = public_function\nread(mod, "_data")\n',
        'read = public_function\nread(mod, "_data")\nread = getattr\nread(mod, "public")\n',
        'read = getattr\nread = lambda *args: None\nread(mod, "_data")\n',
        '[getattr(mod, name) for name in ("public", "also_public")]\n',
        '[getattr(self, name) for name in ("_own_helper",)]\n',
        '[getattr(cls, name) for name in ("_own_helper",)]\n',
        'name = "public"\n[name for name in ("_data",)]\ngetattr(mod, name)\n',
        'name = "_data"\n[getattr(mod, name) for name in ("public",)]\n',
        'TARGET = "public"\n[None for TARGET in getattr(mod, TARGET) for TARGET in ("_data",)]\n',
        'TARGET = "public"\nclass Example:\n    TARGET = "_data"\n    def method(self):\n        getattr(mod, TARGET)\n',
        'TARGET = "_data"\nclass Example:\n    TARGET = "public"\n    def method(self, value=getattr(mod, TARGET)):\n        pass\n',
        'TARGET = "ha_mcp.mod._data"\nclass Example:\n    TARGET = "ha_mcp.mod.public"\n    @patch(TARGET)\n    def method(self):\n        pass\n',
        'NAME = "_data"\ndef test_it():\n    from public_constants import NAME\n    getattr(mod, NAME)\n',
        'NAME = "_data"\ndef test_it():\n    import public_constants as NAME\n    getattr(mod, NAME)\n',
        'NAME = "_data"\ndef test_it():\n    from public_constants import public as NAME\n    getattr(mod, NAME)\n',
        'def getattr(obj, name):\n    return name\ngetattr(None, "_data")\n',
        'NAME = "_data"\ndef test_it():\n    def NAME():\n        pass\n    getattr(mod, NAME)\n',
        'NAME = "_data"\ndef test_it():\n    class NAME:\n        pass\n    getattr(mod, NAME)\n',
        'from builtins import getattr as read\ndef read(obj, name):\n    return name\nread(None, "_data")\n',
        'from builtins import getattr as read\nclass read:\n    pass\nread(None, "_data")\n',
        'patch.dict({"ha_mcp.module._cache": 1}, {"_data": 2})\n',
        'DATA = {"ha_mcp.module._cache": 1}\npatch.dict(in_dict=DATA, values={"_data": 2})\n',
        'DATA = {"ha_mcp.module._cache": 1}\nCOPY = DATA\npatch.dict(COPY, {"_data": 2})\n',
        'from builtins import getattr as read\nclass Example:\n    def read(value=read(mod, "public")):\n        pass\n    read(None, "_data")\n',
        'from builtins import getattr as read\nclass Example:\n    class read(read(mod, "public")):\n        pass\n    read(None, "_data")\n',
        'from builtins import getattr as read\nclass Example:\n    def read(*args):\n        pass\n    @read(None, "_data")\n    def other():\n        pass\n',
        'NAME = "_data"\nclass Example:\n    def NAME():\n        pass\n    getattr(mod, NAME)\n',
        'DATA = {"ha_mcp.module._cache": 1}\nif flag:\n    DATA = {"ha_mcp.module._other": 2}\nCOPY = DATA\npatch.dict(COPY, {})\n',
        'BASE = {"ha_mcp.module._cache": 1}\nDATA = {**BASE}\npatch.dict(DATA, {})\n',
        'from builtins import getattr as read\nclass Example:\n    read = public_callback\n    def method(value=read(None, "_data")):\n        pass\n',
        'from builtins import getattr as read\nread = read(mod, "public")\nread(None, "_data")\n',
        'from builtins import getattr as read\nread: object = read(mod, "public")\nread(None, "_data")\n',
        'from builtins import getattr as read\nread = other = read(mod, "public")\nread(None, "_data")\nother(None, "_data")\n',
        'from builtins import getattr as read\nread = wrapper(read(mod, "public"))\nread(None, "_data")\n',
        'from builtins import getattr as read\nread = lambda: read(None, "_data")\n',
        'from builtins import getattr as read\nread = lambda value=read(mod, "public"): read(None, "_data")\n',
        'from builtins import getattr as read\nclass Example:\n    read = lambda value=read(mod, "public"): value\n',
        'from builtins import getattr as read\nread, other = public_values\nread(None, "_data")\n',
        'from builtins import getattr as read\n[read, other] = public_values\nread(None, "_data")\n',
        'getattr = public_callback()\ngetattr(None, "_data")\n',
        'from builtins import getattr as read\nread: object\nread(mod, "public")\n',
        'from builtins import getattr as read\nread = holder[read(None, "_data")] = public_callback\n',
        'from builtins import getattr as read\nread, holder[read(None, "_data")] = public_values\n',
        'from builtins import getattr as read\n*read, holder[read(None, "_data")] = public_values\n',
        'TARGET = "public"\nclass Example:\n    TARGET = "_data"\n    def method(value=[getattr(mod, TARGET) for index in (1,)]):\n        pass\n',
        'TARGET = "public"\nclass Example:\n    TARGET = "_data"\n    def method(value=lambda: getattr(mod, TARGET)):\n        pass\n',
        pytest.param('TARGET = "_data"\nclass Example:\n    TARGET = "public"\n    TARGET = getattr(mod, TARGET)\n', id="class-prior-public-rhs"),
        pytest.param('TARGET = "public"\nclass Example:\n    TARGET = getattr(mod, TARGET)\n', id="class-public-rhs"),
        pytest.param('TARGET = "public"\nclass Example:\n    TARGET = "_data"\n    TARGET = "public"\n    getattr(mod, TARGET)\n', id="class-definite-public-overwrite"),
        pytest.param('TARGET = "_data"\nclass Example:\n    if flag:\n        TARGET = "public"\n        getattr(mod, TARGET)\n', id="class-public-within-branch"),
        pytest.param('TARGET = "public"\nclass Example:\n    if flag:\n        TARGET = "_data"\n    else:\n        TARGET = "public"\n        getattr(mod, TARGET)\n', id="class-independent-else"),
        pytest.param('TARGET = "ha_mcp.module.cache"\nclass Example:\n    if flag:\n        TARGET = {"_data": 1}\n    patch.dict(TARGET, {})\n', id="class-public-or-mapping"),
        pytest.param('TARGET = "_data"\nclass Example:\n    if flag:\n        TARGET = "public"\n    else:\n        TARGET = "also_public"\n    getattr(mod, TARGET)\n', id="class-both-branches-public"),
        pytest.param('TARGET = "_data"\nclass Example:\n    from constants import TARGET\n    getattr(mod, TARGET)\n', id="class-imported-value"),
        pytest.param('TARGET = "_data"\nclass Example:\n    with context as TARGET:\n        getattr(mod, TARGET)\n', id="class-with-bound-value"),
        pytest.param('TARGET = "_data"\nclass Example:\n    match value:\n        case TARGET:\n            getattr(mod, TARGET)\n', id="class-match-capture"),
        pytest.param('CACHE = "ha_mcp.module._cache"\nclass Example:\n    CACHE = {"_data": 1}\n    patch.dict(CACHE, {})\n', id="class-mapping-data"),
        pytest.param('TARGET = "_data"\nclass Example:\n    for item in items:\n        TARGET = "public"\n        getattr(mod, TARGET)\n', id="class-public-within-loop"),
        pytest.param('TARGET = "_data"\nclass Example:\n    try:\n        risky()\n    except Exception:\n        TARGET = "public"\n        getattr(mod, TARGET)\n', id="class-public-within-handler"),
        pytest.param('TARGET = "_data"\nclass Example:\n    for TARGET in ("public",):\n        pass\n    getattr(mod, TARGET)\n', id="class-nonempty-iteration"),
        pytest.param('TARGET = "_data"\nclass Example:\n    match value:\n        case _:\n            TARGET = "public"\n    getattr(mod, TARGET)\n', id="class-exhaustive-match"),
        pytest.param('TARGET = "public"\nclass Example:\n    TARGET = "_data"\n    try:\n        raise Exception()\n    except Exception as TARGET:\n        pass\n    getattr(mod, TARGET)\n', id="class-exception-name-public-fallback"),
        pytest.param('TARGET = "public"\nclass Example:\n    TARGET = "_data"\n    del TARGET\n    getattr(mod, TARGET)\n', id="class-deleted-public-fallback"),
        pytest.param('read = public_callback\nclass Example:\n    read = getattr\n    del read\n    read(None, "_data")\n', id="class-deleted-public-alias"),
        pytest.param('TARGET = "_data"\nclass Example:\n    TARGET = "public"\n    with context:\n        pass\n    getattr(mod, TARGET)\n', id="class-with-keeps-prior-overwrite"),
        pytest.param('TARGET = "_data"\nclass Example:\n    try:\n        risky()\n    finally:\n        TARGET = "public"\n    getattr(mod, TARGET)\n', id="class-finally-public-override"),
        pytest.param('TARGET = "public"\nclass Example:\n    try:\n        risky()\n    except First:\n        TARGET = "_data"\n    except Second:\n        getattr(mod, TARGET)\n', id="class-independent-handlers"),
        pytest.param('from builtins import getattr as read\nclass Example:\n    if flag:\n        read = public_callback\n    else:\n        read = other_callback\n    read(None, "_data")\n', id="class-both-aliases-public"),
        pytest.param('TARGET = "public"\nclass Example:\n    while flag:\n        TARGET = "public"\n        getattr(mod, TARGET)\n        TARGET = "_data"\n', id="class-loop-definite-public-prefix"),
        pytest.param('getattr = public_callback\nclass Example:\n    try:\n        raise Exception()\n    finally:\n        getattr(None, "_data")\n', id="class-exceptional-finally-keeps-alias"),
    ],
)
def test_public_targets_and_non_target_strings_are_allowed(tmp_path, source):
    result = _run(tmp_path, source)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "definition",
    [
        "def read(obj, name):\n        return name",
        "async def read(obj, name):\n        return name",
        "class read:\n        pass",
    ],
)
def test_local_definitions_shadow_an_imported_reflection_alias(tmp_path, definition):
    source = (
        "from builtins import getattr as read\n"
        "def test_callback():\n"
        f"    {definition}\n"
        '    read(None, "_data")\n'
    )
    result = _run(tmp_path, source)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "source",
    [
        'from builtins import getattr as read\ndef test_it():\n    read(mod, "_helper")\n',
        'from builtins import getattr as read\ndef read(value=read(mod, "_helper")):\n    pass\n',
        'from builtins import getattr as read\nclass read(read(mod, "_helper")):\n    pass\n',
        'from builtins import getattr as read\nclass read:\n    value = read(mod, "_helper")\n',
        'from builtins import getattr as read\nclass Example:\n    def read(value=read(mod, "_helper")):\n        pass\n',
        'from builtins import getattr as read\nclass Example:\n    @read(mod, "_helper")\n    def read():\n        pass\n',
        'from builtins import getattr as read\nclass Example:\n    class read(read(mod, "_helper")):\n        pass\n',
        'from builtins import getattr as read\ndef outer():\n    class Example:\n        def read(value=read(mod, "_helper")):\n            pass\n',
        'from builtins import getattr as read\nclass Example:\n    async def read(value=read(mod, "_helper")):\n        pass\n',
        'NAME = "_helper"\nclass Example:\n    def NAME(value=getattr(mod, NAME)):\n        pass\n',
        'from builtins import getattr as read\nclass Example:\n    def method(value=read(mod, "_helper")):\n        pass\n    read = public_callback\n',
    ],
)
def test_definition_headers_and_unshadowed_aliases_still_reject_private_access(
    tmp_path, source
):
    result = _run(tmp_path, source)
    assert result.returncode == 1, result.stderr
    assert "private string target '_helper'" in result.stdout


@pytest.mark.parametrize(
    ("setup", "call"),
    [
        ("", "patch.dict"),
        ("from unittest.mock import patch as replace\n", "replace.dict"),
        ("import unittest.mock as mocks\n", "mocks.patch.dict"),
        ("change = patch.dict\n", "change"),
    ],
)
@pytest.mark.parametrize("keyword", [False, True])
@pytest.mark.parametrize("separator", [".", ":"])
@pytest.mark.parametrize("private", [False, True])
def test_patch_dict_checks_only_its_import_target(
    tmp_path, setup, call, keyword, separator, private
):
    attribute = "_cache" if private else "cache"
    target = f"ha_mcp.module{separator}{attribute}"
    payload = '{"_private_key": "_private_value"}'
    arguments = (
        f"in_dict={target!r}, values={payload}" if keyword else f"{target!r}, {payload}"
    )
    result = _run(tmp_path, f"{setup}{call}({arguments})\n")
    assert result.returncode == int(private), result.stdout + result.stderr
    if private:
        assert f"private string target {target!r}" in result.stdout


@pytest.mark.parametrize(
    ("assignments", "target"),
    [
        ('TARGET = PATH\nif flag:\n    TARGET = {"_data": 1}\n', "TARGET"),
        ('TARGET = {"_data": 1}\nif flag:\n    TARGET = PATH\n', "TARGET"),
        ('TARGET = PATH\nif flag:\n    TARGET = {"_data": 1}\nCOPY = TARGET\n', "COPY"),
        ('TARGET = {"_data": 1}\nif flag:\n    TARGET = PATH\nCOPY = TARGET\n', "COPY"),
        ('if flag:\n    TARGET = PATH\nelse:\n    TARGET = {"_data": 1}\n', "TARGET"),
        ('if flag:\n    TARGET = {"_data": 1}\nelse:\n    TARGET = PATH\n', "TARGET"),
    ],
    ids=[
        "mapping-last",
        "string-last",
        "mapping-alias",
        "string-alias",
        "else-mapping",
        "else-string",
    ],
)
@pytest.mark.parametrize("private", [False, True], ids=["public", "private"])
def test_patch_dict_keeps_string_alternatives_separate_from_mapping_keys(
    tmp_path, assignments, target, private
):
    path = "ha_mcp.module._cache" if private else "ha_mcp.module.cache"
    source = f"PATH = {path!r}\n{assignments}patch.dict({target}, {{}})\n"
    result = _run(tmp_path, source)
    assert result.returncode == int(private), result.stdout + result.stderr
    assert "private string target '_data'" not in result.stdout
    if private:
        assert f"private string target {path!r}" in result.stdout


@pytest.mark.parametrize("tree", ["tests/src/unit/nested", "tests/addon/nested"])
def test_the_guard_covers_every_python_file_in_both_trees(tmp_path, tree):
    result = _run(tmp_path, 'getattr(mod, "_helper")\n', tree)
    assert result.returncode == 1, result.stderr
    assert f"{tree}/test_sample.py:1:" in result.stdout


def test_invalid_source_fails_instead_of_silently_skipping_a_file(tmp_path):
    result = _run(tmp_path, "def broken(:\n")
    assert result.returncode == 1
    assert "test_sample.py:" in result.stdout


def test_a_suppression_comment_does_not_exempt_private_access(tmp_path):
    result = _run(tmp_path, 'getattr(mod, "_helper")  # noqa: SLF001\n')
    assert result.returncode == 1
    assert "private string target" in result.stdout
