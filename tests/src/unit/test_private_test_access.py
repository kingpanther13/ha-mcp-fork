"""The private-string guard rejects access syntax, never unrelated text."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import indent

import pytest

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
            }.items()
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
