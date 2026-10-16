"""Throwaway proof for the CodeRabbit `except ToolError: raise` finding."""

from functools import partial
from unittest.mock import AsyncMock, MagicMock

import pytest

from ha_mcp._vendor.fastmcp.exceptions import ToolError
from ha_mcp.backup_manager import BackupRestoreError, _apply_and_verify
from ha_mcp.tools.config_entry_flow import (
    OptionsFlowError,
    update_config_entry_options,
)


def _client() -> MagicMock:
    client = MagicMock()
    client.get_config_entry = AsyncMock(
        return_value={"entry_id": "own", "domain": "ha_mcp_tools"}
    )
    client.start_options_flow = AsyncMock()
    return client


async def test_restore_path_wraps_guard_in_options_flow_error() -> None:
    # progress is set only when keep_current_values=False (the restore path).
    with pytest.raises(OptionsFlowError) as exc:
        await update_config_entry_options(
            _client(), "own", {"x": 1}, keep_current_values=False, fixed_options={}
        )
    assert exc.value.apply_status == "not_applied"
    assert isinstance(exc.value.__cause__, ToolError)
    print("\nWRAPPED:", type(exc.value).__name__, "cause=", type(exc.value.__cause__).__name__)


async def test_restore_caller_turns_it_into_backup_restore_error() -> None:
    client = _client()
    with pytest.raises(BackupRestoreError) as exc:
        await _apply_and_verify(
            "x helper",
            partial(
                update_config_entry_options,
                client,
                "own",
                {"x": 1},
                keep_current_values=False,
                fixed_options={},
            ),
            AsyncMock(return_value="matched"),
        )
    print("\nRESTORE CALLER GOT:", type(exc.value).__name__, "-", exc.value)
    client.start_options_flow.assert_not_called()


async def test_edit_path_still_raises_tool_error() -> None:
    # Ordinary ha_set_integration edit: progress is None, ToolError passes through.
    with pytest.raises(ToolError):
        await update_config_entry_options(_client(), "own", {"x": 1})
