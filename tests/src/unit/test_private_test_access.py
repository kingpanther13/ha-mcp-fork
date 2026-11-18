"""The private-string guard rejects access syntax, never unrelated text."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "private_test_access.py"


def _run(tmp_path: Path, source: str, tree: str = "tests/src/unit", *, formatter_spy=False):
    path = tmp_path / tree / "test_sample.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    command = [sys.executable, str(_SCRIPT), "--repo", str(tmp_path)]
    if formatter_spy:
        bootstrap = """
import builtins
from pathlib import Path
import runpy
import sys

real_format = builtins.format
def guarded_format(value, spec=""):
    if "1000000000" in spec:
        raise AssertionError("oversized format reached formatter")
    print("formatter called")
    return real_format(value, spec)
builtins.format = guarded_format
script = sys.argv.pop(1)
sys.argv[0] = script
sys.path.insert(0, str(Path(script).parent))
runpy.run_path(script, run_name="__main__")
"""
        command = [sys.executable, "-c", bootstrap, *command[1:]]
    return subprocess.run(
        command,
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
        'DATA = {"_helper": 1}\nif flag:\n    DATA = {"public": 2}\nCOPY = DATA\npatch.multiple(mod, **COPY)\n',
        'for name in {"ha_mcp.module._cache": 0}:\n    patch.dict(name, {})\n',
        '[patch.dict(name, {}) for name in {"ha_mcp.module._cache": 0}]\n',
        'NAMES = {"ha_mcp.module._cache": 0}\nCOPY = NAMES\nfor name in COPY:\n    patch.dict(name, {})\n',
        'BASE = {"_helper": 1}\nCOPY = {**BASE}\npatch.multiple(mod, **COPY)\n',
        'def TARGET():\n    pass\nTARGET = "ha_mcp.module._cache"\npatch.dict(TARGET, {})\n',
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
        'from builtins import getattr as read\nread: object\nread(mod, "_helper")\n',
        'from builtins import getattr as read\nholder[read(mod, "_helper")] = read = public_callback\n',
        'import unittest.mock as source\nsource = replace = source.patch\nreplace("ha_mcp.module._helper")\n',
        'from builtins import getattr as read\nholder[read(mod, "_helper")], read = public_values\n',
        'from builtins import getattr as read\nholder[read(mod, "_helper")], *read = public_values\n',
        pytest.param(
            "matrix[\"cases\"] = (getattr(mod, \"_helper\"), 'a0', 'a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7', 'a8', 'a9', 'a10', 'a11', 'a12', 'a13', 'a14', 'a15', 'a16') + ('b0', 'b1', 'b2', 'b3', 'b4', 'b5', 'b6', 'b7', 'b8', 'b9', 'b10', 'b11', 'b12', 'b13', 'b14', 'b15', 'b16')\n",
            id="nonbinding-assignment-still-scans-rhs",
        ),
        pytest.param(
            'patch.dict(**{"in_dict": "ha_mcp.module._cache", "values": {}})\n',
            id="unpacked-patch-dict-target",
        ),
        pytest.param(
            'OPTIONS = {"in_dict": "ha_mcp.module._cache", "values": {}}\npatch.dict(**OPTIONS)\n',
            id="aliased-unpacked-patch-dict-target",
        ),
        pytest.param(
            'patch.dict(**{**{"in_dict": "ha_mcp.module._cache"}, "values": {}})\n',
            id="nested-unpacked-patch-dict-target",
        ),
        pytest.param(
            'patch.object(**{"target": obj, "attribute": "_helper", "new": replacement})\n',
            id="unpacked-patch-object-attribute",
        ),
        pytest.param(
            'OPTIONS = {"target": obj, "name": "_helper", "value": replacement}\nmonkeypatch.setattr(**OPTIONS)\n',
            id="aliased-unpacked-setattr-name",
        ),
        pytest.param(
            "FIRST = ('public',)\nSECOND = ('_helper',)\nCASES = FIRST + SECOND\nfor name in CASES:\n    getattr(mod, name)\n",
            id="concatenated-iterable-private-loop",
        ),
        pytest.param(
            "FIRST = ('public',)\nSECOND = ('_helper',)\nresults = [getattr(mod, name) for name in FIRST + SECOND]\n",
            id="concatenated-iterable-private-comprehension",
        ),
        pytest.param(
            "name = '_helper' if flag else 'public'\ngetattr(mod, name)\n",
            id="conditional-private-string",
        ),
        pytest.param(
            "names = ('_helper',) if flag else ('public',)\nfor name in names:\n    getattr(mod, name)\n",
            id="conditional-private-iterable",
        ),
        pytest.param(
            "options = {'_helper': 1} if flag else {'public': 2}\npatch.multiple(mod, **options)\n",
            id="conditional-private-mapping-key",
        ),
        pytest.param(
            "from builtins import getattr as read\nlookup = read if flag else callback\nlookup(mod, '_helper')\n",
            id="conditional-reflection-alias",
        ),
        pytest.param(
            "getattr(mod, f\"{'helper':_>7}\")\n",
            id="formatted-private-fill",
        ),
        pytest.param(
            "getattr(mod, f'{12:_>3}')\n",
            id="formatted-private-literal-number",
        ),
        pytest.param(
            "getattr(mod, f\"{'helper'!r:_>9}\")\n",
            id="formatted-private-conversion-and-fill",
        ),
        pytest.param(
            "SPEC = '_>7'\ngetattr(mod, f\"{'helper':{SPEC}}\")\n",
            id="formatted-private-spec-variable",
        ),
        pytest.param(
            "getattr(mod, f\"{'_helper'!s}\")\n",
            id="formatted-private-string-conversion",
        ),
        pytest.param(
            "getattr(mod, f'_{runtime!r}')\n",
            id="formatted-unknown-field-private-prefix",
        ),
        pytest.param(
            "def test_it():\n    name, count = ('_helper', 1)\n    getattr(mod, name)\n",
            id="function-literal-unpack-private",
        ),
        pytest.param(
            "def test_it():\n    [other, (name, count)] = ['public', ('_helper', 1)]\n    getattr(mod, name)\n",
            id="function-nested-literal-unpack-private",
        ),
        pytest.param(
            "getattr(mod, f'{-1:_>3}')\n",
            id="formatted-negative-literal-private-fill",
        ),
        pytest.param(
            "getattr(mod, f'{+1:_>2}')\n",
            id="formatted-positive-unary-literal-private-fill",
        ),
        pytest.param(
            "def test_it(self):\n    opts = {'target': self if flag else obj, 'attribute': '_helper'}\n    patch.object(**opts)\n",
            id="keyword-mapping-mixed-own-and-other-target",
        ),
        pytest.param(
            "monkeypatch.delattr(**({'target': 'pkg.public'} if flag else {'target': obj, 'name': '_helper'}))\n",
            id="keyword-mapping-delattr-mixed-signatures",
        ),
        pytest.param(
            "opts = {'target': 'pkg.public', 'name': '_data'} if flag else {'target': obj, 'name': '_helper', 'value': replacement}\nmonkeypatch.setattr(**opts)\n",
            id="keyword-mapping-setattr-mixed-signatures",
        ),
        pytest.param(
            "patch.dict(**{'in_dict': 'pkg._cache', **({'in_dict': 'pkg.cache'} if flag else {})})\n",
            id="keyword-overlay-optional-key-retains-private-fallback",
        ),
        pytest.param(
            "extra = {'in_dict': 'pkg.cache'} if flag else {}\nopts = {'in_dict': 'pkg._cache', **extra}\npatch.dict(**opts)\n",
            id="keyword-overlay-alias-retains-private-fallback",
        ),
        pytest.param(
            "monkeypatch.setattr('public', '_helper', replacement)\n",
            id="monkeypatch-setattr-string-object-private-name",
        ),
        pytest.param(
            "monkeypatch.delattr('public', '_helper')\n",
            id="monkeypatch-delattr-string-object-private-name",
        ),
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
        'NAME = "_data"\ndef test_it():\n    from public_constants import NAME\n    getattr(mod, NAME)\n',
        'NAME = "_data"\ndef test_it():\n    import public_constants as NAME\n    getattr(mod, NAME)\n',
        'NAME = "_data"\ndef test_it():\n    from public_constants import public as NAME\n    getattr(mod, NAME)\n',
        'def getattr(obj, name):\n    return name\ngetattr(None, "_data")\n',
        'NAME = "_data"\ndef test_it():\n    def NAME():\n        pass\n    getattr(mod, NAME)\n',
        'from builtins import getattr as read\ndef read(obj, name):\n    return name\nread(None, "_data")\n',
        'patch.dict({"ha_mcp.module._cache": 1}, {"_data": 2})\n',
        'DATA = {"ha_mcp.module._cache": 1}\npatch.dict(in_dict=DATA, values={"_data": 2})\n',
        'DATA = {"ha_mcp.module._cache": 1}\nCOPY = DATA\npatch.dict(COPY, {"_data": 2})\n',
        'DATA = {"ha_mcp.module._cache": 1}\nif flag:\n    DATA = {"ha_mcp.module._other": 2}\nCOPY = DATA\npatch.dict(COPY, {})\n',
        'BASE = {"ha_mcp.module._cache": 1}\nDATA = {**BASE}\npatch.dict(DATA, {})\n',
        'from builtins import getattr as read\nread = read(mod, "public")\nread(None, "_data")\n',
        'from builtins import getattr as read\nread: object = read(mod, "public")\nread(None, "_data")\n',
        'from builtins import getattr as read\nread = other = read(mod, "public")\nread(None, "_data")\nother(None, "_data")\n',
        'from builtins import getattr as read\nread = wrapper(read(mod, "public"))\nread(None, "_data")\n',
        'from builtins import getattr as read\nread = lambda: read(None, "_data")\n',
        'from builtins import getattr as read\nread = lambda value=read(mod, "public"): read(None, "_data")\n',
        'from builtins import getattr as read\nread, other = public_values\nread(None, "_data")\n',
        'from builtins import getattr as read\n[read, other] = public_values\nread(None, "_data")\n',
        'getattr = public_callback()\ngetattr(None, "_data")\n',
        'from builtins import getattr as read\nread: object\nread(mod, "public")\n',
        'from builtins import getattr as read\nread = holder[read(None, "_data")] = public_callback\n',
        'from builtins import getattr as read\nread, holder[read(None, "_data")] = public_values\n',
        'from builtins import getattr as read\n*read, holder[read(None, "_data")] = public_values\n',
        pytest.param(
            "matrix[\"cases\"] = ('a0', 'a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7', 'a8', 'a9', 'a10', 'a11', 'a12', 'a13', 'a14', 'a15', 'a16') + ('b0', 'b1', 'b2', 'b3', 'b4', 'b5', 'b6', 'b7', 'b8', 'b9', 'b10', 'b11', 'b12', 'b13', 'b14', 'b15', 'b16')\n",
            id="nonbinding-assignment-does-not-expand-data",
        ),
        pytest.param(
            'dispatch.dict("_data")\ndispatch.object(obj, "_data")\ndispatch.multiple(obj, _data=value)\n',
            id="dispatch-methods-are-not-patch",
        ),
        pytest.param(
            'patch.dict(**{"in_dict": "ha_mcp.module.cache", "values": {"_data": "_value"}})\n',
            id="unpacked-patch-dict-replacement-data",
        ),
        pytest.param(
            'OPTIONS = {"in_dict": "ha_mcp.module.cache", "values": {"_data": 1}}\npatch.dict(**OPTIONS)\n',
            id="aliased-unpacked-public-patch-dict",
        ),
        pytest.param(
            'patch.object(**{"target": self, "attribute": "_own_helper", "new": replacement})\n',
            id="unpacked-own-object-stays-exempt",
        ),
        pytest.param(
            "name = '_data' if False else 'public'\ngetattr(mod, name)\n",
            id="conditional-reachable-public-string",
        ),
        pytest.param(
            "name = 'public' if flag else 'other'\ngetattr(mod, name)\n",
            id="conditional-both-public-strings",
        ),
        pytest.param(
            "data = ('_data',) if flag else {'_metadata': 1}\npatch.dict(data, {'_replacement': 2})\n",
            id="conditional-private-data-container",
        ),
        pytest.param(
            "FIRST = ('pub',)\nSECOND = ('lic',)\nfor name in FIRST + SECOND:\n    getattr(mod, name)\n",
            id="concatenated-public-iterable",
        ),
        pytest.param(
            "EMPTY = ()\nDATA = EMPTY + ('_data',)\npatch.dict({}, {'payload': DATA})\n",
            id="concatenated-empty-private-data",
        ),
        pytest.param(
            "getattr(mod, f\"{'helper':x>7}\")\n",
            id="formatted-public-fill",
        ),
        pytest.param(
            "getattr(mod, f\"{'_data'!r}\")\n",
            id="formatted-repr-quotes-private-looking-value",
        ),
        pytest.param(
            "getattr(mod, f\"{'_data'!a}\")\n",
            id="formatted-ascii-quotes-private-looking-value",
        ),
        pytest.param(
            "def test_it():\n    name, payload = ('public', '_data')\n    getattr(mod, name)\n",
            id="function-unpack-private-other-element",
        ),
        pytest.param(
            "FIRST = ('first_0', 'first_1', 'first_2', 'first_3', 'first_4', 'first_5', 'first_6', 'first_7', 'first_8', 'first_9', 'first_10', 'first_11', 'first_12', 'first_13', 'first_14', 'first_15', 'first_16')\nSECOND = ('second_0', 'second_1', 'second_2', 'second_3', 'second_4', 'second_5', 'second_6', 'second_7', 'second_8', 'second_9', 'second_10', 'second_11', 'second_12', 'second_13', 'second_14', 'second_15', 'second_16')\nCASES = FIRST + SECOND\n",
            id="named-tuple-concatenation-does-not-cross-product",
        ),
        pytest.param(
            "getattr(mod, f'{-1:x>3}')\ngetattr(mod, f'{+1:x>2}')\n",
            id="formatted-signed-literal-public-fill",
        ),
        pytest.param(
            'print(f"{\'x\':>5000}")\n',
            id="unrelated-call-does-not-resolve-format-targets",
        ),
        pytest.param(
            'getattr(f"{\'x\':>5000}", "public")\n',
            id="reflection-object-data-is-not-an-attribute-name",
        ),
        pytest.param(
            "def test_it(self):\n    opts = {'target': self, 'attribute': '_helper'}\n    patch.object(**opts)\n",
            id="keyword-mapping-own-self-target",
        ),
        pytest.param(
            "def test_it(cls):\n    opts = {'target': cls, 'attribute': '_helper'}\n    copy = opts\n    patch.object(**copy)\n",
            id="keyword-mapping-own-cls-target",
        ),
        pytest.param(
            "def test_it(self, cls):\n    opts = {'target': self if flag else cls, 'attribute': '_helper'}\n    patch.object(**opts)\n",
            id="keyword-mapping-all-own-target-alternatives",
        ),
        pytest.param(
            "opts = {'target': 'pkg.public', 'name': '_data'} if flag else {'target': obj, 'name': 'public', 'value': '_data'}\nmonkeypatch.setattr(**opts)\n",
            id="keyword-mapping-setattr-dotted-replacement-data",
        ),
        pytest.param(
            "opts = {'in_dict': 'pkg._cache', **({'in_dict': 'pkg.cache'} if flag else {'in_dict': 'pkg.other'})}\npatch.dict(**opts)\n",
            id="keyword-overlay-definite-public-key-replaces-private",
        ),
        pytest.param(
            'DATA = {\'label\': f"{\'x\':>5000}"}\n',
            id="dictionary-format-data-remains-lazy",
        ),
        pytest.param(
            'DATA = {\'label\': f"{\'x\':>5000}"}\npatch.dict({}, DATA)\n',
            id="patch-dictionary-format-data-remains-lazy",
        ),
        pytest.param(
            'opts = {\'target\': \'pkg.public\', \'new\': f"{\'x\':>5000}"}\npatch(**opts)\n',
            id="keyword-replacement-format-data-remains-lazy",
        ),
        pytest.param(
            'LABEL = f"{\'x\':>5000}"\nCOPY = LABEL\n',
            id="unused-oversized-scalar-remains-data",
        ),
        pytest.param(
            'LABELS = (f"{\'x\':>5000}",)\nfor label in LABELS:\n    pass\n',
            id="unused-oversized-iteration-remains-data",
        ),
        pytest.param(
            'patch.multiple(mod, field_0=(1 if flag_0 else 2), field_1=(1 if flag_1 else 2), field_2=(1 if flag_2 else 2), field_3=(1 if flag_3 else 2), field_4=(1 if flag_4 else 2), field_5=(1 if flag_5 else 2), field_6=(1 if flag_6 else 2), field_7=(1 if flag_7 else 2), field_8=(1 if flag_8 else 2))\n',
            id="replacement-conditionals-stay-lazy",
        ),
        pytest.param(
            "def test_it(self):\n    monkeypatch.setattr(self if flag else 'pkg.public', '_data')\n",
            id="monkeypatch-own-object-or-dotted-replacement-data",
        ),
        pytest.param(
            'DATA = {f"{\'x\':>5000}": 1}\npatch.dict(DATA, {})\n',
            id="oversized-mapping-key-is-data-for-patch-dict",
        ),
        pytest.param(
            "monkeypatch.setattr('public', 'public', '_data')\nmonkeypatch.delattr('public', 'public')\n",
            id="monkeypatch-string-object-public-names",
        ),
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


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "getattr(mod, f\"{'helper':_>1000000000}\")\n",
            id="formatted-width-bounded-before-allocation",
        ),
        pytest.param(
            "getattr(mod, f'{1:.1000000000f}')\n",
            id="formatted-precision-bounded-before-allocation",
        ),
    ],
)
def test_formatted_targets_are_bounded_before_allocation(tmp_path, source):
    result = _run(tmp_path, source, formatter_spy=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "formatted attribute target exceeds the size limit" in result.stdout, (
        result.stdout + result.stderr
    )
    assert "oversized format reached formatter" not in result.stderr
    assert "formatter called" not in result.stdout


def test_formatter_spy_delegates_ordinary_target_formatting(tmp_path):
    result = _run(tmp_path, "getattr(mod, f'{12:x>3}')\n", formatter_spy=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "formatter called" in result.stdout


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            'opts = {\'target\': f"{\'x\':>5000}"}\npatch(**opts)\n',
            id="keyword-format-target-still-bounded",
        ),
        pytest.param(
            'LABEL = f"{\'x\':>5000}"\nCOPY = LABEL\ngetattr(mod, COPY)\n',
            id="aliased-oversized-target-still-bounded",
        ),
        pytest.param(
            'DATA = {f"{\'x\':>5000}": 1}\npatch.multiple(mod, **DATA)\n',
            id="oversized-mapping-key-is-target-for-patch-multiple",
        ),
    ],
)
def test_oversized_targets_still_fail_after_lazy_binding(tmp_path, source):
    result = _run(tmp_path, source)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "formatted attribute target exceeds the size limit" in result.stdout
