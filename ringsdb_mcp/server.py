"""RingsDB MCP server — exposes RingsDB public API as MCP tools."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
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
)

mcp = FastMCP(
    "ringsdb",
    instructions=(
        "RingsDB API for The Lord of the Rings: The Card Game. "
        "Use card codes like '01001' (Aragorn). Pack codes like 'Core'. "
        "Docs: https://ringsdb.com/api/doc"
    ),
)

_all_cards_cache: list[dict[str, Any]] | None = None


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _card_summary(card: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "code",
        "name",
        "type_name",
        "sphere_name",
        "pack_name",
        "traits",
        "text",
        "cost",
        "threat",
        "willpower",
        "attack",
        "defense",
        "health",
        "quantity",
        "url",
    )
    return {k: card[k] for k in keys if k in card and card[k] not in (None, "")}


@mcp.tool()
def ringsdb_get_card(card_code: str) -> str:
    """Get one card by code (e.g. '01001' for Aragorn).

    Args:
        card_code: RingsDB card code, typically 5 digits like '01001'.
    """
    try:
        return _json(get_card(card_code))
    except RingsDBError as exc:
        return f"Error: {exc}"


@mcp.tool()
def ringsdb_query_card(
    query: str,
    pack_code: str = "",
    type_code: str = "",
    sphere_code: str = "",
    limit: int = 20,
) -> str:
    """Query cards by code, name, trait, or rules text."""
    query = query.strip()
    if not query:
        return "Error: query must not be empty"
    if re.fullmatch(r"\d{5}", query):
        return ringsdb_get_card(query)
    return ringsdb_search_cards(
        query=query,
        pack_code=pack_code,
        type_code=type_code,
        sphere_code=sphere_code,
        limit=limit,
    )


@mcp.tool()
def ringsdb_get_pack_cards(pack_code: str) -> str:
    """Get all cards from one expansion/pack (e.g. 'Core', 'HoN').

    Args:
        pack_code: RingsDB pack code. Use ringsdb_get_packs to list codes.
    """
    try:
        cards = get_pack_cards(pack_code)
        return _json({"pack_code": pack_code, "count": len(cards), "cards": cards})
    except RingsDBError as exc:
        return f"Error: {exc}"


@mcp.tool()
def ringsdb_get_packs() -> str:
    """List all RingsDB expansion/pack metadata (name, code, release date, card counts)."""
    try:
        return _json(get_packs())
    except RingsDBError as exc:
        return f"Error: {exc}"


@mcp.tool()
def ringsdb_get_scenario(scenario_id: str) -> str:
    """Get scenario/quest data by ID (e.g. '01001' for Passage Through Mirkwood).

    Args:
        scenario_id: RingsDB scenario identifier.
    """
    try:
        return _json(get_scenario(scenario_id))
    except RingsDBError as exc:
        return f"Error: {exc}"


@mcp.tool()
def ringsdb_get_decklist(decklist_id: int) -> str:
    """Load a published decklist by numeric ID.

    Args:
        decklist_id: Published decklist ID from ringsdb.com.
    """
    try:
        return _json(get_decklist(decklist_id))
    except RingsDBError as exc:
        return f"Error: {exc}"


@mcp.tool()
def ringsdb_get_decklists_by_date(date: str) -> str:
    """Get decklists published on a given date.

    Args:
        date: Date in YYYY-MM-DD format.
    """
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return "Error: date must be YYYY-MM-DD"
    try:
        decklists = get_decklists_by_date(date)
        return _json({"date": date, "count": len(decklists), "decklists": decklists})
    except RingsDBError as exc:
        return f"Error: {exc}"


@mcp.tool()
def ringsdb_get_top_decklists_by_card(card_code: str) -> str:
    """Get top 10 published decklists containing a specific card.

    Args:
        card_code: Card code like '01001'.
    """
    try:
        decklists = get_top_decklists_by_card(card_code)
        return _json({"card_code": card_code, "count": len(decklists), "decklists": decklists})
    except RingsDBError as exc:
        return f"Error: {exc}"


@mcp.tool()
def ringsdb_search_cards(
    query: str,
    pack_code: str = "",
    type_code: str = "",
    sphere_code: str = "",
    limit: int = 20,
) -> str:
    """Search cards by name, trait, or rules text.

    Args:
        query: Search text (case-insensitive). Matches name, traits, and text.
        pack_code: Optional pack filter (e.g. 'Core'). Empty = search all cards.
        type_code: Optional type filter: hero, ally, attachment, event, treachery, etc.
        sphere_code: Optional sphere filter: leadership, lore, spirit, tactics, neutral.
        limit: Max results (default 20, max 100).
    """
    global _all_cards_cache

    query = query.strip()
    if not query:
        return "Error: query must not be empty"

    limit = max(1, min(limit, 100))
    q = query.lower()

    try:
        if pack_code:
            cards = get_pack_cards(pack_code)
        else:
            if _all_cards_cache is None:
                _all_cards_cache = get_all_cards()
            cards = _all_cards_cache
    except RingsDBError as exc:
        return f"Error: {exc}"

    results: list[dict[str, Any]] = []
    for card in cards:
        if type_code and card.get("type_code", "").lower() != type_code.lower():
            continue
        if sphere_code and card.get("sphere_code", "").lower() != sphere_code.lower():
            continue
        haystack = " ".join(
            str(card.get(k, "") or "")
            for k in ("name", "traits", "text", "flavor", "type_name", "sphere_name")
        ).lower()
        if q not in haystack:
            continue
        results.append(_card_summary(card))
        if len(results) >= limit:
            break

    return _json(
        {
            "query": query,
            "pack_code": pack_code or None,
            "type_code": type_code or None,
            "sphere_code": sphere_code or None,
            "count": len(results),
            "cards": results,
        }
    )


@mcp.tool()
def ringsdb_field_mapping() -> str:
    """Return field mapping reference between RingsDB JSON and 魔戒玩家牌.csv."""
    return _json(field_mapping_reference())


@mcp.tool()
def ringsdb_convert_to_csv(card_code: str, series: str = "") -> str:
    """Convert one RingsDB card to 魔戒玩家牌.csv row format.

    Args:
        card_code: RingsDB card code, e.g. '01001'.
        series: Optional Chinese series name (e.g. '基础'). Defaults to pack_name.
    """
    try:
        card = get_card(card_code)
        return _json(ringsdb_card_to_csv_row(card, series=series))
    except RingsDBError as exc:
        return f"Error: {exc}"


@mcp.tool()
def ringsdb_match_csv_card(
    series: str,
    number: str,
    image_uuid: str = "",
) -> str:
    """Find RingsDB card matching a local CSV row (by UUID or series+number).

    Args:
        series: CSV 系列 column, e.g. '基础'.
        number: CSV 编号 column.
        image_uuid: CSV 图片链接 (octgnid), optional but preferred.
    """
    global _all_cards_cache
    csv_row = {"系列": series, "编号": number, "图片链接": image_uuid}
    try:
        if _all_cards_cache is None:
            _all_cards_cache = get_all_cards()
        from mapping import SERIES_TO_PACK_CODE

        series_map = dict(SERIES_TO_PACK_CODE)
        if image_uuid:
            uid_card = next(
                (c for c in _all_cards_cache if c.get("octgnid") == image_uuid),
                None,
            )
            if uid_card:
                series_map[series] = uid_card["pack_code"]
        matched = match_ringsdb_card(csv_row, _all_cards_cache, series_map)
        if not matched:
            return _json({"matched": False, "csv_row": csv_row, "series_map": series_map})
        return _json(
            {
                "matched": True,
                "ringsdb": matched,
                "csv_equivalent": ringsdb_card_to_csv_row(matched, series=series or ""),
            }
        )
    except RingsDBError as exc:
        return f"Error: {exc}"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
