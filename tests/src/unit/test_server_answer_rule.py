"""Test-policy guards refuse private access and typed-in server answers."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from textwrap import indent

import pytest

from ._rule_scan import REPO, RULES, flagged, scan

_SERVER_ANSWERS = "no-typed-in-server-answers"


@pytest.mark.parametrize(
    ("source", "refused"),
    [
        pytest.param(source, refused, id=case_id)
        for refused, cases in {
            True: {
                "patch-return-value": 'patch("ha_mcp.tools.x.get_component_caps", return_value={"caps": []})\n',
                "patch-object-mock": 'patch.object(update_check, "get_update_info", AsyncMock(return_value=None))\n',
                "setattr-lambda": 'monkeypatch.setattr(helper_flows, "fetch_helper_flow_types", lambda *_: [])\n',
                "patch-target-keyword": 'patch(target="ha_mcp.update_check.get_update_info", return_value=None)\n',
                "patch-object-attribute-keyword": 'patch.object(update_check, attribute="get_update_info", return_value=None)\n',
                "setattr-name-keyword": 'monkeypatch.setattr(update_check, name="get_update_info", value=lambda: None)\n',
                "bare-patch-target-keyword": 'with patch(target="ha_mcp.update_check.get_update_info") as info:\n    info.return_value = None\n',
                "bare-patch-object-keywords": 'patch.object(target=update_check, attribute="get_update_info")\n',
                "patch-multiple-keyword": "patch.multiple(update_check, get_update_info=AsyncMock(return_value=None))\n",
                "patch-multiple-default-mock": "patch.multiple(update_check, get_update_info=DEFAULT)\n",
                "bare-patch-answered-through-its-alias": 'with patch("ha_mcp.tools.component_api.get_component_caps") as caps:\n    caps.return_value = {"available": True}\n',
                "bare-patch-object": 'with patch.object(update_check, "get_update_info") as info:\n    info.return_value = None\n',
                "patch-imported-under-another-name": "from unittest.mock import patch as replace\n",
                "setattr-dotted-target": 'monkeypatch.setattr("ha_mcp.update_check.get_update_info", lambda: None)\n',
                "spy-lambda-that-calls-through": 'patch("ha_mcp.update_check.get_update_info", wraps=lambda: real_get_update_info())\n',
                "spy-lambda-calling-something-else": 'patch("ha_mcp.update_check.get_update_info", wraps=lambda: dict(latest="1"))\n',
                "spy-lambda-calling-a-helper-with-a-listed-name": 'patch("ha_mcp.update_check.get_update_info", wraps=lambda: fetch_fake())\n',
                "spy-lambda-with-a-typed-in-answer": 'patch("ha_mcp.update_check.get_update_info", wraps=lambda: {"latest": "1"})\n',
                "side-effect-lambda": 'patch("ha_mcp.update_check.get_update_info", side_effect=lambda: {"latest": "1"})\n',
            },
            False: {
                "another-function-by-target-keyword": 'patch(target="ha_mcp.paths.get_data_dir", return_value="get_update_info")\n',
                "patch-object-name-keyword-names-the-mock": 'patch.object(renderer, "label", name="get_update_info", return_value=None)\n',
                "patch-multiple-answer-on-another-keyword": "patch.multiple(\n    mod, get_update_info=mod.get_update_info, helper=Mock(return_value=None)\n)\n",
                "spy": 'patch.object(tools_addons, "get_addon_info", wraps=tools_addons.get_addon_info)\n',
                "failure": 'patch.object(update_check, "fetch_latest", side_effect=TimeoutError)\n',
                "another-function": 'patch.object(paths, "get_data_dir", return_value=tmp_path)\n',
                "another-function-returning-a-listed-name": 'patch.object(renderer, "label", return_value="get_update_info")\n',
                "another-function-with-a-listed-keyword-inside": 'patch.object(renderer, "label", new=Mock(get_update_info=lambda: 1))\n',
                "other-import-aliases": "from unittest.mock import AsyncMock as Fake, patch\nimport unittest.mock as mock\n",
                "reading-a-listed-name": 'getattr(update_check, "get_update_info")\ngetattr(update_check, "get_update_info", lambda: None)\nimport_module("ha_mcp.tools.fetch_helper")\n',
                "reading-a-listed-name-inside-a-with": 'with nullcontext(resolve_name("ha_mcp.update_check.get_update_info")):\n    pass\n',
                "spy-on-the-real-function-by-name": 'patch("ha_mcp.update_check.get_update_info", wraps=real_get_update_info)\n',
            },
        }.items()
        for case_id, source in cases.items()
    ],
)
def test_only_a_typed_in_server_answer_is_refused(
    source: str, refused: bool, tmp_path: Path
) -> None:
    assert flagged(_SERVER_ANSWERS, source, tmp_path) is refused


def test_no_test_types_in_a_server_answer() -> None:
    """The rule has no list of exempt files: every hit is a violation."""
    rows = scan(RULES / f"{_SERVER_ANSWERS}.yml", REPO, "tests")

    assert sorted({row["file"].replace("\\", "/") for row in rows}) == []


_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "private_test_access.py"


def _run(
    tmp_path: Path, source: str, tree: str = "tests/src/unit", *, formatter_spy=False
):
    path = tmp_path / tree / "test_sample.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    command = [sys.executable, str(_SCRIPT), "--repo", str(tmp_path)]
    if formatter_spy:
        bootstrap = """
import builtins
from pathlib import Path
from textwrap import indent
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
        'TARGET = "_helper"\nclass Example(getattr(mod, TARGET)):\n    TARGET = "public"\n',
        'TARGET = "_helper"\nclass Example:\n    TARGET = "public"\n    def method(self):\n        getattr(mod, TARGET)\n',
        'TARGET = "public"\nclass Example:\n    TARGET = "_helper"\n    def method(self, value=getattr(mod, TARGET)):\n        pass\n',
        'TARGET = "ha_mcp.mod.public"\nclass Example:\n    TARGET = "ha_mcp.mod._helper"\n    @patch(TARGET)\n    def method(self):\n        pass\n',
        'def outer():\n    TARGET = "_helper"\n    class Example:\n        TARGET = "public"\n        def method(self):\n            getattr(mod, TARGET)\n',
        'TARGET = "_helper"\nclass Example:\n    TARGET = "public"\n    values = [getattr(mod, TARGET) for index in (1,)]\n',
        'class Example:\n    NAMES = ("_helper",)\n    values = [getattr(mod, name) for name in NAMES]\n',
        'class TARGET:\n    pass\nTARGET = "ha_mcp.module._cache"\npatch.dict(TARGET, {})\n',
        'from builtins import getattr as read\nclass Example:\n    read = lambda: read(mod, "_helper")\n',
        'TARGET = "_helper"\nclass Example:\n    TARGET = "public"\n    def method(value=[getattr(mod, TARGET) for index in (1,)]):\n        pass\n',
        'TARGET = "_helper"\nclass Example:\n    TARGET = "public"\n    def method(value=lambda: getattr(mod, TARGET)):\n        pass\n',
        *(
            pytest.param(source, id=case_id)
            for case_id, source in {
                "known-class-reflection-member-is-a-target-helper": "class Example:\n    lookup = getattr\nExample.lookup(mod, '_helper')\n",
                "instance-member-store-keeps-class-method-intact": "NAME = 'public'\nclass Example:\n    @staticmethod\n    def run():\n        getattr(mod, NAME)\nNAME = '_helper'\ninstance = Example()\ninstance.run = callback\nExample.run()\nNAME = 'public'\n",
                "instance-member-delete-restores-class-method": "NAME = 'public'\nclass Example:\n    @staticmethod\n    def run():\n        getattr(mod, NAME)\nNAME = '_helper'\ninstance = Example()\ninstance.run = callback\ndel instance.run\ninstance.run()\nNAME = 'public'\n",
                "uncertain-instance-member-store-keeps-unmodified-alternative": "NAME = 'public'\nclass Example:\n    @staticmethod\n    def run():\n        getattr(mod, NAME)\nNAME = '_helper'\nfirst = Example()\nsecond = Example()\nselected = first if flag else second\nselected.run = callback\nfirst.run()\nsecond.run()\nNAME = 'public'\n",
                "known-class-static-method-uses-early-global": "NAME = '_helper'\nclass Example:\n    @staticmethod\n    def run():\n        getattr(mod, NAME)\nExample.run()\nNAME = 'public'\n",
                "immediate-known-instance-method-uses-early-global": "NAME = '_helper'\nclass Example:\n    def run(self):\n        getattr(mod, NAME)\nExample().run()\nNAME = 'public'\n",
                "copied-known-instance-method-uses-early-global": "NAME = '_helper'\nclass Example:\n    def run(self):\n        getattr(mod, NAME)\ninstance = Example()\ninvoke = instance.run\ninvoke()\nNAME = 'public'\n",
                "known-constructor-init-runs-at-instantiation": "NAME = '_helper'\nclass Example:\n    def __init__(self):\n        getattr(mod, NAME)\nExample()\nNAME = 'public'\n",
                "known-constructor-new-runs-at-instantiation": "NAME = '_helper'\nclass Example:\n    def __new__(cls):\n        getattr(mod, NAME)\n        return object.__new__(cls)\nExample()\nNAME = 'public'\n",
                "awaited-body-uses-current-module-binding": "NAME = 'public'\nasync def helper():\n    getattr(mod, NAME)\nasync def test_it():\n    global NAME\n    NAME = '_helper'\n    await helper()\n    NAME = 'public'\n",
                "immediate-lambda-keeps-private-call-time-binding": "NAME = '_helper'\n(lambda: getattr(mod, NAME))()\nNAME = 'public'\n",
                "deferred-sibling-call-keeps-defining-private-cell": "def outer():\n    NAME = '_helper'\n    def inner():\n        getattr(mod, NAME)\n    def caller():\n        NAME = 'public'\n        inner()\n    caller()\n    NAME = 'public'\n",
                "parameter-callable-shadow-precedes-later-import": "def test_it(range):\n    for item in range(0):\n        getattr(mod, '_helper')\n    from builtins import range\n",
                "parameter-callable-shadow-precedes-later-assignment": "from builtins import range as builtin_range\ndef test_it(range):\n    for item in range(0):\n        getattr(mod, '_helper')\n    range = builtin_range\n",
                "deferred-lambda-uses-final-private-global": "NAME = 'public'\nrun = lambda: getattr(mod, NAME)\nNAME = '_helper'\n",
                "deferred-async-uses-final-private-global": "NAME = 'public'\nasync def test_it():\n    getattr(mod, NAME)\nNAME = '_helper'\n",
                "deferred-returned-function-uses-final-private-closure": "def outer():\n    NAME = 'public'\n    def inner():\n        getattr(mod, NAME)\n    NAME = '_helper'\n    return inner\n",
                "deferred-method-keeps-enclosing-function-cell": "NAME = 'public'\ndef outer():\n    NAME = '_helper'\n    class TestExample:\n        NAME = 'public'\n        def test_it(self):\n            getattr(mod, NAME)\n    return TestExample\n",
                "literal-star-tuple-private-attribute": "getattr(*(obj, '_helper'))\n",
                "literal-star-list-private-attribute": "getattr(*[obj, '_helper'])\n",
                "nested-literal-star-private-attribute": "getattr(*(*[obj], *('_helper',)))\n",
                "literal-star-private-patch-target": "patch.dict(*('pkg._cache', {}), **{'_data': 1})\n",
                "shadowed-range-parameter-body-is-reachable": "def test_it(range):\n    for item in range(0):\n        getattr(obj, '_helper')\n",
                "alias-of-shadowed-range-remains-unknown": "def test_it(range):\n    steps = range\n    for item in steps(0):\n        getattr(obj, '_helper')\n",
                "shadowed-set-call-match-is-unknown": "set = lambda: ['public']\nmatch set():\n    case ['public']:\n        getattr(obj, '_helper')\n",
                "nested-shadowed-set-call-match-is-unknown": "set = lambda: ['public']\nmatch (set(),):\n    case (['public'],):\n        getattr(obj, '_helper')\n",
                "or-pattern-first-match-private-capture": "match ('_helper', 'public'):\n    case (name, 'public') | ('_helper', name):\n        getattr(obj, name)\n",
                "or-pattern-unknown-earlier-arm-retains-private-capture": "match ('_helper', 'public'):\n    case (name, constants.PUBLIC) | ('_helper', name):\n        getattr(obj, name)\n",
                "deferred-module-body-uses-final-private-global": "NAME = 'public'\ndef test_it():\n    getattr(mod, NAME)\nNAME = '_helper'\n",
                "deferred-nested-body-uses-reassigned-closure": "def outer():\n    NAME = 'public'\n    def inner():\n        getattr(mod, NAME)\n    NAME = '_helper'\n    inner()\n",
                "deferred-method-uses-final-module-global": "NAME = 'public'\nclass TestExample:\n    def test_it(self):\n        getattr(mod, NAME)\nNAME = '_helper'\n",
                "deferred-default-still-runs-at-definition": "NAME = '_helper'\ndef test_it(value=getattr(mod, NAME)):\n    pass\nNAME = 'public'\n",
                "deferred-decorator-still-runs-at-definition": "NAME = '_helper'\n@getattr(mod, NAME)\ndef test_it():\n    pass\nNAME = 'public'\n",
                "deferred-direct-call-keeps-call-time-private-global": "NAME = '_helper'\ndef helper():\n    getattr(mod, NAME)\nhelper()\nNAME = 'public'\n",
                "partial-delete-restores-builtin-before-target-failure": "getattr = callback\ntry:\n    del getattr, {}['missing']\nexcept KeyError:\n    getattr(mod, '_helper')\n",
                "nonbinding-assignment-still-scans-rhs": "matrix[\"cases\"] = (getattr(mod, \"_helper\"), 'a0', 'a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7', 'a8', 'a9', 'a10', 'a11', 'a12', 'a13', 'a14', 'a15', 'a16') + ('b0', 'b1', 'b2', 'b3', 'b4', 'b5', 'b6', 'b7', 'b8', 'b9', 'b10', 'b11', 'b12', 'b13', 'b14', 'b15', 'b16')\n",
                "unpacked-patch-dict-target": 'patch.dict(**{"in_dict": "ha_mcp.module._cache", "values": {}})\n',
                "aliased-unpacked-patch-dict-target": 'OPTIONS = {"in_dict": "ha_mcp.module._cache", "values": {}}\npatch.dict(**OPTIONS)\n',
                "nested-unpacked-patch-dict-target": 'patch.dict(**{**{"in_dict": "ha_mcp.module._cache"}, "values": {}})\n',
                "unpacked-patch-object-attribute": 'patch.object(**{"target": obj, "attribute": "_helper", "new": replacement})\n',
                "aliased-unpacked-setattr-name": 'OPTIONS = {"target": obj, "name": "_helper", "value": replacement}\nmonkeypatch.setattr(**OPTIONS)\n',
                "concatenated-iterable-private-loop": "FIRST = ('public',)\nSECOND = ('_helper',)\nCASES = FIRST + SECOND\nfor name in CASES:\n    getattr(mod, name)\n",
                "concatenated-iterable-private-comprehension": "FIRST = ('public',)\nSECOND = ('_helper',)\nresults = [getattr(mod, name) for name in FIRST + SECOND]\n",
                "conditional-private-string": "name = '_helper' if flag else 'public'\ngetattr(mod, name)\n",
                "conditional-private-iterable": "names = ('_helper',) if flag else ('public',)\nfor name in names:\n    getattr(mod, name)\n",
                "conditional-private-mapping-key": "options = {'_helper': 1} if flag else {'public': 2}\npatch.multiple(mod, **options)\n",
                "conditional-reflection-alias": "from builtins import getattr as read\nlookup = read if flag else callback\nlookup(mod, '_helper')\n",
                "formatted-private-fill": "getattr(mod, f\"{'helper':_>7}\")\n",
                "formatted-private-literal-number": "getattr(mod, f'{12:_>3}')\n",
                "formatted-private-conversion-and-fill": "getattr(mod, f\"{'helper'!r:_>9}\")\n",
                "formatted-private-spec-variable": "SPEC = '_>7'\ngetattr(mod, f\"{'helper':{SPEC}}\")\n",
                "formatted-private-string-conversion": "getattr(mod, f\"{'_helper'!s}\")\n",
                "formatted-unknown-field-private-prefix": "getattr(mod, f'_{runtime!r}')\n",
                "function-literal-unpack-private": "def test_it():\n    name, count = ('_helper', 1)\n    getattr(mod, name)\n",
                "function-nested-literal-unpack-private": "def test_it():\n    [other, (name, count)] = ['public', ('_helper', 1)]\n    getattr(mod, name)\n",
                "formatted-negative-literal-private-fill": "getattr(mod, f'{-1:_>3}')\n",
                "formatted-positive-unary-literal-private-fill": "getattr(mod, f'{+1:_>2}')\n",
                "keyword-mapping-mixed-own-and-other-target": "def test_it(self):\n    opts = {'target': self if flag else obj, 'attribute': '_helper'}\n    patch.object(**opts)\n",
                "keyword-mapping-delattr-mixed-signatures": "monkeypatch.delattr(**({'target': 'pkg.public'} if flag else {'target': obj, 'name': '_helper'}))\n",
                "keyword-mapping-setattr-mixed-signatures": "opts = {'target': 'pkg.public', 'name': '_data'} if flag else {'target': obj, 'name': '_helper', 'value': replacement}\nmonkeypatch.setattr(**opts)\n",
                "keyword-overlay-optional-key-retains-private-fallback": "patch.dict(**{'in_dict': 'pkg._cache', **({'in_dict': 'pkg.cache'} if flag else {})})\n",
                "keyword-overlay-alias-retains-private-fallback": "extra = {'in_dict': 'pkg.cache'} if flag else {}\nopts = {'in_dict': 'pkg._cache', **extra}\npatch.dict(**opts)\n",
                "monkeypatch-setattr-string-object-private-name": "monkeypatch.setattr('public', '_helper', replacement)\n",
                "monkeypatch-delattr-string-object-private-name": "monkeypatch.delattr('public', '_helper')\n",
                "class-inherited-rhs": 'TARGET = "_helper"\nclass Example:\n    TARGET = getattr(mod, TARGET)\n',
                "class-annotated-rhs": 'TARGET = "_helper"\nclass Example:\n    TARGET: object = getattr(mod, TARGET)\n',
                "class-read-before-rebind": 'TARGET = "_helper"\nclass Example:\n    getattr(mod, TARGET)\n    TARGET = "public"\n',
                "class-copy-before-rebind": 'TARGET = "_helper"\nclass Example:\n    COPY = TARGET\n    TARGET = "public"\n    getattr(mod, COPY)\n',
                "class-optional-override": 'TARGET = "_helper"\nclass Example:\n    if flag:\n        TARGET = "public"\n    getattr(mod, TARGET)\n',
                "class-value-join": 'TARGET = "public"\nclass Example:\n    if flag:\n        TARGET = "_helper"\n    else:\n        TARGET = "public"\n    getattr(mod, TARGET)\n',
                "class-mapping-string-join": 'TARGET = "ha_mcp.module._cache"\nclass Example:\n    if flag:\n        TARGET = {"_data": 1}\n    patch.dict(TARGET, {})\n',
                "class-optional-alias": 'from builtins import getattr as read\nclass Example:\n    if flag:\n        read = public_callback\n    read(mod, "_helper")\n',
                "class-alias-join": 'read = public_callback\nclass Example:\n    if flag:\n        read = getattr\n    else:\n        read = public_callback\n    read(mod, "_helper")\n',
                "class-loop-zero-iterations": 'TARGET = "_helper"\nclass Example:\n    for item in items:\n        TARGET = "public"\n    getattr(mod, TARGET)\n',
                "class-loop-later-condition": 'TARGET = "public"\nclass Example:\n    while getattr(mod, TARGET):\n        TARGET = "_helper"\n',
                "class-handler-intermediate-value": 'TARGET = "public"\nclass Example:\n    try:\n        TARGET = "_helper"\n        risky()\n        TARGET = "public"\n    except Exception:\n        getattr(mod, TARGET)\n',
                "class-handler-inherited-value": 'TARGET = "_helper"\nclass Example:\n    try:\n        TARGET = "public"\n    except Exception:\n        getattr(mod, TARGET)\n',
                "class-match-no-case": 'TARGET = "_helper"\nclass Example:\n    match value:\n        case 0:\n            TARGET = "public"\n    getattr(mod, TARGET)\n',
                "class-mapping-key-join": 'KEYS = {"_helper": 1}\nclass Example:\n    if flag:\n        KEYS = {"public": 2}\n    patch.multiple(mod, **KEYS)\n',
                "class-annotation-only": 'TARGET = "_helper"\nclass Example:\n    TARGET: str\n    getattr(mod, TARGET)\n',
                "class-loop-break-value": 'TARGET = "public"\nclass Example:\n    for item in items:\n        TARGET = "_helper"\n        if flag:\n            break\n        TARGET = "public"\n    getattr(mod, TARGET)\n',
                "class-finally-unhandled-value": 'TARGET = "public"\nclass Example:\n    try:\n        TARGET = "_helper"\n        risky()\n        TARGET = "public"\n    except Specific:\n        TARGET = "public"\n    finally:\n        getattr(mod, TARGET)\n',
                "class-suppressed-intermediate-value": 'TARGET = "public"\nclass Example:\n    with suppress(Exception):\n        TARGET = "_helper"\n        risky()\n        TARGET = "public"\n    getattr(mod, TARGET)\n',
                "class-exception-name-fallback": 'TARGET = "_helper"\nclass Example:\n    TARGET = "public"\n    try:\n        raise Exception()\n    except Exception as TARGET:\n        pass\n    getattr(mod, TARGET)\n',
                "class-deleted-value-fallback": 'TARGET = "_helper"\nclass Example:\n    TARGET = "public"\n    del TARGET\n    getattr(mod, TARGET)\n',
                "class-deleted-alias-fallback": 'from builtins import getattr as read\nclass Example:\n    read = public_callback\n    del read\n    read(mod, "_helper")\n',
                "class-finally-else-intermediate": 'TARGET = "public"\nclass Example:\n    try:\n        pass\n    except Specific:\n        pass\n    else:\n        TARGET = "_helper"\n        risky()\n        TARGET = "public"\n    finally:\n        getattr(mod, TARGET)\n',
                "class-handler-before-builtin-rebinding": 'class Example:\n    try:\n        risky()\n        getattr = public_callback\n    except Exception:\n        getattr(mod, "_helper")\n',
                "class-handler-after-builtin-restored": 'class Example:\n    getattr = public_callback\n    try:\n        del getattr\n        risky()\n    except Exception:\n        getattr(mod, "_helper")\n',
                "class-bound-name-uses-module-fallback": 'NAME = "_helper"\ndef outer():\n    NAME = "public"\n    class Example:\n        getattr(mod, NAME)\n        NAME = "public"\n',
                "class-bound-alias-uses-module-fallback": 'from builtins import getattr as read\ndef outer():\n    read = public_callback\n    class Example:\n        read(mod, "_helper")\n        read = public_callback\n',
                "class-literal-unpack-preserves-value": 'class Example:\n    NAME = "_helper"\n    NAME, count = (NAME, 1)\n    getattr(mod, NAME)\n',
                "class-literal-unpack-snapshots-rhs": 'class Example:\n    NAME = "_helper"\n    OTHER = "public"\n    NAME, OTHER = (OTHER, NAME)\n    getattr(mod, OTHER)\n',
                "class-literal-unpack-preserves-alias": 'class Example:\n    read = getattr\n    read, count = (read, 1)\n    read(mod, "_helper")\n',
                "class-loop-carries-import-alias": 'class Example:\n    read = public_callback\n    for item in (0, 1):\n        read(mod, "_helper")\n        from builtins import getattr as read\n',
                "class-loop-later-iteration-uses-body-value": 'class Example:\n    NAME = "public"\n    for item in (0, 1):\n        getattr(mod, NAME)\n        NAME = "_helper"\n',
                "class-loop-break-bypasses-else": 'class Example:\n    NAME = "_helper"\n    for item in (0,):\n        break\n    else:\n        NAME = "public"\n    getattr(mod, NAME)\n',
                "class-reachable-try-else-reaches-finally": 'class Example:\n    NAME = "public"\n    try:\n        pass\n    except ValueError:\n        pass\n    else:\n        NAME = "_helper"\n    finally:\n        getattr(mod, NAME)\n',
                "class-except-star-carries-prior-handler": 'class Example:\n    NAME = "public"\n    try:\n        raise ExceptionGroup("both", [ValueError(), TypeError()])\n    except* ValueError:\n        NAME = "_helper"\n    except* TypeError:\n        getattr(mod, NAME)\n',
                "class-false-match-guard-keeps-private-capture": 'class Example:\n    NAME = "public"\n    match "_helper":\n        case NAME if False:\n            pass\n        case _:\n            getattr(mod, NAME)\n',
                "class-failed-match-pattern-keeps-input": 'class Example:\n    NAME = "_helper"\n    match value:\n        case [NAME] if False:\n            pass\n        case _:\n            getattr(mod, NAME)\n',
                "class-whole-subject-private-capture": 'class Example:\n    match "_helper":\n        case NAME:\n            getattr(mod, NAME)\n',
                "class-earlier-context-preserves-unbound-target": 'from contextlib import suppress\nclass Example:\n    NAME = "_helper"\n    with suppress(ValueError), fail() as NAME:\n        pass\n    getattr(mod, NAME)\n',
                "class-nested-literal-unpack-preserves-value": 'class Example:\n    NAME, (count, other) = ("_helper", (1, "public"))\n    getattr(mod, NAME)\n',
                "class-deleted-name-uses-module-not-function-fallback": 'NAME = "_helper"\ndef outer():\n    NAME = "public"\n    class Example:\n        NAME = "public"\n        del NAME\n        getattr(mod, NAME)\n',
                "class-exception-name-uses-module-not-function-fallback": 'NAME = "_helper"\ndef outer():\n    NAME = "public"\n    class Example:\n        try:\n            raise ValueError()\n        except ValueError as NAME:\n            pass\n        getattr(mod, NAME)\n',
                "class-loop-uses-named-iterable-summary": 'NAMES = ("_helper",)\nclass Example:\n    for name in NAMES:\n        getattr(mod, name)\n',
                "class-global-name-uses-module-without-local-store": 'NAME = "_helper"\ndef outer():\n    NAME = "public"\n    class Example:\n        global NAME\n        getattr(mod, NAME)\n',
                "class-nonlocal-name-keeps-enclosing-value": 'NAME = "public"\ndef outer():\n    NAME = "_helper"\n    class Example:\n        nonlocal NAME\n        getattr(mod, NAME)\n        NAME = "public"\n',
                "class-deleted-global-alias-falls-back-to-builtin": 'getattr = public_callback\nclass Example:\n    global getattr\n    del getattr\n    getattr(mod, "_helper")\n',
                "literal-sequence-capture": "class Example:\n    match ('_helper',):\n        case [name]:\n            getattr(mod, name)\n",
                "nested-sequence-capture": "class Example:\n    match [('public', '_helper')]:\n        case [[other, name]]:\n            getattr(mod, name)\n",
                "sequence-capture-callable-alias": "from builtins import getattr as read\nclass Example:\n    match (read,):\n        case [lookup]:\n            lookup(mod, '_helper')\n",
                "conditional-private-class-string": "class Example:\n    name = 'public' if flag else '_helper'\n    getattr(mod, name)\n",
                "class-continue-keeps-live-binding": "class E:\n    NAME = 'public'\n    for item in (0,):\n        NAME = '_helper'\n        continue\n        NAME = 'public'\n    getattr(mod, NAME)\n",
                "class-continue-reaches-loop-else": "class E:\n    NAME = 'public'\n    for item in (0,):\n        NAME = '_helper'\n        continue\n        NAME = 'public'\n    else:\n        getattr(mod, NAME)\n",
                "class-except-star-cleanup-restores-builtin": "class E:\n    getattr = public_callback\n    try:\n        raise ExceptionGroup('group', [ValueError(), TypeError()])\n    except* ValueError as getattr:\n        pass\n    except* TypeError:\n        getattr(mod, '_helper')\n",
                "class-exceptional-finally-reaches-outer-handler": "class E:\n    NAME = 'public'\n    try:\n        try:\n            raise ValueError()\n        finally:\n            NAME = '_helper'\n    except ValueError:\n        getattr(mod, NAME)\n",
                "class-global-write-reaches-module": "NAME = 'public'\nclass E:\n    global NAME\n    NAME = '_helper'\ngetattr(mod, NAME)\n",
                "class-nonlocal-write-reaches-function": "def outer():\n    NAME = 'public'\n    class E:\n        nonlocal NAME\n        NAME = '_helper'\n    getattr(mod, NAME)\n",
                "nested-class-global-write-reaches-parent-class": "NAME = 'public'\nclass E:\n    class Nested:\n        global NAME\n        NAME = '_helper'\n    getattr(mod, NAME)\n",
                "class-conditional-global-write-keeps-private-path": "NAME = 'public'\nclass E:\n    if flag:\n        global NAME\n        NAME = '_helper'\ngetattr(mod, NAME)\n",
                "deferred-class-global-write-does-not-run-at-definition": "NAME = '_helper'\ndef later():\n    class E:\n        global NAME\n        NAME = 'public'\ngetattr(mod, NAME)\n",
                "module-assignment-after-class-global-write": "NAME = 'public'\nclass E:\n    global NAME\n    NAME = 'public'\nNAME = '_helper'\ngetattr(mod, NAME)\n",
                "function-assignment-after-class-nonlocal-write": "def outer():\n    NAME = 'public'\n    class E:\n        nonlocal NAME\n        NAME = 'public'\n    NAME = '_helper'\n    getattr(mod, NAME)\n",
                "mixed-local-binding-refreshes-unbound-module-path": "NAME = 'public'\nclass Example:\n    if flag:\n        NAME = 'public'\n    class Nested:\n        global NAME\n        NAME = '_helper'\n    getattr(mod, NAME)\n",
                "mixed-local-binding-preserves-private-local-path": "NAME = 'public'\nclass Example:\n    if flag:\n        NAME = '_helper'\n    class Nested:\n        global NAME\n        NAME = 'public'\n    getattr(mod, NAME)\n",
                "chained-store-interruption-keeps-earlier-name": "class Example:\n    NAME = 'public'\n    try:\n        NAME = holder[0] = '_helper'\n    except ValueError:\n        getattr(mod, NAME)\n",
                "iterator-next-header-error-keeps-carried-name": "class Example:\n    NAME = 'public'\n    try:\n        for NAME in items:\n            NAME = '_helper'\n    except ValueError:\n        getattr(mod, NAME)\n",
                "while-next-header-error-keeps-carried-name": "class Example:\n    NAME = 'public'\n    try:\n        while condition():\n            try:\n                NAME = '_helper'\n            finally:\n                continue\n    except ValueError:\n        getattr(mod, NAME)\n",
                "literal-range-preserves-private-result": "class Example:\n    NAME = 'public'\n    for index in range(1):\n        NAME = '_helper'\n    getattr(mod, NAME)\n",
                "known-literal-pattern-match-keeps-capture": "class Example:\n    match '_helper':\n        case '_helper' as name:\n            getattr(mod, name)\n",
                "with-exit-error-keeps-body-store": "class Example:\n    NAME = 'public'\n    try:\n        with manager():\n            NAME = '_helper'\n    except ValueError:\n        getattr(mod, NAME)\n",
                "conditional-class-global-preserves-skipped-path": "NAME = '_helper'\nif flag:\n    class Example:\n        global NAME\n        NAME = 'public'\ngetattr(mod, NAME)\n",
                "function-inherited-global-refreshes-after-class": "NAME = 'public'\ndef test_it():\n    class Example:\n        global NAME\n        NAME = '_helper'\n    getattr(mod, NAME)\n",
                "function-true-local-survives-class-global-write": "NAME = 'public'\ndef test_it():\n    NAME = '_helper'\n    class Example:\n        global NAME\n        NAME = 'public'\n    getattr(mod, NAME)\n",
                "enclosing-local-survives-inner-class-global-write": "NAME = 'public'\ndef outer():\n    NAME = '_helper'\n    def test_it():\n        class Example:\n            global NAME\n            NAME = 'public'\n        getattr(mod, NAME)\n",
                "finally-private-access-runs-after-return": "def test_it():\n    try:\n        return\n    finally:\n        getattr(mod, '_helper')\n",
                "wrapper-walk-preserves-reflective-call-suffix": "class Example:\n    fn = callback\n    while hasattr(fn, '__wrapped__'):\n        fn = fn.__wrapped__\n    fn.getattr(mod, '_helper')\n",
                "generator-next-uses-iteration-time-private-global": "NAME = 'public'\ndef values():\n    yield getattr(mod, NAME)\npending = values()\nNAME = '_helper'\nnext(pending)\nNAME = 'public'\n",
                "known-helper-arguments-distinguish-call-cache-entries": "def lookup(name):\n    return getattr(mod, name)\nlookup('public')\nlookup('_helper')\n",
                "known-helper-keyword-argument-is-bound": "def lookup(*, name):\n    return getattr(mod, name)\nlookup(name='_helper')\n",
                "known-helper-default-argument-is-bound": "def lookup(name='_helper'):\n    return getattr(mod, name)\nlookup()\n",
                "known-call-publishes-normal-global-write": "NAME = 'public'\ndef switch():\n    global NAME\n    NAME = '_helper'\nswitch()\ngetattr(mod, NAME)\n",
                "known-call-publishes-return-global-write": "NAME = 'public'\ndef switch():\n    global NAME\n    NAME = '_helper'\n    return None\nswitch()\ngetattr(mod, NAME)\n",
                "known-call-publishes-global-write-on-exception": "NAME = 'public'\ndef switch():\n    global NAME\n    NAME = '_helper'\n    raise ValueError()\ntry:\n    switch()\nexcept ValueError:\n    getattr(mod, NAME)\n",
                "named-coroutine-await-uses-current-private-global": "NAME = 'public'\nasync def helper():\n    getattr(mod, NAME)\nasync def test_it():\n    global NAME\n    pending = helper()\n    NAME = '_helper'\n    await pending\n    NAME = 'public'\n",
                "repeated-allocation-does-not-share-instance-replacement": "NAME = 'public'\nclass Example:\n    def run(self):\n        getattr(mod, NAME)\ncurrent = None\nfor item in (0, 1):\n    previous = current\n    current = Example()\nprevious.run = callback\nNAME = '_helper'\ncurrent.run()\nNAME = 'public'\n",
                "nested-yield-does-not-defer-enclosing-function": "NAME = 'public'\ndef outer():\n    def values():\n        yield 1\n    getattr(mod, NAME)\nNAME = '_helper'\nouter()\nNAME = 'public'\n",
                "helper-default-captures-definition-time-private-value": "NAME = '_helper'\ndef lookup(name=NAME):\n    getattr(mod, name)\nNAME = 'public'\nlookup()\n",
                "known-callable-argument-preserves-reflection-alias": "def lookup(read):\n    read(mod, '_helper')\nlookup(getattr)\n",
                "keyword-callable-argument-keeps-captured-alias": "read = getattr\noptions = {'read': read}\nread = callback\ndef lookup(read):\n    read(mod, '_helper')\nlookup(**options)\n",
                "named-coroutine-keeps-creation-time-argument-value": "async def lookup(name):\n    getattr(mod, name)\nasync def test_it():\n    name = '_helper'\n    pending = lookup(name)\n    name = 'public'\n    await pending\n",
                "cached-known-call-replays-global-write": "NAME = 'public'\ndef switch():\n    global NAME\n    NAME = '_helper'\nswitch()\nNAME = 'public'\nswitch()\ngetattr(mod, NAME)\n",
                "known-star-tuple-keeps-following-attribute-slot": "args = (obj,)\ngetattr(*args, '_helper')\n",
                "known-star-list-retains-second-element": "args = [obj, '_helper']\ngetattr(*args, 'public-default')\n",
                "known-star-alias-keeps-assignment-time-name": "name = '_helper'\nargs = (obj, name)\nname = 'public'\nforward = args\ngetattr(*forward)\n",
                "known-star-conditional-length-keeps-each-slot": "args = (obj,) if flag else (obj, 'public')\ngetattr(*args, '_helper')\n",
                "known-star-nested-prefix-retains-target": "prefix = (obj,)\nargs = (*prefix, '_helper')\ngetattr(*args)\n",
                "patch-dict-expanded-private-target-survives-large-data-key": "from unittest.mock import patch\npatch.dict(**{'in_dict': 'pkg._helper', f\"{'x':>5000}\": 1})\n",
                "patch-dict-optional-overlay-keeps-private-target": "from unittest.mock import patch\nkwargs = {'in_dict': 'pkg._helper', **({'in_dict': {}} if flag else {f\"{'x':>5000}\": 1})}\npatch.dict(**kwargs)\n",
                "patch-dict-aliased-conditional-key-keeps-private-target": "from unittest.mock import patch\nkey = 'in_dict' if flag else f\"{'x':>5000}\"\npatch.dict(**{key: 'pkg._helper'})\n",
                "patch-dict-large-string-precision-retains-short-private-target-key": "from unittest.mock import patch\npatch.dict(**{f\"{'in_dict':.5000s}\": 'pkg._helper'})\n",
                "helper-bound-method-argument-resolves-live-method": "NAME = '_helper'\nclass Example:\n    def run(self):\n        getattr(mod, NAME)\ninstance = Example()\ndef lookup(read):\n    read()\nlookup(instance.run)\nNAME = 'public'\n",
                "helper-tuple-argument-preserves-ordered-shape": "def lookup(args):\n    getattr(*args)\nargs = (mod, '_helper')\nlookup(args)\n",
                "raising-call-evaluates-private-argument-first": "def stop(value):\n    raise RuntimeError\nstop(getattr(mod, '_helper'))\n",
                "false-and-skips-interruption-before-private-access": "def stop():\n    raise RuntimeError\nFalse and stop()\ngetattr(mod, '_helper')\n",
                "true-or-skips-interruption-before-private-access": "def stop():\n    raise RuntimeError\nTrue or stop()\ngetattr(mod, '_helper')\n",
                "unknown-and-keeps-normal-continuation": "def stop():\n    raise RuntimeError\nflag and stop()\ngetattr(mod, '_helper')\n",
                "later-tuple-operand-sees-completed-global-write": "NAME = 'public'\ndef switch():\n    global NAME\n    NAME = '_helper'\n(switch(), getattr(mod, NAME))\nNAME = 'public'\n",
                "helper-argument-captured-before-later-argument-write": "NAME = '_helper'\ndef switch():\n    global NAME\n    NAME = 'public'\ndef use(name, ignored):\n    getattr(mod, name)\nuse(NAME, switch())\n",
                "callee-alias-captured-before-argument-write": "read = getattr\ndef switch():\n    global read\n    read = public_function\nread(mod, '_helper', switch())\n",
                "lambda-default-keeps-value-before-argument-effect": "NAME = '_helper'\ndef switch():\n    global NAME\n    NAME = 'public'\n(lambda ignored, name=NAME: getattr(mod, name))(switch())\nNAME = 'public'\n",
                "conditional-expression-selected-private-arm-is-checked": "getattr(mod, '_helper') if True else None\n",
                "conditional-expression-skipped-raise-keeps-private-else": "def stop():\n    raise ValueError()\nstop() if False else getattr(mod, '_helper')\n",
                "conditional-unknown-raising-body-keeps-normal-else": "def stop():\n    raise ValueError()\nstop() if flag else None\ngetattr(mod, '_helper')\n",
                "conditional-unknown-raising-else-keeps-normal-body": "def stop():\n    raise ValueError()\nNone if flag else stop()\ngetattr(mod, '_helper')\n",
                "loop-nested-conditional-mapping-keeps-stable-state-reject": "for item in items:\n    data = {'config': {'error': 'public'}} if flag else None\ngetattr(mod, '_helper')\n",
                "loop-recursive-card-mapping-keeps-bounded-state-reject": "node = {'type': 'tile'}\nfor item in range(limit):\n    node = {'type': 'vertical-stack', 'cards': [node]}\ngetattr(mod, '_helper')\n",
                "loop-compound-tuple-elements-keep-stable-state-reject": "ARGS = (mod, 'public')\nfor item in items:\n    ARGS = (mod, '_helper' + '')\ngetattr(*ARGS)\n",
                "canonical-loop-expression-keeps-earlier-value-snapshot-reject": "NAME = '_helper'\ndef capture():\n    global ARGS\n    ARGS = (mod, NAME + '')\ncapture()\nSAVED = ARGS\nNAME = 'public'\ncapture()\ngetattr(*SAVED)\nNAME = 'public'\n",
                "canonical-loop-expression-replays-child-effects-reject": "NAME = 'public'\ndef set_name(name):\n    global NAME\n    NAME = name\nfor marker in ('public', '_helper'):\n    ARGS = (mod, set_name(marker))\ngetattr(mod, NAME)\n",
                "loop-tuple-capture-keeps-reject-state-stable": "NAME = '_helper'\nARGS = (mod, NAME)\nwhile flag:\n    ARGS = (mod, NAME)\ngetattr(*ARGS)\n",
                "assigned-tuple-retains-earlier-private-operand": "NAME = '_helper'\ndef clear():\n    global NAME\n    NAME = 'public'\nARGS = (mod, NAME, clear())\ngetattr(*ARGS)\n",
                "generator-loop-next-iteration-observes-current-global": "NAME = 'public'\ndef values():\n    for item in (1, 2):\n        getattr(mod, NAME)\n        yield None\npending = values()\nnext(pending)\nNAME = '_helper'\nnext(pending)\nNAME = 'public'\n",
                "generator-resumed-exception-enters-handler": "NAME = 'public'\ndef values():\n    try:\n        yield None\n        raise ValueError()\n    except ValueError:\n        getattr(mod, NAME)\npending = values()\nnext(pending)\nNAME = '_helper'\nnext(pending)\nNAME = 'public'\n",
                "await-unknown-alternative-retains-normal-continuation": "async def stop():\n    raise ValueError()\nasync def test_it():\n    pending = stop() if flag else client.read()\n    await pending\n    getattr(mod, '_helper')\n",
                "generator-access-before-first-yield-is-checked": "NAME = '_helper'\ndef values():\n    getattr(mod, NAME)\n    yield None\npending = values()\nnext(pending)\nNAME = 'public'\n",
                "generator-resume-observes-current-private-global": "NAME = 'public'\ndef values():\n    yield None\n    getattr(mod, NAME)\npending = values()\nnext(pending)\nNAME = '_helper'\nnext(pending)\nNAME = 'public'\n",
                "known-call-replays-nonlocal-write": "def test_it():\n    name = 'public'\n    def switch():\n        nonlocal name\n        name = '_helper'\n    switch()\n    getattr(mod, name)\n",
                "known-call-replays-nonlocal-write-on-raise": "def test_it():\n    name = 'public'\n    def switch():\n        nonlocal name\n        name = '_helper'\n        raise ValueError()\n    try:\n        switch()\n    except ValueError:\n        getattr(mod, name)\n",
                "stored-keyword-tuple-keeps-private-target-shape": "def lookup(args):\n    getattr(*args)\nargs = (mod, '_helper')\nopts = {'args': args}\nlookup(**opts)\n",
                "stored-keyword-list-keeps-private-shape-after-rebind": "def lookup(args):\n    getattr(*args)\nargs = [mod, '_helper']\nopts = {'args': args}\nargs = [mod, 'public']\nlookup(**opts)\n",
                "bounded-recursive-sequence-still-checks-following-target": "args = ('public',)\nfor index in range(100):\n    args = (args,)\nopts = {'args': args}\ngetattr(mod, '_helper')\n",
                "stored-positional-mapping-keeps-private-patch-dict-target": "from unittest.mock import patch\ndef forward(options):\n    with patch.dict(**options):\n        pass\nopts = {'in_dict': 'pkg._cache'}\nargs = (opts,)\nforward(*args)\n",
                "stored-positional-mapping-keeps-private-target-after-rebind": "from unittest.mock import patch\ndef forward(options):\n    with patch.dict(**options):\n        pass\nopts = {'in_dict': 'pkg._cache'}\nargs = (opts,)\nopts = {'in_dict': 'pkg.public'}\nforward(*args)\n",
                "bounded-recursive-mapping-still-checks-following-target": "opts = {'in_dict': {}}\nfor index in range(100):\n    opts = {'in_dict': opts}\nargs = (opts,)\ngetattr(mod, '_helper')\n",
                "generator-next-default-keeps-following-private-access": "def items():\n    yield None\npending = items()\nnext(pending)\nnext(pending, None)\ngetattr(mod, '_helper')\n",
                "generator-repeated-default-after-exhaustion-stays-normal": "def items():\n    yield None\npending = items()\nnext(pending)\nnext(pending, None)\nnext(pending, None)\ngetattr(mod, '_helper')\n",
                "generator-resume-refreshes-enclosing-cell": "def test_it():\n    name = 'public'\n    def items():\n        yield None\n        getattr(mod, name)\n    pending = items()\n    next(pending)\n    name = '_helper'\n    next(pending)\n    name = 'public'\n",
                "generator-independent-instance-checks-before-first-yield": "NAME = 'public'\ndef items():\n    getattr(mod, NAME)\n    yield None\nfirst = items()\nsecond = items()\nnext(first)\nNAME = '_helper'\nnext(second)\nNAME = 'public'\n",
                "generator-copied-alias-shares-cursor": "NAME = 'public'\ndef items():\n    yield None\n    getattr(mod, NAME)\nfirst = items()\nsecond = first\nnext(first)\nNAME = '_helper'\nnext(second)\nNAME = 'public'\n",
                "later-comprehension-iterable-private-target-reject": "[(x, y) for x in (1,) for y in getattr(obj, '_helper')]\n",
                "later-comprehension-filter-private-target-reject": "[(x, y) for x in (1,) for y in (2,) if getattr(obj, '_helper')]\n",
                "later-comprehension-target-binding-reject": "[getattr(obj, name) for x in (1,) for name in ('_helper',)]\n",
                "comprehension-filters-precede-element-reject": "NAME = 'public'\ndef change():\n    global NAME\n    NAME = '_helper'\n    return True\n[getattr(obj, NAME) for x in (1,) for y in (2,) if change()]\n",
                "lazy-parameter-annotation-keeps-definition-continuation-reject": "from __future__ import annotations\ndef stop():\n    raise ValueError()\ndef f(value: stop()):\n    pass\ngetattr(obj, '_helper')\n",
                "lazy-return-annotation-preserves-enclosing-bindings-reject": "NAME = '_helper'\ndef change():\n    global NAME\n    NAME = 'public'\ndef f() -> change():\n    pass\ngetattr(obj, NAME)\n",
                "lazy-annotation-still-checks-private-policy-reject": "from __future__ import annotations\ndef f(value: getattr(obj, '_helper')):\n    pass\n",
                "starred-generator-next-default-preserves-normal-continuation": "def items():\n    yield None\npending = items()\nnext(pending)\narguments = (pending, None)\nnext(*arguments)\ngetattr(mod, '_helper')\n",
                "starred-generator-next-checks-before-first-yield": "NAME = '_helper'\ndef items():\n    getattr(mod, NAME)\n    yield None\npending = items()\nnext(*(pending, None))\nNAME = 'public'\n",
                "generator-live-captured-parameter-preserves-inner-cursor": "def inner(name):\n    yield None\n    getattr(mod, name)\ndef outer(saved):\n    next(saved)\n    yield None\nfor member in ('_helper', 'public'):\n    pending = inner(member)\n    next(pending)\n    if unknown:\n        holder = outer(pending)\nnext(holder)\n",
                "generator-live-continuation-local-preserves-inner-cursor": "def inner(name):\n    yield None\n    getattr(mod, name)\ndef outer(name):\n    saved = inner(name)\n    next(saved)\n    yield None\n    next(saved)\nfor member in ('_helper', 'public'):\n    pending = outer(member)\n    next(pending)\n    if unknown:\n        holder = pending\nnext(holder)\n",
                "discarded-conditional-generator-loop-retains-private-policy": "def items():\n    yield None\nwhile flag:\n    if choose:\n        items()\ngetattr(mod, '_helper')\n",
                "recursive-yield-from-loop-retains-private-policy": "def paths(routes):\n    for route in routes:\n        if route:\n            yield from paths(route)\n        yield route\ngetattr(mod, '_helper')\n",
                "lazy-lambda-annotation-private-body-is-checked": "def f(value: (lambda: getattr(mod, '_helper'))):\n    pass\n",
                "conditional-generator-resume-preserves-following-private-body": "NAME = 'public'\ndef gen():\n    yield None\n    getattr(mod, NAME)\nif flag:\n    pending = gen()\n    next(pending)\nNAME = '_data'\nnext(pending, None)\nNAME = 'public'\n",
                "stored-general-mapping-private": "from unittest.mock import patch\ndef forward(options):\n    with patch.object(**options):\n        pass\nopts = {'target': mod, 'attribute': '_helper'}\nargs = (opts,)\nforward(*args)\n",
                "stored-general-mapping-private-before-rebind": "from unittest.mock import patch\ndef forward(options):\n    with patch.object(**options):\n        pass\nopts = {'target': mod, 'attribute': '_helper'}\nargs = (opts,)\nopts = {'target': mod, 'attribute': 'public'}\nforward(*args)\n",
                "annotation-known-helper-argument-reject": "def lookup(name):\n    return getattr(mod, name, int)\ndef annotated(value: lookup('_helper')):\n    pass\n",
                "annotation-known-helper-global-write-isolated-reject": "NAME = '_helper'\ndef annotation(name):\n    global NAME\n    NAME = name\n    return int\ndef annotated(value: annotation('public')):\n    pass\ngetattr(mod, NAME)\n",
                "annotation-known-helper-nonlocal-write-isolated-reject": "def test_it():\n    name = '_helper'\n    def annotation(value):\n        nonlocal name\n        name = value\n        return int\n    def annotated(value: annotation('public')):\n        pass\n    getattr(mod, name)\n",
                "annotation-known-helper-captured-frame-reject": "def test_it():\n    name = '_helper'\n    def lookup():\n        return getattr(mod, name, int)\n    def annotated(value: lookup()):\n        pass\n    name = 'public'\n",
                "mixed-next-known-fallback-keeps-normal-continuation": "def items():\n    yield None\ndef fallback(value):\n    pass\npending = items()\nnext(pending)\nadvance = next if flag else fallback\nadvance(pending)\ngetattr(mod, '_data')\n",
                "mixed-next-unknown-fallback-keeps-normal-continuation": "def items():\n    yield None\npending = items()\nnext(pending)\nadvance = next if flag else external\nadvance(pending)\ngetattr(mod, '_data')\n",
                "mixed-next-fallback-replays-normal-global-write": "NAME = 'public'\ndef items():\n    yield None\ndef fallback(value):\n    global NAME\n    NAME = '_data'\npending = items()\nnext(pending)\nadvance = next if flag else fallback\nadvance(pending)\ngetattr(mod, NAME)\nNAME = 'public'\n",
                "mixed-next-fallback-replays-raised-global-write": "NAME = 'public'\ndef items():\n    yield None\ndef fallback(value):\n    global NAME\n    NAME = '_data'\n    raise ValueError()\npending = items()\nnext(pending)\nadvance = next if flag else fallback\ntry:\n    advance(pending)\nexcept Exception:\n    getattr(mod, NAME)\nNAME = 'public'\n",
                "next-argument-alternatives-keep-real-default-continuation": "def finite():\n    yield None\ndef broken():\n    yield None\n    raise ValueError()\na = finite()\nb = broken()\nnext(a)\nnext(b)\nnext(*((a, None) if flag else (b, None)))\ngetattr(mod, '_data')\n",
                "assigned-yield-resumes-before-following-reject-lookup": "NAME = 'public'\ndef gen():\n    received = yield None\n    getattr(mod, NAME)\n    yield None\npending = gen()\nnext(pending)\nNAME = '_helper'\nnext(pending)\nNAME = 'public'\n",
                "annotated-assigned-yield-resumes-before-following-reject-lookup": "NAME = 'public'\ndef gen():\n    received: object = yield None\n    getattr(mod, NAME)\n    yield None\npending = gen()\nnext(pending)\nNAME = '_helper'\nnext(pending)\nNAME = 'public'\n",
                "returned-yield-keeps-first-next-continuation-reject": "def gen():\n    return (yield None)\npending = gen()\nnext(pending)\ngetattr(mod, '_helper')\n",
                "generator-expression-resumes-current-binding-reject": "NAME='public'\nitems=(getattr(mod, NAME) for _ in (0,))\nNAME='_helper'\nnext(items)\n",
                "generator-expression-defers-later-iterable-reject": "NAME='public'\nitems=(y for x in (0,) for y in getattr(mod, NAME))\nNAME='_helper'\nnext(items)\n",
                "generator-expression-defers-later-filter-reject": "NAME='public'\nitems=(y for x in (0,) for y in (1,) if getattr(mod, NAME))\nNAME='_helper'\nnext(items)\n",
                "generator-expression-captures-first-iterable-reject": "NAMES=('_helper',)\nitems=(getattr(mod,name) for name in NAMES)\nNAMES=('public',)\nnext(items)\n",
                "generator-expression-first-iterable-effect-once-reject": "NAME='public'\ndef first():\n    global NAME\n    NAME='public'\n    return (0,)\nitems=(None for _ in (first(),))\nNAME='_helper'\nnext(items)\ngetattr(mod,NAME)\n",
                "generator-expression-first-iterable-is-immediate-reject": "items=(None for _ in getattr(mod,'_helper'))\n",
                "for-loop-consumes-generator-expression-reject": "NAME='public'\nitems=(getattr(mod,NAME) for _ in (0,))\nNAME='_helper'\nfor _ in items:\n    pass\n",
                "comprehension-consumes-generator-expression-reject": "NAME='public'\nitems=(getattr(mod,NAME) for _ in (0,))\nNAME='_helper'\nvalues=[None for _ in items]\n",
                "list-comprehension-remains-eager-reject": "NAME='_helper'\nitems=[getattr(mod,NAME) for _ in (0,)]\nNAME='public'\n",
                "tuple-consumes-all-generator-expression-items-reject": "items=(getattr(mod,name) for name in ('public','_helper'))\ntuple(items)\n",
                "generator-expression-for-break-stops-consumption-reject": "items=(getattr(mod,name) for name in ('_helper','public'))\nfor _ in items:\n    break\n",
                "unknown-generator-expression-list-converges-reject": "items=(item for item in values)\nlist(items)\ngetattr(mod, '_helper')\n",
                "unknown-generator-expression-for-converges-reject": "items=(item for item in values)\nfor _ in items:\n    pass\ngetattr(mod, '_helper')\n",
            }.items()
        ),
    ],
)
def test_reflective_private_access_fails_with_a_source_location(tmp_path, source):
    result = _run(tmp_path, source)
    assert result.returncode == 1, result.stderr
    assert re.search(
        r"^tests/src/unit/test_sample\.py:[1-9]\d*: private string target ",
        result.stdout,
        re.MULTILINE,
    ), result.stdout


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
        'TARGET = "public"\nclass Example:\n    TARGET = "_data"\n    def method(self):\n        getattr(mod, TARGET)\n',
        'TARGET = "_data"\nclass Example:\n    TARGET = "public"\n    def method(self, value=getattr(mod, TARGET)):\n        pass\n',
        'TARGET = "ha_mcp.mod._data"\nclass Example:\n    TARGET = "ha_mcp.mod.public"\n    @patch(TARGET)\n    def method(self):\n        pass\n',
        'NAME = "_data"\ndef test_it():\n    class NAME:\n        pass\n    getattr(mod, NAME)\n',
        'from builtins import getattr as read\nclass read:\n    pass\nread(None, "_data")\n',
        'from builtins import getattr as read\nclass Example:\n    def read(value=read(mod, "public")):\n        pass\n    read(None, "_data")\n',
        'from builtins import getattr as read\nclass Example:\n    class read(read(mod, "public")):\n        pass\n    read(None, "_data")\n',
        'from builtins import getattr as read\nclass Example:\n    def read(*args):\n        pass\n    @read(None, "_data")\n    def other():\n        pass\n',
        'NAME = "_data"\nclass Example:\n    def NAME():\n        pass\n    getattr(mod, NAME)\n',
        'from builtins import getattr as read\nclass Example:\n    read = public_callback\n    def method(value=read(None, "_data")):\n        pass\n',
        'from builtins import getattr as read\nclass Example:\n    read = lambda value=read(mod, "public"): value\n',
        'TARGET = "public"\nclass Example:\n    TARGET = "_data"\n    def method(value=[getattr(mod, TARGET) for index in (1,)]):\n        pass\n',
        'TARGET = "public"\nclass Example:\n    TARGET = "_data"\n    def method(value=lambda: getattr(mod, TARGET)):\n        pass\n',
        *(
            pytest.param(source, id=case_id)
            for case_id, source in {
                "conditional-instance-stores-keep-class-inheritance-live": "NAME = 'public'\nclass Example:\n    @staticmethod\n    def run():\n        getattr(mod, NAME)\nfirst = Example()\nsecond = Example()\nif flag:\n    selected = first if choice else second\n    selected.run = callback\nExample.run = other_callback\nNAME = '_data'\nfirst.run()\nsecond.run()\nNAME = 'public'\n",
                "known-local-class-patch-is-not-unittest-patch": "class Example:\n    @staticmethod\n    def patch(data):\n        pass\nExample.patch('_data')\n",
                "class-member-store-replaces-deferred-method": "NAME = 'public'\nclass Example:\n    @staticmethod\n    def run():\n        getattr(mod, NAME)\nNAME = '_helper'\nExample.run = callback\nExample.run()\nNAME = 'public'\n",
                "instance-member-store-replaces-only-that-method": "NAME = 'public'\nclass Example:\n    @staticmethod\n    def run():\n        getattr(mod, NAME)\nNAME = '_helper'\ninstance = Example()\ninstance.run = callback\ninstance.run()\nNAME = 'public'\n",
                "known-class-method-keeps-public-call-time-global": "NAME = '_data'\nclass Example:\n    @staticmethod\n    def run():\n        getattr(mod, NAME)\nNAME = 'public'\nExample.run()\n",
                "unrelated-class-call-does-not-run-another-method": "NAME = '_data'\nclass First:\n    def run(self):\n        getattr(mod, NAME)\nclass Second:\n    def run(self):\n        getattr(mod, 'public')\nSecond().run()\nNAME = 'public'\n",
                "rebound-class-name-is-not-the-old-constructor": "NAME = '_data'\nclass Example:\n    def run(self):\n        getattr(mod, NAME)\nExample = factory\nExample().run()\nNAME = 'public'\n",
                "known-instance-method-own-object-stays-exempt": "class Example:\n    def run(self):\n        getattr(self, '_own')\ninstance = Example()\ninstance.run()\n",
                "async-creation-does-not-run-body-before-final-binding": "NAME = '_data'\nasync def helper():\n    getattr(mod, NAME)\npending = helper()\nNAME = 'public'\n",
                "immediate-lambda-does-not-replay-after-later-private-store": "NAME = 'public'\n(lambda: getattr(mod, NAME))()\nNAME = '_data'\n",
                "deferred-sibling-call-ignores-callers-private-local": "def outer():\n    NAME = 'public'\n    def inner():\n        getattr(mod, NAME)\n    def caller():\n        NAME = '_data'\n        inner()\n    caller()\n",
                "actual-import-replaces-parameter-callable-shadow": "def test_it(range):\n    from builtins import range\n    for item in range(0):\n        getattr(mod, '_data')\n",
                "deferred-lambda-uses-final-public-global": "NAME = '_data'\nrun = lambda: getattr(mod, NAME)\nNAME = 'public'\n",
                "deferred-async-uses-final-public-global": "NAME = '_data'\nasync def test_it():\n    getattr(mod, NAME)\nNAME = 'public'\n",
                "deferred-returned-function-uses-final-public-closure": "def outer():\n    NAME = '_data'\n    def inner():\n        getattr(mod, NAME)\n    NAME = 'public'\n    return inner\n",
                "deferred-returned-method-uses-final-enclosing-cell": "NAME = 'public'\ndef outer():\n    NAME = '_data'\n    class TestExample:\n        NAME = '_data'\n        def test_it(self):\n            getattr(mod, NAME)\n    NAME = 'public'\n    return TestExample\n",
                "literal-star-default-is-not-attribute": "getattr(*(obj, 'public'), '_default')\n",
                "literal-star-own-object-private-attribute": "def test_it(self):\n    getattr(*(self, '_helper'))\n",
                "unknown-star-does-not-fix-later-positions": "getattr(*arguments, '_data')\n",
                "literal-prefix-before-unknown-star-remains-known": "getattr(*(obj, 'public', *defaults))\n",
                "positional-patch-dict-does-not-resolve-data-keys": "patch.dict({}, **{f\"{'x':>5000}\": 1})\n",
                "starred-positional-patch-dict-keeps-data-keys-lazy": "patch.dict(*({},), **{f\"{'x':>5000}\": 1})\n",
                "explicit-patch-dict-target-keeps-data-keys-lazy": "patch.dict(in_dict={}, **{f\"{'x':>5000}\": 1})\n",
                "unshadowed-builtin-range-empty-body-unreachable": "for item in range(0):\n    getattr(obj, '_data')\n",
                "aliased-builtin-range-empty-body-unreachable": "from builtins import range as steps\nfor item in steps(0):\n    getattr(obj, '_data')\n",
                "shadowed-range-public-body": "def test_it(range):\n    for item in range(0):\n        getattr(obj, 'public')\n",
                "literal-match-definite-mismatch-stays-unreachable": "match ('different',):\n    case ['public']:\n        getattr(obj, '_data')\n",
                "or-pattern-first-match-public-capture": "match ('_data', 'public'):\n    case ('_data', name) | (name, 'public'):\n        getattr(obj, name)\n",
                "or-pattern-skips-definite-mismatch-capture": "match ('_data', 'public'):\n    case (name, 'wrong') | ('_data', name):\n        getattr(obj, name)\n",
                "deferred-module-body-drops-former-private-global": "NAME = '_data'\ndef test_it():\n    getattr(mod, NAME)\nNAME = 'public'\n",
                "deferred-nested-body-drops-former-private-closure": "def outer():\n    NAME = '_data'\n    def inner():\n        getattr(mod, NAME)\n    NAME = 'public'\n    inner()\n",
                "deferred-method-drops-former-private-global": "NAME = '_data'\nclass TestExample:\n    def test_it(self):\n        getattr(mod, NAME)\nNAME = 'public'\n",
                "deferred-default-keeps-public-definition-state": "NAME = 'public'\ndef test_it(value=getattr(mod, NAME)):\n    pass\nNAME = '_data'\n",
                "deferred-decorator-keeps-public-definition-state": "NAME = 'public'\n@getattr(mod, NAME)\ndef test_it():\n    pass\nNAME = '_data'\n",
                "deferred-unrelated-call-does-not-run-test-early": "NAME = '_data'\ndef test_it():\n    getattr(mod, NAME)\nother()\nNAME = 'public'\n",
                "partial-delete-failure-before-builtin-restored": "getattr = callback\ntry:\n    del {}['missing'], getattr\nexcept KeyError:\n    getattr(mod, '_data')\n",
                "nonbinding-assignment-does-not-expand-data": "matrix[\"cases\"] = ('a0', 'a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7', 'a8', 'a9', 'a10', 'a11', 'a12', 'a13', 'a14', 'a15', 'a16') + ('b0', 'b1', 'b2', 'b3', 'b4', 'b5', 'b6', 'b7', 'b8', 'b9', 'b10', 'b11', 'b12', 'b13', 'b14', 'b15', 'b16')\n",
                "dispatch-methods-are-not-patch": 'dispatch.dict("_data")\ndispatch.object(obj, "_data")\ndispatch.multiple(obj, _data=value)\n',
                "unpacked-patch-dict-replacement-data": 'patch.dict(**{"in_dict": "ha_mcp.module.cache", "values": {"_data": "_value"}})\n',
                "aliased-unpacked-public-patch-dict": 'OPTIONS = {"in_dict": "ha_mcp.module.cache", "values": {"_data": 1}}\npatch.dict(**OPTIONS)\n',
                "unpacked-own-object-stays-exempt": 'patch.object(**{"target": self, "attribute": "_own_helper", "new": replacement})\n',
                "conditional-reachable-public-string": "name = '_data' if False else 'public'\ngetattr(mod, name)\n",
                "conditional-both-public-strings": "name = 'public' if flag else 'other'\ngetattr(mod, name)\n",
                "conditional-private-data-container": "data = ('_data',) if flag else {'_metadata': 1}\npatch.dict(data, {'_replacement': 2})\n",
                "concatenated-public-iterable": "FIRST = ('pub',)\nSECOND = ('lic',)\nfor name in FIRST + SECOND:\n    getattr(mod, name)\n",
                "concatenated-empty-private-data": "EMPTY = ()\nDATA = EMPTY + ('_data',)\npatch.dict({}, {'payload': DATA})\n",
                "formatted-public-fill": "getattr(mod, f\"{'helper':x>7}\")\n",
                "formatted-repr-quotes-private-looking-value": "getattr(mod, f\"{'_data'!r}\")\n",
                "formatted-ascii-quotes-private-looking-value": "getattr(mod, f\"{'_data'!a}\")\n",
                "function-unpack-private-other-element": "def test_it():\n    name, payload = ('public', '_data')\n    getattr(mod, name)\n",
                "named-tuple-concatenation-does-not-cross-product": "FIRST = ('first_0', 'first_1', 'first_2', 'first_3', 'first_4', 'first_5', 'first_6', 'first_7', 'first_8', 'first_9', 'first_10', 'first_11', 'first_12', 'first_13', 'first_14', 'first_15', 'first_16')\nSECOND = ('second_0', 'second_1', 'second_2', 'second_3', 'second_4', 'second_5', 'second_6', 'second_7', 'second_8', 'second_9', 'second_10', 'second_11', 'second_12', 'second_13', 'second_14', 'second_15', 'second_16')\nCASES = FIRST + SECOND\n",
                "formatted-signed-literal-public-fill": "getattr(mod, f'{-1:x>3}')\ngetattr(mod, f'{+1:x>2}')\n",
                "unrelated-call-does-not-resolve-format-targets": "print(f\"{'x':>5000}\")\n",
                "reflection-object-data-is-not-an-attribute-name": 'getattr(f"{\'x\':>5000}", "public")\n',
                "keyword-mapping-own-self-target": "def test_it(self):\n    opts = {'target': self, 'attribute': '_helper'}\n    patch.object(**opts)\n",
                "keyword-mapping-own-cls-target": "def test_it(cls):\n    opts = {'target': cls, 'attribute': '_helper'}\n    copy = opts\n    patch.object(**copy)\n",
                "keyword-mapping-all-own-target-alternatives": "def test_it(self, cls):\n    opts = {'target': self if flag else cls, 'attribute': '_helper'}\n    patch.object(**opts)\n",
                "keyword-mapping-setattr-dotted-replacement-data": "opts = {'target': 'pkg.public', 'name': '_data'} if flag else {'target': obj, 'name': 'public', 'value': '_data'}\nmonkeypatch.setattr(**opts)\n",
                "keyword-overlay-definite-public-key-replaces-private": "opts = {'in_dict': 'pkg._cache', **({'in_dict': 'pkg.cache'} if flag else {'in_dict': 'pkg.other'})}\npatch.dict(**opts)\n",
                "dictionary-format-data-remains-lazy": "DATA = {'label': f\"{'x':>5000}\"}\n",
                "patch-dictionary-format-data-remains-lazy": "DATA = {'label': f\"{'x':>5000}\"}\npatch.dict({}, DATA)\n",
                "keyword-replacement-format-data-remains-lazy": "opts = {'target': 'pkg.public', 'new': f\"{'x':>5000}\"}\npatch(**opts)\n",
                "unused-oversized-scalar-remains-data": "LABEL = f\"{'x':>5000}\"\nCOPY = LABEL\n",
                "unused-oversized-iteration-remains-data": "LABELS = (f\"{'x':>5000}\",)\nfor label in LABELS:\n    pass\n",
                "replacement-conditionals-stay-lazy": "patch.multiple(mod, field_0=(1 if flag_0 else 2), field_1=(1 if flag_1 else 2), field_2=(1 if flag_2 else 2), field_3=(1 if flag_3 else 2), field_4=(1 if flag_4 else 2), field_5=(1 if flag_5 else 2), field_6=(1 if flag_6 else 2), field_7=(1 if flag_7 else 2), field_8=(1 if flag_8 else 2))\n",
                "monkeypatch-own-object-or-dotted-replacement-data": "def test_it(self):\n    monkeypatch.setattr(self if flag else 'pkg.public', '_data')\n",
                "oversized-mapping-key-is-data-for-patch-dict": "DATA = {f\"{'x':>5000}\": 1}\npatch.dict(DATA, {})\n",
                "monkeypatch-string-object-public-names": "monkeypatch.setattr('public', 'public', '_data')\nmonkeypatch.delattr('public', 'public')\n",
                "class-prior-public-rhs": 'TARGET = "_data"\nclass Example:\n    TARGET = "public"\n    TARGET = getattr(mod, TARGET)\n',
                "class-public-rhs": 'TARGET = "public"\nclass Example:\n    TARGET = getattr(mod, TARGET)\n',
                "class-definite-public-overwrite": 'TARGET = "public"\nclass Example:\n    TARGET = "_data"\n    TARGET = "public"\n    getattr(mod, TARGET)\n',
                "class-public-within-branch": 'TARGET = "_data"\nclass Example:\n    if flag:\n        TARGET = "public"\n        getattr(mod, TARGET)\n',
                "class-independent-else": 'TARGET = "public"\nclass Example:\n    if flag:\n        TARGET = "_data"\n    else:\n        TARGET = "public"\n        getattr(mod, TARGET)\n',
                "class-public-or-mapping": 'TARGET = "ha_mcp.module.cache"\nclass Example:\n    if flag:\n        TARGET = {"_data": 1}\n    patch.dict(TARGET, {})\n',
                "class-both-branches-public": 'TARGET = "_data"\nclass Example:\n    if flag:\n        TARGET = "public"\n    else:\n        TARGET = "also_public"\n    getattr(mod, TARGET)\n',
                "class-imported-value": 'TARGET = "_data"\nclass Example:\n    from constants import TARGET\n    getattr(mod, TARGET)\n',
                "class-with-bound-value": 'TARGET = "_data"\nclass Example:\n    with context as TARGET:\n        getattr(mod, TARGET)\n',
                "class-match-capture": 'TARGET = "_data"\nclass Example:\n    match value:\n        case TARGET:\n            getattr(mod, TARGET)\n',
                "class-mapping-data": 'CACHE = "ha_mcp.module._cache"\nclass Example:\n    CACHE = {"_data": 1}\n    patch.dict(CACHE, {})\n',
                "class-public-within-loop": 'TARGET = "_data"\nclass Example:\n    for item in items:\n        TARGET = "public"\n        getattr(mod, TARGET)\n',
                "class-public-within-handler": 'TARGET = "_data"\nclass Example:\n    try:\n        risky()\n    except Exception:\n        TARGET = "public"\n        getattr(mod, TARGET)\n',
                "class-nonempty-iteration": 'TARGET = "_data"\nclass Example:\n    for TARGET in ("public",):\n        pass\n    getattr(mod, TARGET)\n',
                "class-exhaustive-match": 'TARGET = "_data"\nclass Example:\n    match value:\n        case _:\n            TARGET = "public"\n    getattr(mod, TARGET)\n',
                "class-exception-name-public-fallback": 'TARGET = "public"\nclass Example:\n    TARGET = "_data"\n    try:\n        raise Exception()\n    except Exception as TARGET:\n        pass\n    getattr(mod, TARGET)\n',
                "class-deleted-public-fallback": 'TARGET = "public"\nclass Example:\n    TARGET = "_data"\n    del TARGET\n    getattr(mod, TARGET)\n',
                "class-deleted-public-alias": 'read = public_callback\nclass Example:\n    read = getattr\n    del read\n    read(None, "_data")\n',
                "class-with-keeps-prior-overwrite": 'TARGET = "_data"\nclass Example:\n    TARGET = "public"\n    with context:\n        pass\n    getattr(mod, TARGET)\n',
                "class-finally-public-override": 'TARGET = "_data"\nclass Example:\n    try:\n        risky()\n    finally:\n        TARGET = "public"\n    getattr(mod, TARGET)\n',
                "class-independent-handlers": 'TARGET = "public"\nclass Example:\n    try:\n        risky()\n    except First:\n        TARGET = "_data"\n    except Second:\n        getattr(mod, TARGET)\n',
                "class-both-aliases-public": 'from builtins import getattr as read\nclass Example:\n    if flag:\n        read = public_callback\n    else:\n        read = other_callback\n    read(None, "_data")\n',
                "class-loop-definite-public-prefix": 'TARGET = "public"\nclass Example:\n    while flag:\n        TARGET = "public"\n        getattr(mod, TARGET)\n        TARGET = "_data"\n',
                "class-exceptional-finally-keeps-alias": 'getattr = public_callback\nclass Example:\n    try:\n        raise Exception()\n    finally:\n        getattr(None, "_data")\n',
                "class-bound-name-ignores-private-function-value": 'NAME = "public"\ndef outer():\n    NAME = "_data"\n    class Example:\n        getattr(mod, NAME)\n        NAME = "public"\n',
                "class-free-name-keeps-function-value": 'NAME = "_data"\ndef outer():\n    NAME = "public"\n    class Example:\n        getattr(mod, NAME)\n',
                "class-bound-alias-ignores-function-reflection": 'read = public_callback\ndef outer():\n    from builtins import getattr as read\n    class Example:\n        read(None, "_data")\n        read = public_callback\n',
                "class-literal-unpack-keeps-elements-separate": 'class Example:\n    NAME, data = ["public", "_data"]\n    getattr(mod, NAME)\n',
                "class-literal-unpack-public-alias": 'class Example:\n    read, other = (public_callback, getattr)\n    read(None, "_data")\n',
                "class-single-iteration-import-does-not-run-backwards": 'class Example:\n    read = public_callback\n    for item in (0,):\n        read(None, "_data")\n        from builtins import getattr as read\n',
                "class-single-iteration-value-does-not-run-backwards": 'class Example:\n    NAME = "public"\n    for item in (0,):\n        getattr(mod, NAME)\n        NAME = "_data"\n',
                "class-for-normal-exit-runs-else": 'class Example:\n    NAME = "_data"\n    for item in items:\n        pass\n    else:\n        NAME = "public"\n    getattr(mod, NAME)\n',
                "class-while-normal-exit-runs-else": 'class Example:\n    NAME = "_data"\n    while flag:\n        pass\n    else:\n        NAME = "public"\n    getattr(mod, NAME)\n',
                "class-raised-try-skips-else-before-finally": 'class Example:\n    NAME = "public"\n    try:\n        raise ValueError()\n    except ValueError:\n        pass\n    else:\n        NAME = "_data"\n    finally:\n        getattr(mod, NAME)\n',
                "class-except-star-public-handlers": 'class Example:\n    NAME = "public"\n    try:\n        raise ExceptionGroup("both", [ValueError(), TypeError()])\n    except* ValueError:\n        NAME = "also_public"\n    except* TypeError:\n        getattr(mod, NAME)\n',
                "class-false-match-guard-keeps-public-capture": 'class Example:\n    NAME = "_data"\n    match "public":\n        case NAME if False:\n            pass\n        case _:\n            getattr(mod, NAME)\n',
                "class-whole-subject-public-capture": 'class Example:\n    match "public":\n        case NAME:\n            getattr(mod, NAME)\n',
                "class-earlier-context-keeps-public-target": 'from contextlib import suppress\nclass Example:\n    NAME = "public"\n    with suppress(ValueError), fail() as NAME:\n        pass\n    getattr(mod, NAME)\n',
                "class-literal-unpack-swap-keeps-public-result": 'class Example:\n    NAME = "_data"\n    OTHER = "public"\n    NAME, OTHER = (OTHER, NAME)\n    getattr(mod, NAME)\n',
                "class-loop-evaluates-literal-iterable-once": 'NAME = "public"\nclass Example:\n    for item in (NAME, NAME):\n        getattr(mod, item)\n        NAME = "_data"\n',
                "class-nonempty-set-loop-replaces-inherited-value": 'NAME = "_data"\nclass Example:\n    for NAME in {"public", "also_public"}:\n        pass\n    getattr(mod, NAME)\n',
                "class-global-name-ignores-private-enclosing-value": 'NAME = "public"\ndef outer():\n    NAME = "_data"\n    class Example:\n        global NAME\n        getattr(mod, NAME)\n',
                "class-nonlocal-name-ignores-private-module-value": 'NAME = "_data"\ndef outer():\n    NAME = "public"\n    class Example:\n        nonlocal NAME\n        getattr(mod, NAME)\n        NAME = "public"\n',
                "class-deleted-nonlocal-alias-stays-unbound": 'def outer():\n    getattr = public_callback\n    class Example:\n        nonlocal getattr\n        del getattr\n        getattr(None, "_data")\n',
                "class-deleted-global-name-does-not-restore-old-value": 'NAME = "_data"\nclass Example:\n    global NAME\n    del NAME\n    getattr(mod, NAME)\n',
                "class-deleted-nonlocal-name-does-not-restore-old-value": 'NAME = "public"\ndef outer():\n    NAME = "_data"\n    class Example:\n        nonlocal NAME\n        del NAME\n        getattr(mod, NAME)\n',
                "literal-sequence-public-capture": "class Example:\n    match ('public', '_data'):\n        case [name, payload]:\n            getattr(mod, name)\n",
                "nested-sequence-public-capture": "class Example:\n    match [('_data', 'public')]:\n        case [[payload, name]]:\n            getattr(mod, name)\n",
                "sequence-public-callable-capture": "class Example:\n    match (callback,):\n        case [lookup]:\n            lookup(mod, '_data')\n",
                "class-definite-break-skips-loop-else": "class E:\n    NAME = 'public'\n    for item in (0,):\n        break\n    else:\n        NAME = '_data'\n    getattr(mod, NAME)\n",
                "class-continue-skips-private-access": "class E:\n    for item in (0,):\n        continue\n        getattr(mod, '_data')\n",
                "class-break-skips-private-access": "class E:\n    for item in (0,):\n        break\n        getattr(mod, '_data')\n",
                "class-ordinary-write-stays-in-class": "NAME = 'public'\nclass E:\n    NAME = '_data'\ngetattr(mod, NAME)\n",
                "class-definite-global-write-removes-old-private-value": "NAME = '_data'\nclass E:\n    global NAME\n    NAME = 'public'\ngetattr(mod, NAME)\n",
                "class-global-branch-does-not-affect-sibling": "NAME = 'public'\nclass E:\n    if flag:\n        global NAME\n        NAME = '_data'\n    else:\n        getattr(mod, NAME)\n",
                "class-finally-continue-skips-remainder": "class E:\n    for item in (0,):\n        try:\n            pass\n        finally:\n            continue\n        getattr(mod, '_data')\n",
                "class-nested-break-does-not-skip-outer-else": "class E:\n    NAME = '_data'\n    for item in (0,):\n        for inner in (0,):\n            break\n    else:\n        NAME = 'public'\n    getattr(mod, NAME)\n",
                "class-global-write-stays-public-before-later-assignment": "NAME = 'public'\nclass E:\n    global NAME\n    NAME = 'public'\ngetattr(mod, NAME)\nNAME = '_data'\n",
                "mixed-local-binding-discards-replaced-private-fallback": "NAME = '_data'\nclass Example:\n    if flag:\n        NAME = 'public'\n    class Nested:\n        global NAME\n        NAME = 'public'\n    getattr(mod, NAME)\n",
                "definite-module-store-replaces-class-global": "NAME = 'public'\nclass Example:\n    global NAME\n    NAME = '_helper'\nNAME = 'public'\ngetattr(mod, NAME)\n",
                "chained-store-interruption-before-name": "class Example:\n    NAME = 'public'\n    try:\n        holder[0] = NAME = '_data'\n    except ValueError:\n        getattr(mod, NAME)\n",
                "iterator-next-header-public-control": "class Example:\n    NAME = 'public'\n    try:\n        for item in items:\n            NAME = 'public'\n    except ValueError:\n        getattr(mod, NAME)\n",
                "while-next-header-public-control": "class Example:\n    NAME = 'public'\n    try:\n        while condition():\n            NAME = 'public'\n    except ValueError:\n        getattr(mod, NAME)\n",
                "literal-range-stops-after-three-iterations": "class Example:\n    label = ''\n    for index in range(3):\n        label = label + 'x'\n    getattr(mod, 'public')\n",
                "literal-empty-range-skips-private-body": "class Example:\n    NAME = 'public'\n    for index in range(0):\n        NAME = '_data'\n    getattr(mod, NAME)\n",
                "known-literal-pattern-mismatch-skips-capture": "class Example:\n    match '_data':\n        case 'public' as name:\n            getattr(mod, name)\n",
                "with-exit-error-public-control": "class Example:\n    NAME = 'public'\n    try:\n        with manager():\n            NAME = 'public'\n    except ValueError:\n        getattr(mod, NAME)\n",
                "function-inherited-global-drops-old-private": "NAME = '_data'\ndef test_it():\n    class Example:\n        global NAME\n        NAME = 'public'\n    getattr(mod, NAME)\n",
                "function-local-stays-independent-of-class-global": "NAME = 'public'\ndef test_it():\n    NAME = 'public'\n    class Example:\n        global NAME\n        NAME = '_helper'\n    getattr(mod, NAME)\n",
                "return-stops-unreachable-private-access": "def test_it():\n    return\n    getattr(mod, '_data')\n",
                "wrapper-walk-converges-without-reflection": "class Example:\n    fn = callback\n    while hasattr(fn, '__wrapped__'):\n        fn = fn.__wrapped__\n    result = fn\n",
                "generator-creation-does-not-use-creation-time-global": "NAME = '_data'\ndef values():\n    yield getattr(mod, NAME)\npending = values()\nNAME = 'public'\nnext(pending)\n",
                "known-helper-public-argument-stays-public": "def lookup(name):\n    return getattr(mod, name)\nlookup('public')\n",
                "known-call-without-global-writes-preserves-caller": "NAME = 'public'\ndef helper():\n    NAME = '_data'\n    return NAME\nhelper()\ngetattr(mod, NAME)\n",
                "known-call-replaces-old-private-global": "NAME = '_data'\ndef switch():\n    global NAME\n    NAME = 'public'\nswitch()\ngetattr(mod, NAME)\n",
                "named-coroutine-creation-alone-does-not-execute": "NAME = 'public'\nasync def helper():\n    getattr(mod, NAME)\nasync def test_it():\n    global NAME\n    pending = helper()\n    NAME = '_data'\n    NAME = 'public'\n",
                "single-loop-allocation-keeps-definite-replacement": "NAME = 'public'\nclass Example:\n    def run(self):\n        getattr(mod, NAME)\nfor item in (0,):\n    current = Example()\ncurrent.run = callback\nNAME = '_data'\ncurrent.run()\nNAME = 'public'\n",
                "helper-default-keeps-definition-time-public-value": "NAME = 'public'\ndef lookup(name=NAME):\n    getattr(mod, name)\nNAME = '_data'\nlookup()\n",
                "known-callable-argument-can-be-an-ordinary-callback": "def callback(*args):\n    pass\ndef lookup(read):\n    read(mod, '_data')\nlookup(callback)\n",
                "named-coroutine-keeps-public-argument-before-rebind": "async def lookup(name):\n    getattr(mod, name)\nasync def test_it():\n    name = 'public'\n    pending = lookup(name)\n    name = '_data'\n    await pending\n",
                "definitely-raising-call-has-no-normal-continuation": "def stop():\n    raise ValueError()\ntry:\n    stop()\n    getattr(mod, '_data')\nexcept ValueError:\n    pass\n",
                "known-star-tuple-keeps-private-default-out-of-target": "args = (obj, 'public')\ngetattr(*args, '_default')\n",
                "unknown-star-still-stops-positional-slots": "args = unknown\ngetattr(*args, '_data')\n",
                "known-star-tuple-snapshots-public-name": "name = 'public'\nargs = (obj, name)\nname = '_data'\ngetattr(*args)\n",
                "patch-dict-expanded-target-ignores-large-replacement-key": "from unittest.mock import patch\npatch.dict(**{'in_dict': {}, f\"{'x':>5000}\": 1})\n",
                "patch-dict-aliased-target-ignores-large-replacement-key": "from unittest.mock import patch\nkwargs = {'in_dict': {}, f\"{'x':>5000}\": 1}\npatch.dict(**kwargs)\n",
                "patch-dict-definite-overlay-replaces-private-target": "from unittest.mock import patch\nkwargs = {'in_dict': 'pkg._data', f\"{'x':>5000}\": 1, **{'in_dict': {}}}\npatch.dict(**kwargs)\n",
                "patch-dict-conditional-key-keeps-public-target": "from unittest.mock import patch\nkey = 'in_dict' if flag else f\"{'x':>5000}\"\npatch.dict(**{key: {}})\n",
                "patch-dict-large-width-key-keeps-private-looking-replacement-data": "from unittest.mock import patch\npatch.dict(**{'in_dict': {}, f\"{'in_dict':>5000}\": 'pkg._data'})\n",
                "helper-bound-method-argument-keeps-public-target": "NAME = 'public'\nclass Example:\n    def run(self):\n        getattr(mod, NAME)\ninstance = Example()\ndef lookup(read):\n    read()\nlookup(instance.run)\nNAME = 'public'\n",
                "helper-tuple-argument-keeps-public-target": "def lookup(args):\n    getattr(*args)\nargs = (mod, 'public')\nlookup(args)\n",
                "raising-call-skips-following-private-expression": "def stop(value):\n    raise RuntimeError\nstop(None)\ngetattr(mod, '_helper')\n",
                "true-and-preserves-executed-interruption": "def stop():\n    raise RuntimeError\nTrue and stop()\ngetattr(mod, '_helper')\n",
                "false-or-preserves-executed-interruption": "def stop():\n    raise RuntimeError\nFalse or stop()\ngetattr(mod, '_helper')\n",
                "short-circuited-private-operand-is-not-evaluated": "False and getattr(mod, '_helper')\n",
                "later-tuple-operand-sees-completed-public-write": "NAME = '_helper'\ndef switch():\n    global NAME\n    NAME = 'public'\n(switch(), getattr(mod, NAME))\n",
                "helper-public-argument-keeps-value-before-later-write": "NAME = 'public'\ndef switch():\n    global NAME\n    NAME = '_helper'\ndef use(name, ignored):\n    getattr(mod, name)\nuse(NAME, switch())\nNAME = 'public'\n",
                "public-callee-captured-before-argument-replacement": "read = public_function\ndef switch():\n    global read\n    read = getattr\nread(mod, '_helper', switch())\n",
                "lambda-default-keeps-public-value-before-argument-effect": "NAME = 'public'\ndef switch():\n    global NAME\n    NAME = '_helper'\n(lambda ignored, name=NAME: getattr(mod, name))(switch())\nNAME = 'public'\n",
                "conditional-expression-skips-unreachable-private-arm": "getattr(mod, '_data') if False else None\n",
                "conditional-expression-selected-raise-skips-private-else": "def stop():\n    raise ValueError()\nstop() if True else getattr(mod, '_data')\n",
                "conditional-unknown-both-raising-arms-stop-continuation": "def stop():\n    raise ValueError()\nstop() if flag else stop()\ngetattr(mod, '_data')\n",
                "conditional-unknown-raising-arm-keeps-public-continuation": "def stop():\n    raise ValueError()\nstop() if flag else None\ngetattr(mod, 'public')\n",
                "loop-nested-conditional-mapping-keeps-stable-state-allow": "for item in items:\n    data = {'config': {'error': 'public'}} if flag else None\ngetattr(mod, 'public')\n",
                "loop-recursive-card-mapping-keeps-bounded-state-allow": "node = {'type': 'tile'}\nfor item in range(limit):\n    node = {'type': 'vertical-stack', 'cards': [node]}\ngetattr(mod, 'public')\n",
                "loop-compound-tuple-elements-keep-stable-state-allow": "ARGS = (mod, 'public')\nfor item in items:\n    ARGS = (mod, 'public' + '')\ngetattr(*ARGS)\n",
                "canonical-loop-expression-keeps-earlier-value-snapshot-allow": "NAME = 'public'\ndef capture():\n    global ARGS\n    ARGS = (mod, NAME + '')\ncapture()\nSAVED = ARGS\nNAME = '_helper'\ncapture()\ngetattr(*SAVED)\nNAME = 'public'\n",
                "canonical-loop-expression-replays-child-effects-allow": "NAME = 'public'\ndef set_name(name):\n    global NAME\n    NAME = name\nfor marker in ('_helper', 'public'):\n    ARGS = (mod, set_name(marker))\ngetattr(mod, NAME)\n",
                "loop-tuple-capture-keeps-allow-state-stable": "NAME = 'public'\nARGS = (mod, NAME)\nwhile flag:\n    ARGS = (mod, NAME)\ngetattr(*ARGS)\n",
                "assigned-tuple-retains-earlier-public-operand": "NAME = 'public'\ndef change():\n    global NAME\n    NAME = '_helper'\nARGS = (mod, NAME, change())\ngetattr(*ARGS)\nNAME = 'public'\n",
                "generator-loop-yield-defers-following-lookup": "NAME = '_helper'\ndef values():\n    for item in (1,):\n        yield None\n    getattr(mod, NAME)\npending = values()\nnext(pending)\nNAME = 'public'\nnext(pending)\n",
                "generator-yield-defers-finally-until-resume": "NAME = '_helper'\ndef values():\n    try:\n        yield None\n    finally:\n        getattr(mod, NAME)\npending = values()\nnext(pending)\nNAME = 'public'\nnext(pending)\n",
                "definite-named-await-still-has-no-normal-continuation": "async def stop():\n    raise ValueError()\nasync def test_it():\n    pending = stop()\n    await pending\n    getattr(mod, '_helper')\n",
                "generator-first-next-stops-before-private-later-lookup": "NAME = '_helper'\ndef values():\n    yield None\n    getattr(mod, NAME)\npending = values()\nnext(pending)\nNAME = 'public'\nnext(pending)\n",
                "generator-conditional-yield-retains-continuation": "NAME = '_helper'\ndef values():\n    if True:\n        yield None\n    getattr(mod, NAME)\npending = values()\nnext(pending)\nNAME = 'public'\nnext(pending)\n",
                "ordinary-callee-local-does-not-write-enclosing-name": "def test_it():\n    name = 'public'\n    def switch():\n        name = '_data'\n    switch()\n    getattr(mod, name)\n",
                "nonlocal-public-write-replaces-old-private-name": "def test_it():\n    name = '_data'\n    def switch():\n        nonlocal name\n        name = 'public'\n    switch()\n    getattr(mod, name)\n",
                "stored-keyword-list-keeps-public-target-shape": "def lookup(args):\n    getattr(*args)\nargs = [mod, 'public']\nopts = {'args': args}\nlookup(**opts)\n",
                "stored-keyword-tuple-keeps-public-shape-after-rebind": "def lookup(args):\n    getattr(*args)\nargs = (mod, 'public')\nopts = {'args': args}\nargs = (mod, '_data')\nlookup(**opts)\n",
                "unused-recursive-sequence-snapshot-stays-bounded": "args = ('public',)\nfor index in range(100):\n    args = (args,)\nopts = {'args': args}\ngetattr(mod, 'public')\n",
                "stored-positional-mapping-keeps-public-patch-dict-target": "from unittest.mock import patch\ndef forward(options):\n    with patch.dict(**options):\n        pass\nopts = {'in_dict': 'pkg.public'}\nargs = (opts,)\nforward(*args)\n",
                "stored-positional-mapping-keeps-public-target-after-rebind": "from unittest.mock import patch\ndef forward(options):\n    with patch.dict(**options):\n        pass\nopts = {'in_dict': 'pkg.public'}\nargs = (opts,)\nopts = {'in_dict': 'pkg._data'}\nforward(*args)\n",
                "unused-recursive-mapping-snapshot-stays-bounded": "opts = {'in_dict': {}}\nfor index in range(100):\n    opts = {'in_dict': opts}\nargs = (opts,)\ngetattr(mod, 'public')\n",
                "generator-exhaustion-without-default-stops-continuation": "def items():\n    yield None\npending = items()\nnext(pending)\nnext(pending)\ngetattr(mod, '_data')\n",
                "generator-default-does-not-swallow-real-exception": "def items():\n    yield None\n    raise ValueError()\npending = items()\nnext(pending)\nnext(pending, None)\ngetattr(mod, '_data')\n",
                "generator-resume-replaces-captured-private-cell": "def test_it():\n    name = '_data'\n    def items():\n        yield None\n        getattr(mod, name)\n    pending = items()\n    next(pending)\n    name = 'public'\n    next(pending)\n",
                "generator-resume-preserves-own-local-over-enclosing-cell": "def test_it():\n    name = 'public'\n    def items():\n        name = 'public'\n        yield None\n        getattr(mod, name)\n    pending = items()\n    next(pending)\n    name = '_data'\n    next(pending)\n    name = 'public'\n",
                "generator-distinct-creations-start-before-first-yield": "NAME = 'public'\ndef items():\n    yield None\n    getattr(mod, NAME)\nfirst = items()\nsecond = items()\nnext(first)\nNAME = '_data'\nnext(second)\nNAME = 'public'\n",
                "generator-repeated-creation-site-starts-fresh": "NAME = '_data'\ndef items():\n    yield None\n    getattr(mod, NAME)\nfor marker in (1, 2, 3, 4):\n    pending = items()\n    next(pending)\nNAME = 'public'\n",
                "later-comprehension-iterable-private-target-allow": "[(x, y) for x in (1,) for y in getattr(obj, 'public')]\n",
                "later-comprehension-filter-private-target-allow": "[(x, y) for x in (1,) for y in (2,) if getattr(obj, 'public')]\n",
                "later-comprehension-target-binding-allow": "[getattr(obj, name) for x in (1,) for name in ('public',)]\n",
                "comprehension-filters-precede-element-allow": "NAME = '_helper'\ndef change():\n    global NAME\n    NAME = 'public'\n    return True\n[getattr(obj, NAME) for x in (1,) for y in (2,) if change()]\n",
                "lazy-parameter-annotation-keeps-definition-continuation-allow": "from __future__ import annotations\ndef stop():\n    raise ValueError()\ndef f(value: stop()):\n    pass\ngetattr(obj, 'public')\n",
                "lazy-return-annotation-preserves-enclosing-bindings-allow": "NAME = 'public'\ndef change():\n    global NAME\n    NAME = '_helper'\ndef f() -> change():\n    pass\ngetattr(obj, NAME)\n",
                "lazy-annotation-still-checks-private-policy-allow": "from __future__ import annotations\ndef f(value: getattr(obj, 'public')):\n    pass\n",
                "starred-generator-next-without-default-stops-at-exhaustion": "def items():\n    yield None\npending = items()\nnext(*(pending,))\nnext(*(pending,))\ngetattr(mod, '_data')\n",
                "starred-generator-next-default-keeps-real-exception": "def items():\n    yield None\n    raise ValueError()\npending = items()\nnext(pending)\nnext(*(pending, None))\ngetattr(mod, '_data')\n",
                "generator-live-captured-parameter-public-control": "def inner(name):\n    yield None\n    getattr(mod, name)\ndef outer(saved):\n    next(saved)\n    yield None\nfor member in ('visible', 'public'):\n    pending = inner(member)\n    next(pending)\n    if unknown:\n        holder = outer(pending)\nnext(holder)\n",
                "generator-live-continuation-local-public-control": "def inner(name):\n    yield None\n    getattr(mod, name)\ndef outer(name):\n    saved = inner(name)\n    next(saved)\n    yield None\n    next(saved)\nfor member in ('visible', 'public'):\n    pending = outer(member)\n    next(pending)\n    if unknown:\n        holder = pending\nnext(holder)\n",
                "discarded-conditional-generator-loop-converges": "def items():\n    yield None\nwhile flag:\n    if choose:\n        items()\ngetattr(mod, 'public')\n",
                "recursive-yield-from-loop-converges": "def paths(routes):\n    for route in routes:\n        if route:\n            yield from paths(route)\n        yield route\ngetattr(mod, 'public')\n",
                "lazy-lambda-annotation-public-body-is-allowed": "def f(value: (lambda: getattr(mod, 'public'))):\n    pass\n",
                "conditional-generator-resume-does-not-restart-consumed-body": "NAME = 'public'\ndef gen():\n    getattr(mod, NAME)\n    yield None\nif flag:\n    pending = gen()\n    next(pending)\nNAME = '_data'\nnext(pending, None)\nNAME = 'public'\n",
                "stored-general-mapping-public": "from unittest.mock import patch\ndef forward(options):\n    with patch.object(**options):\n        pass\nopts = {'target': mod, 'attribute': 'public'}\nargs = (opts,)\nforward(*args)\n",
                "stored-general-mapping-public-before-rebind": "from unittest.mock import patch\ndef forward(options):\n    with patch.object(**options):\n        pass\nopts = {'target': mod, 'attribute': 'public'}\nargs = (opts,)\nopts = {'target': mod, 'attribute': '_data'}\nforward(*args)\n",
                "annotation-known-helper-argument-allow": "def lookup(name):\n    return getattr(mod, name, int)\ndef annotated(value: lookup('public')):\n    pass\n",
                "annotation-known-helper-global-write-isolated-allow": "NAME = 'public'\ndef annotation(name):\n    global NAME\n    NAME = name\n    return int\ndef annotated(value: annotation('_data')):\n    pass\ngetattr(mod, NAME)\n",
                "annotation-known-helper-nonlocal-write-isolated-allow": "def test_it():\n    name = 'public'\n    def annotation(value):\n        nonlocal name\n        name = value\n        return int\n    def annotated(value: annotation('_data')):\n        pass\n    getattr(mod, name)\n",
                "annotation-known-helper-captured-frame-allow": "def test_it():\n    name = 'public'\n    def lookup():\n        return getattr(mod, name, int)\n    def annotated(value: lookup()):\n        pass\n    name = 'public'\n",
                "known-only-next-still-stops-at-exhaustion": "def items():\n    yield None\npending = items()\nnext(pending)\nadvance = next\nadvance(pending)\ngetattr(mod, '_data')\n",
                "mixed-next-unknown-fallback-public-control": "def items():\n    yield None\npending = items()\nnext(pending)\nadvance = next if flag else external\nadvance(pending)\ngetattr(mod, 'public')\n",
                "mixed-next-fallback-public-normal-write-control": "NAME = 'public'\ndef items():\n    yield None\ndef fallback(value):\n    global NAME\n    NAME = 'visible'\npending = items()\nnext(pending)\nadvance = next if flag else fallback\nadvance(pending)\ngetattr(mod, NAME)\nNAME = 'public'\n",
                "mixed-next-fallback-public-raised-write-control": "NAME = 'public'\ndef items():\n    yield None\ndef fallback(value):\n    global NAME\n    NAME = 'visible'\n    raise ValueError()\npending = items()\nnext(pending)\nadvance = next if flag else fallback\ntry:\n    advance(pending)\nexcept Exception:\n    getattr(mod, NAME)\nNAME = 'public'\n",
                "next-argument-alternatives-do-not-share-default": "def finite():\n    yield None\ndef broken():\n    yield None\n    raise ValueError()\na = finite()\nb = broken()\nnext(a)\nnext(b)\nnext(*((a,) if flag else (b, None)))\ngetattr(mod, '_data')\n",
                "assigned-yield-resumes-before-following-allow-lookup": "NAME = '_data'\ndef gen():\n    received = yield None\n    getattr(mod, NAME)\n    yield None\npending = gen()\nnext(pending)\nNAME = 'public'\nnext(pending)\nNAME = 'public'\n",
                "annotated-assigned-yield-resumes-before-following-allow-lookup": "NAME = '_data'\ndef gen():\n    received: object = yield None\n    getattr(mod, NAME)\n    yield None\npending = gen()\nnext(pending)\nNAME = 'public'\nnext(pending)\nNAME = 'public'\n",
                "returned-yield-keeps-first-next-continuation-allow": "def gen():\n    return (yield None)\npending = gen()\nnext(pending)\ngetattr(mod, 'public')\n",
                "generator-expression-resumes-current-binding-allow": "NAME='_helper'\nitems=(getattr(mod, NAME) for _ in (0,))\nNAME='public'\nnext(items)\n",
                "generator-expression-defers-later-iterable-allow": "NAME='_helper'\nitems=(y for x in (0,) for y in getattr(mod, NAME))\nNAME='public'\nnext(items)\n",
                "generator-expression-defers-later-filter-allow": "NAME='_helper'\nitems=(y for x in (0,) for y in (1,) if getattr(mod, NAME))\nNAME='public'\nnext(items)\n",
                "generator-expression-captures-first-iterable-allow": "NAMES=('public',)\nitems=(getattr(mod,name) for name in NAMES)\nNAMES=('_helper',)\nnext(items)\n",
                "generator-expression-first-iterable-effect-once-allow": "NAME='public'\ndef first():\n    global NAME\n    NAME='_helper'\n    return (0,)\nitems=(None for _ in (first(),))\nNAME='public'\nnext(items)\ngetattr(mod,NAME)\n",
                "generator-expression-first-iterable-is-immediate-allow": "items=(getattr(mod,'_helper') for _ in (0,))\n",
                "for-loop-consumes-generator-expression-allow": "NAME='_helper'\nitems=(getattr(mod,NAME) for _ in (0,))\nNAME='public'\nfor _ in items:\n    pass\n",
                "comprehension-consumes-generator-expression-allow": "NAME='_helper'\nitems=(getattr(mod,NAME) for _ in (0,))\nNAME='public'\nvalues=[None for _ in items]\n",
                "list-comprehension-remains-eager-allow": "NAME='public'\nitems=[getattr(mod,NAME) for _ in (0,)]\nNAME='_helper'\n",
                "tuple-consumes-all-generator-expression-items-allow": "items=(getattr(mod,name) for name in ('visible','public'))\ntuple(items)\n",
                "generator-expression-for-break-stops-consumption-allow": "items=(getattr(mod,name) for name in ('public','_helper'))\nfor _ in items:\n    break\n",
                "unknown-generator-expression-list-converges-allow": "items=(item for item in values)\nlist(items)\ngetattr(mod, 'public')\n",
                "unknown-generator-expression-for-converges-allow": "items=(item for item in values)\nfor _ in items:\n    pass\ngetattr(mod, 'public')\n",
            }.items()
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


def test_multiline_private_access_reports_its_call_line(tmp_path):
    result = _run(tmp_path, "name = '_helper'\n\ngetattr(\n    mod,\n    name,\n)\n")
    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        "tests/src/unit/test_sample.py:3: private string target '_helper'"
        in result.stdout
    )


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
            "opts = {'target': f\"{'x':>5000}\"}\npatch(**opts)\n",
            id="keyword-format-target-still-bounded",
        ),
        pytest.param(
            "LABEL = f\"{'x':>5000}\"\nCOPY = LABEL\ngetattr(mod, COPY)\n",
            id="aliased-oversized-target-still-bounded",
        ),
        pytest.param(
            "DATA = {f\"{'x':>5000}\": 1}\npatch.multiple(mod, **DATA)\n",
            id="oversized-mapping-key-is-target-for-patch-multiple",
        ),
    ],
)
def test_oversized_targets_still_fail_after_lazy_binding(tmp_path, source):
    result = _run(tmp_path, source)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "formatted attribute target exceeds the size limit" in result.stdout


def _nested_finally_source(body, depth, prefix="pass"):
    for _ in range(depth):
        body = f"try:\n{indent(prefix, '    ')}\nfinally:\n{indent(body, '    ')}"
    return "class Example:\n" + indent(body, "    ") + "\n"


@pytest.mark.parametrize("private", [False, True], ids=["public", "private"])
def test_nested_finally_reports_each_source_access_once(tmp_path, private):
    target = "_helper" if private else "public"
    source = _nested_finally_source(f"getattr(mod, {target!r})", 3)
    result = _run(tmp_path, source)
    assert result.returncode == int(private), result.stdout + result.stderr
    assert result.stdout.count(": private string target ") == int(private)


def test_nested_finally_reuses_repeated_states(tmp_path):
    source = _nested_finally_source(
        "getattr(mod, 'public')", 24, "marker = 'a'\nmarker = 'b'"
    )
    try:
        result = _run(tmp_path, source)
    except subprocess.TimeoutExpired as error:
        raise AssertionError(
            "Repeated finally states must finish within the guard timeout"
        ) from error
    assert result.returncode == 0, result.stdout + result.stderr
