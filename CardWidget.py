import copy
import csv
import sys
from pathlib import Path
from typing import Optional, Tuple

from PyQt5.QtWidgets import (
    QLabel, QVBoxLayout, QHBoxLayout, QDialog, QMenu,
    QWidget, QGridLayout, QFrame, QApplication, QMainWindow,
    QPushButton,
)
from PyQt5.QtGui import QPixmap, QFont
from PyQt5.QtCore import Qt, pyqtSignal, QTimer

from card_drag_zoom import CardDragZoomController

from 遭遇抽取 import (
    ENCOUNTER_CSV,
    DEFAULT_DECK_SERIES,
    Card,
    fit_encounter_card_size,
)

_PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CARD_NAME = "森林蜘蛛"
ENCOUNTER_CARD_BACK = _PROJECT_ROOT / "cards" / "images" / "encounter_card_back.jpg"

MARKER_ICONS = {
    "Attack": _PROJECT_ROOT / "cards" / "images" / "attack.png",
    "Defense": _PROJECT_ROOT / "cards" / "images" / "Defense.png",
    "Damage": _PROJECT_ROOT / "cards" / "images" / "tokens" / "damage.png",
    "Progress": _PROJECT_ROOT / "cards" / "images" / "tokens" / "progress.png",
    "Threat": _PROJECT_ROOT / "cards" / "images" / "Threat.jpg",
    "Resource": _PROJECT_ROOT / "cards" / "images" / "tokens" / "resource.png",
}

# 按卡牌 id 保留 Debug 标记，控件刷新后恢复
_MARKER_STATE_CACHE: dict[str, dict] = {}


def _parse_int(value, default: int = 0) -> int:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        return default


def load_encounter_row_by_name(
    name: str,
    csv_path: Path = ENCOUNTER_CSV,
    series: Optional[str] = DEFAULT_DECK_SERIES,
) -> Optional[dict]:
    """从魔戒遭遇.csv 按卡牌名称查找一行。"""
    name = (name or "").strip()
    if not name or not csv_path.is_file():
        return None
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("卡牌名称") or "").strip() != name:
                continue
            if series and (row.get("系列") or "").strip() != series:
                continue
            return row
    return None


def load_encounter_card_by_name(
    name: str = DEFAULT_CARD_NAME,
    csv_path: Path = ENCOUNTER_CSV,
    series: Optional[str] = DEFAULT_DECK_SERIES,
) -> Optional[Card]:
    """从魔戒遭遇.csv 加载指定名称的遭遇卡。"""
    row = load_encounter_row_by_name(name, csv_path=csv_path, series=series)
    if not row:
        return None
    return Card.from_csv_row(row)


def clear_marker_state_cache():
    """新局开始时清空已缓存的标记状态。"""
    _MARKER_STATE_CACHE.clear()


def export_marker_state_cache() -> dict:
    """导出当前遭遇卡标记状态缓存的深拷贝（用于环节存档）。"""
    return copy.deepcopy(_MARKER_STATE_CACHE)


def restore_marker_state_cache(data: dict) -> None:
    """用存档中的遭遇卡标记状态覆盖缓存（取消回档时调用）。"""
    _MARKER_STATE_CACHE.clear()
    if data:
        _MARKER_STATE_CACHE.update(copy.deepcopy(data))


def marker_state_key_for_card(card) -> str:
    """与 CardWidget.marker_state_key 一致的缓存键。"""
    card_id = getattr(card, "id", "") or ""
    if card_id:
        return str(card_id)
    series = (getattr(card, "series", "") or "").strip() or DEFAULT_DECK_SERIES
    name = (getattr(card, "name", "") or "").strip()
    return f"{series}:{name}"


def clear_marker_state_for_card(card):
    """遭遇卡进入弃牌堆等场外区域时清除其伤害/进度等标记缓存。"""
    key = marker_state_key_for_card(card)
    if key:
        _MARKER_STATE_CACHE.pop(key, None)


def set_encounter_marker_progress_for_card(card, progress: int) -> None:
    """为遭遇地区卡写入进度标记缓存（如大步佬之路将当前地区送回场景区时）。"""
    key = marker_state_key_for_card(card)
    if not key:
        return
    count = max(0, min(12, int(progress)))
    state = copy.deepcopy(_MARKER_STATE_CACHE.get(key, {}))
    if not isinstance(state, dict):
        state = {}
    state["bottom_left"] = ["Progress"] * count
    state.setdefault("top_right", [])
    state.setdefault("resource_count", 0)
    _MARKER_STATE_CACHE[key] = state


class MarkerLabel(QLabel):
    def __init__(self, pixmap_path=None, fallback="●", color="#4CAF50", parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.setAlignment(Qt.AlignCenter)
        path_str = str(pixmap_path) if pixmap_path else ""
        if path_str and Path(path_str).is_file():
            pixmap = QPixmap(path_str).scaled(16, 16, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.setPixmap(pixmap)
            self.setStyleSheet("background: transparent;")
        else:
            self.setText(fallback)
            self.setStyleSheet(f"background-color: {color}; border: 1px solid #333; border-radius: 8px;")
            self.setFont(QFont("Arial", 8, QFont.Bold))


class CardImageZoomDialog(QDialog):
    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self.setWindowTitle("卡牌放大显示 - 单击关闭")
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setStyleSheet("background: black")
        self.setContextMenuPolicy(Qt.NoContextMenu)

        screen = QApplication.primaryScreen().availableGeometry()
        max_width = min(800, int(screen.width() * 0.8))
        max_height = min(1000, int(screen.height() * 0.8))

        scaled_pixmap = pixmap.scaled(max_width, max_height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setFixedSize(scaled_pixmap.width() + 40, scaled_pixmap.height() + 40)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setPixmap(scaled_pixmap)
        self.image_label.setStyleSheet("border: 2px solid #888;")
        self.image_label.setContextMenuPolicy(Qt.NoContextMenu)
        layout.addWidget(self.image_label)

        center_point = screen.center()
        self.move(center_point.x() - self.width() // 2, center_point.y() - self.height() // 2)

    def contextMenuEvent(self, event):
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.close()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
            self.close()
        super().keyPressEvent(event)


class CardWidget(QWidget):
    clicked = pyqtSignal()
    stats_changed = pyqtSignal()

    def __init__(
        self,
        card_name: str = DEFAULT_CARD_NAME,
        csv_path: Path = ENCOUNTER_CSV,
        series: Optional[str] = DEFAULT_DECK_SERIES,
        show_threat_badge: bool = False,
        card_size: Optional[Tuple[int, int]] = None,
        max_height: Optional[int] = None,
        show_name_label: bool = False,
        restore_markers: bool = True,
        face_down: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._restore_markers = restore_markers
        self._show_threat_badge = show_threat_badge
        self._show_attack_badge = False
        self._show_defense_badge = False
        self._show_name_label = show_name_label
        self._face_down = face_down
        self.csv_path = Path(csv_path)
        self.series = series
        self.card_path: Optional[str] = None
        self.card_name = "无卡牌"
        self.current_card: Optional[Card] = None
        self.current_pixmap = None
        self.zoom_dialog = None
        self.debug_mode = False
        self._ctx_menu_pos = None
        self._ctx_menu_timer = QTimer(self)
        self._ctx_menu_timer.setSingleShot(True)
        self._ctx_menu_timer.timeout.connect(self._open_context_menu)
        self.top_right_markers = []
        self.bottom_left_markers = []
        self._progress_marker_count = 0
        self.bottom_progress_container = None
        self._progress_count_label = None
        self.top_threat_container = None
        self._threat_count_label = None
        self._suppress_marker_persist = False
        self._passive_threat_bonus = 0
        self._resource_count = 0
        self._passive_attack_per_resource = 0
        self._show_resource_badge = False
        self._base_stats = {
            "attack": 0,
            "defense": 0,
            "health": 0,
            "threat": 0,
            "progress": 0,
        }
        self._current_stats = dict(self._base_stats)
        if card_size:
            self._label_w, self._label_h = card_size
        elif max_height is not None:
            self._label_w, self._label_h = fit_encounter_card_size(max_height)
        else:
            self._label_w, self._label_h = 140, 200

        self.init_ui()
        self._drag_zoom = CardDragZoomController(self, self.show_zoomed_card)
        self._drag_zoom.install(self.card_frame, self.card_label)
        self.set_card_by_name(card_name)

    def init_ui(self):
        name_extra = 20 if self._show_name_label else 0
        self.setFixedSize(self._label_w, self._label_h + name_extra)
        self.setStyleSheet("background: transparent; border: none;")
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(2)

        self.card_frame = QFrame(self)
        self.card_frame.setFrameStyle(QFrame.NoFrame)
        self.card_frame.setStyleSheet("""
            QFrame {
                border: none;
                background-color: transparent;
            }
        """)

        card_layout = QVBoxLayout(self.card_frame)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        self.card_label = QLabel(self.card_frame)
        self.card_label.setFixedSize(self._label_w, self._label_h)
        self.card_label.setScaledContents(False)
        self.card_label.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.card_label.setStyleSheet("""
            QLabel {
                border: none;
                border-radius: 4px;
                background-color: transparent;
                padding: 0;
                margin: 0;
            }
        """)
        card_layout.addWidget(self.card_label)

        self.setup_marker_overlays()

        main_layout.addWidget(self.card_frame)

        self.name_label = QLabel("无卡牌", self)
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setStyleSheet("""
            QLabel {
                font-size: 10px;
                font-weight: bold;
                color: #333;
                background-color: transparent;
                border: none;
                padding: 2px;
            }
        """)
        self.name_label.setMaximumHeight(16)
        main_layout.addWidget(self.name_label)
        if not self._show_name_label:
            self.name_label.hide()

    def setup_marker_overlays(self):
        w, h = self._label_w, self._label_h
        # 标记层挂在 card_frame 上，避免作为 QLabel 子控件被卡图遮挡
        overlay_parent = self.card_frame

        self.top_right_container = QWidget(overlay_parent)
        self.top_right_container.setGeometry(w - 35, 2, 32, 80)
        self.top_right_container.setStyleSheet("background: transparent; border: none;")
        self.top_right_container.setAutoFillBackground(False)
        self.top_right_layout = QGridLayout(self.top_right_container)
        self.top_right_layout.setContentsMargins(0, 0, 0, 0)
        self.top_right_layout.setSpacing(1)

        self.bottom_left_container = QWidget(overlay_parent)
        self.bottom_left_container.setGeometry(2, h - 24, w - 8, 18)
        self.bottom_left_container.setStyleSheet("background: transparent; border: none;")
        self.bottom_left_container.setAutoFillBackground(False)
        self.bottom_left_layout = QHBoxLayout(self.bottom_left_container)
        self.bottom_left_layout.setContentsMargins(0, 0, 0, 0)
        self.bottom_left_layout.setSpacing(2)
        self.bottom_left_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.top_threat_container = QWidget(overlay_parent)
        self.top_threat_container.setStyleSheet(
            "background-color: rgba(0, 0, 0, 0.55); border-radius: 4px;"
        )
        self.top_threat_container.setAutoFillBackground(False)
        self.top_threat_container.setAttribute(Qt.WA_TransparentForMouseEvents)
        threat_layout = QHBoxLayout(self.top_threat_container)
        threat_layout.setContentsMargins(4, 1, 4, 1)
        threat_layout.setSpacing(2)
        threat_layout.setAlignment(Qt.AlignCenter)
        self._threat_count_label = QLabel("0")
        self._threat_count_label.setAlignment(Qt.AlignCenter)
        self._threat_count_label.setStyleSheet(
            "color: #FFAA66; font-weight: bold; font-size: 11px; "
            "background: transparent; border: none;"
        )
        self._threat_icon = MarkerLabel(
            pixmap_path=MARKER_ICONS.get("Threat"),
            fallback="T",
            color="#FFAA66",
        )
        self._threat_icon.setFixedSize(14, 14)
        self._threat_icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        threat_layout.addWidget(self._threat_count_label)
        threat_layout.addWidget(self._threat_icon)
        self.top_threat_container.hide()

        self.top_attack_container = QWidget(overlay_parent)
        self.top_attack_container.setStyleSheet(
            "background-color: rgba(0, 0, 0, 0.55); border-radius: 4px;"
        )
        self.top_attack_container.setAutoFillBackground(False)
        self.top_attack_container.setAttribute(Qt.WA_TransparentForMouseEvents)
        attack_layout = QHBoxLayout(self.top_attack_container)
        attack_layout.setContentsMargins(4, 1, 4, 1)
        attack_layout.setSpacing(2)
        attack_layout.setAlignment(Qt.AlignCenter)
        self._attack_count_label = QLabel("0")
        self._attack_count_label.setAlignment(Qt.AlignCenter)
        self._attack_count_label.setStyleSheet(
            "color: #FF8888; font-weight: bold; font-size: 11px; "
            "background: transparent; border: none;"
        )
        self._attack_icon = MarkerLabel(
            pixmap_path=MARKER_ICONS.get("Attack"),
            fallback="A",
            color="#FF8888",
        )
        self._attack_icon.setFixedSize(14, 14)
        self._attack_icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        attack_layout.addWidget(self._attack_count_label)
        attack_layout.addWidget(self._attack_icon)
        self.top_attack_container.hide()

        self.top_defense_container = QWidget(overlay_parent)
        self.top_defense_container.setStyleSheet(
            "background-color: rgba(0, 0, 0, 0.55); border-radius: 4px;"
        )
        self.top_defense_container.setAutoFillBackground(False)
        self.top_defense_container.setAttribute(Qt.WA_TransparentForMouseEvents)
        defense_layout = QHBoxLayout(self.top_defense_container)
        defense_layout.setContentsMargins(4, 1, 4, 1)
        defense_layout.setSpacing(2)
        defense_layout.setAlignment(Qt.AlignCenter)
        self._defense_count_label = QLabel("0")
        self._defense_count_label.setAlignment(Qt.AlignCenter)
        self._defense_count_label.setStyleSheet(
            "color: #B8E0B8; font-weight: bold; font-size: 11px; "
            "background: transparent; border: none;"
        )
        self._defense_icon = MarkerLabel(
            pixmap_path=MARKER_ICONS.get("Defense"),
            fallback="D",
            color="#B8E0B8",
        )
        self._defense_icon.setFixedSize(14, 14)
        self._defense_icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        defense_layout.addWidget(self._defense_count_label)
        defense_layout.addWidget(self._defense_icon)
        self.top_defense_container.hide()

        self.bottom_progress_container = QWidget(overlay_parent)
        self.bottom_progress_container.setStyleSheet(
            "background-color: rgba(0, 0, 0, 0.55); border-radius: 4px;"
        )
        self.bottom_progress_container.setAutoFillBackground(False)
        self.bottom_progress_container.setAttribute(Qt.WA_TransparentForMouseEvents)
        progress_layout = QHBoxLayout(self.bottom_progress_container)
        progress_layout.setContentsMargins(4, 1, 4, 1)
        progress_layout.setSpacing(2)
        progress_layout.setAlignment(Qt.AlignCenter)
        self._progress_count_label = QLabel("0")
        self._progress_count_label.setAlignment(Qt.AlignCenter)
        self._progress_count_label.setStyleSheet(
            "color: #FFD700; font-weight: bold; font-size: 11px; "
            "background: transparent; border: none;"
        )
        self._progress_icon = MarkerLabel(
            pixmap_path=MARKER_ICONS.get("Progress"),
            fallback="P",
            color="#2196F3",
        )
        self._progress_icon.setFixedSize(14, 14)
        self._progress_icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        progress_layout.addWidget(self._progress_count_label)
        progress_layout.addWidget(self._progress_icon)
        self.bottom_progress_container.hide()

        self.top_resource_container = QWidget(overlay_parent)
        self.top_resource_container.setStyleSheet(
            "background-color: rgba(0, 0, 0, 0.55); border-radius: 4px;"
        )
        self.top_resource_container.setAutoFillBackground(False)
        self.top_resource_container.setAttribute(Qt.WA_TransparentForMouseEvents)
        resource_layout = QHBoxLayout(self.top_resource_container)
        resource_layout.setContentsMargins(4, 1, 4, 1)
        resource_layout.setSpacing(2)
        resource_layout.setAlignment(Qt.AlignCenter)
        self._resource_count_label = QLabel("0")
        self._resource_count_label.setAlignment(Qt.AlignCenter)
        self._resource_count_label.setStyleSheet(
            "color: #FFD700; font-weight: bold; font-size: 11px; "
            "background: transparent; border: none;"
        )
        self._resource_icon = MarkerLabel(
            pixmap_path=MARKER_ICONS.get("Resource"),
            fallback="R",
            color="#FFD700",
        )
        self._resource_icon.setFixedSize(14, 14)
        self._resource_icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        resource_layout.addWidget(self._resource_count_label)
        resource_layout.addWidget(self._resource_icon)
        self.top_resource_container.hide()

        self._position_top_threat_badge()
        self._position_top_attack_badge()
        self._position_top_defense_badge()
        self._position_progress_badge()
        self._raise_marker_layers()

    def _position_top_threat_badge(self):
        if self.top_threat_container is None:
            return
        w = self._label_w
        badge_w = 44
        x = max(0, (w - badge_w) // 2)
        self.top_threat_container.setGeometry(x, 2, badge_w, 20)

    def _position_top_attack_badge(self):
        if self.top_attack_container is None:
            return
        w = self._label_w
        badge_w = 44
        x = max(0, (w - badge_w) // 2)
        self.top_attack_container.setGeometry(x, 2, badge_w, 20)

    def _position_top_defense_badge(self):
        if self.top_defense_container is None:
            return
        w = self._label_w
        badge_w = 44
        x = max(0, (w - badge_w) // 2)
        self.top_defense_container.setGeometry(x, 2, badge_w, 20)

    def _position_progress_badge(self):
        if self.bottom_progress_container is None:
            return
        w = self._label_w
        badge_w, badge_h = 44, 20
        margin = 2
        x = max(0, w - badge_w - margin)
        self.bottom_progress_container.setGeometry(x, margin, badge_w, badge_h)

    def _position_resource_badge(self):
        if self.top_resource_container is None:
            return
        badge_w, badge_h = 44, 20
        margin = 2
        self.top_resource_container.setGeometry(margin, margin, badge_w, badge_h)

    def _raise_marker_layers(self):
        if self.top_threat_container is not None:
            self.top_threat_container.raise_()
        if self.top_attack_container is not None:
            self.top_attack_container.raise_()
        if self.top_defense_container is not None:
            self.top_defense_container.raise_()
        if self.bottom_progress_container is not None:
            self.bottom_progress_container.raise_()
        if self.top_resource_container is not None:
            self.top_resource_container.raise_()
        self.top_right_container.raise_()
        self.bottom_left_container.raise_()

    def _update_threat_badge(self):
        if self.top_threat_container is None:
            return
        card_type = (self.current_card.type if self.current_card else "") or ""
        show = self._show_threat_badge and card_type in ("敌人", "地区")
        self.top_threat_container.setVisible(show)
        if show:
            self._threat_count_label.setText(str(self.threat_value()))
            self._position_top_threat_badge()
            self._raise_marker_layers()

    def _update_attack_marker_visibility(self):
        """攻击徽章已显示合计值时，隐藏右上角 Attack 标记，避免与徽章重复。"""
        card_type = (self.current_card.type if self.current_card else "") or ""
        hide_markers = self._show_attack_badge and card_type == "敌人"
        for marker in self.top_right_markers:
            if getattr(marker, "marker_type", "") == "Attack":
                marker.setVisible(not hide_markers)

    def _update_attack_badge(self):
        if self.top_attack_container is None:
            return
        card_type = (self.current_card.type if self.current_card else "") or ""
        show = self._show_attack_badge and card_type == "敌人"
        self.top_attack_container.setVisible(show)
        if show:
            self._attack_count_label.setText(str(self.attack_value()))
            self._position_top_attack_badge()
            self._raise_marker_layers()
        self._update_attack_marker_visibility()

    def _update_defense_badge(self):
        if self.top_defense_container is None:
            return
        card_type = (self.current_card.type if self.current_card else "") or ""
        show = self._show_defense_badge and card_type == "敌人"
        self.top_defense_container.setVisible(show)
        if show:
            self._defense_count_label.setText(str(self.defense_value()))
            self._position_top_defense_badge()
            self._raise_marker_layers()

    def _update_progress_badge(self):
        if self.bottom_progress_container is None:
            return
        card_type = (self.current_card.type if self.current_card else "") or ""
        count = self._progress_marker_count
        show = card_type == "地区" and count > 0
        self.bottom_progress_container.setVisible(show)
        if show:
            self._progress_count_label.setText(str(count))
            self._position_progress_badge()
            self._raise_marker_layers()

    def _update_resource_badge(self):
        if self.top_resource_container is None:
            return
        show = self._show_resource_badge and self._resource_count > 0
        self.top_resource_container.setVisible(show)
        if show:
            self._resource_count_label.setText(str(self._resource_count))
            self._position_resource_badge()
            self._raise_marker_layers()

    def placed_progress_count(self) -> int:
        return self._progress_marker_count

    def marker_state_key(self) -> str:
        if self.current_card and getattr(self.current_card, "id", ""):
            return str(self.current_card.id)
        series = self.series or DEFAULT_DECK_SERIES
        return f"{series}:{self.card_name}"

    def export_marker_state(self) -> dict:
        return {
            "top_right": [
                getattr(m, "marker_type", "")
                for m in self.top_right_markers
                if getattr(m, "marker_type", "")
            ],
            "bottom_left": ["Progress"] * self._progress_marker_count,
            "resource_count": self._resource_count,
        }

    def persist_marker_state(self):
        if self._suppress_marker_persist or not self._restore_markers:
            return
        key = self.marker_state_key()
        if key:
            _MARKER_STATE_CACHE[key] = self.export_marker_state()

    def _restore_marker_state_from_cache(self):
        key = self.marker_state_key()
        state = _MARKER_STATE_CACHE.get(key)
        if not state:
            return
        self._suppress_marker_persist = True
        try:
            while self.top_right_markers:
                marker = self.top_right_markers.pop()
                self.top_right_layout.removeWidget(marker)
                marker.deleteLater()
            while self.bottom_left_markers:
                marker = self.bottom_left_markers.pop()
                self.bottom_left_layout.removeWidget(marker)
                marker.deleteLater()
            self._progress_marker_count = 0
            for marker_type in state.get("top_right", []):
                self.add_top_right_marker(marker_type)
            progress_count = sum(
                1 for marker_type in state.get("bottom_left", [])
                if marker_type == "Progress"
            )
            if progress_count > 0:
                self._set_progress_marker_count(progress_count)
            if "resource_count" in state:
                self.set_resource_count(int(state.get("resource_count", 0)))
        finally:
            self._suppress_marker_persist = False
            self._recalc_stats()
            self.persist_marker_state()

    def set_show_threat_badge(self, show: bool):
        self._show_threat_badge = bool(show)
        self._update_threat_badge()

    def set_show_attack_badge(self, show: bool):
        self._show_attack_badge = bool(show)
        self._update_attack_badge()

    def set_show_defense_badge(self, show: bool):
        self._show_defense_badge = bool(show)
        self._update_defense_badge()

    def set_card_by_name(self, name: str = DEFAULT_CARD_NAME):
        """从魔戒遭遇.csv 加载并显示指定卡牌。"""
        self.persist_marker_state()
        self.clear_all_markers(persist=False)
        row = load_encounter_row_by_name(name, csv_path=self.csv_path, series=self.series)
        if row:
            card = Card.from_csv_row(row)
            self.current_card = card
            self.card_name = card.name
            self.card_path = card.image_path or None
            self._base_stats = {
                "attack": _parse_int(card.Attack),
                "defense": _parse_int(card.Defense),
                "health": _parse_int(card.Health),
                "threat": _parse_int(card.Threat or card.Threat_Level),
                "progress": _parse_int(row.get("探险点数")),
            }
        else:
            self.current_card = None
            self.card_name = name or "无卡牌"
            self.card_path = None
            self._base_stats = {
                "attack": 0,
                "defense": 0,
                "health": 0,
                "threat": 0,
                "progress": 0,
            }
        self._recalc_stats()
        self.load_card()

    def bind_game_card(self, card: Card):
        """绑定局内 Card 实例（含 copy 后缀的唯一 id），避免同名卡标记缓存冲突。"""
        self.clear_all_markers(persist=False)
        self.current_card = card
        self.card_name = card.name
        self.card_path = card.image_path or None
        self._base_stats = {
            "attack": _parse_int(card.Attack),
            "defense": _parse_int(card.Defense),
            "health": _parse_int(card.Health),
            "threat": _parse_int(card.Threat or card.Threat_Level),
            "progress": 0,
        }
        row = load_encounter_row_by_name(
            card.name, csv_path=self.csv_path, series=self.series
        )
        if row:
            self._base_stats["progress"] = _parse_int(row.get("探险点数"))
        self._recalc_stats()
        if self._show_name_label and hasattr(self, "name_label"):
            display_name = "魔影" if self._face_down else self.card_name
            self.name_label.setText(display_name)
        self.load_card()

    def set_face_down(self, face_down: bool = True):
        """面朝下显示（魔影卡牌分发时使用遭遇卡背）。"""
        if self._face_down == face_down:
            return
        self._face_down = face_down
        self.load_card()

    def load_card(self):
        path = self.card_path
        if self._face_down:
            if ENCOUNTER_CARD_BACK.is_file():
                path = str(ENCOUNTER_CARD_BACK)
        elif not path or not Path(path).is_file():
            if ENCOUNTER_CARD_BACK.is_file():
                path = str(ENCOUNTER_CARD_BACK)
            else:
                self.card_label.setText("❌\n无可用卡牌")
                if self._show_name_label:
                    self.name_label.setText(self.card_name)
                self.current_pixmap = None
                return

        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.card_label.setText("⚠️\n加载失败")
            if self._show_name_label:
                self.name_label.setText(f"{self.card_name} (错误)")
            self.current_pixmap = None
            return

        self.current_pixmap = pixmap
        self.update_card_display()
        if self._show_name_label:
            display_name = "魔影" if self._face_down else self.card_name
            self.name_label.setText(display_name)
        self._update_threat_badge()
        self._raise_marker_layers()
        if self._restore_markers:
            self._restore_marker_state_from_cache()

    def update_card_display(self):
        if not self.current_pixmap or self.current_pixmap.isNull():
            return
        scaled_pixmap = self.current_pixmap.scaled(
            self._label_w,
            self._label_h,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.card_label.setPixmap(scaled_pixmap)
        self._raise_marker_layers()

    def _recalc_stats(self):
        stats = dict(self._base_stats)
        for marker in self.top_right_markers:
            marker_type = getattr(marker, "marker_type", "")
            if marker_type == "Attack":
                stats["attack"] += 1
            elif marker_type == "Defense":
                stats["defense"] += 1
            elif marker_type == "Threat":
                stats["threat"] += 1
            elif marker_type == "Damage":
                stats["health"] = max(0, stats["health"] - 1)
        stats["progress"] = max(
            0, stats["progress"] - self._progress_marker_count
        )
        if self._passive_attack_per_resource > 0 and self._resource_count > 0:
            stats["attack"] += self._resource_count * self._passive_attack_per_resource
        stats["threat"] = max(0, stats["threat"] + self._passive_threat_bonus)
        self._current_stats = stats
        self._update_threat_badge()
        self._update_attack_badge()
        self._update_defense_badge()
        self._update_progress_badge()
        self._update_resource_badge()
        self.stats_changed.emit()

    def threat_value(self) -> int:
        return int(self._current_stats.get("threat", 0))

    def set_passive_threat_bonus(self, amount: int):
        amount = int(amount)
        if amount == self._passive_threat_bonus:
            return
        self._passive_threat_bonus = amount
        self._recalc_stats()

    def set_show_resource_badge(self, show: bool):
        self._show_resource_badge = bool(show)
        self._update_resource_badge()

    def set_passive_attack_per_resource(self, amount: int):
        amount = int(amount)
        if amount == self._passive_attack_per_resource:
            return
        self._passive_attack_per_resource = amount
        self._recalc_stats()

    def resource_count(self) -> int:
        return self._resource_count

    def set_resource_count(self, count: int):
        self._resource_count = max(0, int(count))
        self._recalc_stats()
        self.persist_marker_state()

    def add_resource(self, amount: int = 1):
        if amount <= 0:
            return
        self.set_resource_count(self._resource_count + amount)

    def attack_value(self) -> int:
        return int(self._current_stats.get("attack", 0))

    def defense_value(self) -> int:
        return int(self._current_stats.get("defense", 0))

    def get_card_info(self) -> dict:
        if not self.current_card:
            display_name = "魔影" if self._face_down else self.card_name
            return {"name": display_name, **self._current_stats}
        c = self.current_card
        s = self._current_stats
        display_name = "魔影" if self._face_down else c.name
        return {
            "name": display_name,
            "type": c.type,
            "threat": s["threat"],
            "attack": s["attack"],
            "defense": s["defense"],
            "health": s["health"],
            "progress": s["progress"],
            "base_threat": self._base_stats["threat"],
            "base_attack": self._base_stats["attack"],
            "base_defense": self._base_stats["defense"],
            "base_health": self._base_stats["health"],
            "base_progress": self._base_stats["progress"],
            "image_path": c.image_path,
        }

    def _debug_enabled(self) -> bool:
        win = self.window()
        if win is not None and hasattr(win, "debug_mode"):
            return bool(win.debug_mode)
        return self.debug_mode

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if getattr(self, "_drag_zoom", None) and self._drag_zoom.suppress_click():
                self._drag_zoom.clear_suppress_click()
                event.accept()
                super().mouseReleaseEvent(event)
                return
            if self.rect().contains(event.pos()):
                self.clicked.emit()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.RightButton and self.current_pixmap:
            self._ctx_menu_timer.stop()
            self._ctx_menu_pos = None
            self.show_zoomed_card()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def show_zoomed_card(self):
        if not self.current_pixmap:
            return
        self._ctx_menu_timer.stop()
        self._ctx_menu_pos = None
        if self.zoom_dialog:
            self.zoom_dialog.close()
        self.zoom_dialog = CardImageZoomDialog(self.current_pixmap, self)
        self.zoom_dialog.show()

    def contextMenuEvent(self, event):
        if self.zoom_dialog is not None and self.zoom_dialog.isVisible():
            event.accept()
            return
        if not self._debug_enabled():
            return
        self._ctx_menu_pos = event.globalPos()
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
        zoom_card = menu.addAction("放大显示")
        debug_action = menu.addAction("Debug")
        menu.addSeparator()
        menu.addSection("添加标记")
        add_damage = menu.addAction("添加 Damage 标记（生命值 -1）")
        add_attack = menu.addAction("添加 Attack 标记（攻击力 +1）")
        add_defense = menu.addAction("添加 Defense 标记（防御值 +1）")
        add_progress = menu.addAction("添加 Progress 标记（探险点数 -1）")
        add_threat = menu.addAction("添加 Threat 标记（威胁值 +1）")
        menu.addSection("移除标记")
        remove_attack = menu.addAction("移除 Attack 标记")
        remove_defense = menu.addAction("移除 Defense 标记")
        remove_damage = menu.addAction("移除 Damage 标记")
        remove_progress = menu.addAction("移除 Progress 标记")
        remove_threat = menu.addAction("移除 Threat 标记")
        menu.addSeparator()
        clear_markers = menu.addAction("清除所有标记")

        zoom_card.setEnabled(bool(self.current_pixmap))
        clear_markers.setEnabled(
            len(self.top_right_markers) > 0 or self._progress_marker_count > 0
        )

        action = menu.exec_(pos)

        if action == zoom_card:
            self.show_zoomed_card()
        elif action == debug_action:
            print("[DEBUG] 当前卡牌：", self.get_card_info())
            print(f"  Top-Right: {[m.marker_type for m in self.top_right_markers]}")
            print(f"  Progress: {self._progress_marker_count}")
        elif action == add_attack:
            self.add_top_right_marker("Attack")
        elif action == add_defense:
            self.add_top_right_marker("Defense")
        elif action == add_damage:
            self.add_top_right_marker("Damage", top=True)
        elif action == add_progress:
            self.add_progress_marker()
        elif action == add_threat:
            self.add_top_right_marker("Threat")
        elif action == remove_attack:
            self.remove_top_marker_by_type("Attack")
        elif action == remove_defense:
            self.remove_top_marker_by_type("Defense")
        elif action == remove_damage:
            self.remove_top_marker_by_type("Damage")
        elif action == remove_progress:
            self.remove_bottom_marker_by_type("Progress")
        elif action == remove_threat:
            self.remove_top_marker_by_type("Threat")
        elif action == clear_markers:
            self.clear_all_markers()

    def add_top_right_marker(self, marker_type="Attack", top=False):
        if len(self.top_right_markers) >= 20:
            return
        path = MARKER_ICONS.get(marker_type, "")
        fallback = marker_type[0] if marker_type else "●"
        marker = MarkerLabel(pixmap_path=path, fallback=fallback)
        marker.marker_type = marker_type

        index = len(self.top_right_markers)
        row, col = divmod(index, 2)
        self.top_right_layout.addWidget(marker, row, col)

        if top:
            self.top_right_markers.insert(0, marker)
        else:
            self.top_right_markers.append(marker)

        marker.show()
        marker.raise_()
        self._recalc_stats()
        self.persist_marker_state()

    def _set_progress_marker_count(self, count: int):
        self._progress_marker_count = max(0, min(12, int(count)))
        self._update_progress_badge()
        self._recalc_stats()
        self.persist_marker_state()

    def add_progress_marker(self):
        if self._progress_marker_count >= 12:
            return
        self._progress_marker_count += 1
        self._update_progress_badge()
        self._recalc_stats()
        self.persist_marker_state()

    def remove_top_marker_by_type(self, marker_type):
        for i, marker in enumerate(self.top_right_markers):
            if hasattr(marker, "marker_type") and marker.marker_type == marker_type:
                self.top_right_layout.removeWidget(marker)
                marker.deleteLater()
                del self.top_right_markers[i]
                self._recalc_stats()
                self.persist_marker_state()
                break

    def remove_bottom_marker_by_type(self, marker_type):
        if marker_type == "Progress":
            if self._progress_marker_count <= 0:
                return
            self._progress_marker_count -= 1
            self._update_progress_badge()
            self._recalc_stats()
            self.persist_marker_state()
            return
        for i, marker in enumerate(self.bottom_left_markers):
            if hasattr(marker, "marker_type") and marker.marker_type == marker_type:
                self.bottom_left_layout.removeWidget(marker)
                marker.deleteLater()
                del self.bottom_left_markers[i]
                self._recalc_stats()
                self.persist_marker_state()
                break

    def clear_all_markers(self, *, persist: bool = True):
        while self.top_right_markers:
            marker = self.top_right_markers.pop()
            self.top_right_layout.removeWidget(marker)
            marker.deleteLater()
        while self.bottom_left_markers:
            marker = self.bottom_left_markers.pop()
            self.bottom_left_layout.removeWidget(marker)
            marker.deleteLater()
        self._progress_marker_count = 0
        self._update_progress_badge()
        self._recalc_stats()
        if persist and not self._suppress_marker_persist:
            self.persist_marker_state()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self._label_w, self._label_h
        self.top_right_container.setGeometry(w - 35, 2, 32, 80)
        self.bottom_left_container.setGeometry(2, h - 24, w - 8, 18)
        self._position_top_threat_badge()
        self._position_top_attack_badge()
        self._position_top_defense_badge()
        self._position_progress_badge()
        self.update_card_display()


class CardWidgetTestWindow(QMainWindow):
    """CardWidget 测试窗口（默认：魔戒遭遇.csv / 森林蜘蛛）。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CardWidget 模块测试")
        self.setFixedSize(480, 380)
        self.debug_mode = True

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        hint = QLabel(
            "右键双击：放大卡面\n"
            "Debug 开启后右键：标记菜单\n"
            "Damage 生命-1 | Attack 攻+1 | Defense 防+1 | Progress 探险点数-1 | Threat 威胁+1\n"
            f"默认从 {ENCOUNTER_CSV.name} 加载「{DEFAULT_CARD_NAME}」"
        )
        hint.setStyleSheet("color: #444;")
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.addStretch()
        self.card_widget = CardWidget(card_name=DEFAULT_CARD_NAME)
        row.addWidget(self.card_widget)
        row.addStretch()
        layout.addLayout(row)

        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 13px; color: #004488;")
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        for text, slot in (
            ("放大显示", self.card_widget.show_zoomed_card),
            ("清除标记", self.card_widget.clear_all_markers),
            ("重载卡牌", lambda: self.card_widget.set_card_by_name(DEFAULT_CARD_NAME)),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)
        layout.addLayout(btn_row)

        self.card_widget.stats_changed.connect(self._update_status)
        self._update_status()

    def _update_status(self):
        info = self.card_widget.get_card_info()
        name = info.get("name", "?")
        card_type = info.get("type", "?")
        threat = info.get("threat", 0)
        attack = info.get("attack", 0)
        defense = info.get("defense", 0)
        health = info.get("health", 0)
        progress = info.get("progress", 0)
        path = info.get("image_path") or ""
        img = "有图" if path and Path(path).is_file() else "缺图"
        markers = len(self.card_widget.top_right_markers) + self.card_widget.placed_progress_count()
        self.status_label.setText(
            f"{ENCOUNTER_CSV.name}　|　{name} [{card_type}] "
            f"威胁 {threat}　攻 {attack}　防 {defense}　生命 {health}　探险点数 {progress}（{img}）　标记 {markers}"
        )


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = CardWidgetTestWindow()
    window.show()
    sys.exit(app.exec_())
