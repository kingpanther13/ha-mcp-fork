"""
Camera tools for Home Assistant MCP server.

This module provides camera-related tools including snapshot retrieval
that returns images directly to the LLM for visual analysis.
"""

import json
import logging
import struct
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Annotated, Any

from pydantic import Field

from ha_mcp._vendor.fastmcp.tools import ToolResult, tool
from ha_mcp._vendor.fastmcp.utilities.types import Image
from ha_mcp._vendor.mcp.types import TextContent

from .helpers import log_tool_usage, register_tool_methods
from .tool_hints import read_only_hints

logger = logging.getLogger(__name__)


_CONTENT_TYPE_MAP = {
    "jpeg": "jpeg",
    "jpg": "jpeg",
    "png": "png",
    "gif": "gif",
}

# JPEG start-of-frame markers carry the frame size; DHT (C4), JPG (C8) and
# DAC (CC) share the C0-CF range but do not.
_JPEG_SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def _detect_image_format(content_type: str) -> str:
    """Detect image format from Content-Type header, defaulting to JPEG."""
    for key, fmt in _CONTENT_TYPE_MAP.items():
        if key in content_type:
            return fmt
    return "jpeg"


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Walk JPEG segments to the first start-of-frame and read its size."""
    i = 2
    while i + 9 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte before a marker
            i += 1
            continue
        if marker in _JPEG_SOF_MARKERS:
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        if marker == 0x01 or 0xD0 <= marker <= 0xD9:  # standalone markers
            i += 2
            continue
        i += 2 + struct.unpack(">H", data[i + 2 : i + 4])[0]
    return None


def _image_dimensions(data: bytes) -> tuple[int, int] | None:
    """Return (width, height) from a JPEG, PNG or GIF header, or None."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    if data[:2] == b"\xff\xd8":
        return _jpeg_dimensions(data)
    return None


def _captured_at(date_header: str | None) -> str:
    """Use HA's Date response header as the capture time, else the local clock."""
    if date_header:
        try:
            return parsedate_to_datetime(date_header).astimezone(UTC).isoformat()
        except (TypeError, ValueError):
            pass
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class CameraTools:
    """Camera snapshot retrieval tools."""

    def __init__(self, client: Any) -> None:
        self._client = client

    @staticmethod
    def _check_response(response: Any, entity_id: str) -> None:
        """Validate camera proxy HTTP response status and content, raising on errors."""
        if response.status_code == 401:
            raise PermissionError("Invalid authentication token for camera access")
        if response.status_code == 404:
            raise ValueError(
                f"Camera entity not found: {entity_id}. "
                "Use ha_search() to find available cameras."
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Failed to retrieve camera image: HTTP {response.status_code}"
            )
        if not response.content:
            raise RuntimeError(
                f"Camera {entity_id} returned empty image data. "
                "The camera may be offline or unavailable."
            )

    @tool(
        name="ha_get_camera_image",
        tags={"Camera"},
        annotations=read_only_hints("Get Camera Image", open_world=False),
    )
    @log_tool_usage
    async def ha_get_camera_image(
        self,
        entity_id: Annotated[
            str, Field(description="Camera entity ID (e.g., 'camera.front_door')")
        ],
        width: Annotated[
            int | None, Field(description="Width to resize the image to")
        ] = None,
        height: Annotated[
            int | None, Field(description="Height to resize the image to")
        ] = None,
    ) -> ToolResult:
        """Get a snapshot image from a Home Assistant camera entity.

        Fetches the current camera image and returns it directly for visual
        analysis (security checks, delivery verification, confirming a garage
        door actually closed). Only cameras exposed to Home Assistant are
        accessible; images come back in their native format (JPEG, PNG, or GIF).
        Use width/height on high-resolution cameras to reduce token usage.
        A JSON block before the image gives its format, byte size, pixel
        dimensions and UTC capture time, for comparing several snapshots.

        EXAMPLE: ha_get_camera_image(entity_id="camera.backyard", width=640, height=480)
        """
        if not entity_id or "." not in entity_id:
            raise ValueError(
                f"Invalid entity_id format: {entity_id}. "
                "Expected format: camera.entity_name"
            )

        domain = entity_id.split(".", maxsplit=1)[0]
        if domain != "camera":
            raise ValueError(
                f"Entity {entity_id} is not a camera entity. "
                f"Domain is '{domain}', expected 'camera'."
            )

        # Build the camera proxy URL with optional size parameters
        # Home Assistant camera proxy API: /api/camera_proxy/<entity_id>
        endpoint = f"/camera_proxy/{entity_id}"

        params = {}
        if width is not None:
            params["width"] = str(width)
        if height is not None:
            params["height"] = str(height)

        try:
            response = await self._client.httpx_client.get(
                endpoint, params=params or None
            )
            self._check_response(response, entity_id)

            content_type = response.headers.get("content-type", "image/jpeg")
            image_format = _detect_image_format(content_type)
            dimensions = _image_dimensions(response.content)

            metadata: dict[str, Any] = {
                "success": True,
                "entity_id": entity_id,
                "format": image_format,
                "mime_type": f"image/{image_format}",
                "size_bytes": len(response.content),
                "width": dimensions[0] if dimensions else None,
                "height": dimensions[1] if dimensions else None,
                "captured_at": _captured_at(response.headers.get("date")),
            }
            if width is not None or height is not None:
                metadata["requested_width"] = width
                metadata["requested_height"] = height

            logger.info(
                f"Retrieved camera image from {entity_id} "
                f"({len(response.content)} bytes, format={image_format})"
            )

            return ToolResult(
                content=[
                    TextContent(type="text", text=json.dumps(metadata)),
                    Image(
                        data=response.content, format=image_format
                    ).to_image_content(),
                ],
                structured_content=metadata,
            )

        except (PermissionError, ValueError, RuntimeError):
            raise
        except Exception as e:
            logger.error(f"Error retrieving camera image from {entity_id}: {e}")
            raise RuntimeError(
                f"Failed to retrieve camera image from {entity_id}: {str(e)}. "
                "Ensure the camera is online and accessible."
            ) from e


def register_camera_tools(mcp: Any, client: Any, **kwargs: Any) -> None:
    """Register Home Assistant camera tools."""
    register_tool_methods(mcp, CameraTools(client))
