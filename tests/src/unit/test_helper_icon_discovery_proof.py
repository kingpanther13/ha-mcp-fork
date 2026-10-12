"""Check the proposed discovery rule without copying any Core helper schema."""

from unittest.mock import AsyncMock, patch

import pytest

from ha_mcp.tools.config_helpers.icon_route import collection_icon


@pytest.mark.asyncio
async def test_new_collection_and_action_fields_drive_routing():
    """A new collection needs no type list, and create/update can differ."""
    native = {"future_collection": {"create": [], "update": [{"name": "icon"}]}}
    with patch(
        "ha_mcp.tools.config_helpers.icon_route.fetch_helper_schemas",
        new=AsyncMock(return_value=native),
    ):
        assert await collection_icon(object(), "future_collection", "create", "mdi:star") is None
        assert await collection_icon(object(), "future_collection", "update", "mdi:star") == "mdi:star"


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [None, {}, {"future_collection": {}}])
async def test_missing_discovery_uses_registry_without_guessing_collection_fields(native):
    """Absent discovery can still use the standard entity metadata operation."""
    with patch(
        "ha_mcp.tools.config_helpers.icon_route.fetch_helper_schemas",
        new=AsyncMock(return_value=native),
    ):
        assert await collection_icon(object(), "future_collection", "create", "mdi:star") is None


@pytest.mark.asyncio
async def test_clear_retains_stored_icon_removal_without_discovery():
    """Clearing only the registry would reveal an older stored helper icon."""
    with patch(
        "ha_mcp.tools.config_helpers.icon_route.fetch_helper_schemas",
        new=AsyncMock(return_value=None),
    ):
        assert await collection_icon(object(), "future_collection", "update", "") == ""
