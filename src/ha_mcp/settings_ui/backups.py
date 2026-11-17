"""Resolve the edit-backup manager shared by settings routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..backup_manager import get_backup_manager
from ..config import get_global_settings

if TYPE_CHECKING:
    from ..server import HomeAssistantSmartMCPServer


def backup_manager_for_server(server: HomeAssistantSmartMCPServer | None) -> Any:
    settings = get_global_settings()
    client = getattr(server, "client", None) or getattr(server, "_client", None)
    return get_backup_manager(client, settings) if client is not None else None
