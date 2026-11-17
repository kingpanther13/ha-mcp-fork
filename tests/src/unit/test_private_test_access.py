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
        'NAME = "_helper"\ngetattr(mod, NAME)\n',
        'PREFIX = "ha_mcp.module."\npatch(PREFIX + "_helper")\n',
        'NAME = "_helper"\npatch(f"ha_mcp.module.{NAME}")\n',
        'patch(f"ha_mcp.{runtime_module}._helper")\n',
        'patch(runtime_module + "._helper")\n',
        'for name in ("public", "_helper"):\n    getattr(mod, name)\n',
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
    ],
)
def test_public_targets_and_non_target_strings_are_allowed(tmp_path, source):
    result = _run(tmp_path, source)
    assert result.returncode == 0, result.stdout + result.stderr


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
