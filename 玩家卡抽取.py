import csv
import json
import os
import random
import re
import sys
import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass, field

from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QMenu, QVBoxLayout,
    QMessageBox, QMainWindow, QHBoxLayout, QPushButton,
    QDialog, QTextEdit, QSizePolicy, QInputDialog,
)
from PyQt5.QtGui import QPixmap
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QCursor

_PROJECT_ROOT = Path(__file__).resolve().parent
PLAYER_CSV = _PROJECT_ROOT / "魔戒玩家牌.csv"
PLAYER_IMAGE_DIRS = (
    _PROJECT_ROOT / "cards" / "玩家卡牌",
    _PROJECT_ROOT / "cards" / "玩家牌组",
)
CARD_BACK_PATH = _PROJECT_ROOT / "cards" / "images" / "player_card_back.jpg"
LOG_FILE = _PROJECT_ROOT / "playerdraw_log.txt"
DEFAULT_DECK_SERIES = "基础"
# 玩家卡图源尺寸（宽 × 高，竖版）
PLAYER_CARD_W = 358
PLAYER_CARD_H = 500

# 测试用主牌组（50 张，不含英雄）
DEFAULT_MAIN_DECK: Dict[str, int] = {
    "法拉米尔": 2,
    "甘道夫": 3,
    "王城禁卫": 3,
    "长须奥克屠戮者": 3,
    "罗瑞恩向导": 3,
    "北方的追踪者": 3,
    "银脉河弓手": 3,
    "雪河斥候": 3,
    "凯勒布莉安的宝石": 2,
    "刚铎宰相": 3,
    "夫人的眷顾": 2,
    "突来勇气": 2,
    "意志的考验": 3,
    "矮人坟墓": 2,
    "坚定的决心": 1,
    "仓促的攻击": 2,
    "偷袭": 3,
    "奋起战斗": 2,
    "意志之力": 2,
    "英勇牺牲": 3,
}

# 卡组名称别名（文本名 ↔ CSV 名，双向查找时备用）
CARD_NAME_ALIASES: Dict[str, str] = {
    "希奥德温": "希奥杰德",
    "希优德": "希奥杰德",
    "凯勒布林博的秘密": "凯勒布莉安的宝石",
    "亚拉冈": "阿拉贡",
    "刚铎长枪兵": "刚铎长矛手",
    "伊欧玟": "伊奥温",
    "葛罗音": "格罗因",
    "金雳": "吉姆利",
    "贝拉沃": "贝拉芙",
    "Beravor": "贝拉芙",
    "宁若戴尔河之女": "宁洛德尔之女",
    "依鲁伯铁匠": "埃瑞博铁匠",
    "铁丘陵的矿工": "铁丘陵矿工",
    "葛理欧温": "格利奥威奈",
    "法拉墨": "法拉米尔",
    "亚尔诺之子": "阿尔诺之子",
    "雪界河斥候": "雪河斥候",
    "长须屠兽者": "长须奥克屠戮者",
    "铁拳布洛克": "布洛克·铁拳",
    "流浪的图克": "漫游的图克",
    "罗瑞安引路人": "罗瑞恩向导",
    "西大道的旅者": "西大道旅者",
    "登丹人的标记": "杜内丹人的标记",
    "登丹人的记号": "杜内丹人的记号",
    "篝火旁的传说": "篝火故事",
    "西谷驯马师": "西伏尔德驯马师",
    "瑞文戴尔乐手": "幽谷吟游诗人",
    "神行客之路": "大步佬之路",
    "巨鹰来了": "大鹰来了",
    "佛罗多·巴金斯": "弗罗多·巴金斯",
    "登丹人的警示": "杜内丹人的警告",
    "比翁养蜂人": "贝奥恩养蜂人",
    "伊欧蒙德": "伊奥蒙德",
    "我并非陌生人": "我并不是陌生人",
    "燃烧的木棍": "燃烧的火把",
    "曙光会吞没所有人": "曙光会照到你们所有人",
    "迷雾山脉的鹰群": "迷雾山脉鹰群",
    "巨鹰的支援": "大鹰的支持",
    "幽暗密林斥候": "黑森林信使",
    "过往黯影": "往昔阴影",
    "德瓦林": "杜瓦林",
    "毕佛": "比弗",
    "南都布理安的老兵": "南都希瑞安的老兵",
    "矮人故乡之斧": "矮人挖凿斧",
    "西吉尔矿工": "齐吉尔矿工",
    "依伯鲁撰史人": "埃瑞博撰史人",
    "依伯鲁之靴": "埃瑞博靴子",
    "爱罗希尔": "埃洛希尔",
    "爱拉丹": "埃尔拉丹",
    "波佛": "波弗",
    "乌丘斥候": "渡鸦岭斥候",
    "登丹漫游者": "杜内丹漫游者",
    "摩瑞亚的召唤": "墨瑞亚的召唤",
    "瑞文戴尔之剑": "幽谷剑",
    "骠骑兵": "马克骑手",
    "埃兰迪尔之歌": "埃雅仁迪尔之歌",
    "远离荒蛮": "来自荒野",
    "哈玛": "哈马",
    "伊姆拉崔的学识": "伊姆拉缀斯的学识",
    "罗瑞安的财富": "罗瑞恩的财富",
    "瑞达加斯特的狡诈": "拉达加斯特的机敏",
    "幽径": "秘径",
    "甘道夫的搜寻": "甘道夫的查阅",
    "比翁的款待": "贝奥恩的款待",
    "森林诱捕网": "森林罗网",
    "罗瑞安守护者": "罗瑞恩的保护者",
    "暗黑学识": "黑暗知识",
    "矮人墓穴": "矮人坟墓",
}

def _init_series_aliases() -> Dict[str, str]:
    """卡组列表常用简称 / 英文扩展包名 → CSV「系列」列。"""
    try:
        from ringsdb_mcp.mapping import build_series_aliases

        return build_series_aliases()
    except ImportError:
        return {}


# 扩展包/系列别名（卡组列表常用简称 ↔ CSV「系列」列）
SERIES_ALIASES: Dict[str, str] = _init_series_aliases()


def player_row_names(row: Dict[str, str]) -> List[str]:
    """返回 CSV 行中可用于查找的卡牌名称（主名、备用名，去重保序）。"""
    names: List[str] = []
    for key in ("卡牌名称", "备用卡牌名称"):
        name = (row.get(key) or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def player_row_display_name(row: Dict[str, str]) -> str:
    """显示用卡牌名：优先「卡牌名称」，否则「备用卡牌名称」。"""
    return (row.get("卡牌名称") or "").strip() or (row.get("备用卡牌名称") or "").strip()


def build_player_name_index(
    rows: List[Dict[str, str]],
) -> Dict[tuple[str, str], Dict[str, str]]:
    """按系列 + 卡牌名称/备用卡牌名称 构建查找索引。"""
    index: Dict[tuple[str, str], Dict[str, str]] = {}
    for row in rows:
        row_series = (row.get("系列") or "").strip()
        if not row_series:
            continue
        for row_name in player_row_names(row):
            index[(row_series, row_name)] = row
    return index


def lookup_card_row(
    index: Dict[tuple[str, str], Dict[str, str]],
    series: str,
    raw_name: str,
) -> Optional[Dict[str, str]]:
    """按系列与名称查找 CSV 行，支持卡牌名与系列别名。"""
    name = (raw_name or "").strip()
    series_key = (series or "").strip()
    if not name:
        return None
    candidates = [name]
    alias = CARD_NAME_ALIASES.get(name)
    if alias:
        candidates.append(alias)
    for k, v in CARD_NAME_ALIASES.items():
        if v == name and k not in candidates:
            candidates.append(k)
    series_keys = [series_key] if series_key else []
    try:
        from ringsdb_mcp.mapping import normalize_series_alias_key, fold_pack_english

        series_variants = [
            series_key,
            normalize_series_alias_key(series_key),
            fold_pack_english(series_key),
        ]
    except ImportError:
        series_variants = [series_key]
    for variant in series_variants:
        if not variant:
            continue
        alias = SERIES_ALIASES.get(variant)
        if alias and alias not in series_keys:
            series_keys.append(alias)
    for ser in series_keys:
        for candidate in candidates:
            row = index.get((ser, candidate))
            if row:
                return row
    return None


def lookup_card_row_by_name_any_series(
    index: Dict[tuple[str, str], Dict[str, str]],
    raw_name: str,
) -> Optional[Dict[str, str]]:
    """按卡牌名称在任意系列中查找（系列未知时的回退）。"""
    name = (raw_name or "").strip()
    if not name:
        return None
    candidates = [name]
    alias = CARD_NAME_ALIASES.get(name)
    if alias:
        candidates.append(alias)
    for k, v in CARD_NAME_ALIASES.items():
        if v == name and k not in candidates:
            candidates.append(k)
    for (_ser, row_name), row in index.items():
        if row_name in candidates:
            return row
    return None


def resolve_player_csv_series(series: Optional[str]) -> str:
    """将卡组系列简称解析为 CSV「系列」列名。"""
    key = (series or DEFAULT_DECK_SERIES).strip() or DEFAULT_DECK_SERIES
    try:
        from ringsdb_mcp.mapping import normalize_series_alias_key, fold_pack_english

        variants = [key, normalize_series_alias_key(key), fold_pack_english(key)]
    except ImportError:
        variants = [key]
    for variant in variants:
        if not variant:
            continue
        alias = SERIES_ALIASES.get(variant)
        if alias:
            return alias
    return key


def load_player_cards_for_debug(
    *,
    series: Optional[str] = None,
    deck_text: Optional[str] = None,
    deck_cards: Optional[List[Card]] = None,
) -> Tuple[List[Card], str]:
    """Debug 放置玩家卡：返回 (候选卡牌, 来源说明)。"""
    if deck_text:
        parsed = parse_deck_list_text(deck_text)
        if not parsed.errors and parsed.entries:
            rows = _read_player_csv_rows()
            index = build_player_name_index(rows)
            seen: set[tuple[str, str]] = set()
            cards: List[Card] = []
            for entry in parsed.entries:
                if entry.category == "英雄":
                    continue
                dedupe_key = (entry.name, entry.series)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                row = lookup_card_row(index, entry.series, entry.name)
                if row:
                    card_type = (row.get("类型") or "").strip()
                    if card_type == "英雄":
                        continue
                    cards.append(Card.from_csv_row(row))
            if cards:
                return cards, "当前卡组"

    if deck_cards:
        seen_cards: set[tuple[str, str, str]] = set()
        unique: List[Card] = []
        for card in deck_cards:
            if (getattr(card, "type", "") or "").strip() == "英雄":
                continue
            dedupe_key = (card.name or "", card.series or "", card.type or "")
            if dedupe_key in seen_cards:
                continue
            seen_cards.add(dedupe_key)
            unique.append(card)
        if unique:
            return unique, "当前卡组"

    resolved = resolve_player_csv_series(series)
    return (
        load_player_cards_from_csv(series=resolved, exclude_types=()),
        resolved,
    )


DEFAULT_TEST_HEROES = ("阿拉贡", "伊奥温", "希奥杰德")

DEFAULT_DECK_LIST_TEXT = """Main Deck

英雄 (3)
阿拉贡 (基础)
伊奥温 (基础)
希奥杰德 (基础)

盟友 (23)
2x  法拉米尔 (基础)
3x  甘道夫 (基础)
3x  王城禁卫 (基础)
3x  长须奥克屠戮者 (基础)
3x  罗瑞恩向导 (基础)
3x  北方的追踪者 (基础)
3x  银脉河弓手 (基础)
3x  雪河斥候 (基础)

附属 (9)
2x  凯勒布莉安的宝石 (基础)
3x  刚铎宰相 (基础)
2x  夫人的眷顾 (基础)
2x  突来勇气 (基础)

事件 (18)
3x  意志的考验 (基础)
2x  矮人坟墓 (基础)
1x  坚定的决心 (基础)
2x  仓促的攻击 (基础)
3x  偷袭 (基础)
2x  奋起战斗 (基础)
2x  意志之力 (基础)
3x  英勇牺牲 (基础)"""


@dataclass
class DeckListEntry:
    name: str
    series: str
    qty: int
    category: str = ""


@dataclass
class ParsedDeckList:
    entries: List[DeckListEntry] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def parse_deck_list_text(text: str) -> ParsedDeckList:
    """解析 Main Deck 文本格式卡组列表。"""
    result = ParsedDeckList()
    current_category = ""

    for lineno, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.lower().replace(" ", "") == "maindeck":
            continue

        qty_match = re.match(r"^(\d+)x\s+(.+?)\s*\(([^)]+)\)\s*$", line, re.IGNORECASE)
        if qty_match:
            result.entries.append(DeckListEntry(
                name=qty_match.group(2).strip(),
                series=qty_match.group(3).strip(),
                qty=int(qty_match.group(1)),
                category=current_category,
            ))
            continue

        section_match = re.match(
            r"^(?:Player\s+)?(英雄|盟友|附属|事件|任务|次要探险)\s*\((\d+)\)\s*$",
            line,
        )
        if section_match:
            current_category = section_match.group(1)
            continue

        card_match = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", line)
        if card_match:
            name = card_match.group(1).strip()
            series = card_match.group(2).strip()
            if series.isdigit():
                result.errors.append(f"第 {lineno} 行：无法解析「{line}」")
                continue
            result.entries.append(DeckListEntry(
                name=name,
                series=series,
                qty=1,
                category=current_category,
            ))
            continue

        result.errors.append(f"第 {lineno} 行：无法解析「{line}」")

    return result


def build_player_deck_from_entries(
    entries: List[DeckListEntry],
    csv_path: Optional[Path] = None,
    for_draw: bool = True,
) -> Tuple[List[Card], List[Card], List[str]]:
    """
    从解析条目构建牌组。
    返回 (抽牌堆, 英雄列表, 未找到的卡牌描述)。
    """
    rows = _read_player_csv_rows(csv_path)
    index = build_player_name_index(rows)

    draw_pile: List[Card] = []
    heroes: List[Card] = []
    missing: List[str] = []

    for entry in entries:
        row = lookup_card_row(index, entry.series, entry.name)
        if not row:
            missing.append(f"{entry.name} ({entry.series})")
            continue

        card_type = (row.get("类型") or "").strip()
        is_hero = card_type == "英雄" or entry.category == "英雄"
        qty = max(1, entry.qty)

        for i in range(qty):
            card = Card.from_csv_row(row, copy_index=i if qty > 1 else 0)
            if is_hero:
                heroes.append(card)
            elif for_draw:
                draw_pile.append(card)

    return draw_pile, heroes, missing


def build_player_deck_from_text(
    text: str,
    csv_path: Optional[Path] = None,
) -> Tuple[List[Card], List[Card], ParsedDeckList, List[str]]:
    """解析文本并构建抽牌堆与英雄。"""
    parsed = parse_deck_list_text(text)
    if parsed.errors:
        return [], [], parsed, parsed.errors
    draw_pile, heroes, missing = build_player_deck_from_entries(
        parsed.entries, csv_path=csv_path
    )
    return draw_pile, heroes, parsed, missing


class DeckListDialog(QDialog):
    """粘贴/编辑 Main Deck 文本格式的卡组对话框。"""

    def __init__(self, parent=None, initial_text: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle("加载卡组")
        self.setMinimumSize(480, 520)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        hint = QLabel(
            "粘贴或编辑卡组列表（Main Deck 格式），\n"
            "或粘贴 RingsDB 链接后点「加载 RingsDB」/ 直接 OK（支持 decklist 与 deck/view）。\n"
            "鑻遍泟涓嶅弬涓庢娊鐗岋紱盟友/附属/事件构成 50 张主牌组。"
        )
        hint.setStyleSheet("color: #444;")
        layout.addWidget(hint)

        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(
            initial_text if initial_text is not None else DEFAULT_DECK_LIST_TEXT
        )
        self.text_edit.setStyleSheet(
            "font-family: Consolas, 'Microsoft YaHei UI', monospace; "
            "font-size: 13px;"
        )
        layout.addWidget(self.text_edit)

        footer = QHBoxLayout()
        footer.addStretch()

        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        clear_btn = QPushButton("清空文字")
        clear_btn.clicked.connect(self._clear_text)
        ringsdb_btn = QPushButton("加载 RingsDB")
        ringsdb_btn.clicked.connect(self._load_ringsdb_into_editor)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        footer.addWidget(ok_btn)
        footer.addWidget(clear_btn)
        footer.addWidget(ringsdb_btn)
        footer.addWidget(cancel_btn)
        layout.addLayout(footer)

    def _clear_text(self):
        self.text_edit.clear()

    def _load_ringsdb_into_editor(self):
        """从 RingsDB URL/编号拉取牌组并填入编辑框。"""
        current = self.text_edit.toPlainText().strip()
        default = current if current and "\n" not in current else ""
        url, ok = QInputDialog.getText(
            self,
            "加载 RingsDB 牌组",
            "粘贴 RingsDB 牌组链接或编号：\n"
            "（decklist/view 或 deck/view，如 https://ringsdb.com/deck/view/677635）",
            text=default,
        )
        if not ok or not url.strip():
            return
        try:
            from CardViewer import ringsdb_to_deck_text

            QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
            QApplication.processEvents()
            try:
                deck_name, deck_text = ringsdb_to_deck_text(url.strip())
            finally:
                QApplication.restoreOverrideCursor()
            self.text_edit.setPlainText(deck_text)
            QMessageBox.information(self, "完成", f"已加载「{deck_name}」")
        except Exception as exc:
            err = str(exc)
            if "timed out" in err.lower() or "超时" in err:
                err += (
                    "\n\n提示：请检查网络连接后重试；"
                    "若曾成功加载过，本地缓存 .ringsdb_all_cards.json 可加速后续加载。"
                )
            QMessageBox.warning(self, "加载 RingsDB 失败", err)

    def get_text(self) -> str:
        return self.text_edit.toPlainText()


def fit_player_card_size(max_height: int = 182) -> tuple[int, int]:
    """按玩家卡比例计算显示区域宽高。"""
    h = max(1, int(max_height))
    w = max(1, round(h * PLAYER_CARD_W / PLAYER_CARD_H))
    return w, h


def _image_id_stem(image_id: str) -> str:
    """去掉图片链接末尾已有扩展名，避免重复拼接 .jpg。"""
    stem = (image_id or "").strip()
    lower = stem.lower()
    for ext in (".jpg", ".jpeg", ".png"):
        if lower.endswith(ext):
            return stem[: -len(ext)]
    return stem


def resolve_player_image(image_id: str) -> str:
    """根据 CSV 图片链接解析玩家卡图片路径。"""
    stem = _image_id_stem(image_id)
    if not stem:
        return ""
    for folder in PLAYER_IMAGE_DIRS:
        if not folder.is_dir():
            continue
        for ext in (".jpg", ".jpeg", ".png", ".JPG"):
            path = folder / f"{stem}{ext}"
            if path.is_file():
                return str(path)
    return ""


def normalize_card_name(name: str) -> str:
    return (name or "").strip()


@dataclass
class Card:
    id: str
    name: str
    Category: str
    Sphere: str
    Cost: str
    Threat: str
    Willpower: str
    Attack: str
    Defense: str
    Health: str
    type: str
    Text_Effect: str
    image_path: str
    series: str
    unique: str
    restricted: str
    Ranged: str = ""
    Vigilant: str = ""

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Card":
        return Card(
            id=data.get("id", ""),
            name=data.get("name", ""),
            Category=data.get("Category", ""),
            Sphere=data.get("Sphere", ""),
            Cost=data.get("Cost", ""),
            Threat=data.get("Threat", ""),
            Willpower=data.get("Willpower", ""),
            Attack=data.get("Attack", ""),
            Defense=data.get("Defense", ""),
            Health=data.get("Health", ""),
            type=data.get("type", ""),
            Text_Effect=data.get("Text Effect", ""),
            image_path=data.get("image_path", ""),
            series=data.get("series", ""),
            unique=data.get("unique", ""),
            restricted=data.get("restricted", ""),
            Ranged=data.get("Ranged", ""),
            Vigilant=data.get("Vigilant", ""),
        )

    @staticmethod
    def from_csv_row(row: Dict[str, str], copy_index: int = 0) -> "Card":
        card_type = (row.get("类型") or "").strip()
        image_id = (row.get("图片链接") or "").strip()
        series = (row.get("系列") or "").strip()
        number = (row.get("编号") or "").strip()
        card_id = image_id or f"{series}-{number}"
        if copy_index > 0:
            card_id = f"{card_id}#{copy_index + 1}"

        return Card(
            id=card_id,
            name=player_row_display_name(row),
            Category=card_type,
            Sphere=(row.get("派系") or "").strip(),
            Cost=(row.get("卡牌费用") or "").strip(),
            Threat=(row.get("初始威胁") or "").strip(),
            Willpower=(row.get("意志力") or "").strip(),
            Attack=(row.get("攻击力") or "").strip(),
            Defense=(row.get("防御力") or "").strip(),
            Health=(row.get("生命值") or "").strip(),
            type=card_type,
            Text_Effect=(row.get("规则文字") or "").strip(),
            image_path=resolve_player_image(image_id),
            series=series,
            unique=(row.get("独有") or "").strip(),
            restricted=(row.get("限制") or "").strip(),
            Ranged=(row.get("远攻") or "").strip(),
            Vigilant=(row.get("警戒") or "").strip(),
        )


_PLAYER_CSV_ROWS_CACHE: Dict[Path, List[Dict[str, str]]] = {}
_PLAYER_NAME_INDEX_CACHE: Dict[Path, Dict[tuple[str, str], Dict[str, str]]] = {}


def clear_player_csv_cache() -> None:
    """清空玩家牌 CSV 行与名称索引缓存（测试或热更新 CSV 后调用）。"""
    _PLAYER_CSV_ROWS_CACHE.clear()
    _PLAYER_NAME_INDEX_CACHE.clear()


def _read_player_csv_rows(csv_path: Optional[Path] = None) -> List[Dict[str, str]]:
    csv_path = csv_path or PLAYER_CSV
    cached = _PLAYER_CSV_ROWS_CACHE.get(csv_path)
    if cached is not None:
        return cached
    if not csv_path.is_file():
        return []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    _PLAYER_CSV_ROWS_CACHE[csv_path] = rows
    return rows


def get_player_name_index(
    csv_path: Optional[Path] = None,
) -> Dict[tuple[str, str], Dict[str, str]]:
    """按系列+卡牌名构建查找索引（进程内缓存）。"""
    csv_path = csv_path or PLAYER_CSV
    cached = _PLAYER_NAME_INDEX_CACHE.get(csv_path)
    if cached is not None:
        return cached
    index = build_player_name_index(_read_player_csv_rows(csv_path))
    _PLAYER_NAME_INDEX_CACHE[csv_path] = index
    return index


def build_player_deck_from_spec(
    spec: Dict[str, int],
    series: str = DEFAULT_DECK_SERIES,
    csv_path: Optional[Path] = None,
) -> List[Card]:
    """按名称与数量从魔戒玩家牌.csv 构建牌组。"""
    rows = _read_player_csv_rows(csv_path)
    if not rows:
        return []

    index = build_player_name_index(rows)

    cards: List[Card] = []
    missing: List[str] = []
    for raw_name, qty in spec.items():
        row = lookup_card_row(index, series, raw_name)
        if not row:
            missing.append(raw_name)
            continue
        for i in range(max(1, int(qty))):
            cards.append(Card.from_csv_row(row, copy_index=i if qty > 1 else 0))

    if missing:
        print(f"牌组中未在 CSV（系列={series}）找到的卡牌: {', '.join(missing)}")
    return cards


def load_player_cards_from_csv(
    series: Optional[str] = DEFAULT_DECK_SERIES,
    exclude_types: tuple = ("英雄",),
    deck_spec: Optional[Dict[str, int]] = None,
    csv_path: Optional[Path] = None,
) -> List[Card]:
    """从魔戒玩家牌.csv 加载玩家牌组。"""
    if deck_spec is not None:
        return build_player_deck_from_spec(deck_spec, series=series or DEFAULT_DECK_SERIES, csv_path=csv_path)

    csv_path = csv_path or PLAYER_CSV
    if not csv_path.is_file():
        return []

    cards: List[Card] = []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if series and (row.get("系列") or "").strip() != series:
                continue
            card_type = (row.get("类型") or "").strip()
            if card_type in exclude_types:
                continue
            cards.append(Card.from_csv_row(row))
    return cards


def load_deck_cards(file_path: str) -> List[Card]:
    """从 JSON 卡组文件加载卡牌。"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            config = json.load(f)
            cards_data = config.get("cards", [])
            return [
                Card.from_dict(card)
                for card in cards_data
                if str(card.get("Discard", "0")).strip() != "1"
            ]
    except Exception as e:
        print(f"加载卡组失败: {e}")
        return []


def record_player_card(card: "Card", deck_path: Optional[str], log_file: Path = LOG_FILE):
    try:
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(
                f"{datetime.datetime.now().isoformat()}\n"
                f"卡牌名称: {card.name}\n"
                f"抽取卡牌 ID: {card.id}\n"
                f"路径: {card.image_path}\n"
                f"派系: {card.Sphere} | 费用: {card.Cost or '-'}\n"
                f"来源卡组: {deck_path or '未知'}\n"
                f"{'-' * 40}\n"
            )
    except Exception as e:
        print(f"记录抽卡日志失败: {e}")


def read_draw_log(log_file: Path = LOG_FILE) -> list[tuple[str, str]]:
    result = []
    if not log_file.is_file():
        return result
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            for i in range(0, len(lines), 6):
                if i + 2 < len(lines):
                    card_id = lines[i + 2].replace("抽取卡牌 ID: ", "").strip()
                    image_path = lines[i + 3].replace("路径: ", "").strip()
                    result.append((card_id, image_path))
    except Exception as e:
        print(f"读取抽卡日志时出错: {e}")
    return result


class ImageZoomDialog(QWidget):
    """图片放大显示窗口"""

    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self.original_pixmap = pixmap
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("玩家卡放大显示 - 单击关闭")
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.setContextMenuPolicy(Qt.NoContextMenu)

        screen = QApplication.primaryScreen().availableGeometry()
        max_width = int(screen.width() * 0.6)
        max_height = int(screen.height() * 0.8)

        scaled_pixmap = self.original_pixmap.scaled(
            max_width, max_height, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )

        self.setFixedSize(scaled_pixmap.width() + 20, scaled_pixmap.height() + 20)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setPixmap(scaled_pixmap)
        self.image_label.setStyleSheet("border: 2px solid #333; background-color: white;")
        self.image_label.setContextMenuPolicy(Qt.NoContextMenu)
        layout.addWidget(self.image_label)

        self.move((screen.width() - self.width()) // 2, (screen.height() - self.height()) // 2)

    def contextMenuEvent(self, event):
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        super().keyPressEvent(event)


class PlayerCardLabel(QLabel):
    """玩家卡图：随控件尺寸等比缩放。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.original_pixmap: Optional[QPixmap] = None
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            "border: 1px solid #666; "
            "background-color: white; "
            "border-radius: 5px;"
        )

    def set_image(self, pixmap: QPixmap):
        self.original_pixmap = pixmap
        self._apply_scaled_pixmap()

    def clear_image(self):
        self.original_pixmap = None
        self.clear()

    def _apply_scaled_pixmap(self):
        if not self.original_pixmap or self.original_pixmap.isNull():
            return
        self.setPixmap(
            self.original_pixmap.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_scaled_pixmap()


class CardDrawer(QWidget):
    """玩家卡抽取器"""

    card_drawn = pyqtSignal(str)
    deck_loaded = pyqtSignal(str)
    deck_reset = pyqtSignal()
    next_phase_requested = pyqtSignal()

    def __init__(self, parent=None, max_height: int = 182, adaptive: bool = False):
        super().__init__(parent)
        self.cards: List[Card] = []
        self.current_card: Optional[Card] = None
        self.current_pixmap: Optional[QPixmap] = None
        self.deck_path: Optional[str] = None
        self.deck_text: Optional[str] = None
        self.deck_heroes: List[Card] = []
        self.deck_series: Optional[str] = DEFAULT_DECK_SERIES
        self.deck_spec: Optional[Dict[str, int]] = DEFAULT_MAIN_DECK.copy()
        self.debug_mode = False
        self.drawn_ids: set[str] = set()
        self.deck_stack: List[Card] = []
        self.zoom_dialog: Optional[ImageZoomDialog] = None
        self._ctx_menu_pos = None
        self._ctx_menu_timer = QTimer(self)
        self._ctx_menu_timer.setSingleShot(True)
        self._ctx_menu_timer.timeout.connect(self._open_context_menu)
        self._max_height = max(1, int(max_height))
        self._adaptive = adaptive
        self._card_w, self._card_h = fit_player_card_size(self._max_height)
        self.init_ui()
        self.auto_load_default_deck()

    def _apply_card_size(self, height: int):
        """按可用高度更新卡图显示尺寸。"""
        h = max(1, int(height))
        self._card_w, self._card_h = fit_player_card_size(h)
        if self._adaptive:
            self.setMinimumSize(self._card_w, self._card_h)
        else:
            self.setFixedSize(self._card_w, self._card_h)
        if hasattr(self, "image_label"):
            if self._adaptive:
                self.image_label.setMinimumSize(self._card_w, self._card_h)
            else:
                self.image_label.setFixedSize(self._card_w, self._card_h)

    def init_ui(self):
        if self._adaptive:
            self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
            self._apply_card_size(self._max_height)
        else:
            self.setFixedSize(self._card_w, self._card_h)

        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        self.image_label = PlayerCardLabel()
        if self._adaptive:
            self.image_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
            self.image_label.setMinimumSize(self._card_w, self._card_h)
        else:
            self.image_label.setFixedSize(self._card_w, self._card_h)
        self.image_label.mouseDoubleClickEvent = self.on_double_click
        self.image_label.setToolTip("双击：进入下一阶段（弹窗失焦自动确认）")

        self.layout.addWidget(self.image_label)
        self.setLayout(self.layout)
        self.show_card_back()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._adaptive:
            return
        avail_h = self.height()
        if avail_h > 0 and abs(avail_h - self._card_h) > 1:
            self._apply_card_size(avail_h)
            self.image_label.resize(self.width(), self.height())
            if self.current_card and self.current_pixmap and not self.current_pixmap.isNull():
                self.image_label.set_image(self.current_pixmap)
            elif not self.current_card and CARD_BACK_PATH.is_file():
                pixmap = QPixmap(str(CARD_BACK_PATH))
                if not pixmap.isNull():
                    self.image_label.set_image(pixmap)

    def _reload_cards(self) -> List[Card]:
        if self.deck_text:
            draw_pile, _, _, errors = build_player_deck_from_text(self.deck_text)
            if errors and not draw_pile:
                print(f"重新加载卡组失败: {'; '.join(errors)}")
            return draw_pile
        if not self.deck_path:
            return []
        path = Path(self.deck_path)
        if path.suffix.lower() == ".csv":
            return load_player_cards_from_csv(
                series=self.deck_series,
                deck_spec=self.deck_spec,
                csv_path=path,
            )
        return load_deck_cards(self.deck_path)

    def auto_load_default_deck(self):
        """自动加载默认玩家牌组（文本格式 Main Deck）。"""
        if PLAYER_CSV.is_file():
            self.load_deck_from_text(DEFAULT_DECK_LIST_TEXT, silent=True)
            print("自动加载默认玩家卡组（文本 Main Deck）")
            return
        candidates = [
            _PROJECT_ROOT / "cards" / "player_deck.json",
            _PROJECT_ROOT / "player_deck.json",
        ]
        for path in candidates:
            if path.is_file():
                self.load_deck_from_path(str(path))
                print(f"自动加载默认玩家卡组: {path}")
                break

    def on_double_click(self, event):
        if event.button() == Qt.LeftButton:
            self.next_phase_requested.emit()
            return
        if event.button() == Qt.RightButton and self.current_card:
            self._ctx_menu_timer.stop()
            self._ctx_menu_pos = None
            self.show_zoomed_image()

    def show_zoomed_image(self):
        if not self.current_pixmap or self.current_pixmap.isNull():
            return
        self._ctx_menu_timer.stop()
        self._ctx_menu_pos = None
        if self.zoom_dialog:
            self.zoom_dialog.close()
        self.zoom_dialog = ImageZoomDialog(self.current_pixmap, self)
        self.zoom_dialog.show()

    def contextMenuEvent(self, event):
        if self.zoom_dialog is not None and self.zoom_dialog.isVisible():
            event.accept()
            return
        if not self.debug_mode:
            return
        self._ctx_menu_pos = self.mapToGlobal(event.pos())
        self._ctx_menu_timer.start(QApplication.doubleClickInterval())

    def _open_context_menu(self):
        if self.zoom_dialog is not None and self.zoom_dialog.isVisible():
            self._ctx_menu_pos = None
            return
        if self._ctx_menu_pos is None:
            return
        pos = self._ctx_menu_pos
        self._ctx_menu_pos = None
        menu = QMenu(self)

        zoom_action = menu.addAction("放大显示")
        menu.addSeparator()

        debug_action = menu.addAction("关闭Debug模式" if self.debug_mode else "开启Debug模式")

        draw_action = load_action = reset_action = back_action = log_action = place_action = None
        if self.debug_mode:
            menu.addSeparator()
            load_action = menu.addAction("加载卡组")
            reset_action = menu.addAction("重置卡组")
            back_action = menu.addAction("显示卡背")
            log_action = menu.addAction("查看抽卡记录")
            place_action = menu.addAction("放置卡牌（牌库顶）")
            menu.addSeparator()
            draw_action = menu.addAction("抽取卡牌")

        action = menu.exec_(pos)

        if action == zoom_action:
            self.show_zoomed_image()
        elif action == debug_action:
            self.debug_mode = not self.debug_mode
            print(f"玩家卡抽取Debug模式: {'开启' if self.debug_mode else '关闭'}")
        elif self.debug_mode:
            if action == draw_action:
                self.draw_card()
            elif action == load_action:
                self.load_deck()
            elif action == reset_action:
                self.reset_deck()
            elif action == back_action:
                self.show_card_back()
            elif action == log_action:
                self.view_log()
            elif action == place_action:
                self._debug_pick_and_place_on_top()

    def show_card_back(self):
        if CARD_BACK_PATH.is_file():
            pixmap = QPixmap(str(CARD_BACK_PATH))
            if not pixmap.isNull():
                self.image_label.setStyleSheet(
                    "border: 1px solid #666; "
                    "background-color: white; "
                    "border-radius: 5px;"
                )
                self.image_label.set_image(pixmap)
                self.current_pixmap = pixmap
                self.current_card = None
                return

        self.image_label.clear_image()
        self.image_label.setText("玩家牌库\n双击→下一阶段")
        self.image_label.setStyleSheet(
            "border: 1px solid #666; "
            "background-color: #f0f0f0; "
            "color: #666; "
            "font-size: 12px; "
            "border-radius: 5px;"
        )
        self.current_pixmap = None
        self.current_card = None

    def load_deck(self) -> bool:
        dialog = DeckListDialog(self, initial_text=self.deck_text)
        if dialog.exec_() != QDialog.Accepted:
            return False
        return self.load_deck_from_text(dialog.get_text())

    def _maybe_convert_ringsdb_text(
        self, text: str, silent: bool = False
    ) -> Optional[str]:
        """若输入为 RingsDB URL/ID，转换为 Main Deck 文本；失败返回 None。"""
        try:
            from CardViewer import is_ringsdb_source, ringsdb_to_deck_text
        except ImportError:
            return text
        if not is_ringsdb_source(text):
            return text
        try:
            QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
            QApplication.processEvents()
            try:
                deck_name, deck_text = ringsdb_to_deck_text(text)
            finally:
                QApplication.restoreOverrideCursor()
        except Exception as exc:
            msg = f"加载 RingsDB 牌组失败：{exc}"
            if silent:
                print(msg)
            else:
                QMessageBox.warning(self, "RingsDB 加载失败", msg)
            return None
        print(f"已从 RingsDB 加载卡组「{deck_name}」")
        return deck_text

    def load_deck_from_text(self, text: str, silent: bool = False) -> bool:
        text = self._maybe_convert_ringsdb_text(text, silent=silent)
        if text is None:
            return False
        draw_pile, heroes, parsed, issues = build_player_deck_from_text(text)

        if parsed.errors:
            QMessageBox.warning(
                self, "解析错误",
                "卡组文本格式有误：\n" + "\n".join(parsed.errors),
            )
            return False

        if issues:
            msg = "以下卡牌未在 魔戒玩家牌.csv 中找到：\n" + "\n".join(issues)
            if silent:
                print(msg)
            else:
                QMessageBox.warning(self, "部分卡牌未找到", msg)

        if not draw_pile:
            if not silent:
                QMessageBox.information(self, "提示", "主牌组为空，请检查列表。")
            return False

        self.deck_text = text
        self.deck_path = "文本卡组"
        self.deck_heroes = heroes
        self.deck_spec = None
        self.cards = draw_pile
        self.current_card = None
        self.drawn_ids.clear()
        self._init_deck_stack()

        hero_names = "、".join(h.name for h in heroes) if heroes else "无"
        print(
            f"成功加载玩家卡组: 主牌 {len(draw_pile)} 张，"
            f"英雄 {len(heroes)} 名（{hero_names}）"
        )
        self.deck_loaded.emit(self.deck_path)
        self.show_card_back()
        return True

    def load_deck_from_path(self, file_path: str):
        self.deck_text = None
        self.deck_heroes = []
        self.deck_path = file_path
        if Path(file_path).suffix.lower() != ".csv":
            self.deck_spec = None
        self.cards = self._reload_cards()
        self.current_card = None
        self.drawn_ids.clear()
        self._init_deck_stack()

        if self.cards:
            print(f"成功加载玩家卡组: {len(self.cards)} 张卡牌")
            self.deck_loaded.emit(file_path)
        else:
            print("玩家卡组加载失败或为空")

        self.show_card_back()

    def reset_deck(self):
        if self.deck_path:
            self.cards = self._reload_cards()
            self.current_card = None
            self.drawn_ids.clear()
            self._init_deck_stack()
            self.show_card_back()
            self.deck_reset.emit()
            print("玩家卡组已重置")

    def _init_deck_stack(self, shuffle: bool = True) -> None:
        """初始化有序牌库（index 0 为牌库顶）。"""
        self.deck_stack = list(self.cards)
        if shuffle:
            random.shuffle(self.deck_stack)

    def _ensure_deck_stack(self) -> None:
        if self.deck_stack:
            return
        self._init_deck_stack(shuffle=False)
        if self.drawn_ids:
            self.deck_stack = [
                c for c in self.deck_stack if c.id not in self.drawn_ids
            ]

    def peek_deck_top(self, count: int) -> List["Card"]:
        """查看牌库顶若干张（不移除）。"""
        self._ensure_deck_stack()
        return list(self.deck_stack[: max(0, int(count))])

    def take_deck_top(self, count: int) -> List["Card"]:
        """从牌库顶取走若干张。"""
        self._ensure_deck_stack()
        taken: List[Card] = []
        for _ in range(max(0, int(count))):
            if not self.deck_stack:
                break
            card = self.deck_stack.pop(0)
            self.drawn_ids.add(card.id)
            taken.append(card)
        return taken

    def put_cards_on_deck_top(self, ordered_cards: List["Card"]) -> None:
        """按顺序放回牌库顶（ordered_cards[0] 为最顶）。"""
        self._ensure_deck_stack()
        for card in reversed(ordered_cards):
            self.drawn_ids.discard(card.id)
            self.deck_stack.insert(0, card)

    def place_card_on_deck_bottom(self, card: "Card") -> None:
        """将指定卡牌放置于牌库底端。"""
        if card is None:
            return
        self._ensure_deck_stack()
        self.drawn_ids.discard(card.id)
        self.deck_stack.append(card)
        if not any(c.id == card.id for c in self.cards):
            self.cards.append(card)

    def debug_place_card_on_top(self, card: "Card") -> None:
        """Debug：将指定卡牌放置于牌库顶。"""
        if card is None:
            return
        if (getattr(card, "type", "") or "").strip() == "英雄":
            print(f"Debug：无法放置英雄「{card.name}」（英雄不在主牌组中）")
            return
        self._ensure_deck_stack()
        self.drawn_ids.discard(card.id)
        self.deck_stack.insert(0, card)
        if not any(c.id == card.id for c in self.cards):
            self.cards.append(card)
        print(f"Debug：已将「{card.name}」放置于玩家牌库顶（剩余 {len(self.deck_stack)} 张）")

    def _debug_pick_and_place_on_top(self, parent=None) -> None:
        from debug_card_picker import pick_player_card_for_debug

        card = pick_player_card_for_debug(
            parent or self,
            series=self.deck_series,
            deck_text=self.deck_text,
            deck_cards=list(self.cards),
        )
        if card is None:
            return
        self.debug_place_card_on_top(card)

    def _pick_random_card(self, allow_reshuffle: bool = True) -> Optional["Card"]:
        """从牌库顶抽取一张（不更新抽卡器显示）。"""
        if not self.cards:
            return None

        self._ensure_deck_stack()
        if self.deck_stack:
            card = self.deck_stack.pop(0)
            self.drawn_ids.add(card.id)
            record_player_card(card, self.deck_path)
            return card

        available_cards = [card for card in self.cards if card.id not in self.drawn_ids]

        if not available_cards and self.deck_path and allow_reshuffle:
            self.cards = self._reload_cards()
            self.drawn_ids.clear()
            available_cards = self.cards.copy()
            print("玩家卡组已重新洗牌")

        if not available_cards:
            return None

        card = random.choice(available_cards)
        self.drawn_ids.add(card.id)
        record_player_card(card, self.deck_path)
        return card

    def return_cards_to_deck(self, cards: List["Card"]) -> int:
        """将指定卡牌从已抽集合移回牌库（可再次抽到）。"""
        count = 0
        for card in cards:
            if card.id in self.drawn_ids:
                self.drawn_ids.discard(card.id)
                count += 1
        if cards:
            self._ensure_deck_stack()
            self.deck_stack.extend(cards)
            random.shuffle(self.deck_stack)
        if count:
            names = "、".join(c.name for c in cards[:count])
            print(f"已将 {count} 张玩家卡洗回牌库: {names}")
        return count

    def draw_cards(self, count: int = 1, allow_reshuffle: bool = True) -> List["Card"]:
        """随机抽取多张玩家卡，返回 Card 列表。"""
        drawn: List[Card] = []
        for _ in range(max(0, int(count))):
            card = self._pick_random_card(allow_reshuffle=allow_reshuffle)
            if card is None:
                break
            drawn.append(card)
        if drawn:
            names = "、".join(c.name for c in drawn)
            print(f"抽取 {len(drawn)} 张玩家卡: {names}")
        return drawn

    def draw_card(self):
        if not self.cards:
            QMessageBox.information(self, "提示", "请先加载玩家卡组！")
            return

        card = self._pick_random_card()
        if not card:
            QMessageBox.information(self, "提示", "没有可抽取的卡牌！")
            return

        self.current_card = card

        if self.current_card.image_path and os.path.exists(self.current_card.image_path):
            pixmap = QPixmap(self.current_card.image_path)
            if not pixmap.isNull():
                self.image_label.setStyleSheet(
                    "border: 1px solid #666; "
                    "background-color: white; "
                    "border-radius: 5px;"
                )
                self.image_label.set_image(pixmap)
                self.current_pixmap = pixmap
            else:
                self.show_error_image("图片格式错误")
        else:
            self.show_error_image("图片文件不存在")

        self.card_drawn.emit(self.current_card.id)
        print(f"抽取玩家卡: {self.current_card.name} ({self.current_card.id})")

    def show_error_image(self, error_msg: str):
        self.image_label.clear_image()
        self.image_label.setText(f"玩家卡\n{error_msg}")
        self.image_label.setStyleSheet(
            "border: 1px solid red; "
            "background-color: #ffcccc; "
            "color: red; "
            "font-size: 10px; "
            "border-radius: 5px;"
        )
        self.current_pixmap = None

    def view_log(self):
        records = read_draw_log()
        if not records:
            QMessageBox.information(self, "抽卡记录", "暂无抽卡记录")
            return

        text = "最近抽取的玩家卡:\n\n"
        text += "\n".join(
            f"ID: {cid}\n路径: {path}\n" + "-" * 30 for cid, path in records[-10:]
        )
        QMessageBox.information(self, "抽卡记录", text)

    def get_current_card_info(self) -> Dict[str, str]:
        if not self.current_card:
            return {}

        return {
            "name": self.current_card.name,
            "id": self.current_card.id,
            "type": self.current_card.type,
            "sphere": self.current_card.Sphere,
            "cost": self.current_card.Cost,
            "text_effect": self.current_card.Text_Effect,
        }

    def set_debug_mode(self, enabled: bool):
        self.debug_mode = enabled


class PlayerTestWindow(QMainWindow):
    """玩家卡抽取器测试窗口。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("玩家卡抽取模块测试")
        self.setFixedSize(520, 400)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.drawer = CardDrawer()
        self.drawer.set_debug_mode(True)

        heroes = "、".join(h.name for h in self.drawer.deck_heroes) or "、".join(DEFAULT_TEST_HEROES)
        hint = QLabel(
            "右键双击：放大卡面\n"
            "Debug 开启后右键：菜单（加载/重置/抽卡/放置牌库顶/日志）\n"
            "「加载卡组」打开文本窗口，粘贴 Main Deck 格式列表\n"
            f"默认主牌组 50 张；英雄：{heroes}"
        )
        hint.setStyleSheet("color: #444;")
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.drawer)
        row.addStretch()
        layout.addLayout(row)

        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 13px; color: #004488;")
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        for text, slot in (
            ("抽取卡牌", self.drawer.draw_card),
            ("重置卡组", self.drawer.reset_deck),
            ("显示卡背", self.drawer.show_card_back),
            ("加载卡组", self.drawer.load_deck),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)
        layout.addLayout(btn_row)

        self.drawer.card_drawn.connect(lambda _cid: self._update_status())
        self.drawer.deck_loaded.connect(lambda _path: self._update_status())
        self.drawer.deck_reset.connect(self._update_status)
        self._update_status()

    def _update_status(self):
        total = len(self.drawer.cards)
        drawn = len(self.drawer.drawn_ids)
        deck = self.drawer.deck_path or "未加载"
        hero_count = len(self.drawer.deck_heroes)
        info = self.drawer.get_current_card_info()

        if info:
            detail = (
                f"{info.get('name', '?')} [{info.get('type', '?')}] "
                f"{info.get('sphere', '')} 费用 {info.get('cost') or '-'}"
            )
            has_face = self.drawer.current_card is not None
            img = "有图" if has_face and self.drawer.current_pixmap and not self.drawer.current_pixmap.isNull() else "缺图"
            detail += f"（{img}）"
        else:
            detail = "卡背 / 尚未抽卡"

        hero_note = f"英雄 {hero_count}　|　" if hero_count else ""
        self.status_label.setText(
            f"{deck}　|　{hero_note}牌库 {total} 张　已抽 {drawn}　|　{detail}"
        )


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = PlayerTestWindow()
    window.show()
    sys.exit(app.exec_())
