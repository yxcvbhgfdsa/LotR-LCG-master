import sys
import json
import csv
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import URLError
from urllib.request import Request, urlopen

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QGridLayout,
    QScrollArea, QMessageBox, QFileDialog, QDialog,
    QMenu, QAction, QCheckBox, QInputDialog,
)
from PyQt5.QtCore import Qt, pyqtSignal, QSize, QTimer
from PyQt5.QtGui import QPixmap, QFont, QCursor

from 玩家卡抽取 import (
    PLAYER_CSV,
    DEFAULT_DECK_SERIES as PLAYER_DEFAULT_SERIES,
    DEFAULT_DECK_LIST_TEXT,
    Card as PlayerCard,
    build_player_deck_from_spec,
    build_player_deck_from_text,
    resolve_player_image,
    lookup_card_row,
    build_player_name_index,
    player_row_display_name,
    _read_player_csv_rows,
    DeckListDialog,
)
from 遭遇抽取 import (
    ENCOUNTER_CSV,
    DEFAULT_DECK_SERIES as ENCOUNTER_DEFAULT_SERIES,
    Card as EncounterCard,
    load_encounter_cards_from_csv,
    resolve_encounter_image,
)

_PROJECT_ROOT = Path(__file__).resolve().parent

CONFIG_FILTER = (
    "卡组文件 (*.json *.csv *.txt *.o8d);;"
    "JSON (*.json);;CSV (*.csv);;Main Deck 文本 (*.txt);;OctGN 卡组 (*.o8d);;"
    "All Files (*)"
)


def _build_image_id_index(csv_path: Path) -> Dict[str, Dict[str, str]]:
    index: Dict[str, Dict[str, str]] = {}
    if not csv_path.is_file():
        return index
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            image_id = (row.get("图片链接") or "").strip()
            if image_id:
                index[image_id] = row
    return index


def _build_name_index(csv_path: Path) -> Dict[tuple[str, str], Dict[str, str]]:
    if csv_path.resolve() == PLAYER_CSV.resolve():
        return build_player_name_index(_read_player_csv_rows(csv_path))
    index: Dict[tuple[str, str], Dict[str, str]] = {}
    for row in _read_encounter_rows(csv_path):
        series = (row.get("系列") or "").strip()
        name = (row.get("卡牌名称") or "").strip()
        if series and name:
            index[(series, name)] = row
    return index


def _read_encounter_rows(csv_path: Path) -> List[Dict[str, str]]:
    if not csv_path.is_file():
        return []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _viewer_dict_from_player(card: PlayerCard) -> Dict[str, Any]:
    return {
        "name": card.name,
        "image_path": card.image_path or "",
        "id": card.id,
        "type": card.type,
        "series": card.series,
        "deck_type": "player",
        "Threat": card.Threat,
        "Cost": card.Cost,
        "Sphere": card.Sphere,
    }


def _viewer_dict_from_encounter(card: EncounterCard) -> Dict[str, Any]:
    return {
        "name": card.name,
        "image_path": card.image_path or "",
        "id": card.id,
        "type": card.type,
        "series": "",
        "deck_type": "encounter",
        "Threat": card.Threat or card.Threat_Level,
    }


def _resolve_image_path(
    card: Dict[str, Any],
    player_by_id: Dict[str, Dict[str, str]],
    encounter_by_id: Dict[str, Dict[str, str]],
    player_by_name: Dict[tuple[str, str], Dict[str, str]],
    encounter_by_name: Dict[tuple[str, str], Dict[str, str]],
) -> str:
    path = str(card.get("image_path") or "").strip()
    if path and Path(path).is_file():
        return path

    card_id = str(card.get("id") or "").strip()
    if card_id:
        for resolver, index in (
            (resolve_player_image, player_by_id),
            (resolve_encounter_image, encounter_by_id),
        ):
            if card_id in index:
                row = index[card_id]
                deck_type = "player" if index is player_by_id else "encounter"
                if deck_type == "player":
                    c = PlayerCard.from_csv_row(row)
                    return c.image_path or resolver(card_id)
                c = EncounterCard.from_csv_row(row)
                return c.image_path or resolver(card_id)
            resolved = resolver(card_id)
            if resolved and Path(resolved).is_file():
                return resolved

    name = str(card.get("name") or "").strip()
    series = str(card.get("series") or PLAYER_DEFAULT_SERIES).strip()
    if name:
        row = lookup_card_row(player_by_name, series, name)
        if row:
            return PlayerCard.from_csv_row(row).image_path or ""
        row = lookup_card_row(encounter_by_name, series, name)
        if row:
            return EncounterCard.from_csv_row(row).image_path or ""
        for index in (player_by_name, encounter_by_name):
            for (row_series, row_name), row in index.items():
                if row_name == name:
                    if index is player_by_name:
                        return PlayerCard.from_csv_row(row).image_path or ""
                    return EncounterCard.from_csv_row(row).image_path or ""
    return path


def _patch_json_cards(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    player_by_id = _build_image_id_index(PLAYER_CSV)
    encounter_by_id = _build_image_id_index(ENCOUNTER_CSV)
    player_by_name = _build_name_index(PLAYER_CSV)
    encounter_by_name = _build_name_index(ENCOUNTER_CSV)
    patched = []
    for i, card in enumerate(cards):
        if str(card.get("Discard", "0")).strip() == "1":
            continue
        item = dict(card)
        item.setdefault("name", f"卡牌{i + 1}")
        item["image_path"] = _resolve_image_path(
            item, player_by_id, encounter_by_id, player_by_name, encounter_by_name
        )
        patched.append(item)
    return patched


def _load_json_deck(path: Path) -> Tuple[str, List[Dict[str, Any]]]:
    with open(path, encoding="utf-8") as f:
        config = json.load(f)

    deck_name = str(config.get("deck_name") or path.stem)

    if config.get("deck_spec"):
        series = str(config.get("series") or PLAYER_DEFAULT_SERIES)
        csv_path = Path(config.get("csv_path") or PLAYER_CSV)
        cards = build_player_deck_from_spec(config["deck_spec"], series=series, csv_path=csv_path)
        return deck_name, [_viewer_dict_from_player(c) for c in cards]

    if config.get("deck_text"):
        draw_pile, heroes, parsed, issues = build_player_deck_from_text(
            config["deck_text"]
        )
        if parsed.errors:
            raise ValueError("卡组文本格式错误:\n" + "\n".join(parsed.errors))
        if issues:
            raise ValueError("未在 CSV 中找到以下卡牌:\n" + "\n".join(issues))
        cards = heroes + draw_pile
        return deck_name, [_viewer_dict_from_player(c) for c in cards]

    return deck_name, _patch_json_cards(config.get("cards", []))


def _is_encounter_csv(path: Path) -> bool:
    if path.resolve() == ENCOUNTER_CSV.resolve():
        return True
    if "遭遇" in path.name:
        return True
    import csv
    if not path.is_file():
        return False
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
    return "威胁值" in fields and "卡牌费用" not in fields


def _load_csv_deck(path: Path, series: Optional[str] = None) -> Tuple[str, List[Dict[str, Any]]]:
    if _is_encounter_csv(path):
        cards = load_encounter_cards_from_csv(
            series=series or ENCOUNTER_DEFAULT_SERIES,
            exclude_types=(),
            csv_path=path,
        )
        deck_name = f"{path.name}（{series or ENCOUNTER_DEFAULT_SERIES}）"
        return deck_name, [_viewer_dict_from_encounter(c) for c in cards]

    rows = _read_player_csv_rows(path)
    use_series = series or PLAYER_DEFAULT_SERIES
    cards: List[Dict[str, Any]] = []
    for row in rows:
        if use_series and (row.get("系列") or "").strip() != use_series:
            continue
        cards.append(_viewer_dict_from_player(PlayerCard.from_csv_row(row)))
    return f"{path.name}（{use_series}）", cards


def load_deck_from_text(
    text: str,
    deck_name: str = "Main Deck",
) -> Tuple[str, List[Dict[str, Any]]]:
    """从 Main Deck 文本解析卡组（含英雄与主牌组）。"""
    draw_pile, heroes, parsed, issues = build_player_deck_from_text(text)
    if parsed.errors:
        raise ValueError("卡组文本格式错误:\n" + "\n".join(parsed.errors))
    if issues:
        raise ValueError("未在 CSV 中找到以下卡牌:\n" + "\n".join(issues))
    cards = heroes + draw_pile
    if not cards:
        raise ValueError("卡组文本中未解析到任何卡牌")
    return deck_name, [_viewer_dict_from_player(c) for c in cards]


def _load_text_deck(path: Path) -> Tuple[str, List[Dict[str, Any]]]:
    text = path.read_text(encoding="utf-8")
    return load_deck_from_text(text, deck_name=path.stem)


def _load_o8d_deck(path: Path) -> Tuple[str, List[Dict[str, Any]]]:
    tree = ET.parse(path)
    root = tree.getroot()
    player_by_id = _build_image_id_index(PLAYER_CSV)
    encounter_by_id = _build_image_id_index(ENCOUNTER_CSV)
    cards: List[Dict[str, Any]] = []

    for section in root.findall("section"):
        for card_el in section.findall("card"):
            card_id = (card_el.get("id") or "").strip()
            qty_raw = (card_el.get("qty") or "1").strip()
            try:
                qty = max(1, int(qty_raw))
            except ValueError:
                qty = 1
            label = (card_el.text or "").strip()

            row = player_by_id.get(card_id) or encounter_by_id.get(card_id)
            deck_type = "player" if card_id in player_by_id else "encounter" if card_id in encounter_by_id else "unknown"
            for copy_index in range(qty):
                if row and deck_type == "player":
                    card = PlayerCard.from_csv_row(row, copy_index=copy_index if qty > 1 else 0)
                    cards.append(_viewer_dict_from_player(card))
                elif row and deck_type == "encounter":
                    card = EncounterCard.from_csv_row(row, copy_index=copy_index if qty > 1 else 0)
                    cards.append(_viewer_dict_from_encounter(card))
                else:
                    image_path = resolve_player_image(card_id) or resolve_encounter_image(card_id)
                    cards.append({
                        "name": label or card_id,
                        "image_path": image_path or "",
                        "id": card_id,
                        "type": "",
                        "series": "",
                        "deck_type": "unknown",
                    })
    return path.stem, cards


# ---------------------------------------------------------------------------
# RingsDB decklist URL 加载
# ---------------------------------------------------------------------------
RINGSDB_API_BASE = "https://ringsdb.com/api/public"
TRANSLATION_PATH = _PROJECT_ROOT / "translation.json"

# 形如 https://ringsdb.com/decklist/view/66638/xxx-1.0#
_RINGSDB_DECKLIST_ID_RE = re.compile(r"/decklist/[a-z]+/(\d+)", re.IGNORECASE)
_RINGSDB_API_ID_RE = re.compile(r"/decklist/(\d+)(?:\.json)?", re.IGNORECASE)
# 形如 https://ringsdb.com/deck/view/677635#
_RINGSDB_DECK_BUILDER_RE = re.compile(r"/deck/view/(\d+)", re.IGNORECASE)

RINGSDB_OAUTH_API_BASE = "https://ringsdb.com/api/oauth2"
RINGSD_FETCH_TIMEOUT = 90.0
RINGSD_FETCH_RETRIES = 3
RINGSD_ALL_CARDS_CACHE = _PROJECT_ROOT / ".ringsdb_all_cards.json"
RINGSD_CACHE_MAX_AGE_SEC = 7 * 24 * 3600

_ringsdb_card_cache: Dict[str, Dict[str, Any]] = {}
_ringsdb_pack_id_to_code: Dict[int, str] = {}
_ringsdb_fetched_packs: set[str] = set()
_translation_map_cache: Optional[Dict[str, str]] = None


def _parse_ringsdb_source(source: str) -> Optional[Tuple[str, str]]:
    """解析 RingsDB 输入，返回 (编号, 'deck'|'decklist')。"""
    s = (source or "").strip()
    if not s:
        return None
    if "ringsdb.com" in s.lower():
        m = _RINGSDB_DECK_BUILDER_RE.search(s)
        if m:
            return m.group(1), "deck"
        m = _RINGSDB_DECKLIST_ID_RE.search(s) or _RINGSDB_API_ID_RE.search(s)
        if m:
            return m.group(1), "decklist"
        return None
    if s.isdigit():
        return s, "auto"
    return None


def _extract_ringsdb_decklist_id(source: str) -> Optional[str]:
    """从 RingsDB URL / API URL / 纯数字 ID 中提取编号。"""
    parsed = _parse_ringsdb_source(source)
    return parsed[0] if parsed else None


def is_ringsdb_source(text: str) -> bool:
    """判断输入是否为 RingsDB 牌组 URL 或纯数字 ID。"""
    s = (text or "").strip()
    if not s or "\n" in s:
        return False
    return _parse_ringsdb_source(s) is not None


def _fetch_url_json(url: str, timeout: float = RINGSD_FETCH_TIMEOUT) -> Any:
    """带重试的 HTTP GET → JSON。"""
    headers = {
        "User-Agent": "LotR-LCG-Desktop/1.0",
        "Accept": "application/json",
    }
    last_err: Optional[BaseException] = None
    for attempt in range(RINGSD_FETCH_RETRIES):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_err = exc
            if attempt + 1 < RINGSD_FETCH_RETRIES:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"请求超时或失败（已重试 {RINGSD_FETCH_RETRIES} 次）：{last_err}") from last_err


def _fetch_ringsdb_json(path: str) -> Any:
    """请求 RingsDB 公开 API（优先复用 ringsdb_mcp.client）。"""
    try:
        from ringsdb_mcp.client import _get

        return _get(path, timeout=RINGSD_FETCH_TIMEOUT)
    except ImportError:
        return _fetch_url_json(f"{RINGSDB_API_BASE}{path}")


def _fetch_ringsdb_builder_deck(deck_id: str) -> Dict[str, Any]:
    """请求 Deckbuilder 牌组（/deck/view/{id} → oauth2 deck/load）。"""
    return _fetch_url_json(f"{RINGSDB_OAUTH_API_BASE}/deck/load/{deck_id}")


def _index_ringsdb_cards(cards: List[Dict[str, Any]]) -> None:
    """将卡牌列表写入内存缓存（按 code）。"""
    for card in cards:
        code = str(card.get("code") or "").strip()
        if code:
            _ringsdb_card_cache[code] = card


def _load_ringsdb_cards_from_disk_cache() -> bool:
    """尝试从本地缓存加载全卡库。"""
    if not RINGSD_ALL_CARDS_CACHE.is_file():
        return False
    try:
        age = time.time() - RINGSD_ALL_CARDS_CACHE.stat().st_mtime
        if age > RINGSD_CACHE_MAX_AGE_SEC:
            return False
        cards = json.loads(RINGSD_ALL_CARDS_CACHE.read_text(encoding="utf-8"))
        if not isinstance(cards, list):
            return False
        _index_ringsdb_cards(cards)
        return bool(_ringsdb_card_cache)
    except (OSError, json.JSONDecodeError, TypeError):
        return False


def _save_ringsdb_cards_disk_cache(cards: List[Dict[str, Any]]) -> None:
    try:
        RINGSD_ALL_CARDS_CACHE.write_text(
            json.dumps(cards, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _ensure_ringsdb_pack_index() -> None:
    """加载 RingsDB 扩展包 id → pack_code 对照（用于从卡牌编号推断扩展包）。"""
    if _ringsdb_pack_id_to_code:
        return
    packs = _fetch_ringsdb_json("/packs/")
    if not isinstance(packs, list):
        return
    for pack in packs:
        try:
            pack_id = int(pack.get("id"))
        except (TypeError, ValueError):
            continue
        code = str(pack.get("code") or "").strip()
        if code:
            _ringsdb_pack_id_to_code[pack_id] = code


def _pack_code_for_card(card_code: str) -> Optional[str]:
    """RingsDB 五位编号前两位 → pack_code（如 01001→Core，02001→HfG）。"""
    code = (card_code or "").strip()
    if len(code) < 2 or not code[:2].isdigit():
        return None
    _ensure_ringsdb_pack_index()
    return _ringsdb_pack_id_to_code.get(int(code[:2]))


def _fetch_ringsdb_pack_cards(pack_code: str) -> None:
    """拉取单个扩展包的全部卡牌并写入内存缓存。"""
    if pack_code in _ringsdb_fetched_packs:
        return
    cards = _fetch_ringsdb_json(f"/cards/{pack_code}.json")
    if isinstance(cards, list):
        _index_ringsdb_cards(cards)
    _ringsdb_fetched_packs.add(pack_code)


def _ensure_ringsdb_cards_for_codes(card_codes: List[str]) -> None:
    """按需加载牌组涉及的 RingsDB 卡牌（磁盘全量缓存 → 按扩展包拉取）。"""
    needed = {str(c).strip() for c in card_codes if str(c).strip()}
    if not needed:
        return
    if _load_ringsdb_cards_from_disk_cache():
        if all(code in _ringsdb_card_cache for code in needed):
            return
    missing = [code for code in needed if code not in _ringsdb_card_cache]
    packs_to_fetch: List[str] = []
    for code in missing:
        pack_code = _pack_code_for_card(code)
        if pack_code and pack_code not in _ringsdb_fetched_packs:
            packs_to_fetch.append(pack_code)
    for pack_code in dict.fromkeys(packs_to_fetch):
        _fetch_ringsdb_pack_cards(pack_code)
    for code in [c for c in missing if c not in _ringsdb_card_cache]:
        card = _fetch_ringsdb_json(f"/card/{code}.json")
        if isinstance(card, dict):
            _ringsdb_card_cache[code] = card


def _fetch_ringsdb_deck_data(source: str) -> Dict[str, Any]:
    """按 URL 类型拉取 decklist 或 deckbuilder 牌组 JSON。"""
    parsed = _parse_ringsdb_source(source)
    if not parsed:
        raise ValueError(f"无法从输入中识别 RingsDB 牌组编号：{source}")
    deck_id, kind = parsed
    if kind == "deck":
        return _fetch_ringsdb_builder_deck(deck_id)
    if kind == "decklist":
        return _fetch_ringsdb_json(f"/decklist/{deck_id}.json")
    # 纯数字：先尝试公开 decklist，失败再试 deckbuilder
    try:
        return _fetch_ringsdb_json(f"/decklist/{deck_id}.json")
    except Exception:
        return _fetch_ringsdb_builder_deck(deck_id)


def _load_ringsdb_card(card_code: str) -> Dict[str, Any]:
    """按 RingsDB code 获取卡牌 JSON（须先调用 _ensure_ringsdb_cards_for_codes）。"""
    card = _ringsdb_card_cache.get(card_code)
    if card is None:
        _ensure_ringsdb_cards_for_codes([card_code])
        card = _ringsdb_card_cache.get(card_code)
    if card is None:
        raise RuntimeError(f"无法获取 RingsDB 卡牌：{card_code}")
    return card


def _load_translation_map() -> Dict[str, str]:
    """读取 translation.json：英文卡名 → 中文卡名（含小写键回退）。"""
    global _translation_map_cache
    if _translation_map_cache is not None:
        return _translation_map_cache
    mapping: Dict[str, str] = {}
    if TRANSLATION_PATH.is_file():
        try:
            with open(TRANSLATION_PATH, encoding="utf-8") as f:
                data = json.load(f)
            for en, cn in data.items():
                if not isinstance(en, str) or not isinstance(cn, str):
                    continue
                en_key = en.strip()
                cn_val = cn.strip()
                if en_key and cn_val:
                    mapping.setdefault(en_key, cn_val)
                    mapping.setdefault(en_key.lower(), cn_val)
        except (OSError, json.JSONDecodeError):
            mapping = {}
    _translation_map_cache = mapping
    return mapping


def translate_card_name(en_name: str) -> str:
    """英文卡名 → 中文卡名；无翻译时返回原文。"""
    mapping = _load_translation_map()
    name = (en_name or "").strip()
    return mapping.get(name) or mapping.get(name.lower()) or name


def _image_link_stem(image_id: str) -> str:
    """CSV「图片链接」去掉扩展名后的小写 UUID。"""
    stem = (image_id or "").strip()
    lower = stem.lower()
    for ext in (".jpg", ".jpeg", ".png"):
        if lower.endswith(ext):
            return lower[: -len(ext)]
    return lower


def _build_octgnid_index(rows: List[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    """octgnid（小写、无扩展名）→ 玩家 CSV 行。"""
    index: Dict[str, Dict[str, str]] = {}
    for row in rows:
        stem = _image_link_stem(row.get("图片链接") or "")
        if stem:
            index.setdefault(stem, row)
    return index


def _find_player_row_for_ringsdb_card(
    card: Dict[str, Any],
    octgnid_index: Dict[str, Dict[str, str]],
    name_index: Dict[tuple[str, str], Dict[str, str]],
) -> Optional[Dict[str, str]]:
    """RingsDB 卡牌 JSON → 本地玩家 CSV 行。octgnid 优先，翻译名回退。"""
    octgnid = str(card.get("octgnid") or "").strip().lower()
    if octgnid:
        row = octgnid_index.get(octgnid)
        if row:
            return row

    en_name = str(card.get("name") or "").strip()
    cn_name = translate_card_name(en_name)
    pack_name = str(card.get("pack_name") or "").strip()
    for candidate in dict.fromkeys((cn_name, en_name)):
        if not candidate:
            continue
        row = lookup_card_row(name_index, pack_name, candidate)
        if row:
            return row
    # 最后回退：跨系列按名称扫描（处理重印或系列名不一致）
    for candidate in dict.fromkeys((cn_name, en_name)):
        if not candidate:
            continue
        for (_series, row_name), row in name_index.items():
            if row_name == candidate:
                return row
    return None


def _resolve_ringsdb_deck(source: str) -> Tuple[str, List[Tuple[Dict[str, str], int]]]:
    """拉取 RingsDB 牌组并解析为 (牌组名, [(CSV 行, 数量)])，英雄在前。"""
    parsed = _parse_ringsdb_source(source)
    if not parsed:
        raise ValueError(f"无法从输入中识别 RingsDB 牌组编号：{source}")
    deck_id, _kind = parsed

    data = _fetch_ringsdb_deck_data(source)
    deck_name = str(data.get("name") or f"RingsDB {deck_id}")
    heroes: Dict[str, int] = data.get("heroes") or {}
    slots: Dict[str, int] = data.get("slots") or {}

    # slots 中通常包含英雄，先英雄后主牌组，避免重复
    ordered: List[Tuple[str, int]] = []
    for code, qty in heroes.items():
        ordered.append((str(code), int(qty or 1)))
    for code, qty in slots.items():
        if str(code) not in heroes:
            ordered.append((str(code), int(qty or 1)))

    unique_codes = [code for code, _ in ordered]
    _ensure_ringsdb_cards_for_codes(unique_codes)

    player_rows = _read_player_csv_rows(PLAYER_CSV)
    octgnid_index = _build_octgnid_index(player_rows)
    name_index = build_player_name_index(player_rows)

    resolved: List[Tuple[Dict[str, str], int]] = []
    missing: List[str] = []
    for code, qty in ordered:
        try:
            rdb_card = _load_ringsdb_card(code)
        except Exception as exc:
            missing.append(f"{code}（RingsDB 拉取失败：{exc}）")
            continue
        row = _find_player_row_for_ringsdb_card(rdb_card, octgnid_index, name_index)
        if not row:
            en_name = str(rdb_card.get("name") or code)
            cn_name = translate_card_name(en_name)
            hint = f"{en_name} / {cn_name}" if cn_name != en_name else en_name
            missing.append(f"{hint}（code {code}）")
            continue
        resolved.append((row, max(1, qty)))

    if missing:
        raise ValueError(
            "以下 RingsDB 卡牌未能匹配到本地 CSV：\n" + "\n".join(missing)
        )
    if not resolved:
        raise ValueError(f"RingsDB 牌组 {deck_id} 中没有可显示的卡牌")
    return deck_name, resolved


def load_deck_from_ringsdb(source: str) -> Tuple[str, List[Dict[str, Any]]]:
    """从 RingsDB decklist URL / ID 加载牌组并映射到本地中文卡牌。"""
    deck_name, resolved = _resolve_ringsdb_deck(source)
    cards: List[Dict[str, Any]] = []
    for row, qty in resolved:
        for i in range(qty):
            card = PlayerCard.from_csv_row(row, copy_index=i if qty > 1 else 0)
            cards.append(_viewer_dict_from_player(card))
    return deck_name, cards


def ringsdb_to_deck_text(source: str) -> Tuple[str, str]:
    """将 RingsDB decklist 转为 Main Deck 中文文本（供主脚本牌库加载）。"""
    deck_name, resolved = _resolve_ringsdb_deck(source)
    hero_lines: List[str] = []
    groups: Dict[str, List[Tuple[str, int]]] = {"盟友": [], "附属": [], "事件": []}
    extra_lines: List[str] = []
    for row, qty in resolved:
        card_type = (row.get("类型") or "").strip()
        name = player_row_display_name(row)
        series = (row.get("系列") or "").strip()
        if card_type == "英雄":
            hero_lines.append(f"{name} ({series})")
        elif card_type in groups:
            groups[card_type].append((f"{qty}x  {name} ({series})", qty))
        else:
            # 任务/约定等类型：无独立小节标题，按数量行直接附加
            extra_lines.append(f"{qty}x  {name} ({series})")

    lines: List[str] = ["Main Deck", ""]
    if hero_lines:
        lines.append(f"英雄 ({len(hero_lines)})")
        lines.extend(hero_lines)
        lines.append("")
    for category in ("盟友", "附属", "事件"):
        items = groups[category]
        if not items:
            continue
        total = sum(q for _, q in items)
        lines.append(f"{category} ({total})")
        lines.extend(text for text, _ in items)
        lines.append("")
    lines.extend(extra_lines)
    return deck_name, "\n".join(lines).rstrip()


def load_deck_from_path(file_path: str | Path) -> Tuple[str, List[Dict[str, Any]]]:
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")

    suffix = path.suffix.lower()
    if suffix == ".json":
        return _load_json_deck(path)
    if suffix == ".csv":
        return _load_csv_deck(path)
    if suffix == ".txt":
        return _load_text_deck(path)
    if suffix == ".o8d":
        return _load_o8d_deck(path)
    raise ValueError(f"不支持的文件格式: {suffix}")


def load_default_deck() -> Tuple[str, List[Dict[str, Any]]]:
    if PLAYER_CSV.is_file():
        draw_pile, heroes, _, issues = build_player_deck_from_text(DEFAULT_DECK_LIST_TEXT)
        if not issues:
            cards = heroes + draw_pile
            return "默认 Main Deck", [_viewer_dict_from_player(c) for c in cards]
    if ENCOUNTER_CSV.is_file():
        cards = load_encounter_cards_from_csv()
        if cards:
            return f"默认遭遇牌（{ENCOUNTER_DEFAULT_SERIES}）", [_viewer_dict_from_encounter(c) for c in cards]
    return "空牌组", []

class ImageDialog(QDialog):
    closed = pyqtSignal()  # 新增关闭信号

    def __init__(self, image_path, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.initUI(image_path)

    def initUI(self, image_path):
        layout = QVBoxLayout(self)
        self.image_label = QLabel(self)
        self.image_label.setAlignment(Qt.AlignCenter)

        if image_path and Path(image_path).is_file():
            screen_size = QApplication.primaryScreen().size()
            max_width = int(screen_size.width() * 0.8)
            max_height = int(screen_size.height() * 0.8)
            pixmap = QPixmap(image_path).scaled(QSize(max_width, max_height), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.image_label.setPixmap(pixmap)
        else:
            self.image_label.setText("无法加载图片")

        layout.addWidget(self.image_label)
        self.setLayout(layout)

    def contextMenuEvent(self, event):
        win = self.window()
        if win is None or not getattr(win, "debug_mode", False):
            return
        menu = QMenu(self)
        exit_action = QAction("退出查看大图", self)
        exit_action.triggered.connect(self.close)
        menu.addAction(exit_action)
        menu.exec_(event.globalPos())

    def closeEvent(self, event):
        self.closed.emit()  # 发出关闭信号
        super().closeEvent(event)

class CardWidget(QWidget):
    """单张卡牌组件"""
    double_clicked = pyqtSignal(dict)
    right_clicked = pyqtSignal(dict, object)
    selection_changed = pyqtSignal(dict, bool)
    single_clicked = pyqtSignal()

    def __init__(self, card_data, parent=None):
        super().__init__(parent)
        self.card_data = card_data
        self.is_selected = False
        self._right_click_timer = QTimer(self)
        self._right_click_timer.setSingleShot(True)
        self._right_click_timer.timeout.connect(self._emit_right_clicked)
        self.setupUI()

    def setupUI(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(2, 2, 2, 2)

        self.checkbox = QCheckBox()
        self.checkbox.stateChanged.connect(self.on_selection_changed)
        self.image_label = QLabel()
        self.image_label.setFixedSize(180, 250)
        self.image_label.setScaledContents(True)
        self.image_label.setAlignment(Qt.AlignCenter)
        self.load_image()

        self.name_label = QLabel(self.card_data.get('name', '未知卡牌'))
        self.name_label.setAlignment(Qt.AlignCenter)

        layout.addWidget(self.checkbox, 0, Qt.AlignCenter)
        layout.addWidget(self.image_label)
        layout.addWidget(self.name_label)
        self.setLayout(layout)
        self.setFixedSize(184, 290)
        self.update_style()

    def update_style(self):
        if self.is_selected:
            self.setStyleSheet("background-color: rgba(0, 120, 212, 0.2); border: 3px solid #0078d4; border-radius: 8px;")
        else:
            self.setStyleSheet("background-color: transparent; border: 1px solid transparent; border-radius: 5px;")

    def load_image(self):
        image_path = self.card_data.get('image_path', '')
        if image_path and Path(image_path).is_file():
            pixmap = QPixmap(image_path)
            self.image_label.setPixmap(pixmap)
        else:
            self.image_label.setText("无图片")

    def on_selection_changed(self, state):
        self.is_selected = state == Qt.Checked
        self.update_style()
        self.selection_changed.emit(self.card_data, self.is_selected)

    def set_selected(self, selected):
        self.checkbox.setChecked(selected)
        self.is_selected = selected
        self.update_style()

    def _debug_enabled(self) -> bool:
        win = self.window()
        if win is not None and hasattr(win, "debug_mode"):
            return bool(win.debug_mode)
        return False

    def _emit_right_clicked(self):
        if not self._debug_enabled():
            return
        self.right_clicked.emit(self.card_data, self)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.RightButton:
            self._right_click_timer.stop()
            self.double_clicked.emit(self.card_data)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.single_clicked.emit()
        elif event.button() == Qt.RightButton:
            if self._debug_enabled():
                self._right_click_timer.start(QApplication.doubleClickInterval())
        super().mousePressEvent(event)

class CardDeckWidget(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cards_data: List[Dict[str, Any]] = []
        self.card_widgets = []
        self.selected_cards = []
        self.current_image_dialog = None
        self.config_path: Optional[Path] = None
        self.deck_text: Optional[str] = None
        self.ringsdb_source: Optional[str] = None
        self.deck_name = "PyQt5 牌组管理器"
        self.debug_mode = False
        self.setupUI()
        self.load_default_config()

    def setupUI(self):
        self.setWindowTitle("PyQt5 牌组管理器")
        self.setGeometry(100, 100, 1200, 800)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout()
        control_layout = QHBoxLayout()

        load_btn = QPushButton("加载配置文件")
        load_btn.clicked.connect(self.load_config_file)

        load_text_btn = QPushButton("加载 Main Deck 文本")
        load_text_btn.clicked.connect(self.load_text_deck_dialog)

        load_url_btn = QPushButton("加载 RingsDB URL")
        load_url_btn.clicked.connect(self.load_ringsdb_dialog)

        reload_btn = QPushButton("重新加载")
        reload_btn.clicked.connect(self.reload_cards)

        save_btn = QPushButton("保存配置")
        save_btn.clicked.connect(self.save_config_file)

        self.count_label = QLabel("卡牌数量: 0")
        self.count_label.setFont(QFont('Arial', 12, QFont.Bold))
        self.selected_label = QLabel("已选中: 0")
        self.selected_label.setFont(QFont('Arial', 10))
        self.selected_label.setStyleSheet("color: #0078d4;")

        control_layout.addWidget(load_btn)
        control_layout.addWidget(load_text_btn)
        control_layout.addWidget(load_url_btn)
        control_layout.addWidget(reload_btn)
        control_layout.addWidget(save_btn)
        control_layout.addStretch()
        control_layout.addWidget(self.selected_label)
        control_layout.addWidget(QLabel("|"))
        control_layout.addWidget(self.count_label)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)

        self.cards_container = QWidget()
        self.cards_layout = QGridLayout()
        self.cards_layout.setSpacing(10)
        self.cards_container.setLayout(self.cards_layout)

        scroll_area.setWidget(self.cards_container)
        main_layout.addLayout(control_layout)
        main_layout.addWidget(scroll_area)
        central_widget.setLayout(main_layout)

    def load_default_config(self):
        try:
            deck_name, cards = load_default_deck()
            self._apply_deck(deck_name, cards, config_path=None)
        except Exception as e:
            QMessageBox.warning(self, "默认加载失败", str(e))
            self.cards_data = []
            self.display_cards()

    def _apply_deck(
        self,
        deck_name: str,
        cards: List[Dict[str, Any]],
        config_path: Optional[Path] = None,
        deck_text: Optional[str] = None,
        ringsdb_source: Optional[str] = None,
    ):
        self.deck_name = deck_name
        self.config_path = config_path
        self.deck_text = deck_text
        self.ringsdb_source = ringsdb_source
        self.cards_data = cards
        self.setWindowTitle(f"PyQt5 牌组管理器 - {deck_name}")
        self.display_cards()

    def load_deck_from_text_content(
        self,
        text: str,
        deck_name: str = "Main Deck",
        config_path: Optional[Path] = None,
    ):
        deck_name, cards = load_deck_from_text(text, deck_name=deck_name)
        self._apply_deck(deck_name, cards, config_path=config_path, deck_text=text)

    def load_text_deck_dialog(self):
        dialog = DeckListDialog(self, initial_text=self.deck_text)
        if dialog.exec_() != QDialog.Accepted:
            return
        try:
            text = dialog.get_text()
            if is_ringsdb_source(text):
                self.load_ringsdb_deck(text)
            else:
                self.load_deck_from_text_content(text, deck_name="Main Deck")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"解析 Main Deck 失败：{e}")

    def load_ringsdb_deck(self, source: str):
        """从 RingsDB URL / ID 加载牌组并应用。"""
        deck_name, cards = load_deck_from_ringsdb(source)
        self._apply_deck(deck_name, cards, ringsdb_source=source.strip())

    def load_ringsdb_dialog(self):
        source, ok = QInputDialog.getText(
            self,
            "加载 RingsDB 牌组",
            "粘贴 RingsDB 牌组链接或编号：\n"
            "例如 https://ringsdb.com/decklist/view/66638/xxx 或 66638",
            text=self.ringsdb_source or "",
        )
        if not ok or not source.strip():
            return
        try:
            self.load_ringsdb_deck(source)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载 RingsDB 牌组失败：{e}")

    def load_config_path(self, file_path: str | Path):
        deck_name, cards = load_deck_from_path(file_path)
        if not cards:
            raise ValueError("配置文件中没有可显示的卡牌")
        path = Path(file_path)
        deck_text = path.read_text(encoding="utf-8") if path.suffix.lower() == ".txt" else None
        self._apply_deck(deck_name, cards, config_path=path, deck_text=deck_text)

    def load_config_file(self):
        start_dir = str(self.config_path.parent) if self.config_path else str(_PROJECT_ROOT)
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择卡组文件", start_dir, CONFIG_FILTER
        )
        if not file_path:
            return
        try:
            self.load_config_path(file_path)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载失败：{e}")

    def save_config_file(self):
        default_name = f"{self.deck_name}.json" if self.deck_name else "deck_config.json"
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存配置文件", default_name, "JSON Files (*.json);;All Files (*)"
        )
        if not file_path:
            return
        config = {
            "deck_name": self.deck_name,
            "cards": self.cards_data,
        }
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        self.config_path = Path(file_path)
        QMessageBox.information(self, "完成", f"已保存到 {file_path}")

    def display_cards(self):
        for i in reversed(range(self.cards_layout.count())):
            self.cards_layout.itemAt(i).widget().setParent(None)
        self.card_widgets.clear()
        self.selected_cards.clear()

        columns = 5
        for i, card_data in enumerate(self.cards_data):
            card_widget = CardWidget(card_data)
            card_widget.double_clicked.connect(self.show_enlarged_image)
            card_widget.single_clicked.connect(self.close_enlarged_image)
            card_widget.right_clicked.connect(self.show_card_context_menu)
            card_widget.selection_changed.connect(self.on_card_selection_changed)
            self.card_widgets.append(card_widget)
            row, col = divmod(i, columns)
            self.cards_layout.addWidget(card_widget, row, col)
        self.update_status_labels()

    def show_enlarged_image(self, card_data):
        self.close_enlarged_image()
        image_path = card_data.get('image_path', '')
        self.current_image_dialog = ImageDialog(image_path, card_data.get('name', '查看大图'), self)
        self.current_image_dialog.closed.connect(self.clear_image_dialog)
        self.current_image_dialog.show()

    def clear_image_dialog(self):
        self.current_image_dialog = None

    def close_enlarged_image(self):
        if self.current_image_dialog:
            self.current_image_dialog.close()
            self.current_image_dialog = None

    def show_card_context_menu(self, card_data, card_widget):
        menu = QMenu(self)
        view_action = QAction("查看详情", self)
        view_action.triggered.connect(lambda: QMessageBox.information(self, "详情", str(card_data)))
        menu.addAction(view_action)
        image_action = QAction("查看大图", self)
        image_action.triggered.connect(lambda: self.show_enlarged_image(card_data))
        menu.addAction(image_action)
        menu.addSeparator()
        if card_widget.is_selected:
            select_action = QAction("取消选中", self)
            select_action.triggered.connect(lambda: card_widget.set_selected(False))
        else:
            select_action = QAction("选中", self)
            select_action.triggered.connect(lambda: card_widget.set_selected(True))
        menu.addAction(select_action)
        menu.exec_(QCursor.pos())

    def on_card_selection_changed(self, card_data, is_selected):
        if is_selected:
            if card_data not in self.selected_cards:
                self.selected_cards.append(card_data)
        else:
            if card_data in self.selected_cards:
                self.selected_cards.remove(card_data)
        self.update_status_labels()

    def update_status_labels(self):
        self.count_label.setText(f"卡牌数量: {len(self.cards_data)}")
        self.selected_label.setText(f"已选中: {len(self.selected_cards)}")

    def reload_cards(self):
        try:
            if self.ringsdb_source:
                self.load_ringsdb_deck(self.ringsdb_source)
            elif self.deck_text:
                deck_name = self.config_path.stem if self.config_path else self.deck_name
                self.load_deck_from_text_content(
                    self.deck_text, deck_name=deck_name, config_path=self.config_path
                )
            elif self.config_path:
                self.load_config_path(self.config_path)
            else:
                self.load_default_config()
            QMessageBox.information(self, "完成", "卡牌已重新加载")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"重新加载失败：{e}")

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = CardDeckWidget()
    window.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
