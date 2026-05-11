"""MCP manifest ingestion — 03_SCHEMA_VALIDATOR.md §2.2.

Ingests tool schemas from MCP server manifests and registers them
in the SchemaRegistry. Supports file paths and auto-discovery via
CLAUDE_MCP_SERVERS environment variable.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def ingest_mcp_manifest(registry, path: str) -> int:
    """
    Parse an MCP server manifest JSON and register all tool schemas.
    Returns the number of tools registered.

    MCP manifest format:
    {
      "tools": [
        {
          "name": "search_web",
          "description": "...",
          "inputSchema": { "type": "object", "properties": {...}, "required": [...] }
        }
      ]
    }
    """
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise FileNotFoundError(f"MCP manifest not found: {path}")

    with open(manifest_path) as f:
        manifest = json.load(f)

    tools = manifest.get("tools", [])
    registered = 0
    for tool in tools:
        tool_id = tool.get("name")
        schema = tool.get("inputSchema") or tool.get("input_schema")
        if not tool_id or not schema:
            logger.warning("Skipping MCP tool with missing name or schema: %s", tool)
            continue
        version = tool.get("version", "mcp-1.0")
        registry.register(
            tool_id=tool_id,
            schema=schema,
            version=version,
            source="mcp_manifest",
        )
        registered += 1
        logger.info("Registered MCP tool: %s v%s", tool_id, version)

    return registered


def discover_mcp_servers(registry) -> int:
    """
    Auto-discover MCP server manifests from CLAUDE_MCP_SERVERS env var.
    The env var is a JSON object mapping server names to config objects
    with a 'manifest' or 'schema_path' key.

    Returns total tools registered across all servers.
    """
    env_val = os.getenv("CLAUDE_MCP_SERVERS", "")
    if not env_val:
        logger.debug("CLAUDE_MCP_SERVERS not set — no MCP auto-discovery")
        return 0

    try:
        servers = json.loads(env_val)
    except json.JSONDecodeError:
        logger.warning("CLAUDE_MCP_SERVERS is not valid JSON")
        return 0

    total = 0
    for name, config in servers.items():
        manifest_path = config.get("manifest") or config.get("schema_path")
        if manifest_path:
            try:
                count = ingest_mcp_manifest(registry, manifest_path)
                total += count
                logger.info("MCP server '%s': registered %d tools", name, count)
            except Exception as e:
                logger.warning("Failed to ingest MCP server '%s': %s", name, e)

    return total
