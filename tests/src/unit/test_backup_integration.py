"""Restoring an integration snapshot after its config entry was deleted.

The snapshot holds only the entry's metadata and enabled state, so it cannot
recreate the entry. Restore used to send ``config_entries/disable`` anyway and
surface Core's "Config entry not found" with advice to check for schema drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from ha_mcp import backup_manager
from ha_mcp.backup_integration import restore_integration

_ENTRY = {"entry_id": "e1", "domain": "otp", "title": "Door code", "disabled_by": None}


@dataclass
class FakeCore:
    entries: list[dict[str, Any]] = field(default_factory=list)
    sent: list[dict[str, Any]] = field(default_factory=list)

    async def ws_send(self, client: Any, message: dict[str, Any]) -> Any:
        self.sent.append(message)
        return self.entries if message["type"] == "config_entries/get" else {}


@pytest.fixture
def core(monkeypatch: pytest.MonkeyPatch) -> FakeCore:
    fake = FakeCore()
    monkeypatch.setattr(backup_manager, "_ws_send", fake.ws_send)
    return fake


async def test_deleted_entry_is_refused_without_sending_a_disable(core):
    """Without the check the restore fails on Core's not-found and the agent is
    told to verify the entity and compare schemas, which cannot help."""
    with pytest.raises(backup_manager.BackupRestoreError) as exc:
        await restore_integration(object(), "e1", _ENTRY)
    assert exc.value.outcome["reason"] == "entry_deleted"
    assert exc.value.outcome["apply_status"] == "not_applied"
    assert exc.value.outcome["suggestions"]
    assert [m["type"] for m in core.sent] == ["config_entries/get"]


async def test_existing_entry_gets_its_disabled_flag_back(core):
    core.entries.append({**_ENTRY, "disabled_by": "user"})
    await restore_integration(object(), "e1", _ENTRY)
    assert core.sent[-1] == {
        "type": "config_entries/disable",
        "entry_id": "e1",
        "disabled_by": None,
    }
