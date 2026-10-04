"""Unit tests for camera tools module."""

import json
import struct
from unittest.mock import AsyncMock, MagicMock

import pytest

from ha_mcp.tools.tools_camera import CameraTools


def _metadata(result):
    """The JSON text block a client without structuredContent support sees."""
    text, _image = result.content
    return json.loads(text.text)


def _png(width, height):
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sII", 13, b"IHDR", width, height)


def _gif(width, height):
    return b"GIF89a" + struct.pack("<HH", width, height)


def _jpeg(width, height):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = b"\xff\xc0" + struct.pack(">HBHH", 17, 8, height, width) + b"\x03"
    return b"\xff\xd8" + app0 + sof0


class TestHaGetCameraImage:
    """Test ha_get_camera_image tool validation logic."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Home Assistant client."""
        client = MagicMock()
        client.httpx_client = AsyncMock()
        return client

    @pytest.fixture
    def camera_tools(self, mock_client):
        """Create CameraTools instance."""
        return CameraTools(mock_client)

    @pytest.mark.asyncio
    async def test_invalid_entity_id_format_empty(self, camera_tools):
        """Empty entity_id raises ValueError."""
        with pytest.raises(ValueError, match="Invalid entity_id format"):
            await camera_tools.ha_get_camera_image(entity_id="")

    @pytest.mark.asyncio
    async def test_invalid_entity_id_format_no_dot(self, camera_tools):
        """Entity ID without dot raises ValueError."""
        with pytest.raises(ValueError, match="Invalid entity_id format"):
            await camera_tools.ha_get_camera_image(entity_id="front_door")

    @pytest.mark.asyncio
    async def test_non_camera_domain_raises_error(self, camera_tools):
        """Non-camera entity raises ValueError."""
        with pytest.raises(ValueError, match="not a camera entity"):
            await camera_tools.ha_get_camera_image(entity_id="light.living_room")

    @pytest.mark.asyncio
    async def test_non_camera_domain_sensor(self, camera_tools):
        """Sensor entity raises ValueError."""
        with pytest.raises(ValueError, match="Domain is 'sensor', expected 'camera'"):
            await camera_tools.ha_get_camera_image(entity_id="sensor.temperature")

    @pytest.mark.asyncio
    async def test_successful_image_retrieval(self, mock_client):
        """Test successful camera image retrieval."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"\xff\xd8\xff\xe0"  # JPEG magic bytes
        mock_response.headers = {"content-type": "image/jpeg"}
        mock_client.httpx_client.get = AsyncMock(return_value=mock_response)

        tools = CameraTools(mock_client)
        result = await tools.ha_get_camera_image(entity_id="camera.front_door")

        mock_client.httpx_client.get.assert_called_once_with(
            "/camera_proxy/camera.front_door", params=None
        )
        text, image = result.content
        assert image.mimeType == "image/jpeg"
        assert json.loads(text.text) == result.structured_content

    @pytest.mark.asyncio
    async def test_image_retrieval_with_size_params(self, mock_client):
        """Test camera image retrieval with width and height parameters."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"\xff\xd8\xff\xe0"
        mock_response.headers = {"content-type": "image/jpeg"}
        mock_client.httpx_client.get = AsyncMock(return_value=mock_response)

        tools = CameraTools(mock_client)
        await tools.ha_get_camera_image(
            entity_id="camera.front_door", width=640, height=480
        )

        mock_client.httpx_client.get.assert_called_once_with(
            "/camera_proxy/camera.front_door", params={"width": "640", "height": "480"}
        )

    @pytest.mark.asyncio
    async def test_authentication_error(self, mock_client):
        """Test 401 response raises PermissionError."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_client.httpx_client.get = AsyncMock(return_value=mock_response)

        tools = CameraTools(mock_client)
        with pytest.raises(PermissionError, match="Invalid authentication token"):
            await tools.ha_get_camera_image(entity_id="camera.front_door")

    @pytest.mark.asyncio
    async def test_not_found_error(self, mock_client):
        """Test 404 response raises ValueError."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_client.httpx_client.get = AsyncMock(return_value=mock_response)

        tools = CameraTools(mock_client)
        with pytest.raises(ValueError, match="Camera entity not found"):
            await tools.ha_get_camera_image(entity_id="camera.nonexistent")

    @pytest.mark.asyncio
    async def test_server_error(self, mock_client):
        """Test 500 response raises RuntimeError."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client.httpx_client.get = AsyncMock(return_value=mock_response)

        tools = CameraTools(mock_client)
        with pytest.raises(
            RuntimeError, match="Failed to retrieve camera image: HTTP 500"
        ):
            await tools.ha_get_camera_image(entity_id="camera.front_door")

    @pytest.mark.asyncio
    async def test_empty_image_data(self, mock_client):
        """Test empty image data raises RuntimeError."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b""
        mock_response.headers = {"content-type": "image/jpeg"}
        mock_client.httpx_client.get = AsyncMock(return_value=mock_response)

        tools = CameraTools(mock_client)
        with pytest.raises(RuntimeError, match="returned empty image data"):
            await tools.ha_get_camera_image(entity_id="camera.front_door")

    @pytest.mark.asyncio
    async def test_png_content_type(self, mock_client):
        """Test PNG content type is correctly detected."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"\x89PNG\r\n\x1a\n"  # PNG magic bytes
        mock_response.headers = {"content-type": "image/png"}
        mock_client.httpx_client.get = AsyncMock(return_value=mock_response)

        tools = CameraTools(mock_client)
        result = await tools.ha_get_camera_image(entity_id="camera.front_door")
        assert result.content[1].mimeType == "image/png"
        assert _metadata(result)["mime_type"] == "image/png"

    @pytest.mark.asyncio
    async def test_gif_content_type(self, mock_client):
        """Test GIF content type is correctly detected."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"GIF89a"  # GIF magic bytes
        mock_response.headers = {"content-type": "image/gif"}
        mock_client.httpx_client.get = AsyncMock(return_value=mock_response)

        tools = CameraTools(mock_client)
        result = await tools.ha_get_camera_image(entity_id="camera.front_door")
        assert result.content[1].mimeType == "image/gif"
        assert _metadata(result)["mime_type"] == "image/gif"

    @pytest.mark.asyncio
    async def test_default_to_jpeg_for_unknown_content_type(self, mock_client):
        """Test unknown content type defaults to JPEG."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"some image data"
        mock_response.headers = {"content-type": "application/octet-stream"}
        mock_client.httpx_client.get = AsyncMock(return_value=mock_response)

        tools = CameraTools(mock_client)
        result = await tools.ha_get_camera_image(entity_id="camera.front_door")
        assert result.content[1].mimeType == "image/jpeg"
        assert _metadata(result)["mime_type"] == "image/jpeg"

    @pytest.mark.asyncio
    async def test_width_only_param(self, mock_client):
        """Test providing only width parameter."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"\xff\xd8\xff\xe0"
        mock_response.headers = {"content-type": "image/jpeg"}
        mock_client.httpx_client.get = AsyncMock(return_value=mock_response)

        tools = CameraTools(mock_client)
        await tools.ha_get_camera_image(entity_id="camera.front_door", width=800)

        mock_client.httpx_client.get.assert_called_once_with(
            "/camera_proxy/camera.front_door", params={"width": "800"}
        )

    @pytest.mark.asyncio
    async def test_height_only_param(self, mock_client):
        """Test providing only height parameter."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"\xff\xd8\xff\xe0"
        mock_response.headers = {"content-type": "image/jpeg"}
        mock_client.httpx_client.get = AsyncMock(return_value=mock_response)

        tools = CameraTools(mock_client)
        await tools.ha_get_camera_image(entity_id="camera.front_door", height=600)

        mock_client.httpx_client.get.assert_called_once_with(
            "/camera_proxy/camera.front_door", params={"height": "600"}
        )

    @pytest.mark.parametrize(
        ("data", "content_type"),
        [
            (_png(1920, 1080), "image/png"),
            (_gif(1920, 1080), "image/gif"),
            (_jpeg(1920, 1080), "image/jpeg"),
        ],
    )
    @pytest.mark.asyncio
    async def test_metadata_reports_actual_pixel_size(
        self, mock_client, data, content_type
    ):
        """The model can see whether HA honoured a resize without decoding the image."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = data
        mock_response.headers = {"content-type": content_type}
        mock_client.httpx_client.get = AsyncMock(return_value=mock_response)

        tools = CameraTools(mock_client)
        meta = _metadata(
            await tools.ha_get_camera_image(entity_id="camera.front_door", width=640)
        )

        assert (meta["width"], meta["height"]) == (1920, 1080)
        assert meta["requested_width"] == 640
        assert meta["size_bytes"] == len(data)

    @pytest.mark.asyncio
    async def test_metadata_dimensions_null_for_unparseable_image(self, mock_client):
        """A truncated or unknown image still returns, with unknown dimensions."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"\xff\xd8\xff\xe0"
        mock_response.headers = {"content-type": "image/jpeg"}
        mock_client.httpx_client.get = AsyncMock(return_value=mock_response)

        tools = CameraTools(mock_client)
        meta = _metadata(await tools.ha_get_camera_image(entity_id="camera.front_door"))

        assert meta["width"] is None and meta["height"] is None
        assert "requested_width" not in meta

    @pytest.mark.asyncio
    async def test_metadata_capture_time_comes_from_ha_date_header(self, mock_client):
        """Snapshots from two cameras can be ordered by HA's own clock."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = _jpeg(4, 3)
        mock_response.headers = {
            "content-type": "image/jpeg",
            "date": "Sun, 04 Oct 2026 18:12:35 GMT",
        }
        mock_client.httpx_client.get = AsyncMock(return_value=mock_response)

        tools = CameraTools(mock_client)
        meta = _metadata(await tools.ha_get_camera_image(entity_id="camera.front_door"))

        assert meta["captured_at"] == "2026-10-04T18:12:35+00:00"
