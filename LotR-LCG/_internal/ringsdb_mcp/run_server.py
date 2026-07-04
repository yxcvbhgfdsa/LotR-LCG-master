"""Standalone RingsDB MCP server launcher.

This version avoids external MCP dependencies so Codex can start it directly.
"""

from __future__ import annotations

import json
import re
import sys
import traceback
from pathlib import Path
from typing import Any, Callable


_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from client import (  # noqa: E402
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
from mapping import (  # noqa: E402
    field_mapping_reference,
    match_ringsdb_card,
    ringsdb_card_to_csv_row,
)


PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "ringsdb"
SERVER_VERSION = "1.0.0"

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


def ringsdb_get_card(card_code: str) -> str:
    try:
        return _json(get_card(card_code))
    except RingsDBError as exc:
        return f"Error: {exc}"


def ringsdb_query_card(
    query: str,
    pack_code: str = "",
    type_code: str = "",
    sphere_code: str = "",
    limit: int = 20,
) -> str:
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


def ringsdb_get_pack_cards(pack_code: str) -> str:
    try:
        cards = get_pack_cards(pack_code)
        return _json({"pack_code": pack_code, "count": len(cards), "cards": cards})
    except RingsDBError as exc:
        return f"Error: {exc}"


def ringsdb_get_packs() -> str:
    try:
        return _json(get_packs())
    except RingsDBError as exc:
        return f"Error: {exc}"


def ringsdb_get_scenario(scenario_id: str) -> str:
    try:
        return _json(get_scenario(scenario_id))
    except RingsDBError as exc:
        return f"Error: {exc}"


def ringsdb_get_decklist(decklist_id: int) -> str:
    try:
        return _json(get_decklist(decklist_id))
    except RingsDBError as exc:
        return f"Error: {exc}"


def ringsdb_get_decklists_by_date(date: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return "Error: date must be YYYY-MM-DD"
    try:
        decklists = get_decklists_by_date(date)
        return _json({"date": date, "count": len(decklists), "decklists": decklists})
    except RingsDBError as exc:
        return f"Error: {exc}"


def ringsdb_get_top_decklists_by_card(card_code: str) -> str:
    try:
        decklists = get_top_decklists_by_card(card_code)
        return _json({"card_code": card_code, "count": len(decklists), "decklists": decklists})
    except RingsDBError as exc:
        return f"Error: {exc}"


def ringsdb_search_cards(
    query: str,
    pack_code: str = "",
    type_code: str = "",
    sphere_code: str = "",
    limit: int = 20,
) -> str:
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


def ringsdb_field_mapping() -> str:
    return _json(field_mapping_reference())


def ringsdb_convert_to_csv(card_code: str, series: str = "") -> str:
    try:
        card = get_card(card_code)
        return _json(ringsdb_card_to_csv_row(card, series=series))
    except RingsDBError as exc:
        return f"Error: {exc}"


def ringsdb_match_csv_card(series: str, number: str, image_uuid: str = "") -> str:
    global _all_cards_cache
    csv_row = {"系列": series, "编号": number, "图片链接": image_uuid}
    try:
        if _all_cards_cache is None:
            _all_cards_cache = get_all_cards()
        from mapping import SERIES_TO_PACK_CODE

        series_map = dict(SERIES_TO_PACK_CODE)
        if image_uuid:
            uid_card = next((c for c in _all_cards_cache if c.get("octgnid") == image_uuid), None)
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


TOOL_HANDLERS: dict[str, tuple[Callable[..., str], dict[str, Any]]] = {
    "ringsdb_get_card": (
        ringsdb_get_card,
        {
            "type": "object",
            "properties": {
                "card_code": {"type": "string", "description": "RingsDB card code, e.g. 01001."}
            },
            "required": ["card_code"],
            "additionalProperties": False,
        },
    ),
    "ringsdb_query_card": (
        ringsdb_query_card,
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Card code or search text."},
                "pack_code": {"type": "string", "default": ""},
                "type_code": {"type": "string", "default": ""},
                "sphere_code": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    "ringsdb_get_pack_cards": (
        ringsdb_get_pack_cards,
        {
            "type": "object",
            "properties": {
                "pack_code": {"type": "string", "description": "Pack code, e.g. Core."}
            },
            "required": ["pack_code"],
            "additionalProperties": False,
        },
    ),
    "ringsdb_get_packs": (
        ringsdb_get_packs,
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    "ringsdb_get_scenario": (
        ringsdb_get_scenario,
        {
            "type": "object",
            "properties": {
                "scenario_id": {"type": "string", "description": "Scenario ID, e.g. 01001."}
            },
            "required": ["scenario_id"],
            "additionalProperties": False,
        },
    ),
    "ringsdb_get_decklist": (
        ringsdb_get_decklist,
        {
            "type": "object",
            "properties": {
                "decklist_id": {"type": "integer", "description": "Published decklist ID."}
            },
            "required": ["decklist_id"],
            "additionalProperties": False,
        },
    ),
    "ringsdb_get_decklists_by_date": (
        ringsdb_get_decklists_by_date,
        {
            "type": "object",
            "properties": {"date": {"type": "string", "description": "Date in YYYY-MM-DD format."}},
            "required": ["date"],
            "additionalProperties": False,
        },
    ),
    "ringsdb_get_top_decklists_by_card": (
        ringsdb_get_top_decklists_by_card,
        {
            "type": "object",
            "properties": {
                "card_code": {"type": "string", "description": "Card code like 01001."}
            },
            "required": ["card_code"],
            "additionalProperties": False,
        },
    ),
    "ringsdb_search_cards": (
        ringsdb_search_cards,
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search text."},
                "pack_code": {"type": "string", "default": ""},
                "type_code": {"type": "string", "default": ""},
                "sphere_code": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    "ringsdb_field_mapping": (
        ringsdb_field_mapping,
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    "ringsdb_convert_to_csv": (
        ringsdb_convert_to_csv,
        {
            "type": "object",
            "properties": {
                "card_code": {"type": "string", "description": "RingsDB card code."},
                "series": {"type": "string", "default": ""},
            },
            "required": ["card_code"],
            "additionalProperties": False,
        },
    ),
    "ringsdb_match_csv_card": (
        ringsdb_match_csv_card,
        {
            "type": "object",
            "properties": {
                "series": {"type": "string"},
                "number": {"type": "string"},
                "image_uuid": {"type": "string", "default": ""},
            },
            "required": ["series", "number"],
            "additionalProperties": False,
        },
    ),
}


TOOL_DESCRIPTIONS: dict[str, str] = {
    "ringsdb_get_card": "Get one card by code.",
    "ringsdb_query_card": "Query cards by code, name, trait, or rules text.",
    "ringsdb_get_pack_cards": "Get all cards from one expansion or pack.",
    "ringsdb_get_packs": "List all RingsDB pack metadata.",
    "ringsdb_get_scenario": "Get scenario or quest data by ID.",
    "ringsdb_get_decklist": "Load a published decklist by numeric ID.",
    "ringsdb_get_decklists_by_date": "Get decklists published on a given date.",
    "ringsdb_get_top_decklists_by_card": "Get top decklists containing a specific card.",
    "ringsdb_search_cards": "Search cards by name, trait, or rules text.",
    "ringsdb_field_mapping": "Return the RingsDB to CSV field mapping reference.",
    "ringsdb_convert_to_csv": "Convert one RingsDB card to the CSV row format used by this project.",
    "ringsdb_match_csv_card": "Match a local CSV row to a RingsDB card.",
}


def _make_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _make_error(request_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _write_message(message: dict[str, Any]) -> None:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def _read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        if b":" not in line:
            continue
        key, value = line.split(b":", 1)
        headers[key.decode("ascii", errors="ignore").lower()] = value.decode("utf-8", errors="ignore").strip()

    content_length = headers.get("content-length")
    if not content_length:
        return None
    length = int(content_length)
    body = sys.stdin.buffer.read(length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def _handle_initialize(request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    protocol_version = params.get("protocolVersion") or PROTOCOL_VERSION
    return _make_response(
        request_id,
        {
            "protocolVersion": protocol_version,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {}},
        },
    )


def _handle_tools_list(request_id: Any) -> dict[str, Any]:
    tools = []
    for name, (_, schema) in TOOL_HANDLERS.items():
        tools.append(
            {
                "name": name,
                "description": TOOL_DESCRIPTIONS.get(name, ""),
                "inputSchema": schema,
            }
        )
    return _make_response(request_id, {"tools": tools})


def _handle_tools_call(request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if name not in TOOL_HANDLERS:
        return _make_error(request_id, -32601, f"Unknown tool: {name}")

    handler, _ = TOOL_HANDLERS[name]
    try:
        result = handler(**arguments)
        return _make_response(request_id, {"content": [{"type": "text", "text": result}]})
    except TypeError as exc:
        return _make_error(request_id, -32602, f"Invalid arguments for {name}", str(exc))
    except Exception as exc:  # noqa: BLE001
        return _make_error(request_id, -32000, f"Tool execution failed: {name}", str(exc))


def serve() -> None:
    while True:
        request = _read_message()
        if request is None:
            return

        if request.get("method") is None:
            continue

        method = request["method"]
        request_id = request.get("id")
        params = request.get("params") or {}

        try:
            if method == "initialize":
                response = _handle_initialize(request_id, params)
            elif method == "tools/list":
                response = _handle_tools_list(request_id)
            elif method == "tools/call":
                response = _handle_tools_call(request_id, params)
            elif method == "ping":
                response = _make_response(request_id, {})
            elif request_id is None:
                continue
            else:
                response = _make_error(request_id, -32601, f"Method not found: {method}")
        except Exception as exc:  # noqa: BLE001
            response = _make_error(request_id, -32000, f"Internal error handling {method}", str(exc))

        if request_id is not None:
            _write_message(response)


def main() -> None:
    try:
        serve()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
