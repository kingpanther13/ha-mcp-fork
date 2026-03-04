# syntax=docker/dockerfile:1
# Home Assistant MCP Server - Production Docker Image
# Multi-stage build: uv for dependency resolution, slim Python for runtime
# Python 3.14 - Security support until 2030-10
# Base images pinned by digest - Renovate will create PRs for updates

# --- Build stage: install dependencies with uv ---
FROM ghcr.io/astral-sh/uv:0.10.5-python3.14-trixie-slim@sha256:4e82a63c7a103ade000812e9d0e05024c36f9653e9dea56b0aa8e28823b039c6 AS builder

WORKDIR /app

# Compile bytecode for faster startup; copy mode required with cache mounts
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Install build dependencies for packages without prebuilt 3.14 wheels (cffi)
RUN apt-get update && apt-get install -y --no-install-recommends gcc libffi-dev && rm -rf /var/lib/apt/lists/*

# Install dependencies first (cached separately from source changes)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

# Copy source and config, then install the project itself
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# --- Runtime stage: clean image without uv ---
FROM python:3.14-slim@sha256:6a27522252aef8432841f224d9baaa6e9fce07b07584154fa0b9a96603af7456

LABEL org.opencontainers.image.title="Home Assistant MCP Server" \
      org.opencontainers.image.description="AI assistant integration for Home Assistant via Model Context Protocol" \
      org.opencontainers.image.source="https://github.com/homeassistant-ai/ha-mcp" \
      org.opencontainers.image.licenses="MIT" \
      io.modelcontextprotocol.server.name="io.github.homeassistant-ai/ha-mcp"

# Create non-root user for security
RUN groupadd -r mcpuser && useradd -r -g mcpuser -m mcpuser

WORKDIR /app

# Copy the virtual environment, source, and config from builder
COPY --chown=mcpuser:mcpuser --from=builder /app/.venv /app/.venv
COPY --chown=mcpuser:mcpuser --from=builder /app/src /app/src
COPY --chown=mcpuser:mcpuser fastmcp.json fastmcp-http.json ./

USER mcpuser

# Activate virtual environment via PATH
ENV PATH="/app/.venv/bin:$PATH"

# Environment variables (can be overridden)
ENV HOMEASSISTANT_URL="" \
    HOMEASSISTANT_TOKEN="" \
    BACKUP_HINT="normal"

# Default: Run in stdio mode using fastmcp.json
# For HTTP mode: docker run ... IMAGE ha-mcp-web
CMD ["fastmcp", "run", "fastmcp.json"]
