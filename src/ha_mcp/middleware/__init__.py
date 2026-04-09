"""Middleware components for the Home Assistant MCP Server."""

from .first_call_docs import FirstCallDocsMiddleware

__all__ = ["FirstCallDocsMiddleware"]
