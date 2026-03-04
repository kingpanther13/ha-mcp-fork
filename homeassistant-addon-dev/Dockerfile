# syntax=docker/dockerfile:1
# Home Assistant MCP Server Add-on (Dev Channel)
# Multi-stage build: uv for dependency resolution, slim Python for runtime
# WARNING: This is unstable and may break at any time

# --- Build stage: install dependencies with uv ---
FROM ghcr.io/astral-sh/uv:0.10.5-python3.14-trixie-slim@sha256:4e82a63c7a103ade000812e9d0e05024c36f9653e9dea56b0aa8e28823b039c6 AS builder

WORKDIR /app

# Compile bytecode for faster startup; copy mode required with cache mounts
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Install build dependencies for packages without prebuilt 3.14 wheels (cffi)
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc-dev libffi-dev && rm -rf /var/lib/apt/lists/*

# Install dependencies first (cached separately from source changes)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

# Copy source and install the project itself
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# --- Runtime stage: clean image without uv ---
FROM python:3.14-slim@sha256:6a27522252aef8432841f224d9baaa6e9fce07b07584154fa0b9a96603af7456

WORKDIR /app

# Copy the virtual environment and startup script from builder
COPY --from=builder /app/.venv /app/.venv
COPY homeassistant-addon/start.py /

# Activate virtual environment via PATH
ENV PATH="/app/.venv/bin:$PATH"

# Labels
ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL \
    io.hass.name="Home Assistant MCP Server (Dev)" \
    io.hass.description="Development channel - AI assistant integration via MCP" \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="addon" \
    io.hass.arch="${BUILD_ARCH}"

CMD ["python3", "/start.py"]
