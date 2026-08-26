"""RingsDB MCP server — exposes RingsDB public API as MCP tools using low-level Server API."""

from __future__ import annotations

import json
import re
import sys
import asyncio
from pathlib import Path
from typing import Any

# 使用底层 API 避开 FastMCP 导入错误
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

# 确保能导入同目录下的 client.py 和 mapping.py
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from client import (
        RingsDBError,
        get_all_cards,
        get_card,
        get_decklist,
        get_decklists_by_date,
        get_pack_cards,
        get_packs,
        get_scenario,
        get_top_decklists_by_card,
    )
    from mapping import (
        field_mapping_reference,
        match_ringsdb_card,
        ringsdb_card_to_csv_row,
        SERIES_TO_PACK_CODE,
    )
except ImportError as e:
    print(f"Import Error: {e}. Please ensure client.py and mapping.py are in the same folder.")
    sys.exit(1)

_all_cards_cache: list[dict[str, Any]] | None = None

def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)

def _card_summary(card: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "code", "name", "type_name", "sphere_name", "pack_name",
        "traits", "text", "cost", "threat", "willpower",
        "attack", "defense", "health", "quantity", "url"
    )
    return {k: card[k] for k in keys if k in card and card[k] not in (None, "")}

# --- 1. 定义工具列表 ---

async def handle_list_tools(context, params):
    tools = [
        types.Tool(
            name="ringsdb_get_card",
            description="Get one card by code (e.g. '01001' for Aragorn).",
            inputSchema={
                "type": "object",
                "properties": {"card_code": {"type": "string"}},
                "required": ["card_code"]
            }
        ),
        types.Tool(
            name="ringsdb_search_cards",
            description="Search cards by name, trait, or rules text.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "pack_code": {"type": "string"},
                    "type_code": {"type": "string"},
                    "sphere_code": {"type": "string"},
                    "limit": {"type": "integer"}
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="ringsdb_get_decklist",
            description="Load a published decklist by numeric ID.",
            inputSchema={
                "type": "object",
                "properties": {"decklist_id": {"type": "integer"}},
                "required": ["decklist_id"]
            }
        ),
        types.Tool(
            name="ringsdb_get_packs",
            description="List all RingsDB expansion/pack metadata.",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="ringsdb_get_pack_cards",
            description="Get all cards from one expansion/pack.",
            inputSchema={
                "type": "object",
                "properties": {"pack_code": {"type": "string"}},
                "required": ["pack_code"]
            }
        )
    ]
    return types.ListToolsResult(tools=tools)

# --- 2. 处理工具调用 ---

async def handle_call_tool(context, params: types.CallToolRequestParams):
    global _all_cards_cache
    name = params.name
    arguments = params.arguments or {}

    try:
        if name == "ringsdb_get_card":
            content = [types.TextContent(type="text", text=_json(get_card(arguments["card_code"])))]

        elif name == "ringsdb_get_decklist":
            content = [types.TextContent(type="text", text=_json(get_decklist(arguments["decklist_id"])))]

        elif name == "ringsdb_get_packs":
            content = [types.TextContent(type="text", text=_json(get_packs()))]

        elif name == "ringsdb_get_pack_cards":
            content = [types.TextContent(type="text", text=_json(get_pack_cards(arguments["pack_code"])))]

        elif name == "ringsdb_search_cards":
            query = arguments.get("query", "").strip().lower()
            if not query:
                return types.CallToolResult(content=[types.TextContent(type="text", text="Error: query empty")], is_error=True)

            pack_code = arguments.get("pack_code")
            limit = min(int(arguments.get("limit", 20)), 100)

            if pack_code:
                cards = get_pack_cards(pack_code)
            else:
                if _all_cards_cache is None:
                    _all_cards_cache = get_all_cards()
                cards = _all_cards_cache

            results = []
            for card in cards:
                haystack = " ".join(str(card.get(k, "") or "") for k in ("name", "traits", "text")).lower()
                if query in haystack:
                    results.append(_card_summary(card))
                    if len(results) >= limit:
                        break

            content = [types.TextContent(type="text", text=_json(results))]

        else:
            raise ValueError(f"Unknown tool: {name}")

        return types.CallToolResult(content=content)

    except Exception as e:
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"Error: {str(e)}")], is_error=True)

# --- 3. 启动服务 ---

app = Server(
    "ringsdb",
    on_list_tools=handle_list_tools,
    on_call_tool=handle_call_tool,
)

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )

if __name__ == "__main__":
    asyncio.run(main())
