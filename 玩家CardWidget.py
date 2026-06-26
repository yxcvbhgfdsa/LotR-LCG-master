import copy
import sys
from pathlib import Path
from typing import Optional

from PyQt5.QtWidgets import (
    QLabel, QVBoxLayout, QHBoxLayout, QDialog, QMenu,
    QWidget, QGridLayout, QFrame, QApplication, QMainWindow,
    QPushButton,
)
from PyQt5.QtGui import QPixmap, QFont, QPainter, QPen, QColor, QFontMetrics
from PyQt5.QtCore import Qt, pyqtSignal, QTimer

from card_drag_zoom import CardDragZoomController

from 玩家卡抽取 import (
    PLAYER_CSV,
    DEFAULT_DECK_SERIES,
    CARD_BACK_PATH,
    Card,
    fit_player_card_size,
    lookup_card_row,
    get_player_name_index,
)

_PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CARD_NAME = "阿拉贡"
PLAYER_CARD_BACK = CARD_BACK_PATH

MARKER_ICONS = {
    "Attack": _PROJECT_ROOT / "cards" / "images" / "attack.png",
    "Defense": _PROJECT_ROOT / "cards" / "images" / "Defense.png",
    "Damage": _PROJECT_ROOT / "cards" / "images" / "tokens" / "damage.png",
    "Willpower": _PROJECT_ROOT / "cards" / "images" / "Willpower.jpg",
    "Resource": _PROJECT_ROOT / "cards" / "images" / "tokens" / "resource.png",
    "Health": _PROJECT_ROOT / "cards" / "images" / "health.png",
    "Exhaust": _PROJECT_ROOT / "cards" / "images" / "Exhaust.png",
    "Choose": _PROJECT_ROOT / "cards" / "images" / "Choose.png",
}

# 场上角色卡面原位叠加：派系属性圈图标
_ICON_DIR = _PROJECT_ROOT / "cards" / "images" / "icons"
SPHERE_ICONS = {
    "领导": _ICON_DIR / "leadership.png",
    "战术": _ICON_DIR / "tactics.png",
    "精神": _ICON_DIR / "spirit.png",
    "学识": _ICON_DIR / "lore.png",
}

# 叠加层相对坐标（中心点占卡面宽/高的比例）
STAT_OVERLAY_POSITIONS = {
    "willpower": (0.10, 0.22),
    "attack": (0.10, 0.33),
    "defense": (0.10, 0.44),
    "resource": (0.82, 0.09),
    "health": (0.13, 0.91),
    "damage": (0.50, 0.90),
    "sphere": (0.80, 0.90),
}
# 意志/攻/防叠加条背景色（卡面羊皮条近似色）
STAT_ICON_OVERLAY_BG = QColor(193, 160, 117)
STAT_OVERLAY_INCREASE = QColor(80, 220, 80)
STAT_OVERLAY_DECREASE = QColor(255, 70, 70)
STAT_OVERLAY_NEUTRAL = QColor(255, 255, 255)

EXHAUST_MARKER_SIZE = 49
CHOOSE_MARKER_SIZE = 34
CENTER_MARKER_GAP = 3

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


def load_player_row_by_name(
    name: str,
    csv_path: Path = PLAYER_CSV,
    series: Optional[str] = DEFAULT_DECK_SERIES,
) -> Optional[dict]:
    """从魔戒玩家牌.csv 按卡牌名称或备用卡牌名称查找一行。"""
    return lookup_card_row(
        get_player_name_index(csv_path),
        series or DEFAULT_DECK_SERIES,
        name,
    )


def load_player_card_by_name(
    name: str = DEFAULT_CARD_NAME,
    csv_path: Path = PLAYER_CSV,
    series: Optional[str] = DEFAULT_DECK_SERIES,
) -> Optional[Card]:
    """从魔戒玩家牌.csv 加载指定名称的玩家卡。"""
    row = load_player_row_by_name(name, csv_path=csv_path, series=series)
    if not row:
        return None
    return Card.from_csv_row(row)


def clear_marker_state_cache():
    """新局开始时清空已缓存的标记状态。"""
    _MARKER_STATE_CACHE.clear()


def export_marker_state_cache() -> dict:
    """导出当前标记状态缓存的深拷贝（用于环节存档）。"""
    return copy.deepcopy(_MARKER_STATE_CACHE)


def restore_marker_state_cache(data: dict) -> None:
    """用存档中的标记状态覆盖缓存（取消回档时调用）。"""
    _MARKER_STATE_CACHE.clear()
    if data:
        _MARKER_STATE_CACHE.update(copy.deepcopy(data))


def marker_state_key_for_card(card) -> str:
    """与 PlayerCardWidget.marker_state_key 一致的缓存键。"""
    card_id = getattr(card, "id", "") or ""
    if card_id:
        return str(card_id)
    series = (getattr(card, "series", "") or "").strip() or DEFAULT_DECK_SERIES
    name = (getattr(card, "name", "") or "").strip()
    return f"{series}:{name}"


def clear_marker_state_for_card(card):
    """卡牌进入弃牌堆等场外区域时清除其横置/伤害等标记缓存。"""
    key = marker_state_key_for_card(card)
    if key:
        _MARKER_STATE_CACHE.pop(key, None)


class MarkerLabel(QLabel):
    def __init__(
        self,
        pixmap_path=None,
        fallback="●",
        color="#4CAF50",
        parent=None,
        *,
        size: int = 16,
    ):
        super().__init__(parent)
        self._marker_size = size
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignCenter)
        path_str = str(pixmap_path) if pixmap_path else ""
        if path_str and Path(path_str).is_file():
            pixmap = QPixmap(path_str).scaled(
                size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.setPixmap(pixmap)
            self.setStyleSheet("background: transparent;")
        else:
            self.setText(fallback)
            radius = max(size // 2, 4)
            font_size = max(size // 2, 8)
            self.setStyleSheet(
                f"background-color: {color}; border: 1px solid #333;"
                f" border-radius: {radius}px;"
            )
            self.setFont(QFont("Arial", font_size, QFont.Bold))


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


def _styled_context_menu(parent=None) -> QMenu:
    """创建带浅色背景的右键菜单，避免透明控件下菜单发黑。"""
    menu = QMenu(parent)
    menu.setStyleSheet("""
        QMenu {
            background-color: #ffffff;
            color: #222222;
            border: 1px solid #aaaaaa;
            padding: 4px 0;
        }
        QMenu::item {
            padding: 4px 28px 4px 16px;
        }
        QMenu::item:selected {
            background-color: #0078d7;
            color: #ffffff;
        }
        QMenu::separator {
            height: 1px;
            background: #cccccc;
            margin: 4px 8px;
        }
    """)
    return menu


def _stat_overlay_fill_color(value: int, printed: int) -> QColor:
    if int(value) > int(printed):
        return STAT_OVERLAY_INCREASE
    if int(value) < int(printed):
        return STAT_OVERLAY_DECREASE
    return STAT_OVERLAY_NEUTRAL


def _paint_centered_outlined_text(
    painter: QPainter,
    rect,
    text: str,
    font_size: int,
    *,
    fill: QColor = None,
    outline: QColor = None,
):
    """居中绘制带黑色描边的粗体数字，提高卡面叠加上可读性。"""
    if not text:
        return
    if fill is None:
        fill = QColor(255, 255, 255)
    if outline is None:
        outline = QColor(0, 0, 0)
    font = QFont("Arial", font_size, QFont.Bold)
    painter.setFont(font)
    fm = QFontMetrics(font)
    x = rect.x() + (rect.width() - fm.horizontalAdvance(text)) // 2
    y = rect.y() + (rect.height() + fm.ascent() - fm.descent()) // 2
    painter.setPen(outline)
    for dx, dy in (
        (-2, 0), (2, 0), (0, -2), (0, 2),
        (-1, -1), (1, -1), (-1, 1), (1, 1),
    ):
        painter.drawText(x + dx, y + dy, text)
    painter.setPen(fill)
    painter.drawText(x, y, text)


class StatOverlayLabel(QLabel):
    """场上角色卡面原位数值叠加：半透明圆底 + 描边白字。"""

    def __init__(self, parent=None, *, diameter: int = 22):
        super().__init__(parent)
        self._diameter = max(1, int(diameter))
        self._font_size = max(11, int(self._diameter * 0.62))
        self.setAlignment(Qt.AlignCenter)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedSize(self._diameter, self._diameter)
        self.setStyleSheet("background: transparent; border: none;")
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        radius = self._diameter // 2
        painter.setBrush(QColor(0, 0, 0, 210))
        painter.setPen(QPen(QColor(255, 255, 255, 180), 1))
        painter.drawRoundedRect(0, 0, self._diameter, self._diameter, radius, radius)
        _paint_centered_outlined_text(
            painter, self.rect(), self.text(), self._font_size
        )


class OutlinedTextLabel(QLabel):
    """透明底描边数字，用于叠在图标/token 上。"""

    def __init__(self, parent=None, *, font_size: int = 12):
        super().__init__(parent)
        self._font_size = max(1, int(font_size))
        self._fill_color = QColor(255, 255, 255)
        self.setAlignment(Qt.AlignCenter)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setStyleSheet("background: transparent; border: none;")

    def set_fill_color(self, color: QColor):
        self._fill_color = color

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        _paint_centered_outlined_text(
            painter,
            self.rect(),
            self.text(),
            self._font_size,
            fill=self._fill_color,
        )


class TokenStatOverlayLabel(QWidget):
    """代币图 + 居中描边数字（资源池 / 伤害等）。"""

    def __init__(self, parent=None, *, size: int = 28, marker_key: str = "Resource"):
        super().__init__(parent)
        self._size = max(1, int(size))
        self.setFixedSize(self._size, self._size)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setStyleSheet("background: transparent; border: none;")
        self._icon = QLabel(self)
        self._icon.setGeometry(0, 0, self._size, self._size)
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._icon.setStyleSheet("background: transparent; border: none;")
        icon_path = MARKER_ICONS.get(marker_key)
        if icon_path and Path(icon_path).is_file():
            pixmap = QPixmap(str(icon_path)).scaled(
                self._size,
                self._size,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self._icon.setPixmap(pixmap)
        font_size = max(13, int(self._size * 0.50))
        self._count = OutlinedTextLabel(self, font_size=font_size)
        self._count.setGeometry(0, 0, self._size, self._size)
        self.hide()

    def set_visible_count(self, value: int, visible: bool, *, printed: int | None = None):
        if visible:
            self._count.setText(str(int(value)))
            if printed is not None:
                self._count.set_fill_color(
                    _stat_overlay_fill_color(int(value), int(printed))
                )
            self._count.update()
            self.show()
        else:
            self.hide()


ResourceStatOverlayLabel = TokenStatOverlayLabel


class IconStatOverlayLabel(QWidget):
    """左侧属性条：羊皮色底 + 缩小描边数字 + 图标（意志/攻/防）。"""

    _PAD_H = 3
    _PAD_V = 1

    def __init__(self, parent=None, *, height: int = 14, marker_key: str = "Willpower"):
        super().__init__(parent)
        self._height = max(12, int(height))
        icon_size = self._height - self._PAD_V * 2
        font_size = max(8, int(icon_size * 0.72))
        self.setFixedHeight(self._height)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setStyleSheet("background: transparent; border: none;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(self._PAD_H, self._PAD_V, self._PAD_H, self._PAD_V)
        layout.setSpacing(1)
        layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self._count = OutlinedTextLabel(self, font_size=font_size)
        self._count.setFixedHeight(icon_size)

        self._icon = QLabel(self)
        self._icon.setFixedSize(icon_size, icon_size)
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._icon.setStyleSheet("background: transparent; border: none;")
        icon_path = MARKER_ICONS.get(marker_key)
        if icon_path and Path(icon_path).is_file():
            pixmap = QPixmap(str(icon_path)).scaled(
                icon_size,
                icon_size,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self._icon.setPixmap(pixmap)

        layout.addWidget(self._count)
        layout.addWidget(self._icon)
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(STAT_ICON_OVERLAY_BG)
        painter.setPen(Qt.NoPen)
        radius = max(2, self._height // 4)
        painter.drawRoundedRect(0, 0, self.width(), self.height(), radius, radius)
        super().paintEvent(event)

    def set_visible_count(self, value: int, visible: bool, *, printed: int = 0):
        if visible:
            text = str(int(value))
            self._count.set_fill_color(
                _stat_overlay_fill_color(int(value), int(printed))
            )
            self._count.setText(text)
            self._count.update()
            icon_w = self._icon.width()
            digit_w = max(9, int((self._height - self._PAD_V * 2) * 0.62) * len(text))
            self._count.setFixedWidth(digit_w)
            inner_w = digit_w + icon_w + 1
            self.setFixedWidth(inner_w + self._PAD_H * 2)
            self.update()
            self.show()
        else:
            self.hide()


# 兼容旧名
WillpowerStatOverlayLabel = IconStatOverlayLabel


class CardWidget(QWidget):
    stats_changed = pyqtSignal()
    exhaust_changed = pyqtSignal(bool)
    clicked = pyqtSignal()
    play_requested = pyqtSignal()
    selection_changed = pyqtSignal(bool)

    def __init__(
        self,
        card_name: str = DEFAULT_CARD_NAME,
        csv_path: Path = PLAYER_CSV,
        series: Optional[str] = DEFAULT_DECK_SERIES,
        max_height: int = 182,
        show_resource_pool: bool = False,
        show_willpower_badge: bool = False,
        show_name_label: bool = False,
        show_field_stat_overlay: bool = False,
        restore_markers: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self._restore_markers = restore_markers
        self._show_name_label = show_name_label
        self._show_resource_pool = show_resource_pool
        self._show_willpower_badge = show_willpower_badge
        self._show_field_stat_overlay = show_field_stat_overlay
        self._show_defense_badge = False
        self._face_down = False
        self._show_attack_badge = False
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
        self.exhaust_marker = None
        self.action_used_marker = None
        self._passive_attack_per_damage = 0
        self._passive_attack_bonus = 0
        self._passive_willpower_bonus = 0
        self._passive_defense_bonus = 0
        self._phase_willpower_penalty = 0
        self._passive_health_bonus = 0
        self._resource_count = 0
        self.is_selected = False
        self._owner_border_color: str | None = None
        self._suppress_marker_persist = False
        self._label_w, self._label_h = fit_player_card_size(max_height)
        self._base_stats = {
            "willpower": 0,
            "attack": 0,
            "defense": 0,
            "health": 0,
            "threat": 0,
            "cost": 0,
        }
        self._current_stats = dict(self._base_stats)
        self._printed_stats = dict(self._base_stats)

        self.init_ui()
        self._drag_zoom = CardDragZoomController(self, self.show_zoomed_card)
        self._drag_zoom.install(self.card_frame, self.card_label)
        self.set_card_by_name(card_name)

    def init_ui(self):
        name_extra = 18 if self._show_name_label else 0
        self.setFixedSize(self._label_w + 10, self._label_h + 10 + name_extra)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
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
        self._update_selection_style()

    def _update_selection_style(self):
        border = self._owner_border_color
        if self.is_selected:
            self.setStyleSheet(
                "background-color: rgba(0, 120, 212, 0.15); "
                "border: 2px solid #0078d4; border-radius: 6px;"
            )
        elif border:
            self.setStyleSheet(
                f"background: transparent; border: 3px solid {border}; "
                "border-radius: 6px;"
            )
        else:
            self.setStyleSheet("background: transparent; border: none;")

    def set_owner_border(self, color: str | None):
        """一控多：标记卡牌所属玩家；None 为单人模式无边框。"""
        self._owner_border_color = color
        self._update_selection_style()

    def set_selected(self, selected: bool):
        if self.is_selected == selected:
            return
        self.is_selected = selected
        self._update_selection_style()
        self.selection_changed.emit(self.is_selected)

    def toggle_selected(self):
        self.set_selected(not self.is_selected)

    def setup_marker_overlays(self):
        w, h = self._label_w, self._label_h
        # 标记层挂在 card_frame 上，避免作为 QLabel 子控件导致右键重绘发黑
        overlay_parent = self.card_frame

        # Attack / Defense / Willpower / Damage 标记叠放在右上
        self.top_right_container = QWidget(overlay_parent)
        self.top_right_container.setGeometry(w - 35, 2, 32, 80)
        self.top_right_container.setStyleSheet("background: transparent; border: none;")
        self.top_right_container.setAutoFillBackground(False)
        self.top_right_layout = QGridLayout(self.top_right_container)
        self.top_right_layout.setContentsMargins(0, 0, 0, 0)
        self.top_right_layout.setSpacing(1)

        self.top_resource_container = None
        self._resource_count_label = None
        if self._show_resource_pool:
            badge_w = 44
            self.top_resource_container = QWidget(overlay_parent)
            self.top_resource_container.setStyleSheet(
                "background-color: rgba(0, 0, 0, 0.55); border-radius: 4px;"
            )
            self.top_resource_container.setAutoFillBackground(False)
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

        self.top_willpower_container = None
        self._willpower_count_label = None
        if self._show_willpower_badge:
            self.top_willpower_container = QWidget(overlay_parent)
            self.top_willpower_container.setStyleSheet(
                "background-color: rgba(0, 0, 0, 0.55); border-radius: 4px;"
            )
            self.top_willpower_container.setAutoFillBackground(False)
            willpower_layout = QHBoxLayout(self.top_willpower_container)
            willpower_layout.setContentsMargins(4, 1, 4, 1)
            willpower_layout.setSpacing(2)
            willpower_layout.setAlignment(Qt.AlignCenter)
            self._willpower_count_label = QLabel("0")
            self._willpower_count_label.setAlignment(Qt.AlignCenter)
            self._willpower_count_label.setStyleSheet(
                "color: #9ADCF9; font-weight: bold; font-size: 11px; "
                "background: transparent; border: none;"
            )
            self._willpower_icon = MarkerLabel(
                pixmap_path=MARKER_ICONS.get("Willpower"),
                fallback="W",
                color="#9ADCF9",
            )
            self._willpower_icon.setFixedSize(14, 14)
            self._willpower_icon.setAttribute(Qt.WA_TransparentForMouseEvents)
            willpower_layout.addWidget(self._willpower_count_label)
            willpower_layout.addWidget(self._willpower_icon)

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

        self.bottom_left_container = QWidget(overlay_parent)
        self.bottom_left_container.setGeometry(2, h - 24, w - 8, 18)
        self.bottom_left_container.setStyleSheet("background: transparent; border: none;")
        self.bottom_left_container.setAutoFillBackground(False)
        self.bottom_left_layout = QHBoxLayout(self.bottom_left_container)
        self.bottom_left_layout.setContentsMargins(0, 0, 0, 0)
        self.bottom_left_layout.setSpacing(2)
        self.bottom_left_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.center_marker_container = QWidget(overlay_parent)
        cx = (w - 50) // 2
        cy = (h - 50) // 2
        self.center_marker_container.setGeometry(cx, cy, 50, 50)
        self.center_marker_container.setStyleSheet("background: transparent; border: none;")
        self.center_marker_container.setAutoFillBackground(False)
        self.center_marker_layout = QVBoxLayout(self.center_marker_container)
        self.center_marker_layout.setContentsMargins(0, 0, 0, 0)
        self.center_marker_layout.setAlignment(Qt.AlignCenter)
        self._setup_stat_overlays(overlay_parent)
        self._position_top_badges()
        self._raise_marker_layers()
        if self._show_field_stat_overlay:
            self._hide_legacy_badges_for_overlay()

    def _setup_stat_overlays(self, overlay_parent):
        """场上角色：在卡面印刷位置叠加数值与属性圈图标。"""
        self._ov_icon_stats = {}
        self._ov_resource = None
        self._ov_damage = None
        self._ov_health = None
        self._ov_sphere = None
        self._ov_sphere_path = None
        if not self._show_field_stat_overlay:
            return
        stat_height = max(13, int(self._label_w * 0.15))
        for key, marker in (
            ("willpower", "Willpower"),
            ("attack", "Attack"),
            ("defense", "Defense"),
        ):
            self._ov_icon_stats[key] = IconStatOverlayLabel(
                overlay_parent, height=stat_height, marker_key=marker
            )
        token_size = max(30, int(self._label_w * 0.36))
        self._ov_resource = TokenStatOverlayLabel(
            overlay_parent, size=token_size, marker_key="Resource"
        )
        self._ov_damage = TokenStatOverlayLabel(
            overlay_parent, size=token_size, marker_key="Damage"
        )
        self._ov_health = TokenStatOverlayLabel(
            overlay_parent, size=token_size, marker_key="Health"
        )
        sphere_size = max(16, int(self._label_w * 0.24))
        self._ov_sphere = QLabel(overlay_parent)
        self._ov_sphere.setFixedSize(sphere_size, sphere_size)
        self._ov_sphere.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._ov_sphere.setStyleSheet("background: transparent; border: none;")
        self._ov_sphere.setAlignment(Qt.AlignCenter)
        self._ov_sphere.hide()

    def _hide_legacy_badges_for_overlay(self):
        """overlay 模式：隐藏顶部徽章与右上/左下标记图标，仅保留计数逻辑。"""
        for container in (
            self.top_resource_container,
            self.top_willpower_container,
            self.top_attack_container,
            self.top_defense_container,
            self.top_right_container,
            self.bottom_left_container,
        ):
            if container is not None:
                container.hide()

    def _position_top_badges(self):
        """顶部徽章横排：资源 + 意志 + 攻击 + 防御（居中）。"""
        w = self._label_w
        badges = []
        if self.top_resource_container is not None:
            badges.append(self.top_resource_container)
        if self.top_willpower_container is not None:
            badges.append(self.top_willpower_container)
        if self.top_attack_container is not None:
            badges.append(self.top_attack_container)
        if self.top_defense_container is not None:
            badges.append(self.top_defense_container)
        if not badges:
            return
        badge_w = 44
        gap = 4
        total_w = len(badges) * badge_w + max(0, len(badges) - 1) * gap
        x = max(0, (w - total_w) // 2)
        for badge in badges:
            badge.setGeometry(x, 2, badge_w, 20)
            x += badge_w + gap

    def _raise_marker_layers(self):
        if self.top_resource_container is not None:
            self.top_resource_container.raise_()
        if self.top_willpower_container is not None:
            self.top_willpower_container.raise_()
        if self.top_attack_container is not None:
            self.top_attack_container.raise_()
        if self.top_defense_container is not None:
            self.top_defense_container.raise_()
        self.top_right_container.raise_()
        self.bottom_left_container.raise_()
        self.center_marker_container.raise_()
        self._raise_stat_overlays()

    def _raise_stat_overlays(self):
        if not self._show_field_stat_overlay:
            return
        for ov in self._ov_icon_stats.values():
            ov.raise_()
        if self._ov_resource is not None:
            self._ov_resource.raise_()
        if self._ov_damage is not None:
            self._ov_damage.raise_()
        if self._ov_health is not None:
            self._ov_health.raise_()
        if self._ov_sphere is not None:
            self._ov_sphere.raise_()

    def _damage_marker_count(self) -> int:
        return sum(
            1
            for marker in self.top_right_markers
            if getattr(marker, "marker_type", "") == "Damage"
        )

    def _max_health_value(self) -> int:
        return int(self._printed_stats.get("health", 0)) + self._passive_health_bonus

    def _sphere_icon_path(self) -> Optional[Path]:
        sphere = ((self.current_card.Sphere if self.current_card else "") or "").strip()
        return SPHERE_ICONS.get(sphere)

    def _position_stat_overlays(self):
        if not self._show_field_stat_overlay:
            return
        w, h = self._label_w, self._label_h
        for key, ov in self._ov_icon_stats.items():
            rx, ry = STAT_OVERLAY_POSITIONS[key]
            ww, wh = ov.width(), ov.height()
            ov.move(int(w * rx - ww / 2), int(h * ry - wh / 2))
        if self._ov_resource is not None:
            rx, ry = STAT_OVERLAY_POSITIONS["resource"]
            size = self._ov_resource.width()
            self._ov_resource.move(int(w * rx - size / 2), int(h * ry - size / 2))
        if self._ov_damage is not None:
            rx, ry = STAT_OVERLAY_POSITIONS["damage"]
            size = self._ov_damage.width()
            self._ov_damage.move(int(w * rx - size / 2), int(h * ry - size / 2))
        if self._ov_health is not None:
            rx, ry = STAT_OVERLAY_POSITIONS["health"]
            size = self._ov_health.width()
            self._ov_health.move(int(w * rx - size / 2), int(h * ry - size / 2))
        if self._ov_sphere is not None:
            rx, ry = STAT_OVERLAY_POSITIONS["sphere"]
            size = self._ov_sphere.width()
            self._ov_sphere.move(int(w * rx - size / 2), int(h * ry - size / 2))

    def _update_sphere_overlay(self):
        if self._ov_sphere is None:
            return
        path = self._sphere_icon_path()
        if path and Path(path).is_file():
            if self._ov_sphere_path != str(path):
                pixmap = QPixmap(str(path)).scaled(
                    self._ov_sphere.width(),
                    self._ov_sphere.height(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
                self._ov_sphere.setPixmap(pixmap)
                self._ov_sphere_path = str(path)
            self._ov_sphere.show()
        else:
            self._ov_sphere.hide()

    def _update_stat_overlays(self):
        if not self._show_field_stat_overlay:
            return
        printed = self._printed_stats
        cur = self._current_stats
        for key in ("willpower", "attack", "defense"):
            ov = self._ov_icon_stats.get(key)
            if ov is None:
                continue
            val = cur.get(key, 0)
            printed_val = printed.get(key, 0)
            ov.set_visible_count(val, True, printed=printed_val)
        if self._ov_resource is not None:
            self._ov_resource.set_visible_count(
                self._resource_count, self._show_resource_pool
            )
        max_hp = self._max_health_value()
        if self._ov_health is not None:
            self._ov_health.set_visible_count(
                max_hp, True, printed=printed.get("health", 0)
            )
        damage = self._damage_marker_count()
        if self._ov_damage is not None:
            self._ov_damage.set_visible_count(damage, damage > 0)
        self._update_sphere_overlay()
        self._position_stat_overlays()
        self._raise_stat_overlays()

    def willpower_value(self) -> int:
        return int(self._current_stats.get("willpower", 0))

    def raw_willpower_value(self) -> int:
        """意志力（含被动修正与标记，不含 max(0,…) 下限）。"""
        stats = dict(self._base_stats)
        stats["willpower"] += self._passive_willpower_bonus
        for marker in self.top_right_markers:
            if getattr(marker, "marker_type", "") == "Willpower":
                stats["willpower"] += 1
        return stats["willpower"] - self._phase_willpower_penalty

    def defense_value(self) -> int:
        return int(self._current_stats.get("defense", 0))

    def attack_value(self) -> int:
        return int(self._current_stats.get("attack", 0))

    def set_show_attack_badge(self, show: bool):
        self._show_attack_badge = bool(show)
        self._update_attack_badge()

    def _update_attack_badge(self):
        if self.top_attack_container is None:
            return
        if self._show_field_stat_overlay:
            self.top_attack_container.setVisible(False)
            return
        card_type = (self.current_card.type if self.current_card else "") or ""
        show = self._show_attack_badge and card_type in ("英雄", "盟友")
        self.top_attack_container.setVisible(show)
        if show:
            self._attack_count_label.setText(str(self.attack_value()))
            self._position_top_badges()
            self._raise_marker_layers()

    def set_show_defense_badge(self, show: bool):
        self._show_defense_badge = bool(show)
        self._update_defense_badge()

    def _update_defense_badge(self):
        if self.top_defense_container is None:
            return
        if self._show_field_stat_overlay:
            self.top_defense_container.setVisible(False)
            return
        card_type = (self.current_card.type if self.current_card else "") or ""
        show = self._show_defense_badge and card_type in ("英雄", "盟友")
        self.top_defense_container.setVisible(show)
        if show:
            self._defense_count_label.setText(str(self.defense_value()))
            self._position_top_badges()
            self._raise_marker_layers()

    def marker_state_key(self) -> str:
        if self.current_card and getattr(self.current_card, "id", ""):
            return str(self.current_card.id)
        series = self.series or DEFAULT_DECK_SERIES
        return f"{series}:{self.card_name}"

    def export_marker_state(self) -> dict:
        state = {
            "top_right": [
                getattr(m, "marker_type", "")
                for m in self.top_right_markers
                if getattr(m, "marker_type", "")
            ],
            "bottom_left": [
                getattr(m, "marker_type", "")
                for m in self.bottom_left_markers
                if getattr(m, "marker_type", "")
            ],
            "exhaust": self.is_exhausted(),
        }
        if self._show_resource_pool:
            state["resource_count"] = self._resource_count
        return state

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
            if self.exhaust_marker:
                self.center_marker_layout.removeWidget(self.exhaust_marker)
                self.exhaust_marker.deleteLater()
                self.exhaust_marker = None
            for marker_type in state.get("top_right", []):
                self.add_top_right_marker(marker_type)
            for marker_type in state.get("bottom_left", []):
                self.add_bottom_left_marker(marker_type)
            if state.get("exhaust"):
                self.set_exhausted(True)
            if self._show_resource_pool and "resource_count" in state:
                self.set_resource_count(int(state["resource_count"]))
        finally:
            self._suppress_marker_persist = False
            self._recalc_stats()
            self.persist_marker_state()

    def _update_willpower_badge(self):
        if self._willpower_count_label is not None:
            self._willpower_count_label.setText(str(self.willpower_value()))

    def resource_count(self) -> int:
        return self._resource_count

    def set_resource_count(self, count: int):
        self._resource_count = max(0, int(count))
        if self._resource_count_label is not None:
            self._resource_count_label.setText(str(self._resource_count))
        self._update_stat_overlays()
        self.stats_changed.emit()
        self.persist_marker_state()

    def set_passive_attack_per_damage(self, amount: int):
        """被动：每枚伤害标记额外 +攻击力（如吉姆利/金雳）。"""
        amount = max(0, int(amount))
        if amount == self._passive_attack_per_damage:
            return
        self._passive_attack_per_damage = amount
        self._recalc_stats()

    def set_passive_attack_bonus(self, amount: int):
        """被动：面板攻击力加成（如矮人斧 +1/+2）。"""
        amount = max(0, int(amount))
        if amount == self._passive_attack_bonus:
            return
        self._passive_attack_bonus = amount
        self._recalc_stats()

    def set_passive_willpower_bonus(self, amount: int):
        """被动：面板意志力加成（如凯勒布莉安的宝石 +2）。"""
        amount = max(0, int(amount))
        if amount == self._passive_willpower_bonus:
            return
        self._passive_willpower_bonus = amount
        self._recalc_stats()

    def set_passive_defense_bonus(self, amount: int):
        """被动：面板防御力加成（如迷雾山脉鹰群面朝下附属）。"""
        amount = max(0, int(amount))
        if amount == self._passive_defense_bonus:
            return
        self._passive_defense_bonus = amount
        self._recalc_stats()

    def set_phase_willpower_penalty(self, amount: int):
        """本阶段临时意志力惩罚（如乌苟立安特的子嗣 -1 意志）。"""
        amount = max(0, int(amount))
        if amount == self._phase_willpower_penalty:
            return
        self._phase_willpower_penalty = amount
        self._recalc_stats()

    def set_passive_health_bonus(self, amount: int):
        """被动：面板生命值加成（如王城板甲 +4）。"""
        amount = max(0, int(amount))
        if amount == self._passive_health_bonus:
            return
        self._passive_health_bonus = amount
        self._recalc_stats()

    def add_resources(self, amount: int = 1):
        if amount <= 0:
            return
        self.set_resource_count(self._resource_count + amount)

    def remove_resources(self, amount: int = 1):
        if amount <= 0:
            return
        self.set_resource_count(self._resource_count - amount)

    def set_card_by_name(self, name: str = DEFAULT_CARD_NAME):
        """从魔戒玩家牌.csv 加载并显示指定卡牌。"""
        self.persist_marker_state()
        self.clear_all_markers(persist=False)
        row = load_player_row_by_name(name, csv_path=self.csv_path, series=self.series)
        if row:
            card = Card.from_csv_row(row)
            self.current_card = card
            self.card_name = card.name
            self.card_path = card.image_path or None
            self._base_stats = {
                "willpower": _parse_int(card.Willpower),
                "attack": _parse_int(card.Attack),
                "defense": _parse_int(card.Defense),
                "health": _parse_int(card.Health),
                "threat": _parse_int(card.Threat),
                "cost": _parse_int(card.Cost),
            }
        else:
            self.current_card = None
            self.card_name = name or "无卡牌"
            self.card_path = None
            self._base_stats = {
                "willpower": 0,
                "attack": 0,
                "defense": 0,
                "health": 0,
                "threat": 0,
                "cost": 0,
            }
        self._printed_stats = dict(self._base_stats)
        self._recalc_stats()
        self.load_card()

    def bind_game_card(self, card: Card):
        """绑定局内 Card 实例（含 copy 后缀的唯一 id），避免同名卡标记缓存冲突。"""
        self.clear_all_markers(persist=False)
        self.current_card = card
        self.card_name = card.name
        if card.series:
            self.series = card.series
        self.card_path = card.image_path or None
        self._base_stats = {
            "willpower": _parse_int(card.Willpower),
            "attack": _parse_int(card.Attack),
            "defense": _parse_int(card.Defense),
            "health": _parse_int(card.Health),
            "threat": _parse_int(card.Threat),
            "cost": _parse_int(card.Cost),
        }
        self._printed_stats = dict(self._base_stats)
        self._recalc_stats()
        if self._show_name_label and hasattr(self, "name_label"):
            self.name_label.setText(self.card_name)
        self.load_card()

    def set_face_down(self, face_down: bool = True):
        """面朝下显示（如迷雾山脉鹰群上的附属巨鹰）。"""
        if self._face_down == face_down:
            return
        self._face_down = face_down
        if face_down:
            self.clear_all_markers(persist=False)
            if self.current_card is not None:
                clear_marker_state_for_card(self.current_card)
        self.load_card()

    def load_card(self):
        path = self.card_path
        if self._face_down:
            if PLAYER_CARD_BACK.is_file():
                path = str(PLAYER_CARD_BACK)
        elif not path or not Path(path).is_file():
            if PLAYER_CARD_BACK.is_file():
                path = str(PLAYER_CARD_BACK)
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
            self.name_label.setText(self.card_name)
        self._raise_marker_layers()
        if self._restore_markers and not self._face_down:
            self._restore_marker_state_from_cache()

    def update_card_display(self):
        if not self.current_pixmap or self.current_pixmap.isNull():
            return
        scaled_pixmap = self.current_pixmap.scaled(
            self._label_w,
            self._label_h,
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        self.card_label.setPixmap(scaled_pixmap)

    def _recalc_stats(self):
        stats = dict(self._base_stats)
        stats["attack"] += self._passive_attack_bonus
        stats["willpower"] += self._passive_willpower_bonus
        stats["defense"] += self._passive_defense_bonus
        stats["health"] += self._passive_health_bonus
        stats["willpower"] = max(0, stats["willpower"] - self._phase_willpower_penalty)
        for marker in self.top_right_markers:
            marker_type = getattr(marker, "marker_type", "")
            if marker_type == "Attack":
                stats["attack"] += 1
            elif marker_type == "Defense":
                stats["defense"] += 1
            elif marker_type == "Willpower":
                stats["willpower"] += 1
            elif marker_type == "Damage":
                stats["health"] = max(0, stats["health"] - 1)
        if self._passive_attack_per_damage > 0:
            damage_count = sum(
                1
                for marker in self.top_right_markers
                if getattr(marker, "marker_type", "") == "Damage"
            )
            stats["attack"] += damage_count * self._passive_attack_per_damage
        self._current_stats = stats
        self._update_willpower_badge()
        self._update_attack_badge()
        self._update_defense_badge()
        self._update_stat_overlays()
        self.stats_changed.emit()

    def get_card_info(self) -> dict:
        if not self.current_card:
            return {"name": self.card_name, **self._current_stats}
        c = self.current_card
        s = self._current_stats
        return {
            "name": c.name,
            "type": c.type,
            "sphere": c.Sphere,
            "cost": s["cost"],
            "threat": s["threat"],
            "willpower": s["willpower"],
            "attack": s["attack"],
            "defense": s["defense"],
            "health": s["health"],
            "base_willpower": self._base_stats["willpower"],
            "base_attack": self._base_stats["attack"],
            "base_defense": self._base_stats["defense"],
            "base_health": self._base_stats["health"],
            "passive_health_bonus": self._passive_health_bonus,
            "base_threat": self._base_stats["threat"],
            "base_cost": self._base_stats["cost"],
            "image_path": c.image_path,
            "exhausted": self.exhaust_marker is not None,
        }

    def _debug_enabled(self) -> bool:
        win = self.window()
        if win is not None and hasattr(win, "debug_mode"):
            return bool(win.debug_mode)
        return self.debug_mode

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.play_requested.emit()
            event.accept()
            return
        if event.button() == Qt.RightButton and self.current_pixmap:
            self._ctx_menu_timer.stop()
            self._ctx_menu_pos = None
            self.show_zoomed_card()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

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
        menu = _styled_context_menu(self.window())
        zoom_card = menu.addAction("放大显示")
        debug_action = menu.addAction("Debug")
        menu.addSeparator()
        menu.addSection("添加标记")
        add_damage = menu.addAction("添加 Damage 标记（生命值 -1）")
        add_attack = menu.addAction("添加 Attack 标记（攻击力 +1）")
        add_defense = menu.addAction("添加 Defense 标记（防御值 +1）")
        add_willpower = menu.addAction("添加 Willpower 标记（意志力 +1）")
        add_resource = menu.addAction("添加 Resource 标记")
        add_exhaust = menu.addAction("切换 Exhaust 标记")
        menu.addSection("移除标记")
        remove_attack = menu.addAction("移除 Attack 标记")
        remove_defense = menu.addAction("移除 Defense 标记")
        remove_willpower = menu.addAction("移除 Willpower 标记")
        remove_damage = menu.addAction("移除 Damage 标记")
        remove_resource = menu.addAction("移除 Resource 标记")
        menu.addSeparator()
        clear_markers = menu.addAction("清除所有标记")

        zoom_card.setEnabled(bool(self.current_pixmap))
        clear_markers.setEnabled(
            len(self.top_right_markers) > 0
            or len(self.bottom_left_markers) > 0
            or self.exhaust_marker is not None
        )

        action = menu.exec_(pos)
        self.update_card_display()
        self._raise_marker_layers()

        if action == zoom_card:
            self.show_zoomed_card()
        elif action == debug_action:
            print("[DEBUG] 当前卡牌：", self.get_card_info())
            print(f"  Top-Right: {[m.marker_type for m in self.top_right_markers]}")
            print(f"  Bottom-Left: {[m.marker_type for m in self.bottom_left_markers]}")
            print(f"  Exhaust: {self.exhaust_marker is not None}")
            if self._show_resource_pool:
                print(f"  Resource pool: {self.resource_count()}")
        elif action == add_attack:
            self.add_top_right_marker("Attack")
        elif action == add_defense:
            self.add_top_right_marker("Defense")
        elif action == add_willpower:
            self.add_top_right_marker("Willpower")
        elif action == add_damage:
            self.add_top_right_marker("Damage", top=True)
        elif action == add_resource:
            if self._show_resource_pool:
                self.add_resources(1)
            else:
                self.add_bottom_left_marker("Resource")
        elif action == add_exhaust:
            self.toggle_exhaust_marker()
        elif action == remove_attack:
            self.remove_top_marker_by_type("Attack")
        elif action == remove_defense:
            self.remove_top_marker_by_type("Defense")
        elif action == remove_willpower:
            self.remove_top_marker_by_type("Willpower")
        elif action == remove_damage:
            self.remove_top_marker_by_type("Damage")
        elif action == remove_resource:
            if self._show_resource_pool:
                self.remove_resources(1)
            else:
                self.remove_bottom_marker_by_type("Resource")
        elif action == clear_markers:
            self.clear_all_markers()

    def toggle_exhaust_marker(self):
        if self.exhaust_marker:
            self.exhaust_marker.deleteLater()
            self.exhaust_marker = None
        else:
            path = MARKER_ICONS.get("Exhaust")
            marker = MarkerLabel(
                pixmap_path=path,
                fallback="E",
                color="#AAAAAA",
                size=EXHAUST_MARKER_SIZE,
            )
            marker.setParent(self.center_marker_container)
            marker.setToolTip("Exhaust")
            marker.setAttribute(Qt.WA_TransparentForMouseEvents)
            marker.marker_type = "Exhaust"
            self.exhaust_marker = marker
            marker.show()
        self._relayout_center_markers()
        self.stats_changed.emit()
        self.exhaust_changed.emit(self.is_exhausted())
        self.persist_marker_state()

    def is_exhausted(self) -> bool:
        return self.exhaust_marker is not None

    def set_exhausted(self, exhausted: bool):
        if exhausted != self.is_exhausted():
            self.toggle_exhaust_marker()

    def set_action_used_marker(self, visible: bool):
        """本回合限次行动已触发（如伊奥温）：横置标记下方显示 Choose 标记。"""
        if visible == (self.action_used_marker is not None):
            return
        if not visible:
            if self.action_used_marker is not None:
                self.action_used_marker.deleteLater()
                self.action_used_marker = None
        else:
            path = MARKER_ICONS.get("Choose", "")
            marker = MarkerLabel(
                pixmap_path=path,
                fallback="用",
                color="#78909C",
                size=CHOOSE_MARKER_SIZE,
            )
            marker.setParent(self.center_marker_container)
            marker.setAttribute(Qt.WA_TransparentForMouseEvents)
            marker.marker_type = "Choose"
            marker.setToolTip("本回合已触发")
            self.action_used_marker = marker
            marker.show()
        self._relayout_center_markers()
        self._raise_marker_layers()

    def _center_marker_stack_size(self) -> tuple[int, int]:
        width = max(
            EXHAUST_MARKER_SIZE if self.exhaust_marker else 0,
            CHOOSE_MARKER_SIZE if self.action_used_marker else 0,
        )
        if width == 0:
            width = EXHAUST_MARKER_SIZE
        height = 0
        if self.exhaust_marker is not None:
            height += EXHAUST_MARKER_SIZE
        if self.action_used_marker is not None:
            if height:
                height += CENTER_MARKER_GAP
            height += CHOOSE_MARKER_SIZE
        if height == 0:
            height = EXHAUST_MARKER_SIZE
        return width, height

    def _relayout_center_markers(self):
        """横置在上、Choose 在下，二者不重叠。"""
        stack_w, stack_h = self._center_marker_stack_size()
        card_w, card_h = self._label_w, self._label_h
        cx = (card_w - stack_w) // 2
        cy = (card_h - stack_h) // 2
        self.center_marker_container.setGeometry(cx, cy, stack_w, stack_h)
        y = 0
        if self.exhaust_marker is not None:
            x = (stack_w - EXHAUST_MARKER_SIZE) // 2
            self.exhaust_marker.setGeometry(
                x, y, EXHAUST_MARKER_SIZE, EXHAUST_MARKER_SIZE
            )
            y += EXHAUST_MARKER_SIZE + CENTER_MARKER_GAP
        if self.action_used_marker is not None:
            ms = CHOOSE_MARKER_SIZE
            x = (stack_w - ms) // 2
            if self.exhaust_marker is None:
                y = (stack_h - ms) // 2
            self.action_used_marker.setGeometry(x, y, ms, ms)
        if self.exhaust_marker is not None:
            self.exhaust_marker.raise_()
        if self.action_used_marker is not None:
            self.action_used_marker.show()

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
        self.top_right_container.raise_()
        self._recalc_stats()
        self.persist_marker_state()

    def add_bottom_left_marker(self, marker_type="Resource"):
        if len(self.bottom_left_markers) >= 12:
            return
        path = MARKER_ICONS.get(marker_type, "")
        fallback = marker_type[0] if marker_type else "R"
        marker = MarkerLabel(pixmap_path=path, fallback=fallback, color="#FFD700")
        marker.marker_type = marker_type
        self.bottom_left_layout.addWidget(marker)
        self.bottom_left_markers.append(marker)
        marker.show()
        marker.raise_()
        self._raise_marker_layers()
        self.stats_changed.emit()
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
        for i, marker in enumerate(self.bottom_left_markers):
            if hasattr(marker, "marker_type") and marker.marker_type == marker_type:
                self.bottom_left_layout.removeWidget(marker)
                marker.deleteLater()
                del self.bottom_left_markers[i]
                self.stats_changed.emit()
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
        if self.exhaust_marker:
            self.exhaust_marker.deleteLater()
            self.exhaust_marker = None
        if self.action_used_marker is not None:
            self.action_used_marker.deleteLater()
            self.action_used_marker = None
        self._relayout_center_markers()
        self._recalc_stats()
        if persist and not self._suppress_marker_persist:
            self.persist_marker_state()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self._label_w, self._label_h
        self._position_top_badges()
        self.top_right_container.setGeometry(w - 35, 2, 32, 80)
        self.bottom_left_container.setGeometry(2, h - 24, w - 8, 18)
        self._relayout_center_markers()
        self.update_card_display()
        self._position_stat_overlays()
        self._raise_marker_layers()


class PlayerCardWidgetTestWindow(QMainWindow):
    """玩家 CardWidget 测试窗口（默认：魔戒玩家牌.csv / 阿拉贡）。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("玩家 CardWidget 模块测试")
        self.setFixedSize(520, 520)
        self.debug_mode = True

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        hint = QLabel(
            "右键双击：放大卡面\n"
            "Debug 开启后右键：标记菜单\n"
            "Damage 生命-1 | Attack 攻+1 | Defense 防+1 | Willpower 意志+1 | Resource 资源 | Exhaust 横置\n"
            f"默认从 {PLAYER_CSV.name} 加载「{DEFAULT_CARD_NAME}」"
        )
        hint.setStyleSheet("color: #444;")
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.addStretch()
        self.card_widget = CardWidget(
            card_name=DEFAULT_CARD_NAME,
            max_height=260,
            show_resource_pool=True,
            show_field_stat_overlay=True,
        )
        self.card_widget.set_resource_count(2)
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
        sphere = info.get("sphere", "")
        cost = info.get("cost", 0)
        threat = info.get("threat", 0)
        willpower = info.get("willpower", 0)
        attack = info.get("attack", 0)
        defense = info.get("defense", 0)
        health = info.get("health", 0)
        path = info.get("image_path") or ""
        img = "有图" if path and Path(path).is_file() else "缺图"
        exhausted = "横置" if info.get("exhausted") else "就绪"
        markers = (
            len(self.card_widget.top_right_markers)
            + len(self.card_widget.bottom_left_markers)
            + (1 if self.card_widget.exhaust_marker else 0)
        )
        self.status_label.setText(
            f"{PLAYER_CSV.name}　|　{name} [{card_type}] {sphere} "
            f"费用 {cost}　威胁 {threat}　意志 {willpower}　攻 {attack}　防 {defense}　生命 {health} "
            f"（{img}，{exhausted}）　标记 {markers}"
        )


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = PlayerCardWidgetTestWindow()
    window.show()
    sys.exit(app.exec_())
