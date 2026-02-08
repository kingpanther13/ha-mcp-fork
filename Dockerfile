# Home Assistant MCP Server Add-on (Local Fork)
# Built locally from source for development/testing
# Based on homeassistant-addon/Dockerfile

FROM ghcr.io/astral-sh/uv:python3.13-bookworm

WORKDIR /app

# Copy project files from project root
COPY pyproject.toml ./
COPY src ./src

# Install dependencies and project with uv
RUN uv pip install --system --no-cache .

# Copy Python startup script
COPY homeassistant-addon/start.py /
RUN chmod a+x /start.py

# Labels
LABEL \
    io.hass.name="Home Assistant MCP Server (Fork)" \
    io.hass.description="AI assistant integration via Model Context Protocol" \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="addon" \
    io.hass.arch="${BUILD_ARCH}"

CMD ["python3", "/start.py"]
