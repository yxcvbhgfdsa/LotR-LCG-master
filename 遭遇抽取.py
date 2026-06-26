import csv
import json
import os
import random
import sys
import datetime
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass

from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QFileDialog, QMenu, QVBoxLayout,
    QMessageBox, QMainWindow, QHBoxLayout, QPushButton,
)
from PyQt5.QtGui import QPixmap
from PyQt5.QtCore import Qt, pyqtSignal, QTimer

_PROJECT_ROOT = Path(__file__).resolve().parent
ENCOUNTER_CSV = _PROJECT_ROOT / "魔戒遭遇.csv"
ENCOUNTER_IMAGE_DIRS = (
    _PROJECT_ROOT / "cards" / "遭遇牌组",
    _PROJECT_ROOT / "cards" / "场景",
)
CARD_BACK_PATH = _PROJECT_ROOT / "cards" / "images" / "encounter_card_back.jpg"
LOG_FILE = _PROJECT_ROOT / "draw_log.txt"
DEFAULT_DECK_SERIES = "穿越黑森林"
# 遭遇抽牌堆包含的类型（o8d / 遭遇牌库）
ENCOUNTER_DECK_TYPES = frozenset({
    "敌人",
    "地区",
    "目标",
    "目标-盟友",
    "遭遇支线探险",
    "诡计",
})
O8D_ENCOUNTER_SECTION = "Encounter"
O8D_QUEST_SECTION = "Quest"
# 遭遇卡图源尺寸（宽 × 高，竖版）
ENCOUNTER_CARD_W = 358
ENCOUNTER_CARD_H = 500


def fit_encounter_card_size(max_height: int = 158) -> tuple[int, int]:
    """按遭遇卡比例计算显示区域宽高，适配格子高度。"""
    h = max(1, int(max_height))
    w = max(1, round(h * ENCOUNTER_CARD_W / ENCOUNTER_CARD_H))
    return w, h


def _image_id_stem(image_id: str) -> str:
    """去掉图片链接末尾已有扩展名，避免重复拼接 .jpg。"""
    stem = (image_id or "").strip()
    lower = stem.lower()
    for ext in (".jpg", ".jpeg", ".png"):
        if lower.endswith(ext):
            return stem[: -len(ext)]
    return stem


def resolve_encounter_image(image_id: str) -> str:
    """根据 CSV 图片链接解析遭遇卡图片路径。"""
    stem = _image_id_stem(image_id)
    if not stem:
        return ""
    for folder in ENCOUNTER_IMAGE_DIRS:
        if not folder.is_dir():
            continue
        for ext in (".jpg", ".jpeg", ".png", ".JPG"):
            path = folder / f"{stem}{ext}"
            if path.is_file():
                return str(path)
    return ""


@dataclass
class Card:
    id: str
    name: str
    Category: str
    Enemy: str
    Treachery: str
    Area: str
    Progress: str
    Discard: str
    Threat_Level: str
    image_path: str
    Attack: str
    Threat: str
    Engagement: str
    Defense: str
    Health: str
    type: str
    Text_Effect: str
    Demon_Shadow: str

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Card":
        return Card(
            id=data.get("id", ""),
            name=data.get("name", ""),
            Category=data.get("Category", ""),
            Enemy=data.get("Enemy", ""),
            Treachery=data.get("Treachery", ""),
            Area=data.get("Area", ""),
            Progress=data.get("Progress", ""),
            Discard=data.get("Discard", ""),
            Threat_Level=data.get("Threat Level", ""),
            image_path=data.get("image_path", ""),
            Attack=data.get("Attack", ""),
            Threat=data.get("Threat", ""),
            Engagement=data.get("Engagement", ""),
            Defense=data.get("Defense", ""),
            Health=data.get("Health", ""),
            type=data.get("type", ""),
            Text_Effect=data.get("Text Effect", ""),
            Demon_Shadow=data.get("Demon Shadow", ""),
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
            name=(row.get("卡牌名称") or "").strip(),
            Category=card_type,
            Enemy="1" if card_type == "敌人" else "",
            Treachery="1" if card_type in ("阴谋", "诡计") else "",
            Area="1" if card_type == "地区" else "",
            Progress=(row.get("探险进度") or "").strip(),
            Discard="",
            Threat_Level=(row.get("威胁值") or "").strip(),
            image_path=resolve_encounter_image(image_id),
            Attack=(row.get("攻击力") or "").strip(),
            Threat=(row.get("威胁值") or "").strip(),
            Engagement=(row.get("交战值") or "").strip(),
            Defense=(row.get("防御值") or "").strip(),
            Health=(row.get("生命值") or "").strip(),
            type=card_type,
            Text_Effect=(row.get("特性") or "").strip(),
            Demon_Shadow=(row.get("魔影效果") or "").strip(),
        )


def load_encounter_cards_from_csv(
    series: Optional[str] = DEFAULT_DECK_SERIES,
    exclude_types: tuple = ("探险",),
    csv_path: Optional[Path] = None,
) -> List[Card]:
    """从魔戒遭遇.csv 加载遭遇牌组（默认排除探险任务卡）。"""
    csv_path = csv_path or ENCOUNTER_CSV
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
            qty_raw = (row.get("卡牌数量") or "1").strip()
            try:
                qty = max(1, int(qty_raw))
            except ValueError:
                qty = 1
            for i in range(qty):
                cards.append(Card.from_csv_row(row, copy_index=i if qty > 1 else 0))
    return cards


def _read_encounter_csv_rows(csv_path: Optional[Path] = None) -> List[Dict[str, str]]:
    csv_path = csv_path or ENCOUNTER_CSV
    if not csv_path.is_file():
        return []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _build_encounter_index_by_image_id(
    csv_path: Optional[Path] = None,
    series: Optional[str] = None,
) -> Dict[str, Dict[str, str]]:
    index: Dict[str, Dict[str, str]] = {}
    for row in _read_encounter_csv_rows(csv_path):
        if series and (row.get("系列") or "").strip() != series:
            continue
        image_id = (row.get("图片链接") or "").strip()
        if image_id:
            index[image_id] = row
    return index


def _csv_card_qty(row: Dict[str, str]) -> int:
    qty_raw = (row.get("卡牌数量") or "1").strip()
    try:
        return max(1, int(qty_raw))
    except ValueError:
        return 1


def load_encounter_deck_from_o8d(
    o8d_path: str | Path,
    csv_path: Optional[Path] = None,
    series: Optional[str] = None,
) -> Tuple[List[Card], List[str]]:
    """
    从 o8d 的 Encounter 节构建遭遇抽牌堆。
    仅保留 ENCOUNTER_DECK_TYPES 中的类型；每种牌的张数取自 CSV「卡牌数量」，忽略 o8d qty。
    """
    o8d_path = Path(o8d_path)
    csv_path = csv_path or ENCOUNTER_CSV
    series = series or DEFAULT_DECK_SERIES

    tree = ET.parse(o8d_path)
    root = tree.getroot()

    encounter_section = None
    for section in root.findall("section"):
        if (section.get("name") or "").strip() == O8D_ENCOUNTER_SECTION:
            encounter_section = section
            break

    if encounter_section is None:
        return [], [f"未找到 {O8D_ENCOUNTER_SECTION} 节"]

    series_index = _build_encounter_index_by_image_id(csv_path, series=series)
    global_index = _build_encounter_index_by_image_id(csv_path, series=None)

    seen_ids: set[str] = set()
    cards: List[Card] = []
    missing: List[str] = []
    skipped: List[str] = []

    for card_el in encounter_section.findall("card"):
        image_id = (card_el.get("id") or "").strip()
        if not image_id or image_id in seen_ids:
            continue
        seen_ids.add(image_id)

        row = series_index.get(image_id) or global_index.get(image_id)
        label = (card_el.text or "").strip() or image_id
        if not row:
            missing.append(label)
            continue

        card_type = (row.get("类型") or "").strip()
        if card_type not in ENCOUNTER_DECK_TYPES:
            skipped.append(f"{row.get('卡牌名称', label)} [{card_type}]")
            continue

        qty = _csv_card_qty(row)
        for i in range(qty):
            cards.append(Card.from_csv_row(row, copy_index=i if qty > 1 else 0))

    if skipped:
        print(f"o8d 中已跳过非遭遇库类型: {', '.join(skipped)}")
    return cards, missing


def _find_o8d_section(root: ET.Element, section_name: str) -> Optional[ET.Element]:
    for section in root.findall("section"):
        if (section.get("name") or "").strip() == section_name:
            return section
    return None


def infer_series_from_o8d(
    o8d_path: str | Path,
    csv_path: Optional[Path] = None,
) -> Optional[str]:
    """从 o8d Encounter 节首张能在 CSV 匹配的牌推断系列名。"""
    o8d_path = Path(o8d_path)
    csv_path = csv_path or ENCOUNTER_CSV
    try:
        root = ET.parse(o8d_path).getroot()
    except ET.ParseError:
        return None
    section = _find_o8d_section(root, O8D_ENCOUNTER_SECTION)
    if section is None:
        return None
    index = _build_encounter_index_by_image_id(csv_path, series=None)
    for card_el in section.findall("card"):
        image_id = (card_el.get("id") or "").strip()
        row = index.get(image_id)
        if row:
            return (row.get("系列") or "").strip() or None
    return None


def load_deck_cards(file_path: str) -> List[Card]:
    """从 JSON 卡组文件加载卡牌。"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            config = json.load(f)
            cards_data = config.get("cards", [])
            return [
                Card.from_dict(card)
                for card in cards_data
                if card.get("Discard", "0").strip() != "1"
            ]
    except Exception as e:
        print(f"加载卡组失败: {e}")
        return []


def record_card_draw(card: "Card", deck_path: Optional[str], log_file: Path = LOG_FILE):
    """记录抽卡日志"""
    try:
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(
                f"{datetime.datetime.now().isoformat()}\n"
                f"卡牌名称: {card.name}\n"
                f"抽取卡牌 ID: {card.id}\n"
                f"路径: {card.image_path}\n"
                f"敌人(Enemy): {card.Enemy} | 阴谋(Treachery): {card.Treachery} | 区域(Area): {card.Area}\n"
                f"来源卡组: {deck_path or '未知'}\n"
                f"{'-' * 40}\n"
            )
    except Exception as e:
        print(f"记录抽卡日志失败: {e}")


def read_draw_log(log_file: Path = LOG_FILE) -> list[tuple[str, str]]:
    """读取抽卡日志"""
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
    """简化的图片放大显示窗口"""

    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self.original_pixmap = pixmap
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("遭遇卡放大显示 - 单击关闭")
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


class EncounterCardLabel(QLabel):
    """遭遇卡图：随控件尺寸等比缩放，避免原图撑破布局。"""

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
    """遭遇卡抽取器"""

    card_drawn = pyqtSignal(str)
    deck_loaded = pyqtSignal(str)
    deck_reset = pyqtSignal()
    o8d_loaded = pyqtSignal(str)

    def __init__(self, parent=None, max_height: int = 158):
        super().__init__(parent)
        self.cards: List[Card] = []
        self.current_card: Optional[Card] = None
        self.current_pixmap: Optional[QPixmap] = None
        self.deck_path: Optional[str] = None
        self.deck_series: Optional[str] = DEFAULT_DECK_SERIES
        self.debug_mode = False
        self.drawn_ids: set[str] = set()
        self.zoom_dialog: Optional[ImageZoomDialog] = None
        self._ctx_menu_pos = None
        self._ctx_menu_timer = QTimer(self)
        self._ctx_menu_timer.setSingleShot(True)
        self._ctx_menu_timer.timeout.connect(self._open_context_menu)
        self._card_w, self._card_h = fit_encounter_card_size(max_height)
        self.init_ui()
        self.auto_load_default_deck()

    def init_ui(self):
        self.setFixedSize(self._card_w, self._card_h)

        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        self.image_label = EncounterCardLabel()
        self.image_label.setFixedSize(self._card_w, self._card_h)
        self.image_label.mouseDoubleClickEvent = self.on_double_click

        self.layout.addWidget(self.image_label)
        self.setLayout(self.layout)
        self.show_card_back()

    def _reload_cards(self) -> List[Card]:
        if not self.deck_path:
            return []
        path = Path(self.deck_path)
        if path.suffix.lower() == ".csv":
            return load_encounter_cards_from_csv(series=self.deck_series, csv_path=path)
        if path.suffix.lower() == ".o8d":
            cards, missing = load_encounter_deck_from_o8d(
                path, series=self.deck_series
            )
            if missing:
                print(f"o8d 中未在 CSV 找到的遭遇牌: {', '.join(missing)}")
            return cards
        return load_deck_cards(self.deck_path)

    def auto_load_default_deck(self):
        """自动加载默认遭遇牌组（魔戒遭遇.csv）。"""
        candidates = [
            ENCOUNTER_CSV,
            _PROJECT_ROOT / "cards" / "encounter_deck.json",
            _PROJECT_ROOT / "encounter_deck.json",
        ]
        for path in candidates:
            if path.is_file():
                self.load_deck_from_path(str(path))
                print(f"自动加载默认卡组: {path}")
                break

    def on_double_click(self, event):
        if event.button() == Qt.RightButton:
            if self.current_pixmap and not self.current_pixmap.isNull():
                self._ctx_menu_timer.stop()
                self._ctx_menu_pos = None
                self.show_zoomed_image()
            return
        if event.button() == Qt.LeftButton:
            if not (self.current_pixmap and not self.current_pixmap.isNull()):
                self.draw_card()

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
            print(f"遭遇抽取Debug模式: {'开启' if self.debug_mode else '关闭'}")
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
        self.image_label.setText("遭遇卡\n双击抽取")
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
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择遭遇卡组",
            str(_PROJECT_ROOT),
            "遭遇卡组 (*.csv *.json *.o8d);;CSV (*.csv);;JSON (*.json);;OctGN (*.o8d)",
        )
        if not file_path:
            return False
        return self.load_deck_from_path(file_path)

    def load_deck_from_path(self, file_path: str) -> bool:
        self.deck_path = file_path
        path = Path(file_path)
        missing: List[str] = []

        if path.suffix.lower() == ".o8d":
            inferred = infer_series_from_o8d(path)
            if inferred:
                self.deck_series = inferred
            self.cards, missing = load_encounter_deck_from_o8d(
                path, series=self.deck_series
            )
        else:
            self.cards = self._reload_cards()

        self.current_card = None
        self.drawn_ids.clear()

        if self.cards:
            print(f"成功加载遭遇卡组: {len(self.cards)} 张卡牌")
            self.deck_loaded.emit(file_path)
            if path.suffix.lower() == ".o8d":
                self.o8d_loaded.emit(file_path)
        else:
            print("卡组加载失败或为空")
            if missing:
                QMessageBox.warning(
                    self,
                    "部分卡牌未找到",
                    "以下卡牌未在 CSV 中匹配:\n" + "\n".join(missing[:20]),
                )
            self.show_card_back()
            return False

        if missing:
            QMessageBox.warning(
                self,
                "部分卡牌未找到",
                "以下卡牌未在 CSV 中匹配:\n" + "\n".join(missing[:20]),
            )

        self.show_card_back()
        return True

    def reset_deck(self):
        if self.deck_path:
            self.cards = self._reload_cards()
            self.current_card = None
            self.drawn_ids.clear()
            self.show_card_back()
            self.deck_reset.emit()
            print("玩家卡组已重置")

    def extract_cards_by_names(self, names: List[str]) -> Tuple[List["Card"], List[str]]:
        """从牌库各取一张指定名称的牌（移出抽牌堆，用于探查区准备）。"""
        extracted: List[Card] = []
        missing: List[str] = []
        for raw_name in names:
            name = (raw_name or "").strip()
            if not name:
                continue
            found_idx = None
            for i, card in enumerate(self.cards):
                if card.name == name:
                    found_idx = i
                    break
            if found_idx is None:
                missing.append(name)
            else:
                card = self.cards.pop(found_idx)
                self.drawn_ids.discard(card.id)
                extracted.append(card)
        return extracted, missing

    def return_cards_to_deck(self, cards: List["Card"]) -> int:
        """将先前取出的卡牌放回牌库（与 extract_cards_by_names 对称）。"""
        count = 0
        for card in cards:
            self.cards.append(card)
            self.drawn_ids.discard(card.id)
            count += 1
        if count:
            names = "、".join(c.name for c in cards)
            print(f"已将 {count} 张遭遇卡放回牌库: {names}")
        return count

    def shuffle_deck(self):
        """洗混剩余遭遇牌库。"""
        random.shuffle(self.cards)
        print(f"遭遇牌库已洗牌（剩余 {len(self.cards)} 张）")

    def _replenish_deck_from_discard(
        self,
        discard_pile: Optional[List["Card"]] = None,
        *,
        allow_reshuffle: bool = True,
    ) -> bool:
        """牌库空时将遭遇弃牌堆洗入；成功返回 True。"""
        if self.cards:
            return True
        if not allow_reshuffle or not discard_pile:
            return False
        if not discard_pile:
            return False
        count = len(discard_pile)
        self.cards = list(discard_pile)
        discard_pile.clear()
        random.shuffle(self.cards)
        self.drawn_ids.clear()
        print(
            f"遭遇牌库耗尽，已将遭遇弃牌堆 {count} 张洗入并重置遭遇牌库"
        )
        return bool(self.cards)

    def peek_top_card(
        self,
        discard_pile: Optional[List["Card"]] = None,
        allow_reshuffle: bool = True,
    ) -> Optional["Card"]:
        """查看牌库顶（不移除）。"""
        if not self._replenish_deck_from_discard(
            discard_pile, allow_reshuffle=allow_reshuffle
        ):
            return None
        return self.cards[0]

    def move_top_card_to_bottom(self) -> Optional["Card"]:
        """将牌库顶一张移至牌库底。"""
        if not self.cards:
            return None
        card = self.cards.pop(0)
        self.cards.append(card)
        print(f"遭遇牌库：「{card.name}」从顶移至底（剩余 {len(self.cards)} 张）")
        return card

    def draw_top_card(
        self,
        discard_pile: Optional[List["Card"]] = None,
        allow_reshuffle: bool = True,
        record: bool = True,
    ) -> Optional["Card"]:
        """从牌库顶抽取一张；探险环节牌库耗尽时可将遭遇弃牌堆洗入重置。"""
        if not self._replenish_deck_from_discard(
            discard_pile, allow_reshuffle=allow_reshuffle
        ):
            return None

        card = self.cards.pop(0)
        self.drawn_ids.discard(card.id)
        if record and self.deck_path:
            record_card_draw(card, self.deck_path)
        return card

    def debug_place_card_on_top(self, card: "Card") -> None:
        """Debug：将指定卡牌放置于遭遇牌库顶。"""
        if card is None:
            return
        self.drawn_ids.discard(card.id)
        self.cards.insert(0, card)
        print(
            f"Debug：已将「{card.name}」放置于遭遇牌库顶"
            f"（剩余 {len(self.cards)} 张）"
        )

    def _debug_pick_and_place_on_top(self) -> None:
        from debug_card_picker import pick_encounter_card_for_debug

        card = pick_encounter_card_for_debug(self, series=self.deck_series)
        if card is None:
            return
        self.debug_place_card_on_top(card)

    def draw_card(self):
        if not self.cards:
            QMessageBox.information(self, "提示", "请先加载遭遇卡组！")
            return

        available_cards = [card for card in self.cards if card.id not in self.drawn_ids]

        if not available_cards and self.deck_path:
            self.cards = self._reload_cards()
            self.drawn_ids.clear()
            available_cards = self.cards.copy()
            print("遭遇卡组已重新洗牌")

        if not available_cards:
            QMessageBox.information(self, "提示", "没有可抽取的卡牌！")
            return

        self.current_card = random.choice(available_cards)
        self.drawn_ids.add(self.current_card.id)

        record_card_draw(self.current_card, self.deck_path)

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
        print(f"抽取遭遇卡: {self.current_card.name} ({self.current_card.id})")

    def show_error_image(self, error_msg: str):
        self.image_label.clear_image()
        self.image_label.setText(f"遭遇卡\n{error_msg}")
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

        text = "最近抽取的遭遇卡:\n\n"
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
            "enemy": self.current_card.Enemy,
            "treachery": self.current_card.Treachery,
            "area": self.current_card.Area,
            "threat_level": self.current_card.Threat_Level,
            "text_effect": self.current_card.Text_Effect,
        }

    def set_debug_mode(self, enabled: bool):
        self.debug_mode = enabled


class EncounterTestWindow(QMainWindow):
    """遭遇卡抽取器测试窗口。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("遭遇抽取模块测试")
        self.setFixedSize(480, 360)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        hint = QLabel(
            "左键双击：无卡面时抽卡\n"
            "右键双击：放大卡面\n"
            "Debug 开启后右键：菜单（加载/重置/抽卡/日志）\n"
            "支持 CSV / JSON / o8d；o8d 遭遇库张数取自 CSV，探险任务自动加载场景模块"
        )
        hint.setStyleSheet("color: #444;")
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.addStretch()
        self.drawer = CardDrawer()
        self.drawer.set_debug_mode(True)
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
        deck = Path(self.drawer.deck_path).name if self.drawer.deck_path else "未加载"
        info = self.drawer.get_current_card_info()

        if info:
            detail = (
                f"{info.get('name', '?')} [{info.get('type', '?')}] "
                f"威胁 {info.get('threat_level') or '-'}"
            )
            img = "有图" if self.drawer.current_pixmap and not self.drawer.current_pixmap.isNull() else "缺图"
            detail += f"（{img}）"
        else:
            detail = "卡背 / 尚未抽卡"

        self.status_label.setText(
            f"{deck}　|　牌库 {total} 张　已抽 {drawn}　|　{detail}"
        )


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = EncounterTestWindow()
    window.show()
    sys.exit(app.exec_())
