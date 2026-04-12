#!/usr/bin/env python3
"""
Export the NeoDemos MCP tool registry as an OpenAPI 3.1 spec.

WS4 2026-04-11: the registry in `services/mcp_tool_registry.py` is the single
source of truth for every MCP tool's metadata. This script renders that
registry into a standards-compliant OpenAPI document so external integrators
can code-gen clients without parsing Python.

Usage:
    python scripts/export_mcp_openapi.py [--out docs/api/mcp_openapi.json]

The output file is committed to the repo so consumers don't need a running server.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.mcp_tool_registry import REGISTRY, ToolSpec  # noqa: E402


def _tool_to_openapi_path(tool: ToolSpec) -> dict:
    """Render one ToolSpec as a POST operation under /tools/{name}."""
    return {
        "post": {
            "operationId": tool.name,
            "summary": tool.summary,
            "description": tool.ai_description,
            "tags": ["mcp-tools"],
            "x-mcp-scopes": tool.scopes,
            "x-mcp-stability": tool.stability,
            "x-mcp-public": tool.public,
            "x-mcp-added-in": tool.added_in_version,
            "x-mcp-latency-p50-ms": tool.latency_p50_ms,
            "x-mcp-examples": [
                {
                    "description": ex.description,
                    "input": ex.input,
                    "expected_output_shape": ex.expected_output_shape,
                }
                for ex in tool.examples
            ],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": tool.input_schema or {"type": "object"},
                    }
                },
            },
            "responses": {
                "200": {
                    "description": "Successful tool invocation",
                    "content": {
                        "application/json": {
                            "schema": tool.output_schema or {"type": "string"},
                        }
                    },
                },
                "400": {"description": "Invalid parameters"},
                "401": {"description": "Unauthorized — missing or invalid OAuth token"},
                "403": {"description": "Forbidden — scope not granted"},
                "429": {"description": "Rate limit exceeded"},
                "500": {"description": "Server error"},
            },
        }
    }


def build_openapi(registry: dict[str, ToolSpec]) -> dict:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "NeoDemos MCP",
            "version": "0.2.0",
            "description": (
                "MCP tools for Rotterdam council data. Auto-generated from "
                "`services/mcp_tool_registry.py`. Do not hand-edit."
            ),
            "contact": {"url": "https://neodemos.nl"},
            "license": {"name": "Proprietary"},
        },
        "servers": [
            {"url": "https://mcp.neodemos.nl/mcp", "description": "Authenticated MCP endpoint (OAuth 2.1 + DCR)"},
            {"url": "https://mcp.neodemos.nl/public/mcp", "description": "Public MCP endpoint (no auth, rate-limited)"},
        ],
        "tags": [{"name": "mcp-tools", "description": "Retrieval + primer tools for Rotterdam council data"}],
        "paths": {
            f"/tools/{name}": _tool_to_openapi_path(spec)
            for name, spec in sorted(registry.items())
        },
        "components": {
            "securitySchemes": {
                "oauth2": {
                    "type": "oauth2",
                    "flows": {
                        "authorizationCode": {
                            "authorizationUrl": "https://mcp.neodemos.nl/authorize",
                            "tokenUrl": "https://mcp.neodemos.nl/token",
                            "scopes": {
                                "mcp": "Access MCP tools",
                                "search": "Search council documents",
                            },
                        }
                    },
                }
            }
        },
        "security": [{"oauth2": ["mcp", "search"]}],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export MCP tool registry as OpenAPI 3.1")
    parser.add_argument(
        "--out",
        default=str(PROJECT_ROOT / "docs" / "api" / "mcp_openapi.json"),
        help="Output path for the OpenAPI JSON",
    )
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    spec = build_openapi(REGISTRY)
    out_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"OpenAPI exported: {out_path}")
    print(f"  tools: {len(REGISTRY)}")
    print(f"  paths: {len(spec['paths'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
