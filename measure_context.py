#!/usr/bin/env python3
"""Measure the MCP tool context footprint.

This script instantiates the MCP server with a mock client,
extracts all tool schemas, and measures their token/character cost.
"""

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Mock the HA client before importing server
mock_client = MagicMock()
mock_client.get_entity_state = AsyncMock(return_value={})
mock_client.get_states = AsyncMock(return_value=[])
mock_client.call_service = AsyncMock(return_value=[])
mock_client.get_config = AsyncMock(return_value={})
mock_client.test_connection = AsyncMock(return_value=(True, None))
mock_client.send_websocket_message = AsyncMock(return_value={})

# Patch settings to avoid needing env vars
with patch("ha_mcp.config.get_global_settings") as mock_settings:
    settings = MagicMock()
    settings.mcp_server_name = "ha-mcp"
    settings.mcp_server_version = "6.5.0"
    settings.enabled_tool_modules = "all"
    settings.homeassistant_url = "http://localhost:8123"
    settings.homeassistant_token = "fake-token"
    settings.enable_filesystem_tools = True
    settings.enable_hacs_tools = True
    settings.allowed_paths = ["/config"]
    mock_settings.return_value = settings

    from ha_mcp.server import HomeAssistantSmartMCPServer

    server = HomeAssistantSmartMCPServer(client=mock_client)

# Extract tool schemas from FastMCP
mcp = server.mcp


async def extract_tools():
    """Extract all registered tools and their schemas."""
    # FastMCP stores tools internally - access them
    tools = {}

    # Try to get tools from FastMCP's internal registry
    if hasattr(mcp, '_tool_manager'):
        tool_manager = mcp._tool_manager
        if hasattr(tool_manager, '_tools'):
            for name, tool in tool_manager._tools.items():
                tools[name] = tool

    # Alternative: use the list_tools method if available
    if not tools and hasattr(mcp, 'list_tools'):
        tool_list = await mcp.list_tools()
        for t in tool_list:
            tools[t.name] = t

    # Try another approach - get the MCP types
    if not tools:
        # Walk through mcp attributes to find tools
        for attr_name in dir(mcp):
            obj = getattr(mcp, attr_name, None)
            if hasattr(obj, '_tools') or hasattr(obj, 'tools'):
                inner = getattr(obj, '_tools', None) or getattr(obj, 'tools', None)
                if isinstance(inner, dict):
                    tools.update(inner)

    return tools


async def main():
    tools = await extract_tools()

    if not tools:
        # Try a different approach: access FastMCP's internal state
        print("Trying alternative tool extraction...")
        # FastMCP v2 stores tools differently
        for attr in ['_tools', 'tools', '_registered_tools']:
            if hasattr(mcp, attr):
                candidate = getattr(mcp, attr)
                if isinstance(candidate, dict) and candidate:
                    tools = candidate
                    break

    if not tools:
        print(f"Could not find tools. FastMCP type: {type(mcp)}")
        print(f"FastMCP attributes: {[a for a in dir(mcp) if not a.startswith('__')]}")
        # Try to get tools through the MCP protocol
        if hasattr(mcp, '_mcp_server'):
            srv = mcp._mcp_server
            print(f"Inner server type: {type(srv)}")
            print(f"Inner server attrs: {[a for a in dir(srv) if not a.startswith('__')]}")
        return

    print(f"\n{'='*80}")
    print(f"MCP TOOL CONTEXT FOOTPRINT ANALYSIS")
    print(f"{'='*80}\n")
    print(f"Total tools registered: {len(tools)}\n")

    # Build the JSON representation that would go over the wire
    tool_schemas = []
    per_tool_data = []

    for name, tool in sorted(tools.items()):
        # Try to get the MCP-protocol representation
        schema = {}
        if hasattr(tool, 'to_mcp_tool'):
            mcp_tool = tool.to_mcp_tool()
            schema = {
                "name": mcp_tool.name,
                "description": mcp_tool.description or "",
                "inputSchema": mcp_tool.inputSchema if hasattr(mcp_tool, 'inputSchema') else {},
            }
            if hasattr(mcp_tool, 'annotations') and mcp_tool.annotations:
                schema["annotations"] = mcp_tool.annotations
        elif hasattr(tool, 'name') and hasattr(tool, 'description'):
            schema = {
                "name": tool.name,
                "description": tool.description or "",
            }
            if hasattr(tool, 'parameters'):
                schema["inputSchema"] = tool.parameters
            elif hasattr(tool, 'inputSchema'):
                schema["inputSchema"] = tool.inputSchema
        else:
            schema = {"name": name, "raw": str(tool)}

        tool_json = json.dumps(schema, indent=2, default=str)
        tool_chars = len(tool_json)
        # Rough token estimate: ~4 chars per token for English/JSON
        tool_tokens_estimate = tool_chars / 4

        per_tool_data.append({
            "name": schema.get("name", name),
            "description_len": len(schema.get("description", "")),
            "json_chars": tool_chars,
            "estimated_tokens": int(tool_tokens_estimate),
            "param_count": len(schema.get("inputSchema", {}).get("properties", {})),
        })

        tool_schemas.append(schema)

    # Serialize the full tools list as it would appear in context
    full_json = json.dumps(tool_schemas, indent=2, default=str)
    full_chars = len(full_json)
    full_tokens_rough = full_chars / 4  # rough estimate

    # Also compute compact JSON (no indent)
    compact_json = json.dumps(tool_schemas, separators=(',', ':'), default=str)
    compact_chars = len(compact_json)
    compact_tokens_rough = compact_chars / 4

    # Sort by size for the report
    per_tool_data.sort(key=lambda x: x["json_chars"], reverse=True)

    print(f"{'TOOL NAME':<45} {'DESC LEN':>8} {'PARAMS':>6} {'CHARS':>7} {'~TOKENS':>8}")
    print(f"{'-'*45} {'-'*8} {'-'*6} {'-'*7} {'-'*8}")
    for t in per_tool_data:
        print(f"{t['name']:<45} {t['description_len']:>8} {t['param_count']:>6} {t['json_chars']:>7} {t['estimated_tokens']:>8}")

    print(f"\n{'='*80}")
    print(f"TOTALS")
    print(f"{'='*80}")
    print(f"Total tools:                    {len(tool_schemas)}")
    print(f"Total JSON characters (pretty): {full_chars:,}")
    print(f"Total JSON characters (compact):{compact_chars:,}")
    print(f"Estimated tokens (pretty):      ~{int(full_tokens_rough):,}")
    print(f"Estimated tokens (compact):     ~{int(compact_tokens_rough):,}")
    print(f"\nAverage per tool:")
    print(f"  Characters:  {full_chars // len(tool_schemas):,}")
    print(f"  ~Tokens:     ~{int(full_tokens_rough) // len(tool_schemas):,}")

    # Description-only analysis
    total_desc_chars = sum(t["description_len"] for t in per_tool_data)
    print(f"\nDescription text only:")
    print(f"  Total characters:    {total_desc_chars:,}")
    print(f"  Estimated tokens:    ~{total_desc_chars // 4:,}")

    # Top 10 heaviest tools
    print(f"\n{'='*80}")
    print(f"TOP 10 HEAVIEST TOOLS")
    print(f"{'='*80}")
    for i, t in enumerate(per_tool_data[:10], 1):
        print(f"  {i:2}. {t['name']:<40} {t['json_chars']:>6} chars  ~{t['estimated_tokens']:>5} tokens  ({t['param_count']} params)")

    # Context window impact
    print(f"\n{'='*80}")
    print(f"CONTEXT WINDOW IMPACT")
    print(f"{'='*80}")
    for model, ctx_size in [("Claude Sonnet 3.5", 200_000), ("Claude Opus 4", 200_000), ("GPT-4o", 128_000), ("Claude 200K", 200_000)]:
        pct = (full_tokens_rough / ctx_size) * 100
        print(f"  {model} ({ctx_size:,} tokens): ~{pct:.1f}% of context used by tool definitions")

    # Write full schemas to file for inspection
    with open("tool_schemas.json", "w") as f:
        json.dump(tool_schemas, f, indent=2, default=str)
    print(f"\nFull tool schemas written to tool_schemas.json")


asyncio.run(main())
