"""Config-entry (``integration``) snapshots: an entry's metadata and enabled state.

Core's config-entry API exposes neither an entry's data nor its options, so the
snapshot cannot recreate a deleted entry. Restore re-applies the disabled flag
to an entry that still exists and refuses one that is gone, instead of sending
the agent to look for a schema change. Flow helpers are snapshotted separately
(``helper_<type>``) and are recreated on restore.
"""

from __future__ import annotations

from typing import Any

from .backup_entity_ids import _manager


async def fetch_integration(client: Any, entry_id: str) -> Any:
    bm = _manager()
    items = bm._require_list(
        await bm._ws_send(client, {"type": "config_entries/get"}),
        "config_entries/get",
    )
    return next((item for item in items if item.get("entry_id") == entry_id), None)


async def restore_integration(client: Any, entry_id: str, config: Any) -> Any:
    bm = _manager()
    if await fetch_integration(client, entry_id) is None:
        raise bm.BackupRestoreError(
            f"Config entry {entry_id} ({config.get('domain')}) no longer exists. "
            "An integration snapshot holds only the entry's metadata and enabled "
            "state, so it cannot recreate a deleted entry. Nothing was changed; "
            "add the integration again to recover it.",
            reason="entry_deleted",
            suggestions=[
                "Add it again with ha_set_integration, or have the user add it in "
                "the HA UI when it needs their credentials (otp).",
            ],
        )
    disabled = config.get("disabled_by") is not None
    return await bm._ws_send(
        client,
        {
            "type": "config_entries/disable",
            "entry_id": entry_id,
            "disabled_by": "user" if disabled else None,
        },
    )
