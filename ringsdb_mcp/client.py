"""RingsDB HTTP API client (https://ringsdb.com/api/doc)."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://ringsdb.com/api/public"
USER_AGENT = "LotR-LCG-RingsDB-MCP/1.0"


class RingsDBError(Exception):
    """RingsDB API request failed."""


def _get(path: str, timeout: float = 90.0, retries: int = 3) -> Any:
    url = f"{BASE_URL}{path}"
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last_err: BaseException | None = None
    for attempt in range(max(1, retries)):
        try:
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RingsDBError(f"HTTP {exc.code} for {url}: {body[:500]}") from exc
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_err = exc
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RingsDBError(f"Network error for {url}: {last_err}") from last_err


def get_card(card_code: str) -> dict[str, Any]:
    return _get(f"/card/{card_code}.json")


def get_all_cards() -> list[dict[str, Any]]:
    return _get("/cards/")


def get_pack_cards(pack_code: str) -> list[dict[str, Any]]:
    return _get(f"/cards/{pack_code}.json")


def get_packs() -> list[dict[str, Any]]:
    return _get("/packs/")


def get_scenario(scenario_id: str) -> dict[str, Any]:
    return _get(f"/scenario/{scenario_id}.json")


def get_decklist(decklist_id: int) -> dict[str, Any]:
    return _get(f"/decklist/{decklist_id}.json")


def get_decklists_by_date(date: str) -> list[dict[str, Any]]:
    return _get(f"/decklists/by_date/{date}.json")


def get_top_decklists_by_card(card_code: str) -> list[dict[str, Any]]:
    return _get(f"/decklists/top_by_card/{card_code}.json")
