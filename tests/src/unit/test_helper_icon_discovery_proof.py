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
async def test_missing_discovery_does_not_assert_core_lacks_icon(native):
    """Absent discovery forwards to Core; it cannot identify registry-only types."""
    with patch(
        "ha_mcp.tools.config_helpers.icon_route.fetch_helper_schemas",
        new=AsyncMock(return_value=native),
    ):
        assert await collection_icon(object(), "future_collection", "create", "mdi:star") == "mdi:star"
