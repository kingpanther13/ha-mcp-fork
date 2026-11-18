"""Private-target checks follow feasible Python namespace and control-flow paths."""

from __future__ import annotations

import subprocess
from textwrap import indent

import pytest

from .test_private_test_access import _run


@pytest.mark.parametrize(
    "source",
    [
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
            "class Example:\n    name = 'public' if flag else '_helper'\n    getattr(mod, name)\n",
            id="conditional-private-class-string",
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
        pytest.param(
            "NAME = 'public'\nclass Example:\n    if flag:\n        NAME = 'public'\n    class Nested:\n        global NAME\n        NAME = '_helper'\n    getattr(mod, NAME)\n",
            id="mixed-local-binding-refreshes-unbound-module-path",
        ),
        pytest.param(
            "NAME = 'public'\nclass Example:\n    if flag:\n        NAME = '_helper'\n    class Nested:\n        global NAME\n        NAME = 'public'\n    getattr(mod, NAME)\n",
            id="mixed-local-binding-preserves-private-local-path",
        ),
        pytest.param(
            "class Example:\n    NAME = 'public'\n    try:\n        NAME = holder[0] = '_helper'\n    except ValueError:\n        getattr(mod, NAME)\n",
            id="chained-store-interruption-keeps-earlier-name",
        ),
        pytest.param(
            "class Example:\n    NAME = 'public'\n    try:\n        for item in items:\n            NAME = '_helper'\n    except ValueError:\n        getattr(mod, NAME)\n",
            id="iterator-next-header-error-keeps-carried-name",
        ),
        pytest.param(
            "class Example:\n    NAME = 'public'\n    try:\n        while condition():\n            NAME = '_helper'\n    except ValueError:\n        getattr(mod, NAME)\n",
            id="while-next-header-error-keeps-carried-name",
        ),
        pytest.param(
            "class Example:\n    NAME = 'public'\n    for index in range(1):\n        NAME = '_helper'\n    getattr(mod, NAME)\n",
            id="literal-range-preserves-private-result",
        ),
        pytest.param(
            "class Example:\n    match '_helper':\n        case '_helper' as name:\n            getattr(mod, name)\n",
            id="known-literal-pattern-match-keeps-capture",
        ),
        pytest.param(
            "class Example:\n    NAME = 'public'\n    try:\n        with manager():\n            NAME = '_helper'\n    except ValueError:\n        getattr(mod, NAME)\n",
            id="with-exit-error-keeps-body-store",
        ),
        pytest.param(
            "NAME = '_helper'\nif flag:\n    class Example:\n        global NAME\n        NAME = 'public'\ngetattr(mod, NAME)\n",
            id="conditional-class-global-preserves-skipped-path",
        ),
        pytest.param(
            "NAME = 'public'\ndef test_it():\n    class Example:\n        global NAME\n        NAME = '_helper'\n    getattr(mod, NAME)\n",
            id="function-inherited-global-refreshes-after-class",
        ),
        pytest.param(
            "NAME = 'public'\ndef test_it():\n    NAME = '_helper'\n    class Example:\n        global NAME\n        NAME = 'public'\n    getattr(mod, NAME)\n",
            id="function-true-local-survives-class-global-write",
        ),
        pytest.param(
            "NAME = 'public'\ndef outer():\n    NAME = '_helper'\n    def test_it():\n        class Example:\n            global NAME\n            NAME = 'public'\n        getattr(mod, NAME)\n",
            id="enclosing-local-survives-inner-class-global-write",
        ),
        pytest.param(
            "def test_it():\n    try:\n        return\n    finally:\n        getattr(mod, '_helper')\n",
            id="finally-private-access-runs-after-return",
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
        pytest.param(
            "NAME = '_data'\nclass Example:\n    if flag:\n        NAME = 'public'\n    class Nested:\n        global NAME\n        NAME = 'public'\n    getattr(mod, NAME)\n",
            id="mixed-local-binding-discards-replaced-private-fallback",
        ),
        pytest.param(
            "NAME = 'public'\nclass Example:\n    global NAME\n    NAME = '_helper'\nNAME = 'public'\ngetattr(mod, NAME)\n",
            id="definite-module-store-replaces-class-global",
        ),
        pytest.param(
            "class Example:\n    NAME = 'public'\n    try:\n        holder[0] = NAME = '_data'\n    except ValueError:\n        getattr(mod, NAME)\n",
            id="chained-store-interruption-before-name",
        ),
        pytest.param(
            "class Example:\n    NAME = 'public'\n    try:\n        for item in items:\n            NAME = 'public'\n    except ValueError:\n        getattr(mod, NAME)\n",
            id="iterator-next-header-public-control",
        ),
        pytest.param(
            "class Example:\n    NAME = 'public'\n    try:\n        while condition():\n            NAME = 'public'\n    except ValueError:\n        getattr(mod, NAME)\n",
            id="while-next-header-public-control",
        ),
        pytest.param(
            "class Example:\n    label = ''\n    for index in range(3):\n        label = label + 'x'\n    getattr(mod, 'public')\n",
            id="literal-range-stops-after-three-iterations",
        ),
        pytest.param(
            "class Example:\n    NAME = 'public'\n    for index in range(0):\n        NAME = '_data'\n    getattr(mod, NAME)\n",
            id="literal-empty-range-skips-private-body",
        ),
        pytest.param(
            "class Example:\n    match '_data':\n        case 'public' as name:\n            getattr(mod, name)\n",
            id="known-literal-pattern-mismatch-skips-capture",
        ),
        pytest.param(
            "class Example:\n    NAME = 'public'\n    try:\n        with manager():\n            NAME = 'public'\n    except ValueError:\n        getattr(mod, NAME)\n",
            id="with-exit-error-public-control",
        ),
        pytest.param(
            "NAME = '_data'\ndef test_it():\n    class Example:\n        global NAME\n        NAME = 'public'\n    getattr(mod, NAME)\n",
            id="function-inherited-global-drops-old-private",
        ),
        pytest.param(
            "NAME = 'public'\ndef test_it():\n    NAME = 'public'\n    class Example:\n        global NAME\n        NAME = '_helper'\n    getattr(mod, NAME)\n",
            id="function-local-stays-independent-of-class-global",
        ),
        pytest.param(
            "def test_it():\n    return\n    getattr(mod, '_data')\n",
            id="return-stops-unreachable-private-access",
        ),
    ],
)
def test_public_targets_and_non_target_strings_are_allowed(tmp_path, source):
    result = _run(tmp_path, source)
    assert result.returncode == 0, result.stdout + result.stderr


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
