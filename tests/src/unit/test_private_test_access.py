"""The private-string guard rejects access syntax, never unrelated text."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import indent

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
        pytest.param(
            'TARGET = "_helper"\nclass Example:\n    TARGET = getattr(mod, TARGET)\n',
            id="class-inherited-rhs",
        ),
        pytest.param(
            'TARGET = "_helper"\nclass Example:\n    TARGET: object = getattr(mod, TARGET)\n',
            id="class-annotated-rhs",
        ),
        pytest.param(
            'TARGET = "_helper"\nclass Example:\n    getattr(mod, TARGET)\n    TARGET = "public"\n',
            id="class-read-before-rebind",
        ),
        pytest.param(
            'TARGET = "_helper"\nclass Example:\n    COPY = TARGET\n    TARGET = "public"\n    getattr(mod, COPY)\n',
            id="class-copy-before-rebind",
        ),
        pytest.param(
            'TARGET = "_helper"\nclass Example:\n    if flag:\n        TARGET = "public"\n    getattr(mod, TARGET)\n',
            id="class-optional-override",
        ),
        pytest.param(
            'TARGET = "public"\nclass Example:\n    if flag:\n        TARGET = "_helper"\n    else:\n        TARGET = "public"\n    getattr(mod, TARGET)\n',
            id="class-value-join",
        ),
        pytest.param(
            'TARGET = "ha_mcp.module._cache"\nclass Example:\n    if flag:\n        TARGET = {"_data": 1}\n    patch.dict(TARGET, {})\n',
            id="class-mapping-string-join",
        ),
        pytest.param(
            'from builtins import getattr as read\nclass Example:\n    if flag:\n        read = public_callback\n    read(mod, "_helper")\n',
            id="class-optional-alias",
        ),
        pytest.param(
            'read = public_callback\nclass Example:\n    if flag:\n        read = getattr\n    else:\n        read = public_callback\n    read(mod, "_helper")\n',
            id="class-alias-join",
        ),
        pytest.param(
            'TARGET = "_helper"\nclass Example:\n    for item in items:\n        TARGET = "public"\n    getattr(mod, TARGET)\n',
            id="class-loop-zero-iterations",
        ),
        pytest.param(
            'TARGET = "public"\nclass Example:\n    while getattr(mod, TARGET):\n        TARGET = "_helper"\n',
            id="class-loop-later-condition",
        ),
        pytest.param(
            'TARGET = "public"\nclass Example:\n    try:\n        TARGET = "_helper"\n        risky()\n        TARGET = "public"\n    except Exception:\n        getattr(mod, TARGET)\n',
            id="class-handler-intermediate-value",
        ),
        pytest.param(
            'TARGET = "_helper"\nclass Example:\n    try:\n        TARGET = "public"\n    except Exception:\n        getattr(mod, TARGET)\n',
            id="class-handler-inherited-value",
        ),
        pytest.param(
            'TARGET = "_helper"\nclass Example:\n    match value:\n        case 0:\n            TARGET = "public"\n    getattr(mod, TARGET)\n',
            id="class-match-no-case",
        ),
        pytest.param(
            'KEYS = {"_helper": 1}\nclass Example:\n    if flag:\n        KEYS = {"public": 2}\n    patch.multiple(mod, **KEYS)\n',
            id="class-mapping-key-join",
        ),
        pytest.param(
            'TARGET = "_helper"\nclass Example:\n    TARGET: str\n    getattr(mod, TARGET)\n',
            id="class-annotation-only",
        ),
        pytest.param(
            'TARGET = "public"\nclass Example:\n    for item in items:\n        TARGET = "_helper"\n        if flag:\n            break\n        TARGET = "public"\n    getattr(mod, TARGET)\n',
            id="class-loop-break-value",
        ),
        pytest.param(
            'TARGET = "public"\nclass Example:\n    try:\n        TARGET = "_helper"\n        risky()\n        TARGET = "public"\n    except Specific:\n        TARGET = "public"\n    finally:\n        getattr(mod, TARGET)\n',
            id="class-finally-unhandled-value",
        ),
        pytest.param(
            'TARGET = "public"\nclass Example:\n    with suppress(Exception):\n        TARGET = "_helper"\n        risky()\n        TARGET = "public"\n    getattr(mod, TARGET)\n',
            id="class-suppressed-intermediate-value",
        ),
        pytest.param(
            'TARGET = "_helper"\nclass Example:\n    TARGET = "public"\n    try:\n        raise Exception()\n    except Exception as TARGET:\n        pass\n    getattr(mod, TARGET)\n',
            id="class-exception-name-fallback",
        ),
        pytest.param(
            'TARGET = "_helper"\nclass Example:\n    TARGET = "public"\n    del TARGET\n    getattr(mod, TARGET)\n',
            id="class-deleted-value-fallback",
        ),
        pytest.param(
            'from builtins import getattr as read\nclass Example:\n    read = public_callback\n    del read\n    read(mod, "_helper")\n',
            id="class-deleted-alias-fallback",
        ),
        pytest.param(
            'TARGET = "public"\nclass Example:\n    try:\n        pass\n    except Specific:\n        pass\n    else:\n        TARGET = "_helper"\n        risky()\n        TARGET = "public"\n    finally:\n        getattr(mod, TARGET)\n',
            id="class-finally-else-intermediate",
        ),
        pytest.param(
            'class Example:\n    try:\n        risky()\n        getattr = public_callback\n    except Exception:\n        getattr(mod, "_helper")\n',
            id="class-handler-before-builtin-rebinding",
        ),
        pytest.param(
            'class Example:\n    getattr = public_callback\n    try:\n        del getattr\n        risky()\n    except Exception:\n        getattr(mod, "_helper")\n',
            id="class-handler-after-builtin-restored",
        ),
        pytest.param(
            'NAME = "_helper"\ndef outer():\n    NAME = "public"\n    class Example:\n        getattr(mod, NAME)\n        NAME = "public"\n',
            id="class-bound-name-uses-module-fallback",
        ),
        pytest.param(
            'from builtins import getattr as read\ndef outer():\n    read = public_callback\n    class Example:\n        read(mod, "_helper")\n        read = public_callback\n',
            id="class-bound-alias-uses-module-fallback",
        ),
        pytest.param(
            'class Example:\n    NAME = "_helper"\n    NAME, count = (NAME, 1)\n    getattr(mod, NAME)\n',
            id="class-literal-unpack-preserves-value",
        ),
        pytest.param(
            'class Example:\n    NAME = "_helper"\n    OTHER = "public"\n    NAME, OTHER = (OTHER, NAME)\n    getattr(mod, OTHER)\n',
            id="class-literal-unpack-snapshots-rhs",
        ),
        pytest.param(
            'class Example:\n    read = getattr\n    read, count = (read, 1)\n    read(mod, "_helper")\n',
            id="class-literal-unpack-preserves-alias",
        ),
        pytest.param(
            "matrix[\"cases\"] = (getattr(mod, \"_helper\"), 'a0', 'a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7', 'a8', 'a9', 'a10', 'a11', 'a12', 'a13', 'a14', 'a15', 'a16') + ('b0', 'b1', 'b2', 'b3', 'b4', 'b5', 'b6', 'b7', 'b8', 'b9', 'b10', 'b11', 'b12', 'b13', 'b14', 'b15', 'b16')\n",
            id="nonbinding-assignment-still-scans-rhs",
        ),
        pytest.param(
            'class Example:\n    read = public_callback\n    for item in (0, 1):\n        read(mod, "_helper")\n        from builtins import getattr as read\n',
            id="class-loop-carries-import-alias",
        ),
        pytest.param(
            'class Example:\n    NAME = "public"\n    for item in (0, 1):\n        getattr(mod, NAME)\n        NAME = "_helper"\n',
            id="class-loop-later-iteration-uses-body-value",
        ),
        pytest.param(
            'class Example:\n    NAME = "_helper"\n    for item in (0,):\n        break\n    else:\n        NAME = "public"\n    getattr(mod, NAME)\n',
            id="class-loop-break-bypasses-else",
        ),
        pytest.param(
            'class Example:\n    NAME = "public"\n    try:\n        pass\n    except ValueError:\n        pass\n    else:\n        NAME = "_helper"\n    finally:\n        getattr(mod, NAME)\n',
            id="class-reachable-try-else-reaches-finally",
        ),
        pytest.param(
            'class Example:\n    NAME = "public"\n    try:\n        raise ExceptionGroup("both", [ValueError(), TypeError()])\n    except* ValueError:\n        NAME = "_helper"\n    except* TypeError:\n        getattr(mod, NAME)\n',
            id="class-except-star-carries-prior-handler",
        ),
        pytest.param(
            'class Example:\n    NAME = "public"\n    match "_helper":\n        case NAME if False:\n            pass\n        case _:\n            getattr(mod, NAME)\n',
            id="class-false-match-guard-keeps-private-capture",
        ),
        pytest.param(
            'class Example:\n    NAME = "_helper"\n    match value:\n        case [NAME] if False:\n            pass\n        case _:\n            getattr(mod, NAME)\n',
            id="class-failed-match-pattern-keeps-input",
        ),
        pytest.param(
            'class Example:\n    match "_helper":\n        case NAME:\n            getattr(mod, NAME)\n',
            id="class-whole-subject-private-capture",
        ),
        pytest.param(
            'from contextlib import suppress\nclass Example:\n    NAME = "_helper"\n    with suppress(ValueError), fail() as NAME:\n        pass\n    getattr(mod, NAME)\n',
            id="class-earlier-context-preserves-unbound-target",
        ),
        pytest.param(
            'class Example:\n    NAME, (count, other) = ("_helper", (1, "public"))\n    getattr(mod, NAME)\n',
            id="class-nested-literal-unpack-preserves-value",
        ),
        pytest.param(
            'NAME = "_helper"\ndef outer():\n    NAME = "public"\n    class Example:\n        NAME = "public"\n        del NAME\n        getattr(mod, NAME)\n',
            id="class-deleted-name-uses-module-not-function-fallback",
        ),
        pytest.param(
            'NAME = "_helper"\ndef outer():\n    NAME = "public"\n    class Example:\n        try:\n            raise ValueError()\n        except ValueError as NAME:\n            pass\n        getattr(mod, NAME)\n',
            id="class-exception-name-uses-module-not-function-fallback",
        ),
        pytest.param(
            'NAMES = ("_helper",)\nclass Example:\n    for name in NAMES:\n        getattr(mod, name)\n',
            id="class-loop-uses-named-iterable-summary",
        ),
        pytest.param(
            'NAME = "_helper"\ndef outer():\n    NAME = "public"\n    class Example:\n        global NAME\n        getattr(mod, NAME)\n',
            id="class-global-name-uses-module-without-local-store",
        ),
        pytest.param(
            'NAME = "public"\ndef outer():\n    NAME = "_helper"\n    class Example:\n        nonlocal NAME\n        getattr(mod, NAME)\n        NAME = "public"\n',
            id="class-nonlocal-name-keeps-enclosing-value",
        ),
        pytest.param(
            'getattr = public_callback\nclass Example:\n    global getattr\n    del getattr\n    getattr(mod, "_helper")\n',
            id="class-deleted-global-alias-falls-back-to-builtin",
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
            "class Example:\n    match ('_helper',):\n        case [name]:\n            getattr(mod, name)\n",
            id="literal-sequence-capture",
        ),
        pytest.param(
            "class Example:\n    match [('public', '_helper')]:\n        case [[other, name]]:\n            getattr(mod, name)\n",
            id="nested-sequence-capture",
        ),
        pytest.param(
            "from builtins import getattr as read\nclass Example:\n    match (read,):\n        case [lookup]:\n            lookup(mod, '_helper')\n",
            id="sequence-capture-callable-alias",
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
            "class Example:\n    name = 'public' if flag else '_helper'\n    getattr(mod, name)\n",
            id="conditional-private-class-string",
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
            "class E:\n    NAME = 'public'\n    for item in (0,):\n        NAME = '_helper'\n        continue\n        NAME = 'public'\n    getattr(mod, NAME)\n",
            id="class-continue-keeps-live-binding",
        ),
        pytest.param(
            "class E:\n    NAME = 'public'\n    for item in (0,):\n        NAME = '_helper'\n        continue\n        NAME = 'public'\n    else:\n        getattr(mod, NAME)\n",
            id="class-continue-reaches-loop-else",
        ),
        pytest.param(
            "class E:\n    getattr = public_callback\n    try:\n        raise ExceptionGroup('group', [ValueError(), TypeError()])\n    except* ValueError as getattr:\n        pass\n    except* TypeError:\n        getattr(mod, '_helper')\n",
            id="class-except-star-cleanup-restores-builtin",
        ),
        pytest.param(
            "class E:\n    NAME = 'public'\n    try:\n        try:\n            raise ValueError()\n        finally:\n            NAME = '_helper'\n    except ValueError:\n        getattr(mod, NAME)\n",
            id="class-exceptional-finally-reaches-outer-handler",
        ),
        pytest.param(
            "NAME = 'public'\nclass E:\n    global NAME\n    NAME = '_helper'\ngetattr(mod, NAME)\n",
            id="class-global-write-reaches-module",
        ),
        pytest.param(
            "def outer():\n    NAME = 'public'\n    class E:\n        nonlocal NAME\n        NAME = '_helper'\n    getattr(mod, NAME)\n",
            id="class-nonlocal-write-reaches-function",
        ),
        pytest.param(
            "NAME = 'public'\nclass E:\n    class Nested:\n        global NAME\n        NAME = '_helper'\n    getattr(mod, NAME)\n",
            id="nested-class-global-write-reaches-parent-class",
        ),
        pytest.param(
            "NAME = 'public'\nclass E:\n    if flag:\n        global NAME\n        NAME = '_helper'\ngetattr(mod, NAME)\n",
            id="class-conditional-global-write-keeps-private-path",
        ),
        pytest.param(
            "NAME = '_helper'\ndef later():\n    class E:\n        global NAME\n        NAME = 'public'\ngetattr(mod, NAME)\n",
            id="deferred-class-global-write-does-not-run-at-definition",
        ),
        pytest.param(
            "NAME = 'public'\nclass E:\n    global NAME\n    NAME = 'public'\nNAME = '_helper'\ngetattr(mod, NAME)\n",
            id="module-assignment-after-class-global-write",
        ),
        pytest.param(
            "def outer():\n    NAME = 'public'\n    class E:\n        nonlocal NAME\n        NAME = 'public'\n    NAME = '_helper'\n    getattr(mod, NAME)\n",
            id="function-assignment-after-class-nonlocal-write",
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
        pytest.param(
            'TARGET = "_data"\nclass Example:\n    TARGET = "public"\n    TARGET = getattr(mod, TARGET)\n',
            id="class-prior-public-rhs",
        ),
        pytest.param(
            'TARGET = "public"\nclass Example:\n    TARGET = getattr(mod, TARGET)\n',
            id="class-public-rhs",
        ),
        pytest.param(
            'TARGET = "public"\nclass Example:\n    TARGET = "_data"\n    TARGET = "public"\n    getattr(mod, TARGET)\n',
            id="class-definite-public-overwrite",
        ),
        pytest.param(
            'TARGET = "_data"\nclass Example:\n    if flag:\n        TARGET = "public"\n        getattr(mod, TARGET)\n',
            id="class-public-within-branch",
        ),
        pytest.param(
            'TARGET = "public"\nclass Example:\n    if flag:\n        TARGET = "_data"\n    else:\n        TARGET = "public"\n        getattr(mod, TARGET)\n',
            id="class-independent-else",
        ),
        pytest.param(
            'TARGET = "ha_mcp.module.cache"\nclass Example:\n    if flag:\n        TARGET = {"_data": 1}\n    patch.dict(TARGET, {})\n',
            id="class-public-or-mapping",
        ),
        pytest.param(
            'TARGET = "_data"\nclass Example:\n    if flag:\n        TARGET = "public"\n    else:\n        TARGET = "also_public"\n    getattr(mod, TARGET)\n',
            id="class-both-branches-public",
        ),
        pytest.param(
            'TARGET = "_data"\nclass Example:\n    from constants import TARGET\n    getattr(mod, TARGET)\n',
            id="class-imported-value",
        ),
        pytest.param(
            'TARGET = "_data"\nclass Example:\n    with context as TARGET:\n        getattr(mod, TARGET)\n',
            id="class-with-bound-value",
        ),
        pytest.param(
            'TARGET = "_data"\nclass Example:\n    match value:\n        case TARGET:\n            getattr(mod, TARGET)\n',
            id="class-match-capture",
        ),
        pytest.param(
            'CACHE = "ha_mcp.module._cache"\nclass Example:\n    CACHE = {"_data": 1}\n    patch.dict(CACHE, {})\n',
            id="class-mapping-data",
        ),
        pytest.param(
            'TARGET = "_data"\nclass Example:\n    for item in items:\n        TARGET = "public"\n        getattr(mod, TARGET)\n',
            id="class-public-within-loop",
        ),
        pytest.param(
            'TARGET = "_data"\nclass Example:\n    try:\n        risky()\n    except Exception:\n        TARGET = "public"\n        getattr(mod, TARGET)\n',
            id="class-public-within-handler",
        ),
        pytest.param(
            'TARGET = "_data"\nclass Example:\n    for TARGET in ("public",):\n        pass\n    getattr(mod, TARGET)\n',
            id="class-nonempty-iteration",
        ),
        pytest.param(
            'TARGET = "_data"\nclass Example:\n    match value:\n        case _:\n            TARGET = "public"\n    getattr(mod, TARGET)\n',
            id="class-exhaustive-match",
        ),
        pytest.param(
            'TARGET = "public"\nclass Example:\n    TARGET = "_data"\n    try:\n        raise Exception()\n    except Exception as TARGET:\n        pass\n    getattr(mod, TARGET)\n',
            id="class-exception-name-public-fallback",
        ),
        pytest.param(
            'TARGET = "public"\nclass Example:\n    TARGET = "_data"\n    del TARGET\n    getattr(mod, TARGET)\n',
            id="class-deleted-public-fallback",
        ),
        pytest.param(
            'read = public_callback\nclass Example:\n    read = getattr\n    del read\n    read(None, "_data")\n',
            id="class-deleted-public-alias",
        ),
        pytest.param(
            'TARGET = "_data"\nclass Example:\n    TARGET = "public"\n    with context:\n        pass\n    getattr(mod, TARGET)\n',
            id="class-with-keeps-prior-overwrite",
        ),
        pytest.param(
            'TARGET = "_data"\nclass Example:\n    try:\n        risky()\n    finally:\n        TARGET = "public"\n    getattr(mod, TARGET)\n',
            id="class-finally-public-override",
        ),
        pytest.param(
            'TARGET = "public"\nclass Example:\n    try:\n        risky()\n    except First:\n        TARGET = "_data"\n    except Second:\n        getattr(mod, TARGET)\n',
            id="class-independent-handlers",
        ),
        pytest.param(
            'from builtins import getattr as read\nclass Example:\n    if flag:\n        read = public_callback\n    else:\n        read = other_callback\n    read(None, "_data")\n',
            id="class-both-aliases-public",
        ),
        pytest.param(
            'TARGET = "public"\nclass Example:\n    while flag:\n        TARGET = "public"\n        getattr(mod, TARGET)\n        TARGET = "_data"\n',
            id="class-loop-definite-public-prefix",
        ),
        pytest.param(
            'getattr = public_callback\nclass Example:\n    try:\n        raise Exception()\n    finally:\n        getattr(None, "_data")\n',
            id="class-exceptional-finally-keeps-alias",
        ),
        pytest.param(
            'NAME = "public"\ndef outer():\n    NAME = "_data"\n    class Example:\n        getattr(mod, NAME)\n        NAME = "public"\n',
            id="class-bound-name-ignores-private-function-value",
        ),
        pytest.param(
            'NAME = "_data"\ndef outer():\n    NAME = "public"\n    class Example:\n        getattr(mod, NAME)\n',
            id="class-free-name-keeps-function-value",
        ),
        pytest.param(
            'read = public_callback\ndef outer():\n    from builtins import getattr as read\n    class Example:\n        read(None, "_data")\n        read = public_callback\n',
            id="class-bound-alias-ignores-function-reflection",
        ),
        pytest.param(
            'class Example:\n    NAME, data = ["public", "_data"]\n    getattr(mod, NAME)\n',
            id="class-literal-unpack-keeps-elements-separate",
        ),
        pytest.param(
            'class Example:\n    read, other = (public_callback, getattr)\n    read(None, "_data")\n',
            id="class-literal-unpack-public-alias",
        ),
        pytest.param(
            "matrix[\"cases\"] = ('a0', 'a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7', 'a8', 'a9', 'a10', 'a11', 'a12', 'a13', 'a14', 'a15', 'a16') + ('b0', 'b1', 'b2', 'b3', 'b4', 'b5', 'b6', 'b7', 'b8', 'b9', 'b10', 'b11', 'b12', 'b13', 'b14', 'b15', 'b16')\n",
            id="nonbinding-assignment-does-not-expand-data",
        ),
        pytest.param(
            'class Example:\n    read = public_callback\n    for item in (0,):\n        read(None, "_data")\n        from builtins import getattr as read\n',
            id="class-single-iteration-import-does-not-run-backwards",
        ),
        pytest.param(
            'class Example:\n    NAME = "public"\n    for item in (0,):\n        getattr(mod, NAME)\n        NAME = "_data"\n',
            id="class-single-iteration-value-does-not-run-backwards",
        ),
        pytest.param(
            'class Example:\n    NAME = "_data"\n    for item in items:\n        pass\n    else:\n        NAME = "public"\n    getattr(mod, NAME)\n',
            id="class-for-normal-exit-runs-else",
        ),
        pytest.param(
            'class Example:\n    NAME = "_data"\n    while flag:\n        pass\n    else:\n        NAME = "public"\n    getattr(mod, NAME)\n',
            id="class-while-normal-exit-runs-else",
        ),
        pytest.param(
            'class Example:\n    NAME = "public"\n    try:\n        raise ValueError()\n    except ValueError:\n        pass\n    else:\n        NAME = "_data"\n    finally:\n        getattr(mod, NAME)\n',
            id="class-raised-try-skips-else-before-finally",
        ),
        pytest.param(
            'class Example:\n    NAME = "public"\n    try:\n        raise ExceptionGroup("both", [ValueError(), TypeError()])\n    except* ValueError:\n        NAME = "also_public"\n    except* TypeError:\n        getattr(mod, NAME)\n',
            id="class-except-star-public-handlers",
        ),
        pytest.param(
            'class Example:\n    NAME = "_data"\n    match "public":\n        case NAME if False:\n            pass\n        case _:\n            getattr(mod, NAME)\n',
            id="class-false-match-guard-keeps-public-capture",
        ),
        pytest.param(
            'class Example:\n    match "public":\n        case NAME:\n            getattr(mod, NAME)\n',
            id="class-whole-subject-public-capture",
        ),
        pytest.param(
            'from contextlib import suppress\nclass Example:\n    NAME = "public"\n    with suppress(ValueError), fail() as NAME:\n        pass\n    getattr(mod, NAME)\n',
            id="class-earlier-context-keeps-public-target",
        ),
        pytest.param(
            'class Example:\n    NAME = "_data"\n    OTHER = "public"\n    NAME, OTHER = (OTHER, NAME)\n    getattr(mod, NAME)\n',
            id="class-literal-unpack-swap-keeps-public-result",
        ),
        pytest.param(
            'NAME = "public"\nclass Example:\n    for item in (NAME, NAME):\n        getattr(mod, item)\n        NAME = "_data"\n',
            id="class-loop-evaluates-literal-iterable-once",
        ),
        pytest.param(
            'NAME = "_data"\nclass Example:\n    for NAME in {"public", "also_public"}:\n        pass\n    getattr(mod, NAME)\n',
            id="class-nonempty-set-loop-replaces-inherited-value",
        ),
        pytest.param(
            'NAME = "public"\ndef outer():\n    NAME = "_data"\n    class Example:\n        global NAME\n        getattr(mod, NAME)\n',
            id="class-global-name-ignores-private-enclosing-value",
        ),
        pytest.param(
            'NAME = "_data"\ndef outer():\n    NAME = "public"\n    class Example:\n        nonlocal NAME\n        getattr(mod, NAME)\n        NAME = "public"\n',
            id="class-nonlocal-name-ignores-private-module-value",
        ),
        pytest.param(
            'def outer():\n    getattr = public_callback\n    class Example:\n        nonlocal getattr\n        del getattr\n        getattr(None, "_data")\n',
            id="class-deleted-nonlocal-alias-stays-unbound",
        ),
        pytest.param(
            'NAME = "_data"\nclass Example:\n    global NAME\n    del NAME\n    getattr(mod, NAME)\n',
            id="class-deleted-global-name-does-not-restore-old-value",
        ),
        pytest.param(
            'NAME = "public"\ndef outer():\n    NAME = "_data"\n    class Example:\n        nonlocal NAME\n        del NAME\n        getattr(mod, NAME)\n',
            id="class-deleted-nonlocal-name-does-not-restore-old-value",
        ),
        pytest.param(
            'dispatch.dict("_data")\n',
            id="dispatch-dict-is-not-patch",
        ),
        pytest.param(
            'dispatch.object(obj, "_data")\n',
            id="dispatch-object-is-not-patch",
        ),
        pytest.param(
            "dispatch.multiple(obj, _data=value)\n",
            id="dispatch-multiple-is-not-patch",
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
            "class Example:\n    match ('public', '_data'):\n        case [name, payload]:\n            getattr(mod, name)\n",
            id="literal-sequence-public-capture",
        ),
        pytest.param(
            "class Example:\n    match [('_data', 'public')]:\n        case [[payload, name]]:\n            getattr(mod, name)\n",
            id="nested-sequence-public-capture",
        ),
        pytest.param(
            "class Example:\n    match (callback,):\n        case [lookup]:\n            lookup(mod, '_data')\n",
            id="sequence-public-callable-capture",
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
            "getattr(mod, f'{-1:x>3}')\n",
            id="formatted-negative-literal-public-fill",
        ),
        pytest.param(
            "getattr(mod, f'{+1:x>2}')\n",
            id="formatted-positive-unary-literal-public-fill",
        ),
        pytest.param(
            "class E:\n    NAME = 'public'\n    for item in (0,):\n        break\n    else:\n        NAME = '_data'\n    getattr(mod, NAME)\n",
            id="class-definite-break-skips-loop-else",
        ),
        pytest.param(
            "class E:\n    for item in (0,):\n        continue\n        getattr(mod, '_data')\n",
            id="class-continue-skips-private-access",
        ),
        pytest.param(
            "class E:\n    for item in (0,):\n        break\n        getattr(mod, '_data')\n",
            id="class-break-skips-private-access",
        ),
        pytest.param(
            "NAME = 'public'\nclass E:\n    NAME = '_data'\ngetattr(mod, NAME)\n",
            id="class-ordinary-write-stays-in-class",
        ),
        pytest.param(
            "NAME = '_data'\nclass E:\n    global NAME\n    NAME = 'public'\ngetattr(mod, NAME)\n",
            id="class-definite-global-write-removes-old-private-value",
        ),
        pytest.param(
            "NAME = 'public'\nclass E:\n    if flag:\n        global NAME\n        NAME = '_data'\n    else:\n        getattr(mod, NAME)\n",
            id="class-global-branch-does-not-affect-sibling",
        ),
        pytest.param(
            "class E:\n    for item in (0,):\n        try:\n            pass\n        finally:\n            continue\n        getattr(mod, '_data')\n",
            id="class-finally-continue-skips-remainder",
        ),
        pytest.param(
            "class E:\n    NAME = '_data'\n    for item in (0,):\n        for inner in (0,):\n            break\n    else:\n        NAME = 'public'\n    getattr(mod, NAME)\n",
            id="class-nested-break-does-not-skip-outer-else",
        ),
        pytest.param(
            "NAME = 'public'\nclass E:\n    global NAME\n    NAME = 'public'\ngetattr(mod, NAME)\nNAME = '_data'\n",
            id="class-global-write-stays-public-before-later-assignment",
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
    result = _run(tmp_path, source)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "formatted attribute target exceeds the size limit" in result.stdout


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
