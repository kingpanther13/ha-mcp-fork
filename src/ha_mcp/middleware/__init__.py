"""Middleware components for the Home Assistant MCP Server."""

from .password_gated_docs import PasswordGatedDocsMiddleware

__all__ = ["PasswordGatedDocsMiddleware"]
