"""
E2E tests for ha_get_camera_image against the demo cameras.
"""

import base64
import json

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entity_id", "mime_type"),
    [("camera.demo_camera", "image/jpeg"), ("camera.demo_camera_png", "image/png")],
)
async def test_camera_image_metadata_describes_the_delivered_image(
    mcp_client, entity_id, mime_type
):
    """A client sees a metadata block that matches the image it received (#2525)."""
    result = await mcp_client.call_tool("ha_get_camera_image", {"entity_id": entity_id})

    text, image = result.content
    meta = json.loads(text.text)
    assert meta == result.structured_content
    assert meta["entity_id"] == entity_id
    assert image.mimeType == meta["mime_type"] == mime_type
    assert meta["size_bytes"] == len(base64.b64decode(image.data))
    assert meta["width"] > 0 and meta["height"] > 0
    assert meta["captured_at"].endswith("+00:00")
