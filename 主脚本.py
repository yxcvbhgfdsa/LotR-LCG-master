import csv
import json
import os
import random
import re
import sys
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path

_PYQT5_QT_PLUGIN_DIR = (
    Path(sys.executable).resolve().parent.parent
    / "Lib"
    / "site-packages"
    / "PyQt5"
    / "Qt5"
    / "plugins"
)
if _PYQT5_QT_PLUGIN_DIR.is_dir():
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(_PYQT5_QT_PLUGIN_DIR))

from 场景 import (
    任务,
    SecondQuestDeckPanel,
    load_second_quest_scenes_from_o8d,
    resolve_scene_image,
)
from 遭遇抽取 import (
    CardDrawer,
    DEFAULT_DECK_SERIES,
    ENCOUNTER_CSV,
    Card as EncounterCard,
    fit_encounter_card_size,
    load_second_special_cards_from_o8d,
    load_encounter_cards_from_csv,
    _image_id_stem,
)
from 玩家卡抽取 import (
    CARD_NAME_ALIASES,
    CardDrawer as PlayerCardDrawer,
    Card as PlayerCard,
    DeckListDialog,
    build_player_name_index,
    fit_player_card_size,
    lookup_card_row_by_name_any_series,
    _read_player_csv_rows,
)
from 玩家CardWidget import (
    CardWidget as PlayerCardWidget,
    SPHERE_ICONS,
    TokenStatOverlayLabel,
    clear_marker_state_cache as clear_player_marker_cache,
    clear_marker_state_for_card,
    export_marker_state_cache as export_player_marker_cache,
    restore_marker_state_cache as restore_player_marker_cache,
    load_player_row_by_name,
)
from CardWidget import (
    CardWidget as EncounterCardWidget,
    CardImageZoomDialog,
    ENCOUNTER_CARD_BACK,
    MarkerLabel,
    load_encounter_card_by_name,
    load_encounter_row_by_name,
    clear_marker_state_cache as clear_encounter_marker_cache,
    clear_marker_state_for_card as clear_encounter_marker_state_for_card,
    export_marker_state_cache as export_encounter_marker_cache,
    restore_marker_state_cache as restore_encounter_marker_cache,
    set_encounter_marker_progress_for_card,
)
from card_drag_zoom import CardDragZoomController
from 旧版术语 import (
    card_text_contains,  # noqa  # type: ignore
    card_text_contains_all,  # noqa  # type: ignore
    card_text_contains_any,  # noqa  # type: ignore
    term_variants,
    text_contains,
    text_contains_all,  # noqa  # type: ignore
    text_contains_any,
)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFrame, QLabel, QPushButton, QScrollArea, QMessageBox,
    QDialog, QListWidget, QInputDialog,
    QListWidgetItem, QAbstractItemView, QSpinBox, QButtonGroup, QToolButton, QSizePolicy,  # noqa  # type: ignore
    QStackedWidget, QPlainTextEdit, QGridLayout,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QEvent, QEventLoop, QSize, QObject, QRect
from PyQt5.QtGui import QFont, QPixmap, QPainter, QColor, QPen, QIcon, QImageReader, QCursor


class _CardHoverPreviewController(QObject):
    """在主窗口外侧显示当前悬停的正面卡牌。"""

    MARGIN = 12
    MAX_WIDTH = 520
    MIN_VISIBLE_WIDTH = 160
    SCREEN_HEIGHT_RATIO = 0.90

    _ORIGINAL_PIXMAP_LABELS = frozenset({
        "LocationCardLabel",
        "_HeroPayImageLabel",
        "_CharacterPickClickLabel",
        "ClickableTaskLabel",
        "_DeckPreviewLabel",
        "EncounterCardLabel",
        "PlayerCardLabel",
    })
    _PLAIN_CARD_CONTEXTS = frozenset({
        "QuestDeckPreviewDialog",
    })
    _ZOOM_WINDOWS = frozenset({
        "CardImageZoomDialog",
        "ImageZoomDialog",
    })

    def __init__(self, main_window: QMainWindow):
        super().__init__(main_window)
        self._main_window = main_window
        self._source: QWidget | None = None
        self._source_pixmap: QPixmap | None = None
        self._hide_check_pending = False

        self._preview = QLabel(
            None,
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        )
        self._preview.setObjectName("cardHoverPreview")
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setFocusPolicy(Qt.NoFocus)
        self._preview.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._preview.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._preview.setStyleSheet(
            "QLabel#cardHoverPreview {"
            " background-color: #111; border: 2px solid #888;"
            " border-radius: 6px; padding: 0px;"
            "}"
        )

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    @staticmethod
    def _valid_pixmap(value) -> QPixmap | None:
        if callable(value):
            try:
                value = value()
            except (RuntimeError, TypeError):
                return None
        if isinstance(value, QPixmap) and not value.isNull():
            return value
        return None

    @staticmethod
    def _widget_chain(widget: QWidget) -> list[QWidget]:
        chain: list[QWidget] = []
        current: QWidget | None = widget
        while current is not None:
            chain.append(current)
            try:
                current = current.parentWidget()
            except RuntimeError:
                break
        return chain

    def _source_from_widget(self, widget) -> QWidget | None:
        if not isinstance(widget, QWidget) or widget is self._preview:
            return None
        chain = self._widget_chain(widget)
        if any(type(item).__name__ in self._ZOOM_WINDOWS for item in chain):
            return None

        # 特殊 QLabel/QToolButton 可在创建时登记原始卡图。
        for item in chain:
            if not bool(getattr(item, "_hover_card_face_up", True)):
                continue
            if self._valid_pixmap(getattr(item, "_hover_card_pixmap", None)):
                return item

        # 玩家/遭遇 CardWidget 的子标签、标记和边框都映射回卡牌根控件。
        for item in chain:
            if isinstance(item, (PlayerCardWidget, EncounterCardWidget)):
                if bool(getattr(item, "_face_down", False)):
                    return None
                if self._valid_pixmap(getattr(item, "current_pixmap", None)):
                    return item

        # 主任务的资源/伤害标记与卡图是兄弟控件，需要映射回 task_label。
        for item in chain:
            if type(item).__name__ != "任务":
                continue
            task_container = getattr(item, "task_container", None)
            task_label = getattr(item, "task_label", None)
            if not isinstance(task_container, QWidget) or not isinstance(
                task_label, QWidget
            ):
                continue
            try:
                over_card = widget is task_container or task_container.isAncestorOf(widget)
            except RuntimeError:
                over_card = False
            if over_card and self._valid_pixmap(
                getattr(task_label, "original_pixmap", None)
            ):
                return task_label

        for item in chain:
            if type(item).__name__ not in self._ORIGINAL_PIXMAP_LABELS:
                continue
            # 抽牌器没有公开卡牌时只显示牌背，不应触发悬停预览。
            drawer = next(
                (
                    parent
                    for parent in chain
                    if type(parent).__name__ == "CardDrawer"
                    and hasattr(parent, "current_card")
                ),
                None,
            )
            if drawer is not None and getattr(drawer, "current_card", None) is None:
                return None
            pixmap = self._valid_pixmap(
                getattr(item, "original_pixmap", None)
            ) or self._valid_pixmap(getattr(item, "_original_pixmap", None))
            if pixmap is not None:
                return item

        # 场景模块的整套任务牌预览只保留了 QLabel 中的缩略图。
        if isinstance(widget, QLabel) and any(
            type(item).__name__ in self._PLAIN_CARD_CONTEXTS for item in chain
        ):
            pixmap = self._valid_pixmap(widget.pixmap())
            if pixmap is not None:
                return widget
        return None

    def _pixmap_for_source(self, source: QWidget) -> QPixmap | None:
        if not bool(getattr(source, "_hover_card_face_up", True)):
            return None
        pixmap = self._valid_pixmap(getattr(source, "_hover_card_pixmap", None))
        if pixmap is not None:
            return pixmap
        if isinstance(source, (PlayerCardWidget, EncounterCardWidget)):
            if bool(getattr(source, "_face_down", False)):
                return None
            return self._valid_pixmap(getattr(source, "current_pixmap", None))
        pixmap = self._valid_pixmap(getattr(source, "original_pixmap", None))
        if pixmap is not None:
            return pixmap
        pixmap = self._valid_pixmap(getattr(source, "_original_pixmap", None))
        if pixmap is not None:
            return pixmap
        if isinstance(source, QLabel):
            return self._valid_pixmap(source.pixmap())
        return None

    def _show_source(self, source: QWidget) -> None:
        pixmap = self._pixmap_for_source(source)
        if pixmap is None:
            self.hide_preview()
            return
        self._source = source
        self._source_pixmap = QPixmap(pixmap)
        self._reposition_or_hide()

    @staticmethod
    def _vertical_overlap(a: QRect, b: QRect) -> int:
        return max(0, min(a.bottom(), b.bottom()) - max(a.top(), b.top()) + 1)

    def _side_regions(
        self, frame: QRect, side: str
    ) -> list[tuple[int, int, QRect]]:
        regions: list[tuple[int, int, QRect]] = []
        app = QApplication.instance()
        if app is None:
            return regions
        for screen in app.screens():
            available = screen.availableGeometry()
            if self._vertical_overlap(frame, available) <= 0:
                continue
            if side == "right":
                start = max(frame.right() + 1 + self.MARGIN, available.left() + self.MARGIN)
                end = available.right() + 1 - self.MARGIN
                distance = max(0, available.left() - frame.right() - 1)
            else:
                start = available.left() + self.MARGIN
                end = min(frame.left() - self.MARGIN, available.right() + 1 - self.MARGIN)
                distance = max(0, frame.left() - available.right() - 1)
            width = max(0, end - start)
            if width > 0:
                regions.append((width, distance, available))
        regions.sort(key=lambda item: (item[1], -item[0]))
        return regions

    @staticmethod
    def _scaled_size(pixmap: QPixmap, max_width: int, max_height: int) -> QSize:
        if max_width <= 0 or max_height <= 0:
            return QSize()
        return pixmap.size().scaled(
            max_width,
            max_height,
            Qt.KeepAspectRatio,
        )

    def _choose_region(
        self, frame: QRect, pixmap: QPixmap
    ) -> tuple[str, QRect, QSize] | None:
        app = QApplication.instance()
        if app is None:
            return None
        main_screen = app.screenAt(frame.center()) or app.primaryScreen()
        if main_screen is None:
            return None
        main_available = main_screen.availableGeometry()
        ideal_max_width = min(
            self.MAX_WIDTH,
            max(self.MIN_VISIBLE_WIDTH, int(main_available.width() * 0.34)),
        )
        ideal_max_height = min(
            max(1, frame.height() - 2 * self.MARGIN),
            max(1, int(main_available.height() * self.SCREEN_HEIGHT_RATIO)),
        )
        ideal = self._scaled_size(pixmap, ideal_max_width, ideal_max_height)
        if ideal.isEmpty():
            return None

        right_regions = self._side_regions(frame, "right")
        left_regions = self._side_regions(frame, "left")
        right_fit = next(
            (region for region in right_regions if region[0] >= ideal.width()),
            None,
        )
        if right_fit is not None:
            return "right", right_fit[2], ideal
        left_fit = next(
            (region for region in left_regions if region[0] >= ideal.width()),
            None,
        )
        if left_fit is not None:
            return "left", left_fit[2], ideal

        candidates: list[tuple[int, str, QRect]] = []
        if right_regions:
            widest_right = max(right_regions, key=lambda item: item[0])
            candidates.append((widest_right[0], "right", widest_right[2]))
        if left_regions:
            widest_left = max(left_regions, key=lambda item: item[0])
            candidates.append((widest_left[0], "left", widest_left[2]))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1] == "right"), reverse=True)
        width, side, available = candidates[0]
        if width < self.MIN_VISIBLE_WIDTH:
            return None
        max_height = min(
            ideal_max_height,
            max(1, available.height() - 2 * self.MARGIN),
        )
        scaled = self._scaled_size(pixmap, min(width, ideal_max_width), max_height)
        if scaled.isEmpty() or scaled.width() < self.MIN_VISIBLE_WIDTH:
            return None
        return side, available, scaled

    def _reposition_or_hide(self) -> None:
        pixmap = self._source_pixmap
        if pixmap is None or pixmap.isNull():
            self.hide_preview()
            return
        try:
            if not self._main_window.isVisible() or self._main_window.isMinimized():
                self.hide_preview()
                return
            frame = self._main_window.frameGeometry()
        except RuntimeError:
            self.hide_preview()
            return
        chosen = self._choose_region(frame, pixmap)
        if chosen is None:
            self._preview.hide()
            return
        side, available, scaled_size = chosen
        scaled = pixmap.scaled(
            scaled_size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self._preview.setPixmap(scaled)
        self._preview.resize(scaled.width() + 4, scaled.height() + 4)
        if side == "right":
            x = max(
                frame.right() + 1 + self.MARGIN,
                available.left() + self.MARGIN,
            )
        else:
            right_edge = min(
                frame.left() - self.MARGIN,
                available.right() + 1 - self.MARGIN,
            )
            x = right_edge - self._preview.width()
        min_y = available.top() + self.MARGIN
        max_y = available.bottom() + 1 - self.MARGIN - self._preview.height()
        y = frame.center().y() - self._preview.height() // 2
        y = max(min_y, min(y, max_y))
        self._preview.move(x, y)
        self._preview.show()
        self._preview.raise_()

    def hide_preview(self) -> None:
        self._source = None
        self._source_pixmap = None
        try:
            self._preview.hide()
        except RuntimeError:
            # QApplication 退出时顶层预览窗可能先于事件过滤器销毁。
            pass

    def _schedule_hide_check(self) -> None:
        if self._hide_check_pending:
            return
        self._hide_check_pending = True
        QTimer.singleShot(0, self._hide_if_cursor_left_source)

    def _hide_if_cursor_left_source(self) -> None:
        self._hide_check_pending = False
        app = QApplication.instance()
        if app is None:
            self.hide_preview()
            return
        watched = app.widgetAt(QCursor.pos())
        source = self._source_from_widget(watched)
        if source is None:
            self.hide_preview()
            return
        if source is not self._source:
            self._show_source(source)

    def eventFilter(self, watched, event) -> bool:
        et = event.type()
        if watched is self._main_window:
            if et in (
                QEvent.Move,
                QEvent.Resize,
                QEvent.Show,
                QEvent.WindowStateChange,
            ):
                QTimer.singleShot(0, self._reposition_or_hide)
            elif et in (QEvent.Hide, QEvent.Close, QEvent.DeferredDelete):
                self.hide_preview()

        if et == QEvent.Enter:
            source = self._source_from_widget(watched)
            if source is not None and source is not self._source:
                self._show_source(source)
        elif et == QEvent.Leave:
            if self._source is not None:
                self._schedule_hide_check()
        elif et in (QEvent.Hide, QEvent.Close, QEvent.DeferredDelete):
            if watched is self._source:
                self.hide_preview()
        return False

# 威胁转盘资源（58×181，横向）
THREAT_DIAL_IMAGE = Path(__file__).resolve().parent / "cards" / "images" / "threatdial.png"
WILLPOWER_ICON = Path(__file__).resolve().parent / "cards" / "images" / "Willpower.jpg"
THREAT_ICON = Path(__file__).resolve().parent / "cards" / "images" / "Threat.jpg"
PROGRESS_ICON = (
    Path(__file__).resolve().parent / "cards" / "images" / "tokens" / "progress.png"
)
FIRST_PLAYER_TOKEN = (
    Path(__file__).resolve().parent / "cards" / "images" / "tokens" / "first_player.png"
)
# 数字叠加在中心红色数值区（相对整图高度的比例）
THREAT_VALUE_X_RATIO = 0.50
THREAT_VALUE_Y_RATIO = 0.50

# 威胁转盘布局调优（应用户标注：绿色=边框/外框，红色=威胁转盘底图，黄色=意志徽章，紫色=威胁徽章）
# 红色转盘外框 OUTER_HEIGHT；放大后的意志/威胁徽章排列在转盘底图下方。
THREAT_DIAL_OUTER_HEIGHT = 60
THREAT_DIAL_IMAGE_HEIGHT_RATIO = 1.00  # 红色转盘视觉高度 / 转盘区高度
THREAT_DIAL_IMAGE_V_OFFSET = 0.0       # 相对转盘区的垂直偏移（0=贴顶）
THREAT_DIAL_BADGE_BAND = 26            # 转盘下方徽章带高度（外框总高 = OUTER_HEIGHT + BADGE_BAND）
THREAT_DIAL_BADGE_WIDTH_RATIO = 0.58   # 单个徽章宽度 / 外框宽度
THREAT_DIAL_BADGE_HEIGHT_RATIO = 0.38  # 徽章高度 / BADGE_BAND

# 环节存档：进入每个大环节前自动写入；取消时读回存档（单槽、整文件覆盖）
CHECKPOINT_PATH = Path(__file__).resolve().parent / "save.rb"
CHECKPOINT_VERSION = 1
CHECKPOINT_HEADER = "# LotR-LCG phase checkpoint (auto-generated, do not edit)"

def _jsonify(obj):
    """将含 Card/集合/元组/非字符串键字典的对象转换为可 JSON 序列化结构。"""
    if isinstance(obj, PlayerCard):
        return {"__card__": "player", "data": asdict(obj)}
    if isinstance(obj, EncounterCard):
        data = asdict(obj)
        hidden = getattr(obj, "_uncharted_hidden_card", None)
        if hidden is not None:
            data["_uncharted_hidden_card"] = _jsonify(hidden)
        if getattr(obj, "_uncharted_proxy", False):
            data["_uncharted_proxy"] = True
        if getattr(obj, "_uncharted_peeked", False):
            data["_uncharted_peeked"] = True
        grotto_face = getattr(obj, "_grotto_face", "")
        if grotto_face:
            data["_grotto_face"] = grotto_face
        grotto_other_face = getattr(obj, "_grotto_other_face_data", None)
        if grotto_other_face is not None:
            data["_grotto_other_face_data"] = _jsonify(grotto_other_face)
        grotto_victory = getattr(obj, "_grotto_victory", None)
        if grotto_victory is not None:
            data["_grotto_victory"] = int(grotto_victory or 0)
        return {"__card__": "encounter", "data": data}
    if isinstance(obj, dict):
        all_str_keys = all(isinstance(k, str) for k in obj)
        if all_str_keys:
            return {k: _jsonify(v) for k, v in obj.items()}
        return {"__dict__": [[_jsonify(k), _jsonify(v)] for k, v in obj.items()]}
    if isinstance(obj, (set, frozenset)):
        return {"__set__": [_jsonify(v) for v in obj]}
    if isinstance(obj, tuple):
        return {"__tuple__": [_jsonify(v) for v in obj]}
    if isinstance(obj, list):
        return [_jsonify(v) for v in obj]
    return obj


def _unjsonify(obj):
    """Reverse of _jsonify."""
    if isinstance(obj, dict):
        if "__card__" in obj:
            data = obj.get("data", {})
            if obj["__card__"] == "player":
                return PlayerCard(**data)
            data.setdefault("Keywords", "")
            hidden = _unjsonify(data.pop("_uncharted_hidden_card", None))
            is_proxy = bool(data.pop("_uncharted_proxy", False))
            peeked = bool(data.pop("_uncharted_peeked", False))
            grotto_face = data.pop("_grotto_face", "")
            grotto_other_face = _unjsonify(
                data.pop("_grotto_other_face_data", None)
            )
            grotto_victory = data.pop("_grotto_victory", None)
            card = EncounterCard(**data)
            if hidden is not None:
                setattr(card, "_uncharted_hidden_card", hidden)
            if is_proxy:
                setattr(card, "_uncharted_proxy", True)
            if peeked:
                setattr(card, "_uncharted_peeked", True)
            if grotto_face:
                setattr(card, "_grotto_face", grotto_face)
            if grotto_other_face is not None:
                setattr(card, "_grotto_other_face_data", grotto_other_face)
            if grotto_victory is not None:
                setattr(card, "_grotto_victory", int(grotto_victory or 0))
            return card
        if "__set__" in obj:
            return {_unjsonify(v) for v in obj["__set__"]}
        if "__tuple__" in obj:
            return tuple(_unjsonify(v) for v in obj["__tuple__"])
        if "__dict__" in obj:
            return {_unjsonify(k): _unjsonify(v) for k, v in obj["__dict__"]}
        return {k: _unjsonify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_unjsonify(v) for v in obj]
    return obj


class LocationCardLabel(QLabel):
    """当前地区卡图：左键单击；右键双击放大（与遭遇卡一致）。"""

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.original_pixmap = None
        self.zoom_dialog = None
        self.setAlignment(Qt.AlignCenter)
        self.setScaledContents(False)
        self.setStyleSheet("background: transparent; border: none;")
        self._drag_zoom = CardDragZoomController(self, self.show_zoomed_image)
        self._drag_zoom.install()

    def set_image(self, pixmap: QPixmap):
        self.original_pixmap = pixmap
        self._apply_scaled_pixmap()

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
        if event.button() == Qt.RightButton:
            if self.original_pixmap and not self.original_pixmap.isNull():
                self.show_zoomed_image()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def show_zoomed_image(self):
        if not self.original_pixmap or self.original_pixmap.isNull():
            return
        if self.zoom_dialog:
            self.zoom_dialog.close()
        self.zoom_dialog = CardImageZoomDialog(self.original_pixmap, self)
        self.zoom_dialog.show()

    def contextMenuEvent(self, event):
        if self.zoom_dialog is not None and self.zoom_dialog.isVisible():
            event.accept()
            return
        super().contextMenuEvent(event)


class CurrentLocationAttachmentsDialog(QDialog):
    """当前地区及其附属：弹窗显示，单击任意处关闭"""

    CARD_HEIGHT = 200

    def __init__(
        self,
        location_card,
        attachment_cards,
        series: str,
        *,
        facedown_ids=None,
        owner_color_fn=None,
        parent=None,
    ):
        super().__init__(parent)
        loc_name = getattr(location_card, "name", "") or '当前地区'
        att_count = len(attachment_cards)
        self.setWindowTitle(
            f"当前地区 - {loc_name}（{att_count} 张附属，单击关闭）"
        )
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setContextMenuPolicy(Qt.NoContextMenu)
        facedown_ids = set(facedown_ids or ())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(8)

        group = CharacterGroupWidget()
        host_widget = EncounterCardWidget(
            card_name=location_card.name,
            series=series,
            show_threat_badge=False,
            max_height=self.CARD_HEIGHT,
        )
        host_widget.bind_game_card(location_card)
        group.set_host(host_widget)
        for att_card in attachment_cards:
            if isinstance(att_card, PlayerCard):
                att_widget = PlayerCardWidget(
                    card_name=att_card.name,
                    series=getattr(att_card, "series", "") or series,
                    max_height=self.CARD_HEIGHT,
                )
                att_widget.bind_game_card(att_card)
                if owner_color_fn is not None:
                    owner_color = owner_color_fn(att_card)
                    if owner_color:
                        att_widget.set_owner_border(owner_color)
            else:
                att_widget = EncounterCardWidget(
                    card_name=att_card.name,
                    series=series,
                    show_threat_badge=False,
                    max_height=self.CARD_HEIGHT,
                    face_down=getattr(att_card, "id", "") in facedown_ids,
                    restore_markers=False,
                )
                att_widget.bind_game_card(att_card)
            group.add_attachment(att_widget)
        layout.addWidget(group, alignment=Qt.AlignCenter)

        hint = QLabel('单击任意处关闭')
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(hint)

        self.adjustSize()
        screen = QApplication.primaryScreen().availableGeometry()
        center = screen.center()
        self.move(
            center.x() - self.width() // 2,
            center.y() - self.height() // 2,
        )

    def showEvent(self, event):
        super().showEvent(event)
        self.install_event_filters()

    def install_event_filters(self):
        for widget in self.findChildren(QWidget):
            widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if (
            event.type() == QEvent.MouseButtonRelease
            and event.button() == Qt.LeftButton
        ):
            self.close()
            return False
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
            self.close()
        super().keyPressEvent(event)


class CurrentLocationPanel(QWidget):
    """区域 1-1 当前地区：遭遇卡 + 底部进度条。"""

    # 与右侧探险任务同宽同高，保证深蓝进度条始终露底
    CARD_W = 140
    CARD_H = 100
    BAR_H = 26

    def __init__(
        self,
        card_name: str,
        series: str,
        progress: int = 0,
        *,
        image_path: str = "",
        attachment_count: int = 0,
        on_location_click=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setFixedSize(self.CARD_W, self.CARD_H + self.BAR_H)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.card_container = QFrame(self)
        self.card_container.setFixedSize(self.CARD_W, self.CARD_H)
        self.card_container.setStyleSheet("background: transparent; border: none;")

        self.card_label = LocationCardLabel(self.card_container)
        self.card_label.setGeometry(0, 0, self.CARD_W, self.CARD_H)
        self._load_card_image(card_name, series, image_path=image_path)
        if attachment_count > 0 and on_location_click is not None:
            self.card_label.clicked.connect(on_location_click)
            self.card_label.setToolTip(
                f"单击查看附属（{attachment_count} 张）"
            )

        self.progress_bar = QFrame(self)
        self.progress_bar.setFixedSize(self.CARD_W, self.BAR_H)
        self.progress_bar.setStyleSheet(
            "QFrame { background-color: #1e3a5f; border: none; }"
        )
        bar_layout = QHBoxLayout(self.progress_bar)
        bar_layout.setContentsMargins(6, 2, 6, 2)
        bar_layout.setSpacing(4)
        bar_layout.setAlignment(Qt.AlignCenter)

        self.progress_value_label = QLabel("0")
        value_font = QFont()
        value_font.setPointSize(14)
        value_font.setWeight(QFont.Bold)
        self.progress_value_label.setFont(value_font)
        self.progress_value_label.setStyleSheet(
            "color: #FFD700; background: transparent; border: none;"
        )
        self.progress_value_label.setAlignment(Qt.AlignVCenter | Qt.AlignRight)

        self.progress_icon_label = QLabel()
        self.progress_icon_label.setFixedSize(18, 18)
        self.progress_icon_label.setScaledContents(True)
        self.progress_icon_label.setStyleSheet(
            "background: transparent; border: none;"
        )
        if PROGRESS_ICON.is_file():
            icon = QPixmap(str(PROGRESS_ICON)).scaled(
                18, 18, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.progress_icon_label.setPixmap(icon)

        bar_layout.addStretch()
        bar_layout.addWidget(self.progress_value_label)
        bar_layout.addWidget(self.progress_icon_label)
        bar_layout.addStretch()

        layout.addWidget(self.card_container)
        layout.addWidget(self.progress_bar)
        self.set_progress(progress)

    def _load_card_image(
        self, card_name: str, series: str, *, image_path: str = ""
    ):
        path = image_path or ""
        if not path or not Path(path).is_file():
            card = load_encounter_card_by_name(card_name, series=series)
            path = (card.image_path if card else None) or ""
        if not path or not Path(path).is_file():
            path = str(ENCOUNTER_CARD_BACK) if ENCOUNTER_CARD_BACK.is_file() else ""
        if path:
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                self.card_label.set_image(pixmap)
                return
        self.card_label.original_pixmap = None
        self.card_label.setText('无卡图')

    def show_zoomed_card(self):
        self.card_label.show_zoomed_image()

    def set_progress(self, progress: int):
        self.progress_value_label.setText(str(max(0, int(progress))))


def _parse_threat(value) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return int(text)
    except ValueError:
        return 0


MIRLONDE_HERO_NAMES = frozenset({"米兰德", "米尔隆德", "Mirlonde"})


def _is_mirlonde_hero_card(card) -> bool:
    if (getattr(card, "type", "") or "").strip() != '英雄':
        return False
    name = (getattr(card, "name", "") or "").strip()
    if name in MIRLONDE_HERO_NAMES:
        return True
    canonical = CARD_NAME_ALIASES.get(name, "")
    return canonical in MIRLONDE_HERO_NAMES


def hero_has_printed_lore_sphere(hero) -> bool:
    """英雄印刷【学识】资源池号（不含附属赋予）。"""
    return _card_sphere(hero) in ('学识', "Lore")


def hero_has_printed_leadership_sphere(hero) -> bool:
    """英雄印刷【领导】资源池号（不含附属赋予）。"""
    return _card_sphere(hero) in ("领导", "Leadership")


def hero_has_printed_tactics_sphere(hero) -> bool:
    """英雄印刷【战术】资源池号（不含附属赋予）。"""
    return _card_sphere(hero) in ('战术', "Tactics")


def hero_has_printed_spirit_sphere(hero) -> bool:
    """英雄印刷【精神】资源池号（不含附属赋予）。"""
    return _card_sphere(hero) in ("精神", "Spirit")


def _mirlonde_lore_threat_passive_active(heroes) -> bool:
    return any(_is_mirlonde_hero_card(h) for h in heroes)


def effective_hero_starting_threat(hero, heroes) -> int:
    """单名英雄计入起始威胁（含米尔隆德：印刷学识英雄 -1）。"""
    threat = _parse_threat(getattr(hero, "Threat", ""))
    if _mirlonde_lore_threat_passive_active(heroes) and hero_has_printed_lore_sphere(
        hero
    ):
        threat = max(0, threat - 1)
    return threat


def calc_initial_threat_from_heroes(heroes) -> int:
    """根据英雄列表求和初始威胁（CSV「初始威胁」列，含米尔隆德被动）。"""
    return sum(effective_hero_starting_threat(h, heroes) for h in heroes)


def _parse_card_cost(card) -> int:
    return _parse_threat(getattr(card, "Cost", ""))


def _parse_secrecy_value(card) -> int:
    """卡牌「隐秘X」数值（仅用于从手牌打出时的费用减免）。"""
    text = (getattr(card, "Text_Effect", "") or "")
    match = re.search(r"(?:隐秘|隐匿)\s*(\d+)", text)
    if match:
        return int(match.group(1))
    return 0


def _card_rule_keyword_clauses(card) -> list[str]:
    """规则文字中的关键词短句（如「远攻」「警戒.隐秘 3.」等）。"""
    text = (getattr(card, "Text_Effect", "") or "").strip()
    if not text:
        return []
    return [c.strip() for c in text.replace("。", ".").split(".") if c.strip()]


def _entangle_source_text(card) -> str:
    """返回遭遇卡上用于解析「缠绕」的关键字/规则文字。"""
    keywords = (getattr(card, "Keywords", "") or "").strip()
    text = (getattr(card, "Text_Effect", "") or "").strip()
    return "\n".join(part for part in (keywords, text) if part)


def _parse_entangle_condition(card) -> str:
    """解析「缠绕（条件）」中的括号条件；找不到时返回空字符串。"""
    source = _entangle_source_text(card)
    if not source or "缠绕" not in source:
        return ""
    match = re.search(r"缠绕\s*[（(]\s*([^）)]*?)\s*[）)]", source)
    return (match.group(1) or "").strip() if match else ""


def _has_entangle_keyword(card) -> bool:
    """判断遭遇卡是否具有「缠绕（条件）」关键词。"""
    return bool(_parse_entangle_condition(card))


def _card_sphere(card) -> str:
    return (getattr(card, "Sphere", "") or "").strip()


def _is_neutral_sphere(sphere: str) -> bool:
    return not sphere or sphere in ("中立", "Neutral", '无')


def _is_unique_card(card) -> bool:
    return (getattr(card, "unique", "") or "").strip() in ("*", "√", "是", "Y", "y", "1")


def _is_player_side_quest_card(card) -> bool:
    """玩家支线探险：玩家卡背的探险卡，本地 CSV 类型为「任务」。"""
    card_type = (getattr(card, "type", "") or "").strip()
    if isinstance(card, PlayerCard):
        return card_type in ("任务", "探险", "Side Quest", "Quest")
    return card_type in ("支线探险", "遭遇支线探险", "Side Quest")


def _player_side_quest_points(card) -> int:
    """读取玩家支线探险的任务点；旧存档缺字段时回查玩家 CSV。"""
    for attr in ("QuestPoints", "quest_points", "Progress"):
        value = getattr(card, attr, "")
        parsed = _parse_threat(value)
        if parsed > 0:
            return parsed
    name = (getattr(card, "name", "") or "").strip()
    series = (getattr(card, "series", "") or "").strip()
    if not name:
        return 0
    try:
        rows = _read_player_csv_rows()
    except Exception:
        rows = []
    for row in rows:
        row_type = (row.get("类型") or "").strip()
        if row_type not in ("任务", "探险", "Side Quest", "Quest"):
            continue
        row_series = (row.get("系列") or "").strip()
        row_names = {
            (row.get("卡牌名称") or "").strip(),
            (row.get("备用卡牌名称") or "").strip(),
        }
        if name in row_names and (not series or not row_series or series == row_series):
            return _parse_threat(row.get("任务点"))
    return 0


def _canonical_card_title(name: str) -> str:
    """卡牌标题规范名（含别名），用于独特同名判定。"""
    name = (name or "").strip()
    return CARD_NAME_ALIASES.get(name, name)


def _cards_share_title(a, b) -> bool:
    return _canonical_card_title(getattr(a, "name", "")) == _canonical_card_title(
        getattr(b, "name", "")
    )


def _is_restricted_attachment(card) -> bool:
    """带「限制」/「受限」关键词的附属（卡牌文本含「不受限制附属」的除外）。"""
    if (getattr(card, "type", "") or "").strip() != "附属":
        return False
    text = (getattr(card, "Text_Effect", "") or "")
    if '不受限制附属' in text.replace('"', ''):
        return False
    restricted = (getattr(card, "restricted", "") or "").strip()
    return restricted in ("*", "√", "是", "Y", "y", "1")


def _auto_resource_payment(payers, cost: int, hero_resources: dict) -> dict[str, int]:
    """匹配派系英雄可用资源总和等于费用时，按英雄顺序自动分配。"""
    payment: dict[str, int] = {}
    remaining = cost
    for hero in payers:
        if remaining <= 0:
            break
        take = min(int(hero_resources.get(hero.id, 0)), remaining)
        if take > 0:
            payment[hero.id] = take
            remaining -= take
    return payment


def _min_contributing_heroes_from_card(card) -> int:
    """卡牌文本要求的不同英雄资源池最低贡献人数。"""
    text = (getattr(card, "Text_Effect", "") or "")
    if '三名不同英雄' in text:
        return 3
    return 0


def _max_contributing_heroes_from_card(card) -> int:
    """卡牌文本要求的英雄资源池最高贡献人数。0 表示不限制。"""
    text = (getattr(card, "Text_Effect", "") or "")
    normalized = text.replace('1名', '一名').replace('1 个', '一个')
    normalized_lower = normalized.lower()
    if '费用必须从一名英雄的资源池中支付' in normalized:
        return 1
    if 'resource cost must be paid from 1 hero' in normalized_lower:
        return 1
    if "resource cost must be paid from a single hero's resource pool" in normalized_lower:
        return 1
    return 0


def _payment_contributor_count(payment: dict[str, int]) -> int:
    return sum(1 for amount in payment.values() if amount > 0)


def _auto_distinct_hero_payment(
    payers,
    cost: int,
    hero_resources: dict,
    min_heroes: int,
) -> dict[str, int] | None:
    """可用资源总和等于费用时，从至少 min_heroes 名不同英雄自动分配。"""
    funded = [h for h in payers if int(hero_resources.get(h.id, 0)) > 0]
    if len(funded) < min_heroes:
        return None
    total = sum(int(hero_resources.get(h.id, 0)) for h in payers)
    if total != cost:
        return None
    payment: dict[str, int] = {}
    remaining = cost
    for hero in funded[:min_heroes]:
        payment[hero.id] = 1
        remaining -= 1
    idx = 0
    while remaining > 0:
        hero = funded[idx % len(funded)]
        available = int(hero_resources.get(hero.id, 0)) - payment.get(hero.id, 0)
        take = min(available, remaining)
        if take > 0:
            payment[hero.id] = payment.get(hero.id, 0) + take
            remaining -= take
        idx += 1
        if idx > len(funded) * max(cost, 1) + min_heroes:
            return None
    if (
        sum(payment.values()) != cost
        or _payment_contributor_count(payment) < min_heroes
    ):
        return None
    return payment


PLANNING_PLAYABLE_TYPES = ("盟友", "附属", "任务", '事件')
ACTION_WINDOW_PLAYABLE_TYPES = ('事件',)
ACTION_WINDOW_PLAY_HINT = '操作：双击手牌打出事件。'
COMMIT_ROSTER_READY_HINT = (
    "已报名角色仍计入结算；报名后可在行动窗口将其重整"
    "（如突来勇气、支付资源响应等），结算时不看其当前横置/重整状态。"
)
_HERO_PAY_CARD_HEIGHT = 110
_CHARACTER_PICK_CARD_HEIGHT = 130
_ATTACK_ICON = Path(__file__).resolve().parent / "cards" / "images" / "attack.png"
_DEFENSE_ICON = Path(__file__).resolve().parent / "cards" / "images" / "Defense.png"


@dataclass
class CharacterPickOption:
    char_id: str
    label: str
    image_path: str
    attack: int
    defense: int
    health: int
    player_tag: str = ""


class _HeroPayImageLabel(QLabel):
    """英雄卡图：单击 +1、双击 -1 分配资源。"""

    single_clicked = pyqtSignal()
    double_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._original_pixmap = None
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            "border: 2px solid #888; background-color: white; border-radius: 5px;"
        )
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._emit_single_click)
        self._suppress_single = False

    def _emit_single_click(self):
        if self._suppress_single:
            return
        self.single_clicked.emit()

    def set_card_pixmap(self, pixmap: QPixmap):
        if pixmap is None or pixmap.isNull():
            self._original_pixmap = None
            self.setText('无图')
            return
        self._original_pixmap = pixmap
        self._apply_scaled_pixmap()

    def _apply_scaled_pixmap(self):
        if self._original_pixmap is None or self._original_pixmap.isNull():
            return
        self.setPixmap(
            self._original_pixmap.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_scaled_pixmap()

    def mouseReleaseEvent(self, event):
        if (
            event.button() == Qt.LeftButton
            and self.rect().contains(event.pos())
            and not self._suppress_single
        ):
            self._click_timer.start(QApplication.doubleClickInterval())
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._click_timer.stop()
            self._suppress_single = True
            self.double_clicked.emit()
            QTimer.singleShot(
                QApplication.doubleClickInterval(),
                self._clear_suppress_single,
            )
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _clear_suppress_single(self):
        self._suppress_single = False


class _CardRowHorizontalScroller(QObject):
    """卡牌行：滚轮或长按左右拖拽，驱动底部横向滚动条。"""

    _LONG_PRESS_MS = 300
    _WHEEL_STEP = 80
    _DRAG_THRESHOLD = 12

    def __init__(self, scroll_area: QScrollArea, content_widget: QWidget):
        super().__init__(scroll_area)
        self._scroll = scroll_area
        self._viewport = scroll_area.viewport()
        self._bar = scroll_area.horizontalScrollBar()
        self._content = content_widget
        self._press_global_x: int | None = None
        self._press_global_y: int | None = None
        self._press_scroll_value = 0
        self._long_press_ready = False
        self._drag_scrolling = False
        self._press_timer = QTimer(self)
        self._press_timer.setSingleShot(True)
        self._press_timer.timeout.connect(self._on_long_press_ready)
        for target in (scroll_area, self._viewport, content_widget):
            target.installEventFilter(self)
        scroll_area.destroyed.connect(self._on_widgets_destroyed)

    def _on_widgets_destroyed(self) -> None:
        self._press_timer.stop()
        self._scroll = None
        self._viewport = None
        self._content = None
        self._bar = None

    def _alive(self) -> bool:
        if self._scroll is None:
            return False
        try:
            self._scroll.objectName()
            return True
        except RuntimeError:
            self._on_widgets_destroyed()
            return False

    def _on_long_press_ready(self) -> None:
        self._long_press_ready = True

    def _is_row_surface(self, watched) -> bool:
        if not self._alive():
            return False
        if watched is self._scroll or watched is self._viewport or watched is self._content:
            return True
        if not isinstance(watched, QWidget):
            return False
        try:
            return self._content.isAncestorOf(watched)
        except RuntimeError:
            return False

    def _apply_wheel(self, delta: int) -> bool:
        if not self._alive() or self._bar is None:
            return False
        if delta == 0:
            return False
        self._bar.setValue(
            self._bar.value() - int(delta / 120) * self._WHEEL_STEP
        )
        return True

    def sync_child_filters(self) -> None:
        """卡牌行刷新后，为行内控件挂上事件过滤以支持拖拽滚动。"""
        if not self._alive():
            return
        for child in self._content.findChildren(QWidget):
            if child is self._content:
                continue
            child.installEventFilter(self)

    @staticmethod
    def _card_drag_zoom_active(watched) -> bool:
        w = watched if isinstance(watched, QWidget) else None
        while w is not None:
            drag_zoom = getattr(w, "_drag_zoom", None)
            if drag_zoom is not None and drag_zoom.is_drag_active():
                return True
            w = w.parentWidget()
        return False

    def eventFilter(self, watched, event) -> bool:
        if not self._alive():
            return False
        if not self._is_row_surface(watched):
            return False
        et = event.type()
        if et == QEvent.Wheel:
            delta = event.angleDelta().y()
            if delta == 0:
                delta = event.angleDelta().x()
            if self._apply_wheel(delta):
                event.accept()
                return True
            return False
        if et == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            if self._bar is None:
                return False
            self._press_global_x = event.globalX()
            self._press_global_y = event.globalY()
            self._press_scroll_value = self._bar.value()
            self._long_press_ready = False
            self._drag_scrolling = False
            self._press_timer.start(self._LONG_PRESS_MS)
            return False
        if et == QEvent.MouseMove and self._press_global_x is not None:
            if self._bar is None:
                return False
            if self._card_drag_zoom_active(watched):
                return False
            dx = event.globalX() - self._press_global_x
            dy = event.globalY() - (self._press_global_y or event.globalY())
            if self._long_press_ready and abs(dy) >= abs(dx):
                return False
            if not self._long_press_ready and abs(dx) < self._DRAG_THRESHOLD:
                return False
            if abs(dx) <= abs(dy):
                return False
            self._drag_scrolling = True
            self._bar.setValue(self._press_scroll_value - dx)
            event.accept()
            return True
        if et == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            if self._card_drag_zoom_active(watched):
                return False
            self._press_timer.stop()
            was_drag = self._drag_scrolling
            self._press_global_x = None
            self._press_global_y = None
            self._long_press_ready = False
            self._drag_scrolling = False
            if was_drag:
                event.accept()
                return True
            return False
        return False


def _setup_horizontal_card_scroll(
    content_widget: QWidget,
    *,
    min_height: int,
    min_viewport_width: int = 520,
    scrollbar_policy=Qt.ScrollBarAsNeeded,
) -> tuple[QScrollArea, _CardRowHorizontalScroller]:
    """卡牌横排区域：滚轮或长按左右拖拽横向滚动。"""
    content_widget.adjustSize()
    scroll = QScrollArea()
    scroll.setWidgetResizable(False)
    scroll.setHorizontalScrollBarPolicy(scrollbar_policy)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setWidget(content_widget)
    scroll.setMinimumSize(min_viewport_width, min_height)
    scroller = _CardRowHorizontalScroller(scroll, content_widget)
    scroller.sync_child_filters()
    return scroll, scroller


class _HeroResourcePayCard(QWidget):
    """单个英雄：卡图+资源池已分配显示。"""

    allocation_changed = pyqtSignal()

    def __init__(self, hero, available: int, sphere_label: str = "", parent=None):
        super().__init__(parent)
        self.hero = hero
        self._sphere_label = sphere_label
        self.available = max(0, int(available))
        self.allocated = 0
        card_w, card_h = fit_player_card_size(_HERO_PAY_CARD_HEIGHT)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self._image = _HeroPayImageLabel()
        self._image.setFixedSize(card_w, card_h)
        img_path = getattr(hero, "image_path", "") or ""
        if img_path and Path(img_path).is_file():
            pixmap = QPixmap(img_path)
            if not pixmap.isNull():
                self._image.set_card_pixmap(pixmap)
        else:
            self._image.setText(hero.name)
        self._image.single_clicked.connect(self._on_single_click)
        self._image.double_clicked.connect(self._on_double_click)
        layout.addWidget(self._image, alignment=Qt.AlignCenter)
        self._info_label = QLabel()
        self._info_label.setAlignment(Qt.AlignCenter)
        self._info_label.setWordWrap(True)
        layout.addWidget(self._info_label)
        self._refresh_labels()

    def _refresh_labels(self):
        sphere = self._sphere_label or _card_sphere(self.hero) or "?"
        self._info_label.setText(
            f"{self.hero.name}\n"
            f"{sphere} · 资源 {self.available}\n"
            f"已分配 {self.allocated}"
        )
        border = "#2a7ae2" if self.allocated > 0 else "#888"
        self._image.setStyleSheet(
            f"border: 2px solid {border}; background-color: white; "
            "border-radius: 5px;"
        )

    def _on_single_click(self):
        if self.allocated < self.available:
            self.allocated += 1
            self._refresh_labels()
            self.allocation_changed.emit()

    def _on_double_click(self):
        if self.allocated > 0:
            self.allocated -= 1
            self._refresh_labels()
            self.allocation_changed.emit()


class _EnemyDamagePayCard(QWidget):
    """单个敌军：卡图+已分配伤害显示（参考X费用交互）。"""

    allocation_changed = pyqtSignal()

    def __init__(self, enemy_card, available: int, parent=None):
        super().__init__(parent)
        self.enemy_card = enemy_card
        self.available = max(0, int(available))
        self.allocated = 0
        card_w, card_h = fit_encounter_card_size(_HERO_PAY_CARD_HEIGHT)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self._image = _HeroPayImageLabel()
        self._image.setFixedSize(card_w, card_h)
        img_path = getattr(enemy_card, "image_path", "") or ""
        if img_path and Path(img_path).is_file():
            pixmap = QPixmap(img_path)
            if not pixmap.isNull():
                self._image.set_card_pixmap(pixmap)
        else:
            self._image.setText(getattr(enemy_card, "name", "") or '敌军')
        self._image.single_clicked.connect(self._on_single_click)
        self._image.double_clicked.connect(self._on_double_click)
        layout.addWidget(self._image, alignment=Qt.AlignCenter)
        self._info_label = QLabel()
        self._info_label.setAlignment(Qt.AlignCenter)
        self._info_label.setWordWrap(True)
        layout.addWidget(self._info_label)
        self._refresh_labels()

    def _refresh_labels(self):
        name = getattr(self.enemy_card, "name", "") or '敌军'
        self._info_label.setText(
            f"{name}\n"
            f"伤害 {self.available}\n"
            f"已选择 {self.allocated}"
        )
        border = "#2a7ae2" if self.allocated > 0 else "#888"
        self._image.setStyleSheet(
            f"border: 2px solid {border}; background-color: white; "
            "border-radius: 5px;"
        )

    def _on_single_click(self):
        if self.allocated < self.available:
            self.allocated += 1
            self._refresh_labels()
            self.allocation_changed.emit()

    def _on_double_click(self):
        if self.allocated > 0:
            self.allocated -= 1
            self._refresh_labels()
            self.allocation_changed.emit()


_PROMINENT_CONFIRM_STYLE = """
QPushButton {
    background-color: #2d8f4e;
    color: #ffffff;
    border: 2px solid #1e6b38;
    border-radius: 10px;
    padding: 10px 28px;
}
QPushButton:hover { background-color: #36a85c; }
QPushButton:pressed { background-color: #247a42; }
"""

_PROMINENT_SECONDARY_STYLE = """
QPushButton {
    background-color: #6c757d;
    color: #ffffff;
    border: 2px solid #545b62;
    border-radius: 10px;
    padding: 10px 20px;
}
QPushButton:hover { background-color: #7d868f; }
QPushButton:pressed { background-color: #5a6268; }
"""


def _style_prominent_button(btn: QPushButton, *, primary: bool = True):
    btn.setStyleSheet(
        _PROMINENT_CONFIRM_STYLE if primary else _PROMINENT_SECONDARY_STYLE
    )
    if primary:
        btn.setMinimumSize(156, 60)
    else:
        btn.setMinimumSize(132, 56)
    font = btn.font()
    font.setPointSize(16)
    font.setBold(True)
    btn.setFont(font)


def _add_prominent_dialog_buttons(
    layout: QVBoxLayout,
    *,
    accept_text: str = "确认",
    cancel_text: str = "",
    on_accept=None,
    on_reject=None,
) -> QPushButton:
    """弹窗底部右侧：放大号确认按钮（叉取消）。"""
    row = QHBoxLayout()
    row.setContentsMargins(0, 10, 0, 4)
    row.addStretch(1)
    if cancel_text:
        cancel_btn = QPushButton(cancel_text)
        _style_prominent_button(cancel_btn, primary=False)
        if on_reject is not None:
            cancel_btn.clicked.connect(on_reject)
        row.addWidget(cancel_btn)
    confirm_btn = QPushButton(accept_text)
    _style_prominent_button(confirm_btn, primary=True)
    if on_accept is not None:
        confirm_btn.clicked.connect(on_accept)
    row.addWidget(confirm_btn)
    layout.addLayout(row)
    return confirm_btn


class SpendResourceDialog(QDialog):
    """计划环节：点击英雄卡片分配资源（单击+1，双击-1）"""

    def __init__(
        self,
        parent,
        card,
        heroes,
        hero_resources: dict,
        sphere_labels: dict | None = None,
        min_contributing_heroes: int = 0,
        max_contributing_heroes: int = 0,
        override_cost: int | None = None,
        header_text: str | None = None,
        payment_hint: str | None = None,
        variable_cost_max: int | None = None,
    ):
        super().__init__(parent)
        self._heroes = heroes
        self._sphere_labels = sphere_labels or {}
        self._min_contributing_heroes = max(0, int(min_contributing_heroes))
        self._max_contributing_heroes = max(0, int(max_contributing_heroes))
        self._variable_cost = variable_cost_max is not None
        self._variable_cost_min = 1
        self.setWindowTitle('花费资源')
        sphere = _card_sphere(card) or "中立"
        if self._variable_cost:
            self._cost = max(1, int(variable_cost_max))
        else:
            self._cost = (
                int(override_cost)
                if override_cost is not None
                else _parse_card_cost(card)
            )
        layout = QVBoxLayout(self)
        default_hint = (
            '单击英雄卡图 +1 资源，双击 -1（总和须等于费用 X）'
            if self._variable_cost
            else "单击英雄卡图 +1 资源，双击 -1（总和须等于费用）"
        )
        payment_hint_text = payment_hint or default_hint
        if self._min_contributing_heroes >= 2:
            payment_hint_text += (
                f"\n须从至少 {self._min_contributing_heroes} 名不同英雄"
                '各支付至少 1 资源。'
            )
        if self._max_contributing_heroes == 1:
            payment_hint_text += "\n必须全部由同一名英雄支付。"
        if header_text:
            header = f"{header_text}\n{payment_hint_text}"
        else:
            cost_label = "X" if self._variable_cost else str(self._cost)
            header = (
                f"打出「{card.name}」\n"
                f"费用 {cost_label} · 影响力派系 {sphere}\n"
                f"{payment_hint_text}"
            )
        layout.addWidget(QLabel(header))
        row = QHBoxLayout()
        row.setSpacing(8)
        self._pay_cards: dict[str, _HeroResourcePayCard] = {}
        for hero in heroes:
            available = int(hero_resources.get(hero.id, 0))
            pay_card = _HeroResourcePayCard(
                hero,
                available,
                sphere_label=self._sphere_labels.get(hero.id, ""),
            )
            pay_card.allocation_changed.connect(self._update_total)
            self._pay_cards[hero.id] = pay_card
            row.addWidget(pay_card)
        row.addStretch()
        layout.addLayout(row)
        self._total_label = QLabel()
        layout.addWidget(self._total_label)
        self._update_total()
        _add_prominent_dialog_buttons(
            layout,
            accept_text="确认",
            cancel_text="取消",
            on_accept=self._on_accept,
            on_reject=self.reject,
        )

    def _update_total(self):
        total = sum(c.allocated for c in self._pay_cards.values())
        if self._variable_cost:
            self._total_label.setText(
                f"已分配 {total} / X（可选 1–{self._cost}）"
            )
        else:
            self._total_label.setText(f"已分配 {total} / {self._cost}")

    def _on_accept(self):
        total = sum(c.allocated for c in self._pay_cards.values())
        if self._variable_cost:
            if total < self._variable_cost_min or total > self._cost:
                QMessageBox.warning(
                    self,
                    '花费资源',
                    f"分配的资源须在 1–{self._cost}（当前 {total}）。",
                )
                return
        elif total != self._cost:
            QMessageBox.warning(
                self, '花费资源',
                f"分配的资源须等于卡牌费用（需要 {self._cost}，当前 {total}）。",
            )
            return
        if self._min_contributing_heroes:
            contributors = sum(
                1 for card in self._pay_cards.values() if card.allocated > 0
            )
            if contributors < self._min_contributing_heroes:
                QMessageBox.warning(
                    self,
                    '花费资源',
                    f"须从至少 {self._min_contributing_heroes} 名不同英雄"
                    '各支付至少 1 资源。',
                )
                return
        if self._max_contributing_heroes:
            contributors = sum(
                1 for card in self._pay_cards.values() if card.allocated > 0
            )
            if contributors > self._max_contributing_heroes:
                QMessageBox.warning(
                    self,
                    '花费资源',
                    "该卡的费用必须全部由同一名英雄支付。",
                )
                return
        self.accept()

    def get_payment(self) -> dict[str, int]:
        return {
            hid: card.allocated
            for hid, card in self._pay_cards.items()
            if card.allocated > 0
        }


class _CharacterPickClickLabel(QLabel):
    """角色区卡牌：单击选中，双击取消；支持拖动/右键双击放大。"""

    single_clicked = pyqtSignal()
    double_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._original_pixmap = None
        self.zoom_dialog = None
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            "border: 2px solid #888; background-color: white; border-radius: 5px;"
        )
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._emit_single_click)
        self._suppress_single = False
        self._drag_zoom = CardDragZoomController(self, self.show_zoomed_image)
        self._drag_zoom.install()

    @property
    def original_pixmap(self):
        return self._original_pixmap

    @original_pixmap.setter
    def original_pixmap(self, value):
        self._original_pixmap = value

    def show_zoomed_image(self):
        if not self._original_pixmap or self._original_pixmap.isNull():
            return
        if self.zoom_dialog:
            self.zoom_dialog.close()
        self.zoom_dialog = CardImageZoomDialog(self._original_pixmap, self)
        self.zoom_dialog.show()

    def _emit_single_click(self):
        if self._suppress_single:
            return
        self.single_clicked.emit()

    def set_card_pixmap(self, pixmap: QPixmap):
        if pixmap is None or pixmap.isNull():
            self._original_pixmap = None
            self.setText('无图')
            return
        self._original_pixmap = pixmap
        self._apply_scaled_pixmap()

    def _apply_scaled_pixmap(self):
        if self._original_pixmap is None or self._original_pixmap.isNull():
            return
        self.setPixmap(
            self._original_pixmap.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_scaled_pixmap()

    def mouseReleaseEvent(self, event):
        if (
            event.button() == Qt.LeftButton
            and getattr(self, "_drag_zoom", None)
            and self._drag_zoom.suppress_click()
        ):
            self._drag_zoom.clear_suppress_click()
            event.accept()
            super().mouseReleaseEvent(event)
            return
        if (
            event.button() == Qt.LeftButton
            and self.rect().contains(event.pos())
            and not self._suppress_single
        ):
            self._click_timer.start(QApplication.doubleClickInterval())
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.RightButton:
            if self._original_pixmap and not self._original_pixmap.isNull():
                self.show_zoomed_image()
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            self._click_timer.stop()
            self._suppress_single = True
            self.double_clicked.emit()
            QTimer.singleShot(
                QApplication.doubleClickInterval(),
                self._clear_suppress_single,
            )
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _clear_suppress_single(self):
        self._suppress_single = False

    def contextMenuEvent(self, event):
        if self.zoom_dialog is not None and self.zoom_dialog.isVisible():
            event.accept()
            return
        super().contextMenuEvent(event)

    def set_selected(self, selected: bool):
        border = "#2a7ae2" if selected else "#888"
        self.setStyleSheet(
            f"border: 3px solid {border}; background-color: white; "
            "border-radius: 5px;"
        )


class _CharacterPickTile(QWidget):
    """场上角色选项：卡图+攻防数值。"""

    def __init__(
        self,
        option: CharacterPickOption,
        highlight_stat: str = "attack",
        parent=None,
    ):
        super().__init__(parent)
        self.option = option
        self._selected = False
        card_w, card_h = fit_player_card_size(_CHARACTER_PICK_CARD_HEIGHT)
        tile_width = card_w + 16
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)
        layout.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setFixedWidth(tile_width)
        self._image = _CharacterPickClickLabel()
        self._image.setFixedSize(card_w, card_h)
        has_pixmap = False
        if option.image_path and Path(option.image_path).is_file():
            pixmap = QPixmap(option.image_path)
            if not pixmap.isNull():
                self._image.set_card_pixmap(pixmap)
                has_pixmap = True
        if not has_pixmap:
            short = (option.label.split("路")[-1].strip() or option.label)[:6]
            self._image.setText(short)
        layout.addWidget(self._image, alignment=Qt.AlignCenter)
        name = QLabel(option.label.split("路")[-1].strip() or option.label)
        name.setAlignment(Qt.AlignCenter)
        name.setWordWrap(True)
        name.setFixedWidth(card_w)
        layout.addWidget(name)
        stats = QHBoxLayout()
        stats.setSpacing(6)
        stats.setAlignment(Qt.AlignCenter)
        atk_color = "#FF8888" if highlight_stat == "attack" else "#aaa"
        def_color = "#B8E0B8" if highlight_stat == "defense" else "#aaa"
        stats.addWidget(self._stat_badge(_ATTACK_ICON, option.attack, atk_color))
        stats.addWidget(self._stat_badge(_DEFENSE_ICON, option.defense, def_color))
        stats.addWidget(QLabel(f"{option.health}"))
        layout.addLayout(stats)
        if option.player_tag:
            tag = QLabel(option.player_tag)
            tag.setAlignment(Qt.AlignCenter)
            tag.setStyleSheet(
                "font-size: 11px; font-weight: bold; color: #003366;"
            )
            layout.addWidget(tag)

    def _stat_badge(self, icon_path: Path, value: int, color: str) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        if icon_path.is_file():
            icon = QLabel()
            icon.setPixmap(
                QPixmap(str(icon_path)).scaled(
                    14, 14, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )
            row.addWidget(icon)
        num = QLabel(str(value))
        num.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 11px;")
        row.addWidget(num)
        return box

    def set_selected(self, selected: bool):
        self._selected = selected
        self._image.set_selected(selected)

    def is_selected(self) -> bool:
        return self._selected


class CharacterImagePickDialog(QDialog):
    """6.8b 攻击者 / 6.4.1 防御者：卡图多选或单选（单击选择，双击取消）。"""

    def __init__(
        self,
        parent,
        title: str,
        prompt: str,
        options: list[CharacterPickOption],
        *,
        mode: str = "multi",
        highlight_stat: str = "attack",
        allow_none: bool = False,
        none_label: str = '（无人防御）',
        min_select: int = 1,
        bottom_preview_options: list[CharacterPickOption] | None = None,
        bottom_preview_prompt: str = "",
        mandatory: bool = False,
    ):
        super().__init__(parent)
        self._mode = mode
        self._min_select = min_select
        self._allow_none = allow_none
        self._mandatory = mandatory
        self._none_selected = allow_none and mode == "single"
        self._tiles: dict[str, _CharacterPickTile] = {}
        self.setWindowTitle(title)
        if mandatory:
            self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        self.setMinimumWidth(min(720, 160 + len(options) * 150))
        layout = QVBoxLayout(self)
        hint = (
            "须完成选择后点「确定」继续。"
            if mandatory
            else '单击卡图选择，双击取消选择：'
        )
        hint += (
            "滚轮或长按左右拖动可横向浏览卡图；"
            '右键双击或长按上下拖动可放大查看卡面：'
        )
        layout.addWidget(QLabel(f"{prompt}\n\n{hint}"))
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        for opt in options:
            tile = _CharacterPickTile(opt, highlight_stat=highlight_stat)
            tile._image.single_clicked.connect(
                lambda t=tile: self._on_single_click(t)
            )
            tile._image.double_clicked.connect(
                lambda t=tile: self._on_double_click(t)
            )
            self._tiles[opt.char_id] = tile
            row.addWidget(tile)
        row.addStretch(1)
        scroll, self._pick_scroller = _setup_horizontal_card_scroll(
            row_widget,
            min_height=_CHARACTER_PICK_CARD_HEIGHT + 72,
        )
        layout.addWidget(scroll)
        if bottom_preview_options:
            if bottom_preview_prompt:
                preview_label = QLabel(bottom_preview_prompt)
                preview_label.setWordWrap(True)
                layout.addWidget(preview_label)
            preview_widget = QWidget()
            preview_row = QHBoxLayout(preview_widget)
            preview_row.setContentsMargins(0, 0, 0, 0)
            preview_row.setSpacing(8)
            preview_row.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            for opt in bottom_preview_options:
                preview_tile = _CharacterPickTile(
                    opt, highlight_stat=highlight_stat
                )
                preview_tile.setEnabled(False)
                preview_row.addWidget(preview_tile)
            preview_row.addStretch(1)
            preview_scroll, self._preview_scroller = _setup_horizontal_card_scroll(
                preview_widget,
                min_height=_CHARACTER_PICK_CARD_HEIGHT + 72,
            )
            layout.addWidget(preview_scroll)
        if allow_none and mode == "single":
            self._none_button = QPushButton(none_label)
            self._none_button.setCheckable(True)
            self._none_button.setChecked(self._none_selected)
            self._none_button.clicked.connect(self._on_none_clicked)
            layout.addWidget(self._none_button)
        _add_prominent_dialog_buttons(
            layout,
            accept_text="确认",
            cancel_text="" if mandatory else "取消",
            on_accept=self._on_accept,
            on_reject=None if mandatory else self.reject,
        )

    def closeEvent(self, event):
        if self._mandatory:
            event.ignore()
            return
        super().closeEvent(event)

    def _on_single_click(self, tile: _CharacterPickTile):
        self._none_selected = False
        if hasattr(self, "_none_button"):
            self._none_button.setChecked(False)
        if self._mode == "multi":
            if not tile.is_selected():
                tile.set_selected(True)
            return
        for t in self._tiles.values():
            t.set_selected(t is tile)

    def _on_double_click(self, tile: _CharacterPickTile):
        if tile.is_selected():
            tile.set_selected(False)
            if self._mode == "single" and self._allow_none:
                self._none_selected = True
                if hasattr(self, "_none_button"):
                    self._none_button.setChecked(True)

    def _on_none_clicked(self):
        self._none_selected = True
        for tile in self._tiles.values():
            tile.set_selected(False)

    def _on_accept(self):
        count = sum(1 for t in self._tiles.values() if t.is_selected())
        if self._mode == "single":
            if count > 1:
                QMessageBox.warning(self, '选择', "只能选择一名角色")
                return
            if count == 0 and not (self._allow_none and self._none_selected):
                if self._min_select <= 0:
                    self.accept()
                    return
                QMessageBox.warning(self, '选择', "请选择一名角色或「无人防守」")
                return
            self.accept()
            return
        if count < self._min_select:
            QMessageBox.warning(
                self, '选择', f"请至少选择 {self._min_select} 名攻击者"
            )
            return
        self.accept()

    def selected_id(self) -> str:
        if self._none_selected:
            return ""
        for char_id, tile in self._tiles.items():
            if tile.is_selected():
                return char_id
        return ""

    def selected_ids(self) -> list[str]:
        return [
            char_id
            for char_id, tile in self._tiles.items()
            if tile.is_selected()
        ]


class AttachTargetDialog(CharacterImagePickDialog):
    """计划环节：选择附属目标（角色、地区或交锋敌军）。"""

    _DEFAULT_CHARACTER_PROMPT = (
        '选择要附属的角色（英雄或盟友）：\n'
        "附属将显示在角色卡牌旁边。\n"
        '宿主派系无需与附属牌一致；仅须满足附属牌上的「附属至…」限制。\n'
        "附属与其宿主独立横置/重整。\n"
        "提示：每名角色最多同时拥有 2 张「限制」附属；"
        '附着第 3 张时须立即将其中一张移入弃牌堆：'
    )

    def __init__(
        self,
        parent,
        targets: list,
        *,
        prompt: str | None = None,
        target_kind: str = "character",
    ):
        options = parent._attachment_target_pick_options(
            targets, kind=target_kind
        )
        highlight = "defense" if target_kind == "enemy" else "attack"
        super().__init__(
            parent,
            "附属目标",
            prompt or self._DEFAULT_CHARACTER_PROMPT,
            options,
            mode="single",
            highlight_stat=highlight,
        )


class CharacterChoiceDialog(QDialog):
    """战斗结算：选择英雄（见 6.4.3 分配伤害）。allow_none 时提供空白选项："""

    NONE_VALUE = "__none__"

    def __init__(
        self,
        parent,
        title: str,
        prompt: str,
        options: list,
        allow_none: bool = False,
        none_label: str = '（无人防御）',
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(prompt))
        self.list_widget = QListWidget()
        if allow_none:
            item = QListWidgetItem(none_label)
            item.setData(Qt.UserRole, self.NONE_VALUE)
            self.list_widget.addItem(item)
        for char_id, label in options:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, char_id)
            self.list_widget.addItem(item)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        layout.addWidget(self.list_widget)
        _add_prominent_dialog_buttons(
            layout, accept_text="确认", on_accept=self._on_accept
        )

    def _on_accept(self):
        if not self.list_widget.currentItem():
            QMessageBox.warning(self, '选择', '请选择一项')
            return
        self.accept()

    def selected_id(self) -> str:
        item = self.list_widget.currentItem()
        if not item:
            return ""
        value = item.data(Qt.UserRole)
        return "" if value == self.NONE_VALUE else value


class LargeChoiceDialog(QDialog):
    """大号文字选项（单击即确认，用于三选一等场景）。"""

    def __init__(
        self,
        parent,
        title: str,
        prompt: str,
        options: list,
        *,
        button_min_height: int = 80,
        font_size: int = 20,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._selected_id = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        prompt_label = QLabel(prompt)
        prompt_label.setWordWrap(True)
        prompt_label.setStyleSheet(
            "font-size: 15px; font-weight: bold; color: #222;"
        )
        layout.addWidget(prompt_label)
        for choice_id, label in options:
            btn = QPushButton(label)
            btn.setMinimumHeight(button_min_height)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton {"
                f"font-size: {font_size}px;"
                "font-weight: bold;"
                "padding: 18px 28px;"
                "border: 2px solid #bbb;"
                "border-radius: 12px;"
                "background-color: #fafafa;"
                "color: #111;"
                "text-align: center;"
                "}"
                "QPushButton:hover {"
                "background-color: #eef5ff;"
                "border: 2px solid #2a7ae2;"
                "}"
                "QPushButton:pressed {"
                "background-color: #dcecff;"
                "padding-top: 20px;"
                "}"
            )
            btn.clicked.connect(
                lambda _, cid=choice_id: self._choose(cid)
            )
            layout.addWidget(btn)

    def _choose(self, choice_id: str) -> None:
        self._selected_id = choice_id
        self.accept()

    def selected_id(self) -> str:
        return self._selected_id


class LargeMultiChoiceDialog(QDialog):
    """大号文字多选（如米茹沃选两项）。"""

    _BTN_STYLE = (
        "QPushButton {"
        "font-size: 20px;"
        "font-weight: bold;"
        "padding: 18px 28px;"
        "border: 2px solid #bbb;"
        "border-radius: 12px;"
        "background-color: #fafafa;"
        "color: #111;"
        "text-align: center;"
        "}"
        "QPushButton:hover {"
        "background-color: #eef5ff;"
        "border: 2px solid #2a7ae2;"
        "}"
        "QPushButton:checked {"
        "background-color: #dcecff;"
        "border: 3px solid #2a7ae2;"
        "}"
    )

    def __init__(
        self,
        parent,
        title: str,
        prompt: str,
        options: list,
        *,
        min_select: int = 2,
        max_select: int = 2,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._min_select = max(1, int(min_select))
        self._max_select = max(self._min_select, int(max_select))
        self._selected: set[str] = set()
        self._buttons: dict[str, QPushButton] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        prompt_label = QLabel(prompt)
        prompt_label.setWordWrap(True)
        prompt_label.setStyleSheet(
            "font-size: 15px; font-weight: bold; color: #222;"
        )
        layout.addWidget(prompt_label)
        hint = QLabel(
            f"请选择 {self._min_select} 项"
            + (
                f"（最多 {self._max_select} 项）"
                if self._max_select != self._min_select
                else ""
            )
        )
        hint.setStyleSheet("font-size: 14px; color: #555;")
        layout.addWidget(hint)
        for choice_id, label in options:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setMinimumHeight(80)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(self._BTN_STYLE)
            btn.toggled.connect(
                lambda checked, cid=choice_id: self._on_toggle(cid, checked)
            )
            self._buttons[choice_id] = btn
            layout.addWidget(btn)
        _add_prominent_dialog_buttons(
            layout, accept_text="确认", on_accept=self._on_accept
        )

    def _on_toggle(self, choice_id: str, checked: bool) -> None:
        btn = self._buttons.get(choice_id)
        if btn is None:
            return
        if checked:
            if len(self._selected) >= self._max_select:
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
                QMessageBox.information(
                    self,
                    '选择',
                    f"最多只能选择 {self._max_select} 项。",
                )
                return
            self._selected.add(choice_id)
        else:
            self._selected.discard(choice_id)

    def _on_accept(self) -> None:
        count = len(self._selected)
        if count < self._min_select:
            QMessageBox.warning(
                self,
                '选择',
                f"请选择 {self._min_select} 项效果。",
            )
            return
        if count > self._max_select:
            QMessageBox.warning(
                self,
                '选择',
                f"最多只能选择 {self._max_select} 项。",
            )
            return
        self.accept()

    def selected_ids(self) -> list[str]:
        return list(self._selected)


class _StatIconPickTile(QWidget):
    """攻击力防御力大图标选项（单击选中）。"""

    clicked = pyqtSignal()

    def __init__(
        self,
        stat_id: str,
        icon_path: Path,
        amount: int,
        *,
        icon_size: int = 88,
        show_amount: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.stat_id = stat_id
        self._selected = False
        self.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignCenter)
        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignCenter)
        if icon_path.is_file():
            icon_label.setPixmap(
                QPixmap(str(icon_path)).scaled(
                    icon_size,
                    icon_size,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
        layout.addWidget(icon_label)
        if show_amount:
            amount_label = QLabel(f"+{amount}")
            amount_label.setAlignment(Qt.AlignCenter)
            amount_label.setStyleSheet(
                "font-size: 18px; font-weight: bold; color: #222;"
            )
            layout.addWidget(amount_label)
            self.setFixedSize(icon_size + 56, icon_size + 52)
        else:
            self.setFixedSize(icon_size + 36, icon_size + 36)
        self._update_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def set_selected(self, selected: bool):
        self._selected = selected
        self._update_style()

    def _update_style(self):
        if self._selected:
            self.setStyleSheet(
                "border: 3px solid #2a7ae2; border-radius: 10px;"
                " background-color: #eef5ff;"
            )
        else:
            self.setStyleSheet(
                "border: 2px solid #bbb; border-radius: 10px;"
                " background-color: white;"
            )


class StatIconPickDialog(QDialog):
    """选择攻击力或防御力加成（大图标）："""

    def __init__(
        self,
        parent,
        title: str,
        prompt: str,
        options: list[tuple[str, Path, int]],
        *,
        icon_size: int = 88,
        show_amount: bool = True,
        empty_warning: str = '请选择攻击力或防御力',
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._selected_id = ""
        self._empty_warning = empty_warning
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(prompt))
        row = QHBoxLayout()
        row.setSpacing(28)
        row.setAlignment(Qt.AlignCenter)
        self._tiles: list[_StatIconPickTile] = []
        for stat_id, icon_path, amount in options:
            tile = _StatIconPickTile(
                stat_id,
                icon_path,
                amount,
                icon_size=icon_size,
                show_amount=show_amount,
            )
            tile.clicked.connect(
                lambda sid=stat_id: self._on_tile_clicked(sid)
            )
            row.addWidget(tile)
            self._tiles.append(tile)
        layout.addLayout(row)
        _add_prominent_dialog_buttons(
            layout, accept_text="确认", on_accept=self._on_accept
        )
        if options:
            self._on_tile_clicked(options[0][0])

    def _on_tile_clicked(self, stat_id: str):
        self._selected_id = stat_id
        for tile in self._tiles:
            tile.set_selected(tile.stat_id == stat_id)

    def _on_accept(self):
        if not self._selected_id:
            QMessageBox.warning(self, '选择', self._empty_warning)
            return
        self.accept()

    def selected_id(self) -> str:
        return self._selected_id


class FirstPlayerPickDialog(QDialog):
    """多人局面：指向玩家按钮选择起始玩家。"""

    def __init__(
        self,
        parent,
        player_count: int,
        player_colors: tuple[str, ...] = (),
    ):
        super().__init__(parent)
        self.setWindowTitle("起始玩家")
        self._picked: int | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "选择本局起始玩家（仅选择一次；\n"
                "之后每回合恢复环节结束时自动传递标记）。"
            )
        )
        row = QHBoxLayout()
        row.setSpacing(10)
        for i in range(player_count):
            btn = QPushButton(f"玩家{i + 1}")
            btn.setFixedSize(96, 72)
            color = (
                player_colors[i]
                if i < len(player_colors)
                else "#0078d4"
            )
            btn.setStyleSheet(
                "QPushButton {"
                f"background-color: {color};"
                "color: white;"
                "font-size: 18px;"
                "font-weight: bold;"
                "border: 2px solid #aaa;"
                "border-radius: 8px;"
                "}"
                "QPushButton:hover {"
                "border: 2px solid white;"
                "}"
                "QPushButton:pressed {"
                "padding-top: 4px;"
                "}"
            )
            btn.clicked.connect(lambda _, idx=i: self._choose(idx))
            row.addWidget(btn)
        layout.addLayout(row)

    def _choose(self, player_index: int) -> None:
        self._picked = player_index
        self.accept()

    def picked_index(self) -> int | None:
        return self._picked


class ExperienceModePickDialog(QDialog):
    """本局首回合：选择新手 / 熟练模式（大按钮）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择游戏模式（仅本局首次）")
        self._mode: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        intro = QLabel(
            "请选择本局游戏模式（仅本局首次，可随时通过流程条「切换」查看详细流程）："
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("font-size: 14px;")
        layout.addWidget(intro)

        row = QHBoxLayout()
        row.setSpacing(16)
        beginner_btn = self._make_mode_button(
            '新手模式',
            "保留每个行动窗口的提示弹窗\n手动点击「下一阶段」推进",
            "#27ae60",
            "beginner",
        )
        expert_btn = self._make_mode_button(
            '熟练模式',
            "行动窗口提示静默（仅记日志）\n流程条显示宏观环节并自动跳过行动窗口",
            "#c8a44a",
            "expert",
        )
        row.addWidget(beginner_btn)
        row.addWidget(expert_btn)
        layout.addLayout(row)

    def _make_mode_button(
        self, title: str, desc: str, color: str, mode: str
    ) -> QPushButton:
        btn = QPushButton(f"{title}\n\n{desc}")
        btn.setMinimumSize(240, 132)
        btn.setStyleSheet(
            "QPushButton {"
            f"background-color: {color};"
            "color: white;"
            "font-size: 16px;"
            "font-weight: bold;"
            "border: 2px solid #aaa;"
            "border-radius: 10px;"
            "padding: 10px;"
            "text-align: center;"
            "}"
            "QPushButton:hover { border: 2px solid white; }"
            "QPushButton:pressed { padding-top: 12px; }"
        )
        btn.clicked.connect(lambda _, m=mode: self._choose(m))
        return btn

    def _choose(self, mode: str) -> None:
        self._mode = mode
        self.accept()

    def picked_mode(self) -> str | None:
        return self._mode


@dataclass
class CardPickOption:
    card_id: str
    label: str
    image_path: str


class CardPickDialog(QDialog):
    """手牌选择对话框：列表单选。"""

    def __init__(
        self,
        parent,
        title: str,
        prompt: str,
        options: list[CardPickOption],
        *,
        mode: str = "single",
    ):
        super().__init__(parent)
        self._mode = mode
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(prompt))
        self._list = QListWidget()
        for opt in options:
            item = QListWidgetItem(opt.label)
            item.setData(Qt.UserRole, opt.card_id)
            self._list.addItem(item)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)
        self._list.itemDoubleClicked.connect(lambda _: self.accept())
        layout.addWidget(self._list)
        _add_prominent_dialog_buttons(
            layout,
            accept_text="确认",
            cancel_text="取消",
            on_accept=self.accept,
            on_reject=self.reject,
        )

    def selected_id(self) -> str:
        item = self._list.currentItem()
        if item is None:
            return ""
        return item.data(Qt.UserRole) or ""


class _ResourceReferencePickTile(QWidget):
    """参考资源标记：单击 +1、双击 -1（与英雄资源池标记同款图标）。"""

    value_changed = pyqtSignal()

    def __init__(
        self,
        *,
        min_value: int = 0,
        max_value: int = 10,
        default_value: int | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._min_value = int(min_value)
        self._max_value = int(max_value)
        if default_value is None:
            default_value = min(3, self._max_value)
        self._value = max(
            self._min_value, min(int(default_value), self._max_value)
        )
        self.setFixedSize(96, 96)
        self.setCursor(Qt.PointingHandCursor)
        self._token = TokenStatOverlayLabel(self, size=56, marker_key="Resource")
        self._token.move(20, 6)
        self._hint = QLabel('单击 +1 · 双击 -1', self)
        self._hint.setAlignment(Qt.AlignCenter)
        self._hint.setGeometry(0, 68, 96, 24)
        self._hint.setStyleSheet("color: #666; font-size: 11px;")
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._emit_single_click)
        self._suppress_single = False
        self._refresh_display()

    def value(self) -> int:
        return self._value

    def _refresh_display(self):
        self._token.set_visible_count(self._value, True)

    def _emit_single_click(self):
        if self._suppress_single:
            return
        if self._value < self._max_value:
            self._value += 1
            self._refresh_display()
            self.value_changed.emit()

    def mouseReleaseEvent(self, event):
        if (
            event.button() == Qt.LeftButton
            and self.rect().contains(event.pos())
            and not self._suppress_single
        ):
            self._click_timer.start(QApplication.doubleClickInterval())
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._click_timer.stop()
            self._suppress_single = True
            if self._value > self._min_value:
                self._value -= 1
                self._refresh_display()
                self.value_changed.emit()
            QTimer.singleShot(
                QApplication.doubleClickInterval(),
                self._clear_suppress_single,
            )
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _clear_suppress_single(self):
        self._suppress_single = False


class PickXDialog(QDialog):
    """通用：指定整数 X（参考资源标记：单击 +1，双击 -1）。"""

    def __init__(
        self,
        parent,
        title: str,
        prompt: str,
        max_value: int,
        *,
        min_value: int = 1,
        default_value: int | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(prompt))
        self._pick_tile = _ResourceReferencePickTile(
            min_value=min_value,
            max_value=max_value,
            default_value=default_value,
        )
        tile_row = QHBoxLayout()
        tile_row.addStretch()
        tile_row.addWidget(self._pick_tile)
        tile_row.addStretch()
        layout.addLayout(tile_row)
        self._total_label = QLabel()
        self._total_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._total_label)
        self._pick_tile.value_changed.connect(self._update_total_label)
        self._update_total_label()
        _add_prominent_dialog_buttons(
            layout,
            accept_text="确认",
            cancel_text="取消",
            on_accept=self.accept,
            on_reject=self.reject,
        )

    def _update_total_label(self):
        self._total_label.setText(f"指定数字：{self._pick_tile.value()}")

    def selected_value(self) -> int:
        return self._pick_tile.value()


def pick_x_value(
    parent,
    title: str,
    prompt: str,
    max_value: int,
    *,
    min_value: int = 1,
    default_value: int | None = None,
) -> int | None:
    """弹出通用 X 选择窗口；max_value < min_value 时返回 None。"""
    if max_value < min_value:
        return None
    dlg = PickXDialog(
        parent,
        title,
        prompt,
        max_value,
        min_value=min_value,
        default_value=default_value,
    )
    if dlg.exec_() != QDialog.Accepted:
        return None
    return dlg.selected_value()


def _exec_dismiss_dialog(dlg: QDialog):
    """非模态显示弹窗并阻塞直到关闭（NonModal 下 exec_ 会立即返回）："""
    loop = QEventLoop()
    dlg.finished.connect(loop.quit)
    dlg.show()
    loop.exec_()


def _parent_dismiss_event(dlg: QDialog, obj, event) -> bool:
    """主窗口点击或最小化时触发自动确认；返回 True 表示已消费事件。"""
    if not getattr(dlg, "_dismiss_ready", False) or not dlg.isVisible():
        return False
    parent = dlg.parent()
    if parent is None:
        return False
    et = event.type()
    if et == QEvent.MouseButtonPress and (
        obj is parent or parent.isAncestorOf(obj)
    ):
        dlg._auto_accept()
        return True
    if et == QEvent.WindowStateChange and obj is parent:
        if parent.windowState() & Qt.WindowMinimized:
            dlg._auto_accept()
    return False


class ClickDismissDialog(QDialog):
    """紧急提示框：失焦、最小化或点击任意处即自动确认。"""

    def __init__(self, parent, title: str, text: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._dismiss_ready = False
        self._focus_hook = None
        self._parent_click_filter = parent is not None
        if self._parent_click_filter:
            parent.installEventFilter(self)
        self.setMinimumWidth(460)
        self.setMaximumWidth(520)
        self.setWindowModality(Qt.NonModal)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(12)
        layout.setSpacing(6)
        body = f"{text}\n\n（切换焦点或最小化后自动确认）"
        label = QLabel(body)
        label.setWordWrap(True)
        label.setMaximumWidth(340)
        layout.addWidget(label)
        _add_prominent_dialog_buttons(
            layout, accept_text="确认", on_accept=self.accept
        )
        app = QApplication.instance()
        if app is not None:
            self._focus_hook = self._on_focus_changed
            app.focusChanged.connect(self._focus_hook)
        QTimer.singleShot(120, self._arm_dismiss)

    def _arm_dismiss(self):
        self._dismiss_ready = True

    def _on_focus_changed(self, old, new):
        if not self._dismiss_ready or not self.isVisible():
            return
        if old is None:
            return
        if old.window() == self and (new is None or new.window() != self):
            self._auto_accept()

    def _auto_accept(self):
        if self._dismiss_ready and self.isVisible():
            self.accept()

    def eventFilter(self, obj, event):
        if self._parent_click_filter and self.isVisible():
            consumed = _parent_dismiss_event(self, obj, event)
            if consumed:
                return True
        return False

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._dismiss_ready:
            self.accept()
        super().mousePressEvent(event)

    def _cleanup(self):
        app = QApplication.instance()
        if app is not None and self._focus_hook is not None:
            app.focusChanged.disconnect(self._focus_hook)
            self._focus_hook = None
        if self._parent_click_filter and self.parent() is not None:
            self.parent().removeEventFilter(self)

    def accept(self):
        self._cleanup()
        super().accept()

    def reject(self):
        self._cleanup()
        super().reject()

    def changeEvent(self, event):
        if event.type() == QEvent.WindowStateChange:
            if self.windowState() & Qt.WindowMinimized:
                self._auto_accept()
        elif event.type() == QEvent.WindowDeactivate:
            if self._dismiss_ready and self.isVisible():
                self._auto_accept()
        super().changeEvent(event)


class ClickDismissQuestionDialog(QDialog):
    """是非提示：失焦或最小化时按默认选项自动确认。"""

    def __init__(
        self,
        parent,
        title: str,
        text: str,
        default_yes: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._result = QMessageBox.Yes if default_yes else QMessageBox.No
        self._dismiss_ready = False
        self._focus_hook = None
        self._parent_click_filter = parent is not None
        if self._parent_click_filter:
            parent.installEventFilter(self)
        self.setMinimumWidth(460)
        self.setMaximumWidth(520)
        self.setWindowModality(Qt.NonModal)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(12)
        default_hint = '是' if default_yes else "否"
        body = (
            f"{text}\n\n"
            f"（切换焦点或最小化后自动选择「{default_hint}」）"
        )
        label = QLabel(body)
        label.setWordWrap(True)
        label.setMaximumWidth(480)
        layout.addWidget(label)
        row = QHBoxLayout()
        row.setContentsMargins(0, 14, 0, 4)
        row.setSpacing(14)
        row.addStretch(1)
        no_btn = QPushButton("否")
        _style_prominent_button(no_btn, primary=False)
        no_btn.clicked.connect(self._choose_no)
        row.addWidget(no_btn)
        yes_btn = QPushButton('是')
        _style_prominent_button(yes_btn, primary=True)
        yes_btn.clicked.connect(self._choose_yes)
        row.addWidget(yes_btn)
        layout.addLayout(row)
        app = QApplication.instance()
        if app is not None:
            self._focus_hook = self._on_focus_changed
            app.focusChanged.connect(self._focus_hook)
        QTimer.singleShot(120, self._arm_dismiss)

    def _arm_dismiss(self):
        self._dismiss_ready = True

    def _on_focus_changed(self, old, new):
        if not self._dismiss_ready or not self.isVisible():
            return
        if old is None:
            return
        if old.window() == self and (new is None or new.window() != self):
            self._auto_accept()

    def _auto_accept(self):
        if self._dismiss_ready and self.isVisible():
            self.accept()

    def eventFilter(self, obj, event):
        if self._parent_click_filter and self.isVisible():
            consumed = _parent_dismiss_event(self, obj, event)
            if consumed:
                return True
        return False

    def _cleanup(self):
        app = QApplication.instance()
        if app is not None and self._focus_hook is not None:
            app.focusChanged.disconnect(self._focus_hook)
            self._focus_hook = None
        if self._parent_click_filter and self.parent() is not None:
            self.parent().removeEventFilter(self)

    def _choose_yes(self):
        self._result = QMessageBox.Yes
        self.accept()

    def _choose_no(self):
        self._result = QMessageBox.No
        self.accept()

    def result_code(self) -> int:
        return self._result

    def accept(self):
        self._cleanup()
        super().accept()

    def reject(self):
        self._cleanup()
        super().reject()

    def changeEvent(self, event):
        if event.type() == QEvent.WindowStateChange:
            if self.windowState() & Qt.WindowMinimized:
                self._auto_accept()
        elif event.type() == QEvent.WindowDeactivate:
            if self._dismiss_ready and self.isVisible():
                self._auto_accept()
        super().changeEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.accept()
        super().mousePressEvent(event)


class ModalQuestionDialog(QDialog):
    """模态是非提示（战斗响应等；须手动点选，不因失焦自动关闭）。"""

    def __init__(
        self,
        parent,
        title: str,
        text: str,
        default_yes: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._result = QMessageBox.Yes if default_yes else QMessageBox.No
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        label = QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label)
        row = QHBoxLayout()
        row.setContentsMargins(0, 14, 0, 4)
        row.setSpacing(12)
        row.addStretch(1)
        no_btn = QPushButton("否")
        _style_prominent_button(no_btn, primary=False)
        no_btn.setMinimumSize(120, 52)
        no_btn.clicked.connect(self._choose_no)
        row.addWidget(no_btn)
        yes_btn = QPushButton('是')
        _style_prominent_button(yes_btn, primary=True)
        yes_btn.setMinimumSize(144, 56)
        yes_btn.clicked.connect(self._choose_yes)
        row.addWidget(yes_btn)
        layout.addLayout(row)
        (yes_btn if default_yes else no_btn).setDefault(True)
        (yes_btn if default_yes else no_btn).setFocus()

    def _choose_yes(self):
        self._result = QMessageBox.Yes
        self.accept()

    def _choose_no(self):
        self._result = QMessageBox.No
        self.reject()

    def result_code(self) -> int:
        return self._result


class CharacterGroupWidget(QWidget):
    """英雄/盟友与其附属横向排列，并用红线连接。"""

    LINK_COLOR = QColor("#D02020")
    LINK_WIDTH = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._host_widget = None
        self._attachment_widgets: list = []
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self._layout.setAlignment(Qt.AlignBottom)

    def set_host(self, widget: QWidget):
        self._host_widget = widget
        self._layout.addWidget(widget)

    def add_attachment(self, widget: QWidget):
        self._attachment_widgets.append(widget)
        self._layout.addWidget(widget)
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.update)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._host_widget or not self._attachment_widgets:
            return
        host_rect = self._host_widget.geometry()
        if host_rect.isEmpty():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(self.LINK_COLOR, self.LINK_WIDTH, Qt.SolidLine, Qt.RoundCap))

        prev_right = host_rect.right()
        prev_y = host_rect.center().y()
        for att in self._attachment_widgets:
            att_rect = att.geometry()
            if att_rect.isEmpty():
                continue
            x1 = prev_right + 1
            x2 = att_rect.left() - 1
            y2 = att_rect.center().y()
            painter.drawLine(x1, prev_y, x2, y2)
            prev_right = att_rect.right()
            prev_y = y2
        painter.end()


class DiscardPilePanel(QFrame):
    """弃牌堆摘要：单击打开完整列表："""

    clicked = pyqtSignal()

    def __init__(self, title: str = '弃牌堆', card_kind: str = "player", parent=None):
        super().__init__(parent)
        self._title = title
        self._card_kind = card_kind
        self._preview_series = None
        self._count = 0
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("""
            DiscardPilePanel {
                border: 1px dashed #888;
                background-color: #f9f9f9;
            }
            DiscardPilePanel:hover {
                background-color: #eef4ff;
                border-color: #6688cc;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
        self._count_label = QLabel(f"{title} (0)")
        self._count_label.setAlignment(Qt.AlignCenter)
        self._count_label.setStyleSheet("font-size: 12px; font-weight: bold; color: #444;")
        layout.addWidget(self._count_label)
        self._hint_label = QLabel('单击查看')
        self._hint_label.setAlignment(Qt.AlignCenter)
        self._hint_label.setStyleSheet("font-size: 11px; color: #666;")
        layout.addWidget(self._hint_label)
        self._top_slot = QVBoxLayout()
        self._top_slot.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        layout.addLayout(self._top_slot)
        layout.addStretch()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def set_count(self, count: int):
        self._count = count
        self._count_label.setText(f"{self._title} ({count})")

    def set_title(self, title: str):
        self._title = title
        count = getattr(self, "_count", 0)
        self._count_label.setText(f"{self._title} ({count})")

    def set_top_card(self, card, series=None):
        if series is not None:
            self._preview_series = series
        self._clear_top_slot()
        if card is None:
            empty = QLabel("（空）")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("font-size: 11px; color: #999;")
            self._top_slot.addWidget(empty)
            return
        if self._card_kind == "encounter":
            widget = self._encounter_preview_label(
                card, self._preview_series, max_height=100
            )
        else:
            widget = PlayerCardWidget(
                card_name=card.name,
                series=card.series,
                max_height=100,
                restore_markers=False,
            )
        self._top_slot.addWidget(widget, alignment=Qt.AlignHCenter)

    @staticmethod
    def _encounter_preview_label(card, series=None, max_height: int = 100):
        label = QLabel()
        label.setAlignment(Qt.AlignCenter)
        path = getattr(card, "image_path", "") or ""
        if path and Path(path).is_file():
            pix = QPixmap(path)
            if not pix.isNull():
                label._hover_card_pixmap = pix
                label._hover_card_face_up = True
                label.setPixmap(
                    pix.scaledToHeight(max_height, Qt.SmoothTransformation)
                )
                return label
        if series:
            widget = EncounterCardWidget(
                card_name=card.name,
                series=series,
                show_threat_badge=False,
                restore_markers=False,
            )
            return widget
        label.setText(getattr(card, "name", "?"))
        label.setStyleSheet("font-size: 11px; color: #333;")
        return label

    def _clear_top_slot(self):
        while self._top_slot.count():
            item = self._top_slot.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()


class DiscardPileViewDialog(QDialog):
    """单击后查看弃牌堆全部卡牌（顶牌在左）："""

    def __init__(
        self,
        parent,
        discard_cards: list,
        title: str = "玩家弃牌堆",
        card_kind: str = "player",
        series=None,
        play_callback=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"共 {len(discard_cards)} 张 · 左为顶牌 · 滚轮或长按左右拖动可浏览 · 单击关闭"
            if discard_cards else f"{title}为空"
        ))
        if discard_cards:
            container = QWidget()
            bar = QHBoxLayout(container)
            bar.setContentsMargins(0, 0, 0, 0)
            bar.setSpacing(8)
            bar.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            for card in reversed(discard_cards):
                if card_kind == "encounter":
                    if isinstance(card, PlayerCard) and getattr(card, "image_path", ""):
                        bar.addWidget(PlayerCardWidget(
                            card_name=card.name,
                            series=card.series,
                            max_height=160,
                            restore_markers=False,
                        ))
                    else:
                        # 优先使用 PlayerCardWidget 显示玩家卡牌（如事件卡）
                        if isinstance(card, PlayerCard):
                            bar.addWidget(PlayerCardWidget(
                                card_name=card.name,
                                series=getattr(card, "series", "") or series,
                                max_height=160,
                                restore_markers=False,
                            ))
                        else:
                            bar.addWidget(
                                DiscardPilePanel._encounter_preview_label(
                                    card,
                                    series=getattr(card, "series", "") or series,
                                    max_height=160,
                                )
                            )
                else:
                    bar.addWidget(PlayerCardWidget(
                        card_name=card.name,
                        series=card.series,
                        max_height=160,
                        restore_markers=False,
                    ))
            bar.addStretch(1)
            min_h = 240 if card_kind == "encounter" else 200
            scroll, self._discard_scroller = _setup_horizontal_card_scroll(
                container,
                min_height=min_h,
                min_viewport_width=520,
            )
            layout.addWidget(scroll)
        if play_callback is not None:
            row = QHBoxLayout()
            row.setContentsMargins(0, 10, 0, 4)
            row.addStretch(1)
            play_btn = QPushButton("打出弃牌堆的卡牌")
            _style_prominent_button(play_btn, primary=False)
            play_btn.clicked.connect(
                lambda: (self.accept(), play_callback())
            )
            row.addWidget(play_btn)
            close_btn = QPushButton("关闭")
            _style_prominent_button(close_btn, primary=True)
            close_btn.clicked.connect(self.accept)
            row.addWidget(close_btn)
            layout.addLayout(row)
        else:
            _add_prominent_dialog_buttons(
                layout, accept_text='关闭', on_accept=self.accept
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.accept()
        super().mousePressEvent(event)


class ThreatValueLabel(QLabel):
    """黄色威胁数字，无描边。"""

    def __init__(self, parent=None, font_size: int = 18):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        font = QFont()
        font.setPointSize(font_size)
        font.setWeight(QFont.DemiBold)
        self.setFont(font)

    def paintEvent(self, _event):  # noqa  # type: ignore
        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setPen(QColor("#FFD700"))
        painter.setFont(self.font())
        painter.drawText(self.rect(), Qt.AlignCenter, self.text())


class ThreatDialWidget(QWidget):
    """威胁转盘：底图 + 中央黄色威胁值；探险环节在转盘下方显示放大的意志/威胁徽章。"""

    def __init__(self, threat_level=30, parent=None):
        super().__init__(parent)
        self._threat_level = threat_level
        dial_h = THREAT_DIAL_OUTER_HEIGHT
        self._dial_slot_height = dial_h
        self._outer_height = dial_h + THREAT_DIAL_BADGE_BAND
        self._outer_width = int(dial_h * 358 / 181)
        self._dial_height = int(dial_h * THREAT_DIAL_IMAGE_HEIGHT_RATIO)
        self._dial_width = int(self._dial_height * 358 / 181)
        self._dial_scale = self._dial_height / 120.0
        self._outer_scale = dial_h / 120.0
        self.setFixedSize(self._outer_width, self._outer_height)
        self.setStyleSheet("background: transparent; border: none;")

        self._image_label = QLabel(self)
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setStyleSheet("background: transparent; border: none;")

        self._value_label = ThreatValueLabel(
            self, font_size=max(10, int(18 * self._dial_scale))
        )
        self._value_label.setAlignment(Qt.AlignCenter)

        self._will_badge, self._will_count_label = self._make_icon_badge(
            "#9ADCF9", WILLPOWER_ICON, "W"
        )
        self._threat_badge, self._threat_count_label = self._make_icon_badge(
            "#FFAA66", THREAT_ICON, "T"
        )
        self._will_badge.hide()
        self._threat_badge.hide()

        self._load_image()
        self._position_dial_image()
        self.set_threat_level(threat_level)
        self._position_value_label()
        self._position_quest_badges()

    def _make_icon_badge(self, color: str, icon_path: Path, fallback: str):
        s = getattr(self, "_dial_scale", 1.0)
        so = getattr(self, "_outer_scale", 1.0)
        badge = QWidget(self)
        br = max(3, int(4 * so))
        badge.setStyleSheet(f"background-color: rgba(0, 0, 0, 0.65); border-radius: {br}px;")
        badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout = QHBoxLayout(badge)
        m = max(2, int(4 * so))
        layout.setContentsMargins(m, 0, m, 0)
        layout.setSpacing(max(2, int(3 * so)))
        layout.setAlignment(Qt.AlignCenter)
        count_label = QLabel("0")
        count_label.setAlignment(Qt.AlignCenter)
        fs = max(10, int(13 * s))
        count_label.setStyleSheet(
            f"color: {color}; font-weight: bold; font-size: {fs}px; "
            "background: transparent; border: none;"
        )
        icon = MarkerLabel(pixmap_path=icon_path, fallback=fallback, color=color)
        icon_sz = max(12, int(16 * s))
        icon.setFixedSize(icon_sz, icon_sz)
        icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(count_label)
        layout.addWidget(icon)
        return badge, count_label

    def _position_quest_badges(self):
        dial_rect = self._image_label.geometry()
        if dial_rect.isEmpty():
            dial_rect = QRect(0, 0, self._dial_width, self._dial_height)
        so = getattr(self, "_outer_scale", 1.0)
        badge_w = max(40, int(self.width() * THREAT_DIAL_BADGE_WIDTH_RATIO))
        badge_h = max(20, int(THREAT_DIAL_BADGE_BAND * THREAT_DIAL_BADGE_HEIGHT_RATIO))
        spacing = max(4, int(6 * so))
        gap = max(1, int(2 * so))
        total_w = badge_w * 2 + spacing
        start_x = dial_rect.center().x() - total_w // 2
        y = dial_rect.bottom() + gap
        y = min(y, self.height() - badge_h)
        self._will_badge.setFixedSize(badge_w, badge_h)
        self._threat_badge.setFixedSize(badge_w, badge_h)
        self._will_badge.move(start_x, y)
        self._threat_badge.move(start_x + badge_w + spacing, y)
        self._will_badge.raise_()
        self._threat_badge.raise_()
        self._value_label.raise_()
        self._image_label.lower()

    def _position_dial_image(self):
        """红色威胁转盘底图贴齐外框顶部（转盘区），徽章带在其下方独立排列。"""
        if not self._image_label.pixmap() or self._image_label.pixmap().isNull():
            return
        ow = self.width()
        dw, dh = self._dial_width, self._dial_height
        slot_h = getattr(self, "_dial_slot_height", THREAT_DIAL_OUTER_HEIGHT)
        x = (ow - dw) // 2
        y = max(0, min(int(slot_h * THREAT_DIAL_IMAGE_V_OFFSET), slot_h - dh))
        self._image_label.setGeometry(x, y, dw, dh)

    def set_quest_summary(self, will_total: int, staging_threat: int, visible: bool):
        """探查环节：转盘下方居中显示总意志与探查区威胁（放大徽章）。"""
        self._will_badge.setVisible(visible)
        self._threat_badge.setVisible(visible)
        if visible:
            self._will_count_label.setText(str(will_total))
            self._threat_count_label.setText(str(staging_threat))
            self._position_quest_badges()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_dial_image()
        self._position_value_label()
        self._position_quest_badges()

    def _load_image(self):
        if not THREAT_DIAL_IMAGE.is_file():
            self._image_label.setText("威胁转盘")
            self._image_label.setStyleSheet("color: #666;")
            return
        pixmap = QPixmap(str(THREAT_DIAL_IMAGE))
        scaled = pixmap.scaled(
            self._dial_width,
            self._dial_height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)
        self._image_label.resize(self._dial_width, self._dial_height)

    def _position_value_label(self):
        # 数字中心对齐红色威胁转盘底图的几何中心（而非外框），以匹配转盘刻度区
        dial_rect = self._image_label.geometry()
        if dial_rect.isEmpty() or not self._image_label.pixmap():
            dial_rect = self.rect()
        cx = dial_rect.center().x()
        cy = dial_rect.center().y()
        s = getattr(self, "_dial_scale", 1.0)
        box_w, box_h = int(56 * s), int(36 * s)
        x = cx - box_w // 2
        y = cy - box_h // 2
        self._value_label.setGeometry(x, y, box_w, box_h)

    def threat_level(self):
        return self._threat_level

    def set_threat_level(self, value):
        self._threat_level = int(value)
        self._value_label.setText(str(self._threat_level))
        self._position_value_label()
        self._value_label.raise_()


@dataclass
class PlayerState:
    """主控制：每位玩家的独立游戏状态。"""
    index: int
    hand_cards: list = field(default_factory=list)
    ally_cards: list = field(default_factory=list)
    discard_cards: list = field(default_factory=list)
    encounter_set_aside_cards: list = field(default_factory=list)
    removed_from_game_cards: list = field(default_factory=list)
    engagement_cards: list = field(default_factory=list)
    attachments: dict = field(default_factory=dict)
    hero_resources: dict = field(default_factory=dict)
    threat_level: int = 0
    initial_threat_level: int = 0
    mulligan_used: bool = False
    drawer: PlayerCardDrawer | None = None
    deck_path: str | None = None
    deck_text: str | None = None


class _StdoutTee:
    """将 print 输出同时写入终端与内存缓冲，供日志窗口显示。"""

    def __init__(self, original):
        self._original = original
        self._chunks: list[str] = []

    def write(self, text: str):
        if not text:
            return
        if self._original is not None:
            self._original.write(text)
            self._original.flush()
        self._chunks.append(text)

    def flush(self):
        if self._original is not None:
            self._original.flush()

    def get_text(self) -> str:
        return ''.join(self._chunks)

    def clear(self):
        self._chunks.clear()

    def __getattr__(self, name):
        return getattr(self._original, name)


class GameLogDialog(QDialog):
    """游戏运行日志（终端 print 输出）。"""

    def __init__(self, get_text_fn, clear_fn, parent=None):
        super().__init__(parent)
        self._get_text = get_text_fn
        self._clear = clear_fn
        self.setWindowTitle('游戏日志')
        self.setMinimumSize(560, 360)
        self.resize(720, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._log_view.setStyleSheet(
            "QPlainTextEdit {"
            "background-color: #1e1e1e; color: #d4d4d4;"
            "font-family: Consolas, 'Courier New', monospace;"
            "font-size: 12px; border: 1px solid #444;"
            "}"
        )
        layout.addWidget(self._log_view, 1)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        clear_btn = QPushButton('清空')
        close_btn = QPushButton('关闭')
        refresh_btn.clicked.connect(self.refresh)
        clear_btn.clicked.connect(self._on_clear)
        close_btn.clicked.connect(self.close)
        btn_row.addStretch()
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        self.refresh()

    def _scroll_to_bottom(self):
        bar = self._log_view.verticalScrollBar()
        bar.setValue(bar.maximum())
        cursor = self._log_view.textCursor()
        cursor.movePosition(cursor.End)
        self._log_view.setTextCursor(cursor)

    def refresh(self):
        self._log_view.setPlainText(self._get_text())
        QTimer.singleShot(0, self._scroll_to_bottom)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _on_clear(self):
        if QMessageBox.question(
            self,
            '清空日志',
            "确认清空当前游戏日志？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self._clear()
        self.refresh()


class TitleBarWidget(QFrame):
    """无边框窗口标题栏：左侧放应用操作按钮，右侧保留窗口控制。"""

    def __init__(self, parent=None, title: str = "魔戒 LCG"):
        super().__init__(parent)
        self._drag_pos = None
        self.setObjectName("customTitleBar")
        self.setFixedHeight(42)
        self.setStyleSheet("""
            QFrame#customTitleBar {
                background-color: #2a2a2a;
                border-bottom: 1px solid #444;
            }
            QLabel#titleText {
                color: white;
                font-size: 13px;
                font-weight: bold;
            }
            QLabel#titleHint {
                color: #cccccc;
                font-size: 12px;
            }
            QPushButton {
                background-color: #3a3a3a;
                color: #eeeeee;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 3px 8px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
            }
            QPushButton:disabled {
                color: #777777;
                background-color: #2f2f2f;
            }
            QSpinBox {
                background-color: #f5f5f5;
                color: #222;
                border: 1px solid #777;
                border-radius: 3px;
                min-height: 24px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 6, 4)
        layout.setSpacing(6)

        title_label = QLabel(title)
        title_label.setObjectName("titleText")
        layout.addWidget(title_label)

        self.tool_layout = QHBoxLayout()
        self.tool_layout.setContentsMargins(0, 0, 0, 0)
        self.tool_layout.setSpacing(6)
        layout.addLayout(self.tool_layout)
        layout.addStretch()

        self.min_button = self._make_window_button("−", '最小化')
        self.max_button = self._make_window_button("□", '最大化/还原')
        self.close_button = self._make_window_button("×", '关闭')
        self.close_button.setStyleSheet("""
            QPushButton {
                background-color: #5a2a2a;
                color: white;
                border: 1px solid #744;
                border-radius: 3px;
                padding: 3px 8px;
            }
            QPushButton:hover {
                background-color: #c42b1c;
            }
        """)
        layout.addWidget(self.min_button)
        layout.addWidget(self.max_button)
        layout.addWidget(self.close_button)

        self.min_button.clicked.connect(self.window().showMinimized)
        self.max_button.clicked.connect(self._toggle_max_restore)
        self.close_button.clicked.connect(self.window().close)

    def _make_window_button(self, text: str, tooltip: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedSize(32, 26)
        btn.setToolTip(tooltip)
        btn.setFocusPolicy(Qt.NoFocus)
        return btn

    def _toggle_max_restore(self):
        win = self.window()
        if win.isMaximized():
            win.showNormal()
            self.max_button.setText("□")
        else:
            win.showMaximized()
            self.max_button.setText("❐")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.window().frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and self._drag_pos is not None:
            if self.window().isMaximized():
                return
            self.window().move(event.globalPos() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._toggle_max_restore()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class _PhaseFlowNodeLabel(QLabel):
    """流程条单层阶段节点：▲ 文字上方，可点击（仅行动节点），悬停高亮。"""

    clicked = pyqtSignal(str)

    def __init__(self, node_id: str, text: str, clickable: bool, parent=None):
        super().__init__(parent)
        self.node_id = node_id
        self.node_text = text
        self.clickable = clickable
        self._hovered = False
        self.setTextFormat(Qt.RichText)
        self.setAlignment(Qt.AlignCenter)
        self.setText(f"▲<br/>{text}")
        if clickable:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip("点击标红：到达该行动窗口时自动跳过")

    def enterEvent(self, event):
        self._hovered = True
        parent = self.parent()
        while parent is not None and not isinstance(parent, PhaseFlowBar):
            parent = parent.parent()
        if parent is not None:
            parent.restyle_node(self)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        parent = self.parent()
        while parent is not None and not isinstance(parent, PhaseFlowBar):
            parent = parent.parent()
        if parent is not None:
            parent.restyle_node(self)
        super().leaveEvent(event)

    def set_display_text(self, text: str):
        if text == self.node_text:
            return
        self.node_text = text
        self.setText(f"▲<br/>{text}")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.clickable:
            self.clicked.emit(self.node_id)
            event.accept()
            return
        super().mousePressEvent(event)


class _FlowBarCenterOverlay(QWidget):
    """流程条中间层：底层横向节点 + 顶层偏右浮动的「行动」提示。"""

    def __init__(
        self,
        flow_stack: QStackedWidget,
        hint: "_PhaseFlowNodeLabel",
        parent=None,
        *,
        hint_x_ratio: float = 0.68,
    ):
        super().__init__(parent)
        self._hint = hint
        self._hint_x_ratio = hint_x_ratio
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(flow_stack)
        hint.setParent(self)
        hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        hint.hide()

    def reposition_hint(self):
        if not self._hint.isVisible():
            return
        self._hint.adjustSize()
        x = int(self.width() * self._hint_x_ratio) - self._hint.width() // 2
        x = max(0, min(x, self.width() - self._hint.width()))
        y = max(0, (self.height() - self._hint.height()) // 2)
        self._hint.move(x, y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reposition_hint()


class PhaseFlowBar(QFrame):
    """顶部阶段流程导航条：左回合同信息 / 中阶段节点串联 / 右「切换」按钮。
    高亮只跟随真实游戏状态；点击「行动」节点标红 = 该行动窗口自动跳过。
    「切换」可在主流程 / 敌人攻击 / 玩家攻击三条流程条间循环；进入战斗
    子步骤结算时仍自动切到对应子流程。"""

    skip_toggled = pyqtSignal(str, bool)
    flow_view_changed = pyqtSignal()

    FLOW_VARIANT_ORDER = ("", "enemy_attack", "player_attack")
    # 熟练模式循环顺序：宏观流程 → 详细主流程 → 战斗子流程
    EXPERT_FLOW_VARIANT_ORDER = (
        "expert_macro", "", "enemy_attack", "player_attack",
    )
    FLOW_VARIANT_LABELS = {
        "expert_macro": "宏观流程",
        "": '主流程',
        "enemy_attack": '敌人攻击',
        "player_attack": "玩家攻击",
    }
    # 变体 ↭ QStackedWidget 页索引
    _VARIANT_INDEX = {
        "": 0,
        "enemy_attack": 1,
        "player_attack": 2,
        "expert_macro": 3,
    }

    # (kind, id_or_text, text, is_action_clickable)
    # kind: "node" 可高亮节点；"sep" 纯文字分隔（→ / 分组括号 / 环尾）
    FLOW_ITEMS = (
        ("node", "resource", '资源', False),
        ("sep", "→", "", False),
        ("node", "act_resource", '行动', True),
        ("sep", "→", "", False),
        ("node", "planning", "计划", False),
        ("sep", "→", "", False),
        ("node", "planning_action", '特殊行动', False),
        ("sep", "→", "", False),
        ("sep", '探险（', "", False),
        ("node", "quest_assign", '指派', False),
        ("sep", "→", "", False),
        ("node", "act_assign", '行动', True),
        ("sep", "→", "", False),
        ("node", "staging", '探查', False),
        ("sep", "→", "", False),
        ("node", "act_staging", '行动', True),
        ("sep", "→", "", False),
        ("node", "quest_resolve", "结算", False),
        ("sep", "）→", "", False),
        ("node", "act_quest_resolve", '行动', True),
        ("sep", "→", "", False),
        ("node", "travel", '游历', False),
        ("sep", "→", "", False),
        ("node", "act_travel", '行动', True),
        ("sep", "→", "", False),
        ("sep", "遭遇（", "", False),
        ("node", "voluntary_engage", '主动交战', False),
        ("sep", "→", "", False),
        ("node", "act_engage", '行动', True),
        ("sep", "→", "", False),
        ("node", "engage_check", '交战', False),
        ("sep", "）→", "", False),
        ("node", "act_encounter", '行动', True),
        ("sep", "→", "", False),
        ("node", "combat", '战斗', False),
        ("sep", "→", "", False),
        ("node", "act_combat", '行动', True),
        ("sep", "→", "", False),
        ("node", "refresh", "恢复", False),
        ("sep", "→", "", False),
        ("node", "act_refresh", '行动', True),
        ("sep", '↭资源', "", False),
    )

    # 6.4b 单次敌人攻击结算子流程（进入 6.4b 时切换显示）
    ENEMY_ATTACK_FLOW_ITEMS = (
        ("node", "ea_6_4b", '敌人发动攻击', False),
        ("sep", "→", "", False),
        ("node", "act_ea_b", '行动', True),
        ("sep", "→", "", False),
        ("node", "ea_6_4_1", "宣告防御", False),
        ("sep", "→", "", False),
        ("node", "act_ea_1", '行动', True),
        ("sep", "→", "", False),
        ("node", "ea_6_4_2", "结算魔影", False),
        ("sep", "→", "", False),
        ("node", "act_ea_2", '行动', True),
        ("sep", "→", "", False),
        ("node", "ea_6_4_3", "战斗结算", False),
        ("sep", "→", "", False),
        ("node", "act_ea_3", '行动', True),
        ("sep", "→", "", False),
        ("node", "ea_6_4_4", '敌人攻击结束', False),
    )

    # 6.8b 单次玩家攻击结算子流程（进入 6.8b 时切换显示）
    PLAYER_ATTACK_FLOW_ITEMS = (
        ("node", "pa_6_8b", "玩家发动攻击", False),
        ("sep", "→", "", False),
        ("node", "pa_6_8_1", "可选远程", False),
        ("sep", "→", "", False),
        ("node", "act_pa_1", '行动', True),
        ("sep", "→", "", False),
        ("node", "pa_6_8_2", '宣告攻击', False),
        ("sep", "→", "", False),
        ("node", "act_pa_2", '行动', True),
        ("sep", "→", "", False),
        ("node", "pa_6_8_3", "战斗结算", False),
        ("sep", "→", "", False),
        ("node", "act_pa_3", '行动', True),
        ("sep", "→", "", False),
        ("node", "pa_6_8_4", "玩家攻击结束", False),
    )

    # 熟练模式宏观流程：大环节（「行动」居中浮层单独显示）
    EXPERT_MACRO_FLOW_ITEMS = (
        ("node", "ex_resource", "资源环节", False),
        ("sep", "→", "", False),
        ("node", "ex_planning", "计划环节", False),
        ("sep", "→", "", False),
        ("node", "ex_quest", "探险环节", False),
        ("sep", "→", "", False),
        ("node", "ex_travel", "游历环节", False),
        ("sep", "→", "", False),
        ("node", "ex_encounter", "遭遇环节", False),
        ("sep", "→", "", False),
        ("node", "ex_combat", "战斗环节", False),
        ("sep", "→", "", False),
        ("node", "ex_enemy_attack", '敌人攻击', False),
        ("sep", "→", "", False),
        ("node", "ex_player_attack", "玩家攻击", False),
        ("sep", "→", "", False),
        ("node", "ex_refresh", "恢复环节", False),
        ("sep", '↭资源', "", False),
    )

    COLOR_BG = "#2a2a2a"
    COLOR_TEXT = "#cccccc"
    COLOR_GOLD = "#c8a44a"
    COLOR_GOLD_BG = "#3a3020"
    COLOR_SKIP = "#e05050"
    # 计划环节特殊行动窗口不可标红/自动跳过
    NON_SKIPPABLE_ACTION_NODES = frozenset({"planning_action"})

    def __init__(self, parent=None, *, enlarged: bool = False):
        super().__init__(parent)
        self._enlarged = enlarged
        self._turn_number = 0
        self._node_font_size = 16 if enlarged else 11
        self._sep_font_size = 14 if enlarged else 11
        bar_h = 96 if enlarged else 52
        self.setObjectName("phaseFlowBar")
        self.setFixedHeight(bar_h)
        border = "" if enlarged else "border-bottom: 1px solid #444;"
        self.setStyleSheet(f"""
            QFrame#phaseFlowBar {{
                background-color: {self.COLOR_BG};
                {border}
            }}
            QLabel {{
                background: transparent;
            }}
        """)
        self._active_node_id: str | None = None
        self._skip_nodes: set[str] = set()
        self._node_labels: dict[str, _PhaseFlowNodeLabel] = {}
        self._auto_variant = ""
        self._manual_override = False
        self._manual_variant = ""
        self._expert_mode = False
        self._expert_action_hint_text: str | None = None
        self._cycle_order_override: tuple[str, ...] | None = None
        self._flow_view_controller: "PhaseFlowBar | None" = None
        self._flow_view_sync_callback = None

        margin = 14 if enlarged else 10
        layout = QHBoxLayout(self)
        layout.setContentsMargins(margin, 4 if enlarged else 2, margin, 4 if enlarged else 2)
        layout.setSpacing(8 if enlarged else 4)

        turn_fs = 16 if enlarged else 12
        self.turn_label = QLabel("未开始")
        self.turn_label.setStyleSheet(
            f"color: white; font-size: {turn_fs}px; font-weight: bold;"
        )
        layout.addWidget(self.turn_label)
        layout.addSpacing(8)

        self._flow_stack = QStackedWidget()
        main_row, main_nodes = self._build_flow_row(self.FLOW_ITEMS)
        ea_row, ea_nodes = self._build_flow_row(self.ENEMY_ATTACK_FLOW_ITEMS)
        pa_row, pa_nodes = self._build_flow_row(self.PLAYER_ATTACK_FLOW_ITEMS)
        macro_row, macro_nodes = self._build_flow_row(self.EXPERT_MACRO_FLOW_ITEMS)
        self._flow_stack.addWidget(main_row)
        self._flow_stack.addWidget(ea_row)
        self._flow_stack.addWidget(pa_row)
        self._flow_stack.addWidget(macro_row)
        self._node_labels.update(main_nodes)
        self._node_labels.update(ea_nodes)
        self._node_labels.update(pa_nodes)
        self._node_labels.update(macro_nodes)
        self._expert_action_hint = _PhaseFlowNodeLabel(
            "ex_action", "玩家行动", False,
        )
        self._node_labels["ex_action"] = self._expert_action_hint
        for node in self._node_labels.values():
            node.clicked.connect(self._on_node_clicked)
            self.restyle_node(node)
        self._flow_center_overlay = _FlowBarCenterOverlay(
            self._flow_stack,
            self._expert_action_hint,
            hint_x_ratio=0.72 if self._enlarged else 0.68,
        )
        layout.addWidget(self._flow_center_overlay, 1)

        btn_h = 34 if enlarged else 26
        btn_fs = 13 if enlarged else 11
        self.switch_button = QPushButton("切换")
        self.switch_button.setFixedHeight(btn_h)
        self.switch_button.setFocusPolicy(Qt.NoFocus)
        self._refresh_switch_button_tooltip()
        self.switch_button.setStyleSheet(f"""
            QPushButton {{
                background: #383838;
                color: #cccccc;
                border: 1px solid #555;
                border-radius: 3px;
                font-size: {btn_fs}px;
                padding: 0px 10px;
            }}
            QPushButton:hover {{
                color: white;
                border-color: #888;
            }}
        """)
        self.switch_button.clicked.connect(self._on_switch_button_clicked)
        self.switch_button.installEventFilter(self)
        self._switch_single_timer = QTimer(self)
        self._switch_single_timer.setSingleShot(True)
        self._switch_single_timer.setInterval(280)
        self._switch_single_timer.timeout.connect(self._cycle_flow_view)
        layout.addWidget(self.switch_button)

    def _build_flow_row(
        self,
        flow_items: tuple,
    ) -> tuple[QWidget, dict[str, "_PhaseFlowNodeLabel"]]:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8 if self._enlarged else 4)
        nodes: dict[str, _PhaseFlowNodeLabel] = {}
        bar = container  # restyle_node 通过 parent 链查找 PhaseFlowBar
        for kind, key, text, clickable in flow_items:
            if kind == "sep":
                sep = QLabel(key)
                sep.setStyleSheet(
                    f"color: {self.COLOR_TEXT}; font-size: {self._sep_font_size}px;"
                )
                sep.setAlignment(Qt.AlignCenter)
                row.addWidget(sep)
            else:
                node = _PhaseFlowNodeLabel(key, text, clickable, bar)
                nodes[key] = node
                row.addWidget(node)
        row.addStretch()
        return container, nodes

    def set_turn(self, round_number: int):
        self._turn_number = round_number
        self.turn_label.setText(
            f"回合 {round_number}" if round_number > 0 else "未开始"
        )

    def _variant_index(self, variant: str | None) -> int:
        return self._VARIANT_INDEX.get(variant or "", 0)

    def _visible_variant(self) -> str:
        idx = self._flow_stack.currentIndex()
        for variant, vidx in self._VARIANT_INDEX.items():
            if vidx == idx:
                return variant
        return ""

    def _cycle_order(self) -> tuple[str, ...]:
        if self._cycle_order_override is not None:
            return self._cycle_order_override
        return (
            self.EXPERT_FLOW_VARIANT_ORDER
            if self._expert_mode
            else self.FLOW_VARIANT_ORDER
        )

    def set_cycle_order_override(self, order: tuple[str, ...] | None):
        """熟练模式战斗子步骤行动窗口：限制「切换」不跳入另一套攻击子流程。"""
        self._cycle_order_override = order
        self._refresh_switch_button_tooltip()

    def is_macro_view(self) -> bool:
        return self._visible_variant() == "expert_macro"

    def set_expert_mode(self, on: bool):
        self._expert_mode = bool(on)
        self._refresh_switch_button_tooltip()

    def _apply_variant(self, variant: str | None):
        idx = self._variant_index(variant)
        if self._flow_stack.currentIndex() != idx:
            self._flow_stack.setCurrentIndex(idx)
        self._refresh_switch_button_tooltip()
        for node in self._node_labels.values():
            self.restyle_node(node)

    def _refresh_switch_button_tooltip(self):
        label = self.FLOW_VARIANT_LABELS.get(self._visible_variant(), '主流程')
        seq = " →".join(
            self.FLOW_VARIANT_LABELS.get(v, v) for v in self._cycle_order()
        )
        self.switch_button.setToolTip(
            f"当前：{label}\n"
            f"单击切换：{seq} →\n"
            f"双击：快速切至宏观流程（熟练模式）"
        )

    def _on_switch_button_clicked(self):
        """单击延迟触发，避免与双击冲突："""
        self._switch_single_timer.start()

    def _jump_to_expert_macro_flow(self):
        """熟练模式：快速切至宏观流程视图。"""
        target = self._flow_view_controller or self
        if target is not self:
            target._jump_to_expert_macro_flow()
            cb = self._flow_view_sync_callback
            if cb is not None:
                cb()
            return
        if not self._expert_mode:
            print('流程条：新手模式无宏观流程（开局请选择熟练模式）')
            return
        if self._visible_variant() == "expert_macro":
            return
        print('流程条：双击快速切换至「宏观流程」')
        self.set_flow_variant("expert_macro", from_game=False)
        self.flow_view_changed.emit()

    def eventFilter(self, obj, event):
        if (
            obj is self.switch_button
            and event.type() == QEvent.MouseButtonDblClick
            and event.button() == Qt.LeftButton
        ):
            self._switch_single_timer.stop()
            self._jump_to_expert_macro_flow()
            return True
        return super().eventFilter(obj, event)

    def action_node_ids(self) -> list[str]:
        """所有可标红跳过的「行动」节点 id（用于熟练模式批量标红）。"""
        ids: list[str] = []
        for items in (
            self.FLOW_ITEMS,
            self.ENEMY_ATTACK_FLOW_ITEMS,
            self.PLAYER_ATTACK_FLOW_ITEMS,
        ):
            for kind, key, _, clickable in items:
                if (
                    kind == "node"
                    and clickable
                    and key not in self.NON_SKIPPABLE_ACTION_NODES
                ):
                    ids.append(key)
        return ids

    def apply_action_skip_preset(self, skip_all: bool):
        """批量标红/清除所有行动节点（不逐个发 skip_toggled）。"""
        for node_id in self.action_node_ids():
            if skip_all:
                self._skip_nodes.add(node_id)
            else:
                self._skip_nodes.discard(node_id)
        for node_id in self.NON_SKIPPABLE_ACTION_NODES:
            self._skip_nodes.discard(node_id)
        for node in self._node_labels.values():
            self.restyle_node(node)

    def update_expert_macro_labels(self, labels: dict[str, str]):
        """刷新宏观流程节点显示文字（写入 Pn 前缀）。"""
        for node_id, text in labels.items():
            node = self._node_labels.get(node_id)
            if node is not None:
                node.set_display_text(text)

    def set_expert_action_hint(self, text: str | None):
        """熟练宏观视图：居中显示「前缀→玩家行动」；None 则隐藏。"""
        self._expert_action_hint_text = text
        if text:
            self._expert_action_hint.set_display_text(text)
            if not self._expert_action_hint.isVisible():
                self._expert_action_hint.show()
            self.restyle_node(self._expert_action_hint)
            self._flow_center_overlay.reposition_hint()
        else:
            self._expert_action_hint.hide()

    def reset_view_to_main(self):
        """回到详细主流程视图，清除手动覆盖（新手默认重开）。"""
        self._manual_override = False
        self._manual_variant = ""
        self._auto_variant = ""
        self._apply_variant("")

    def set_flow_variant(self, variant: str | None, *, from_game: bool = True):
        """None=主流程；enemy_attack / player_attack=战斗子流程。"""
        key = variant or ""
        if from_game:
            self._auto_variant = key
            if key in ("enemy_attack", "player_attack"):
                self._manual_override = False
            if not self._manual_override:
                self._apply_variant(key)
        else:
            self._manual_variant = key
            self._manual_override = True
            self._apply_variant(key)

    def set_active(self, node_id: str | None, *, force: bool = False):
        if not force and node_id == self._active_node_id:
            return
        old_id = self._active_node_id
        self._active_node_id = node_id
        if old_id and old_id in self._node_labels:
            self.restyle_node(self._node_labels[old_id])
        if node_id and node_id in self._node_labels:
            self.restyle_node(self._node_labels[node_id])

    def skip_marked(self, node_id: str) -> bool:
        return node_id in self._skip_nodes

    def set_skip_marked(self, node_id: str, marked: bool):
        """设置行动节点标红状态并发出 skip_toggled。"""
        if marked:
            if node_id in self._skip_nodes:
                return
            self._skip_nodes.add(node_id)
        else:
            if node_id not in self._skip_nodes:
                return
            self._skip_nodes.discard(node_id)
        label = self._node_labels.get(node_id)
        if label is not None:
            self.restyle_node(label)
        self.skip_toggled.emit(node_id, marked)

    def copy_state_from(self, other: "PhaseFlowBar"):
        """从另一条流程条同步显示状态（用于放大窗口）。"""
        self.set_turn(other._turn_number)
        self._skip_nodes = set(other._skip_nodes)
        self._expert_mode = other._expert_mode
        self._manual_override = other._manual_override
        self._manual_variant = other._manual_variant
        self._auto_variant = other._auto_variant
        self._cycle_order_override = other._cycle_order_override
        for node_id, node in self._node_labels.items():
            src = other._node_labels.get(node_id)
            if src is not None:
                node.set_display_text(src.node_text)
        if other._manual_override:
            self.set_flow_variant(other._manual_variant or None, from_game=False)
        else:
            self.set_flow_variant(other._auto_variant or None, from_game=True)
        self.set_active(other._active_node_id, force=True)
        self.set_expert_action_hint(other._expert_action_hint_text)
        for node in self._node_labels.values():
            self.restyle_node(node)

    # ---------- 内部 ----------

    def _on_node_clicked(self, node_id: str):
        label = self._node_labels.get(node_id)
        if (
            label is None
            or not label.clickable
            or node_id in self.NON_SKIPPABLE_ACTION_NODES
        ):
            return
        if node_id in self._skip_nodes:
            self._skip_nodes.discard(node_id)
            marked = False
        else:
            self._skip_nodes.add(node_id)
            marked = True
        self.restyle_node(label)
        self.skip_toggled.emit(node_id, marked)

    def _cycle_flow_view(self):
        current = self._visible_variant()
        order = self._cycle_order()
        try:
            pos = order.index(current)
        except ValueError:
            pos = -1
        nxt = order[(pos + 1) % len(order)]
        label = self.FLOW_VARIANT_LABELS.get(nxt, nxt)
        print(f"{label}。")
        self.set_flow_variant(nxt or None, from_game=False)
        self.flow_view_changed.emit()

    def restyle_node(self, label: _PhaseFlowNodeLabel):
        active = label.node_id == self._active_node_id
        if label.node_id == "ex_action":
            active = bool(self._expert_action_hint_text)
        skipped = label.node_id in self._skip_nodes
        color = self.COLOR_SKIP if skipped else (
            "white" if active else self.COLOR_TEXT
        )
        if label._hovered and not active and not skipped:
            color = "#ffffff"
        weight = "bold" if active else "normal"
        border = (
            f"1px solid {self.COLOR_GOLD}" if active
            else "1px solid transparent"
        )
        bg = self.COLOR_GOLD_BG if active else (
            "#383838" if label._hovered else "transparent"
        )
        pad = "2px 8px" if self._enlarged else "0px 3px"
        label.setStyleSheet(
            f"color: {color}; font-size: {self._node_font_size}px;"
            f" font-weight: {weight};"
            f" border: {border}; border-radius: 3px;"
            f" background-color: {bg}; padding: {pad};"
        )


class PhaseFlowZoomDialog(QDialog):
    """放大显示阶段流程条。"""

    def __init__(self, source_bar: PhaseFlowBar, parent=None):
        super().__init__(parent)
        self._source = source_bar
        self.setWindowTitle("阶段流程")
        self.setMinimumSize(720, 180)
        self.resize(1040, 220)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self._phase_hint = QLabel("")
        self._phase_hint.setWordWrap(True)
        self._phase_hint.setStyleSheet(
            "color: #cccccc; font-size: 13px; padding: 4px 2px;"
        )
        layout.addWidget(self._phase_hint)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { border: 1px solid #444; background: #2a2a2a; }"
        )
        self._zoom_bar = PhaseFlowBar(self._scroll, enlarged=True)
        self._scroll.setWidget(self._zoom_bar)
        self._flow_scroller = _CardRowHorizontalScroller(
            self._scroll, self._zoom_bar
        )
        layout.addWidget(self._scroll, 1)

        close_btn = QPushButton('关闭')
        close_btn.setFixedHeight(30)
        close_btn.clicked.connect(self.close)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close_btn)
        layout.addLayout(row)

        self._zoom_bar.skip_toggled.connect(self._on_zoom_skip)
        self._zoom_bar._flow_view_controller = self._source
        self._zoom_bar._flow_view_sync_callback = self.sync_from_source
        self._zoom_bar.switch_button.clicked.disconnect(
            self._zoom_bar._on_switch_button_clicked
        )
        self._zoom_bar.switch_button.clicked.connect(self._on_zoom_switch)
        self._source._switch_single_timer.timeout.connect(
            self._sync_after_switch_cycle
        )
        self._scroll.setToolTip("滚轮或按住左右拖动，可横向滚动流程条")
        self.sync_from_source()

    def _sync_after_switch_cycle(self):
        if self.isVisible():
            self.sync_from_source()

    def _update_zoom_scroll_geometry(self):
        """按当前流程条内容宽度更新横向滚动的区域。"""
        bar = self._zoom_bar
        bar.adjustSize()
        row = bar._flow_stack.currentWidget()
        row_w = row.sizeHint().width() if row is not None else 0
        total = (
            bar.turn_label.sizeHint().width()
            + row_w
            + bar.switch_button.sizeHint().width()
            + 48
        )
        bar.setMinimumWidth(max(total, self._scroll.viewport().width()))
        self._flow_scroller.sync_child_filters()

    def sync_from_source(self):
        self._zoom_bar.copy_state_from(self._source)
        self._update_zoom_scroll_geometry()
        parent = self.parent()
        phase = getattr(parent, "current_phase", "") if parent else ""
        self._phase_hint.setText(phase or "")
        tip = self._source.toolTip()
        if tip:
            self.setToolTip(tip)
            self._zoom_bar.setToolTip(tip)

    def _on_zoom_skip(self, node_id: str, marked: bool):
        self._source.set_skip_marked(node_id, marked)

    def _on_zoom_switch(self):
        self._source._on_switch_button_clicked()


def _island_map_pixmap_with_progress(card, width: int, height: int, progress: int) -> QPixmap:
    """岛屿地图卡图；右上角叠加进度数字徽章。"""
    image_path = (getattr(card, "image_path", "") or "").strip()
    source = QPixmap(image_path) if image_path and Path(image_path).is_file() else QPixmap()
    if source.isNull():
        result = QPixmap(width, height)
        result.fill(QColor("#f3f3f3"))
    else:
        result = source.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    progress = max(0, int(progress or 0))
    if progress <= 0:
        return result
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    diameter = max(24, min(34, result.width() // 3))
    x = result.width() - diameter - 3
    y = 3
    painter.setBrush(QColor(20, 20, 20, 210))
    painter.setPen(QPen(QColor("#d8b24a"), 2))
    painter.drawEllipse(x, y, diameter, diameter)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(max(14, diameter // 2))
    painter.setFont(font)
    painter.setPen(QColor("#ffd75a"))
    painter.drawText(QRect(x, y, diameter, diameter), Qt.AlignCenter, str(progress))
    painter.end()
    return result


class IslandMapCardButton(QToolButton):
    single_clicked = pyqtSignal()
    double_clicked = pyqtSignal()
    right_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._single_timer = QTimer(self)
        self._single_timer.setSingleShot(True)
        self._single_timer.setInterval(240)
        self._single_timer.timeout.connect(self.single_clicked.emit)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._single_timer.start()
            event.accept()
            return
        if event.button() == Qt.RightButton:
            self._single_timer.stop()
            self.right_clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._single_timer.stop()
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class IslandMapDialog(QDialog):
    """欺诈者神庙的 3×5 岛屿地图；只公开正面并允许选择当前位置。"""

    CARD_WIDTH = 112
    CARD_HEIGHT = 156

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller
        self.setWindowTitle("岛屿地图")
        self.setModal(True)
        root = QVBoxLayout(self)
        hint = QLabel(
            "左键选择合法游历目标；双击失落的岛屿触发行动；右键放大。"
            "右方始终朝向欺诈者神庙。"
        )
        hint.setAlignment(Qt.AlignCenter)
        root.addWidget(hint)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        self._buttons = {}
        for cell in controller._island_map_cells:
            row = int(cell.get("row", 0))
            col = int(cell.get("col", 0))
            button = IslandMapCardButton(self)
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.setIconSize(QSize(self.CARD_WIDTH, self.CARD_HEIGHT))
            button.setFixedSize(self.CARD_WIDTH + 18, self.CARD_HEIGHT + 38)
            front_card = controller._island_map_display_card(cell)
            button.setText(getattr(front_card, "name", "岛屿地区"))
            image_path = (getattr(front_card, "image_path", "") or "").strip()
            progress = int(cell.get("progress", 0) or 0)
            if image_path and Path(image_path).is_file():
                hover_pixmap = QPixmap(image_path)
                if not hover_pixmap.isNull():
                    button._hover_card_pixmap = hover_pixmap
                    button._hover_card_face_up = True
            button.setIcon(QIcon(_island_map_pixmap_with_progress(
                front_card, self.CARD_WIDTH, self.CARD_HEIGHT, progress
            )))
            button.single_clicked.connect(lambda r=row, c=col: self._select_position(r, c))
            button.double_clicked.connect(lambda r=row, c=col: self._activate_lost_island(r, c))
            button.right_clicked.connect(lambda r=row, c=col: self._zoom_card(r, c))
            grid.addWidget(button, row, col)
            self._buttons[(row, col)] = button
        root.addLayout(grid)
        close_button = QPushButton("关闭")
        close_button.setMinimumHeight(42)
        close_button.clicked.connect(self.accept)
        root.addWidget(close_button)
        self._refresh_selection()

    def _select_position(self, row: int, col: int):
        if self._controller._can_travel_on_island_map(row, col):
            self._controller._travel_on_island_map(row, col)
            self.accept()

    def _zoom_card(self, row: int, col: int):
        cell = self._controller._island_map_cell(row, col)
        card = self._controller._island_map_display_card(cell) if cell else None
        image_path = (getattr(card, "image_path", "") or "").strip()
        if image_path and Path(image_path).is_file():
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                CardImageZoomDialog(pixmap, self).exec_()

    def _activate_lost_island(self, row: int, col: int):
        if self._controller._activate_island_map_lost_island_action(row, col):
            cell = self._controller._island_map_cell(row, col)
            card = self._controller._island_map_display_card(cell)
            button = self._buttons.get((row, col))
            if button is not None:
                button.setIcon(QIcon(_island_map_pixmap_with_progress(
                    card, self.CARD_WIDTH, self.CARD_HEIGHT,
                    int(cell.get("progress", 0) or 0),
                )))
            self.accept()

    def _refresh_selection(self):
        current = self._controller._island_map_position
        for coord, button in self._buttons.items():
            selected = coord == current
            button.setStyleSheet(
                "QToolButton { border: 4px solid #e0a000; background: #fff7cf; "
                "font-weight: bold; }"
                if selected
                else "QToolButton { border: 1px solid #888; background: #f7f7f7; }"
            )


class IslandProgressAllocationDialog(QDialog):
    """搜寻小岛 1B：一次性在主任务与虚拟场景区地区间分配进度。"""

    def __init__(self, controller, amount: int, locations: list, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._amount = max(0, int(amount))
        self._spins: dict[str, QSpinBox] = {}
        self._cards: dict[str, object] = {}
        self._map_images: dict[str, tuple[QLabel, dict, object]] = {}
        self.setWindowTitle("搜寻小岛 1B · 分配探索进度")
        root = QVBoxLayout(self)
        prompt = QLabel(
            f"本次共结算 {self._amount} 枚进度。分配给「搜寻小岛」的进度无效果；"
            "可改放到绿色边框的相邻地区。黄色边框为当前地区。"
        )
        prompt.setWordWrap(True)
        root.addWidget(prompt)
        grid = QGridLayout()
        location_ids = {getattr(card, "id", "") for card in locations}
        for cell in controller._island_map_cells:
            row, col = int(cell["row"]), int(cell["col"])
            card = controller._island_map_display_card(cell)
            box = QWidget(self)
            box.setObjectName(f"islandAllocationCell_{row}_{col}")
            layout = QVBoxLayout(box)
            layout.setContentsMargins(3, 3, 3, 3)
            image = QLabel()
            image.setAlignment(Qt.AlignCenter)
            image.setFixedSize(92, 126)
            image_path = (getattr(card, "image_path", "") or "").strip()
            if image_path and Path(image_path).is_file():
                hover_pixmap = QPixmap(image_path)
                if not hover_pixmap.isNull():
                    image._hover_card_pixmap = hover_pixmap
                    image._hover_card_face_up = True
            layout.addWidget(image)
            card_id = getattr(card, "id", "") or f"map:{row},{col}"
            self._map_images[card_id] = (image, cell, card)
            is_current = (row, col) == controller._island_map_position
            eligible = card_id in location_ids and not is_current
            border = "#f2c200" if is_current else ("#20a64a" if eligible else "#999")
            box.setStyleSheet(
                f"QWidget#{box.objectName()} {{ border: 4px solid {border}; background: white; }}"
            )
            if eligible:
                spin = QSpinBox()
                spin.setRange(0, self._amount)
                spin.valueChanged.connect(self._update_remaining)
                layout.addWidget(spin)
                self._spins[card_id] = spin
                self._cards[card_id] = card
            grid.addWidget(box, row, col)
        root.addLayout(grid)
        for card in locations:
            if controller._is_island_map_card(card):
                continue
            card_id = getattr(card, "id", "") or f"ordinary:{len(self._spins)}"
            row_layout = QHBoxLayout()
            row_layout.addWidget(QLabel(f"其他场景区地区 · {card.name}："))
            spin = QSpinBox()
            spin.setRange(0, self._amount)
            spin.valueChanged.connect(self._update_remaining)
            row_layout.addWidget(spin)
            root.addLayout(row_layout)
            self._spins[card_id] = spin
            self._cards[card_id] = card
        self._quest_spin = QSpinBox()
        self._quest_spin.setRange(0, self._amount)
        self._quest_spin.setValue(self._amount)
        self._quest_spin.valueChanged.connect(self._update_remaining)
        quest_row = QHBoxLayout()
        quest_row.addWidget(QLabel("放到「搜寻小岛」（无效果）："))
        quest_row.addWidget(self._quest_spin)
        root.addLayout(quest_row)
        self._remaining_label = QLabel()
        self._remaining_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self._remaining_label)
        _add_prominent_dialog_buttons(
            root, accept_text="确认分配", cancel_text="",
            on_accept=self._accept_if_complete, on_reject=None,
        )
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        self._update_remaining()

    def _allocated_total(self) -> int:
        return self._quest_spin.value() + sum(spin.value() for spin in self._spins.values())

    def _update_remaining(self, *_args):
        remaining = self._amount - self._allocated_total()
        self._remaining_label.setText(
            f"尚未分配：{remaining}" if remaining >= 0 else f"超出总数：{-remaining}"
        )
        self._refresh_map_progress_badges()

    def _refresh_map_progress_badges(self):
        for card_id, (label, cell, card) in self._map_images.items():
            existing = int(cell.get("progress", 0) or 0)
            proposed = self._spins.get(card_id).value() if card_id in self._spins else 0
            label.setPixmap(_island_map_pixmap_with_progress(card, 88, 122, existing + proposed))

    def _accept_if_complete(self):
        if self._allocated_total() != self._amount:
            QMessageBox.warning(self, "分配进度", f"分配总数必须等于 {self._amount}。")
            return
        self.accept()

    def result_allocation(self) -> tuple[int, list[tuple[object, int]]]:
        allocations = [
            (self._cards[card_id], spin.value())
            for card_id, spin in self._spins.items() if spin.value() > 0
        ]
        return self._quest_spin.value(), allocations


class MainWindow(QMainWindow):
    STAGING_ROW_INDEX = 0      # 左侧第1行：探查区（遭遇）
    ENGAGEMENT_ROW_INDEX = 1   # 左侧第2行：交战区
    FIELD_ROW_INDEX = 2        # 左侧第3行：英雄与盟友（同排）
    HAND_ROW_INDEX = 3         # 左侧第4行：手牌
    FIELD_CARD_HEIGHT = 130  # 场上角色/附属统一高度
    PLAYER_COLORS = ("#0078d4", "#e67e22", "#27ae60", "#9b59b6")
    MAX_PLAYERS = 4
    DEFAULT_ELIMINATION_THREAT = 50  # 标准退场威胁阈值
    ENTANGLE_THREAT_BONUS = 2
    ENTANGLE_CONDITION_ALIASES = {
        "最高威胁的地区": "highest_threat",
        "最高威胁地区": "highest_threat",
        "最高印刷威胁的地区": "highest_threat",
        "最高印刷威胁地区": "highest_threat",
        "highest threat location": "highest_threat",
        "highest threat area": "highest_threat",
        "lowest threat location": "lowest_threat",
        "lowest threat area": "lowest_threat",
        "最低威胁的地区": "lowest_threat",
        "最低威胁地区": "lowest_threat",
        "最低印刷威胁的地区": "lowest_threat",
        "最低印刷威胁地区": "lowest_threat",
        "最高探险点数的地区": "highest_progress",
        "最高探险点数地区": "highest_progress",
        "最高探险点的地区": "highest_progress",
        "最高印刷任务点的地区": "highest_progress",
        "最高印刷任务点地区": "highest_progress",
        "highest quest point location": "highest_progress",
        "highest quest points location": "highest_progress",
        "highest progress location": "highest_progress",
        "lowest quest point location": "lowest_progress",
        "lowest quest points location": "lowest_progress",
        "lowest progress location": "lowest_progress",
        "最低探险点数的地区": "lowest_progress",
        "最低探险点数地区": "lowest_progress",
        "最低任务点的地区": "lowest_progress",
        "最低任务点地区": "lowest_progress",
        "最低印刷任务点的地区": "lowest_progress",
        "最低印刷任务点地区": "lowest_progress",
    }
    # 当旧卡数据没有补入 Keywords/Text_Effect 时，可按名称或 OCTGN 基础 id 回退。
    ENTANGLE_CARD_CONDITION_FALLBACKS = {}
    FIELD_AUTO_SCROLL_CARD_THRESHOLD = 7  # 多人切换玩家时，场上牌数达到该值自动滚到该玩家
    BELEGOST_LOOT_OBJECTIVE_NAMES = frozenset({
        "贝磊勾斯特之剑",
        "Sword of Belegost",
        "伊瑞德隆地图",
        "Map of Ered Luin",
        "贝磊勾斯特的钥匙",
        "Keys of Belegost",
        "奥力子民之书",
        "Book of Aulë's Children",
        "Book of Aule's Children",
        "矮人火炬",
        "Dwarven Torch",
        "蓝山宝石",
        "Blue Mountain Gem",
    })
    BELEGOST_1A_NAMES = frozenset({"第一纪元的废墟", "Ruins of the First Age"})
    EXPLORE_ISLAND_1A_NAMES = frozenset({"探索岛屿", "Explore the Island"})
    ISLAND_MAP_1A_NAMES = frozenset({"搜寻小岛", "Searching the Island"})
    HROGARS_HILL_1A_BASE_ID = "1ab32b8c-b942-4c77-af19-9703fba1dfa9"
    HROGARS_HILL_1A_NAMES = frozenset({"霍加堡", "Hrogar's Hill", "Hrogar’s Hill"})
    NIGHT_FIRE_SIDE_QUEST_BASE_IDS = frozenset({
        "dae6e913-3b2e-4e53-aad4-69946d3386ca",
        "a6b39458-b0f1-4b5d-a915-d3921eb35658",
        "73515869-890b-4169-9104-b2e882d921ae",
        "5fefdbc7-05e2-4c1b-96fd-46121f87d6b3",
        "a6e68650-e413-4e85-b687-b927d5a87821",
        "a837fe8b-6f21-4d20-9897-89a3c4706b40",
        "df9ba79f-91a3-4fc7-a2af-6bddcc795f29",
        "1574141b-b11e-4be9-82c9-ddc1fb2f5269",
    })
    DRAW_HER_FIRE_BASE_ID = "dae6e913-3b2e-4e53-aad4-69946d3386ca"
    DRAW_HER_FIRE_NAMES = frozenset({"吸引火力", "Draw Her Fire"})
    FORTIFY_THE_DEFENSE_BASE_ID = "a6b39458-b0f1-4b5d-a915-d3921eb35658"
    FORTIFY_THE_DEFENSE_NAMES = frozenset({"筑造防御工事", "Fortify the Defense"})
    RALLY_THE_WOODMEN_BASE_ID = "73515869-890b-4169-9104-b2e882d921ae"
    RALLY_THE_WOODMEN_NAMES = frozenset({"召集樵夫", "Rally the Woodmen"})
    DOUSE_THE_FLAMES_BASE_ID = "5fefdbc7-05e2-4c1b-96fd-46121f87d6b3"
    DOUSE_THE_FLAMES_NAMES = frozenset({"熄灭火焰", "Douse the Flames"})
    HOLD_THE_DOOR_BASE_ID = "a6e68650-e413-4e85-b687-b927d5a87821"
    HOLD_THE_DOOR_NAMES = frozenset({"守住大门", "Hold the Door"})
    DEFEND_THE_TOWN_BASE_ID = "a837fe8b-6f21-4d20-9897-89a3c4706b40"
    DEFEND_THE_TOWN_NAMES = frozenset({"守卫城镇", "Defend the Town"})
    ROUT_THE_GOBLINS_BASE_ID = "df9ba79f-91a3-4fc7-a2af-6bddcc795f29"
    ROUT_THE_GOBLINS_NAMES = frozenset({"击溃地精", "Rout the Goblins"})
    FACE_THE_DRAGON_BASE_ID = "1574141b-b11e-4be9-82c9-ddc1fb2f5269"
    FACE_THE_DRAGON_NAMES = frozenset({"面对巨龙", "Face the Dragon"})
    DAGNIR_BASE_ID = "f7badc7a-4e37-4606-8557-0fa4719227c0"
    DAGNIR_NAMES = frozenset({"达格尼尔", "Dagnir"})
    TOWN_GATE_BASE_ID = "aba5ebdb-81d8-4e41-bddd-568f9ab2bd13"
    TOWN_GATE_NAMES = frozenset({"城镇大门", "Town Gate"})
    WOODEN_PALISADE_BASE_ID = "3b7c4f67-7de4-4b77-be50-0fd86e4ef2bc"
    WOODEN_PALISADE_NAMES = frozenset({"木制栅栏", "Wooden Palisade"})
    POWERFUL_IN_WRATH_BASE_ID = "721bbc14-f14c-4464-9963-55a77ab2d9bc"
    POWERFUL_IN_WRATH_NAMES = frozenset({"愤怒的力量", "Powerful in Wrath", "Powerful in Wraith"})
    DRAGONS_FURY_BASE_ID = "bcd41784-d260-4165-ae0f-74cb844d10b3"
    DRAGONS_FURY_NAMES = frozenset({"巨龙的愤怒", "The Dragon's Fury", "The Dragon’s Fury"})
    BRIGHT_FLAMES_BASE_ID = "463d0c53-2275-410f-b311-139f19561b46"
    BRIGHT_FLAMES_NAMES = frozenset({"明亮的焰火", "Bright Flames"})
    ISLAND_MAP_SERIES = "欺诈者神庙"
    ISLAND_MAP_ROWS = 3
    ISLAND_MAP_COLS = 5
    ISLAND_MAP_TEMPLE_BASE_IDS = frozenset({
        "f3bc3759-1f94-4983-bebb-66c7d9e3e0b3",
        "3371c102-4aab-4b15-a738-a4b3483a6004",
    })
    FATEFUL_DISCOVERY_QUEST_NAMES = frozenset({"重大发现", "A Fateful Discovery"})
    BELEGOST_2A_NAMES = frozenset({
        "被打扰的远古邪恶",
        "Ancient Evils Disturbed",
    })
    BELEGOST_2B_NAMES = BELEGOST_2A_NAMES
    BELEGOST_2C_NAMES = frozenset({
        "深入旧城",
        "Deeper into the Past (C)",
    })
    BELEGOST_3A_NAMES = frozenset({
        "魔苟斯的仆从",
        "The Servants of Morgoth",
    })
    BELEGOST_3B_NAMES = BELEGOST_3A_NAMES
    BELEGOST_3C_NAMES = frozenset({
        "伊瑞德隆山脚",
        "The Roots of Ered Luin (C)",
    })
    BELEGOST_3D_NAMES = BELEGOST_3C_NAMES
    BELEGOST_3E_NAMES = frozenset({
        "贝磊勾斯特矿洞",
        "The Mines of Belegost (E)",
    })
    BELEGOST_3F_NAMES = BELEGOST_3E_NAMES
    BELEGOST_4A_NAMES = frozenset({
        "贝磊勾斯特的野兽",
        "The Beast of Belegost",
    })
    BELEGOST_4B_NAMES = BELEGOST_4A_NAMES
    BELEGOST_1A_STAGING_CARD = "废墟中潜行"
    BELEGOST_STALKING_THE_RUINS_NAMES = frozenset({
        "废墟中潜行",
        "Stalking the Ruins",
    })
    BELEGOST_NAURLHUG_NAMES = frozenset({
        "纳乌尔路赫",
        "Naurlhûg",
        "Naurlhug",
    })
    BELEGOST_NAURLHUG_LAIR_NAMES = frozenset({
        "纳乌尔路赫的巢穴",
        "Naurlhûg's Lair",
        "Naurlhug's Lair",
    })
    BELEGOST_ORC_OF_ERED_LUIN_NAMES = frozenset({
        "伊瑞德隆的半兽人",
        "Orc of Ered Luin",
    })
    BELEGOST_BLUE_MOUNTAIN_GOBLIN_NAMES = frozenset({
        "蓝山地精",
        "Blue Mountain Goblin",
    })
    BELEGOST_SPAWN_OF_THANGORODRIM_NAMES = frozenset({
        "安戈洛坠姆的末裔",
        "Spawn of Thangorodrim",
    })
    BELEGOST_ECHOES_IN_THE_DARK_NAMES = frozenset({
        "黑暗中的回声",
        "Echoes in the Dark",
    })
    BELEGOST_1A_PER_PLAYER_CARD = "废弃的矿坑"
    BELEGOST_ABANDONED_MINE_NAMES = frozenset({
        "废弃的矿坑",
        "Abandoned Mine",
    })
    BELEGOST_DARKENED_TUNNEL_NAMES = frozenset({
        "黑暗的通道",
        "Darkened Tunnel",
    })
    BELEGOST_SUNKEN_TREASURY_NAMES = frozenset({
        "沉没的宝库",
        "Sunken Treasury",
    })
    BELEGOST_FLOODED_HALL_NAMES = frozenset({
        "淹没的大厅",
        "Flooded Hall",
    })
    BELEGOST_SECRET_CHAMBER_NAMES = frozenset({
        "密室",
        "Secret Chamber",
    })
    BELEGOST_OLD_STONE_TROLL_NAMES = frozenset({
        "古老的石巨魔",
        "Old Stone Troll",
    })
    BELEGOST_NAMELESS_CAVE_DWELLER_NAMES = frozenset({
        "无名的洞穴居住者",
        "Nameless Cave Dweller",
    })
    BELEGOST_LURKER_OF_THE_DEPTHS_NAMES = frozenset({
        "深水潜伏者",
        "Lurker of the Depths",
    })
    BELEGOST_1A_SET_ASIDE_CARDS = (
        "深水潜伏者",
        "纳乌尔路赫",
        "纳乌尔路赫的巢穴",
        "贝磊勾斯特之剑",
    )
    EXPLORE_ISLAND_1A_SET_ASIDE_CARD = "魔苟斯祭坛"
    EXPLORE_ISLAND_1A_OBJECTIVE_ALLY = "卡冯"
    BELEGOST_1A_FIXED_SET_ASIDE_LOOT = frozenset({"贝磊勾斯特之剑", "Sword of Belegost"})
    BELEGOST_KEYS_NAMES = frozenset({"贝磊勾斯特的钥匙", "Keys of Belegost"})
    BELEGOST_BOOK_OF_AULE_NAMES = frozenset({
        "奥力子民之书",
        "Book of Aulë's Children",
        "Book of Aule's Children",
    })
    BELEGOST_MAP_NAMES = frozenset({
        "伊瑞德隆地图",
        "Map of Ered Luin",
    })
    BELEGOST_SWORD_NAMES = frozenset({
        "贝磊勾斯特之剑",
        "Sword of Belegost",
    })
    BELEGOST_DWARVEN_TORCH_NAMES = frozenset({
        "矮人火炬",
        "Dwarven Torch",
    })
    BELEGOST_BLUE_MOUNTAIN_GEM_NAMES = frozenset({
        "蓝山宝石",
        "Blue Mountain Gem",
    })
    BELEGOST_CONCEALED_SPIKES_NAMES = frozenset({
        "暗钉",
        "Concealed Spikes",
    })
    BELEGOST_COVERED_PIT_NAMES = frozenset({
        "掩盖的矿坑",
        "Covered Pit",
    })
    EOWYN_HERO_NAMES = frozenset({"伊奥温", "伊欧玟"})
    GLOIN_HERO_NAMES = frozenset({"格罗因", "葛罗音"})
    GLOIN_ALLY_NAMES = frozenset({"格罗因", "葛罗音", "Gloin", "Glóin"})
    BOMBUR_HERO_NAMES = frozenset({"邦伯", "庞伯", "Bombur"})
    OIN_HERO_NAMES = frozenset({"欧音", "Óin", "Oin"})
    FRODO_HERO_NAMES = frozenset({'弗罗多·巴金斯', '佛罗多·巴金斯'})
    PIPPIN_HERO_NAMES = frozenset({"皮平", "皮聘", "Pippin"})
    PIPPIN_EAAD_HERO_OCTGN_BASES = frozenset({
        "fd89bdbf-7475-4f3e-96fc-8f5315a90021",
    })
    PIPPIN_TBR_HERO_OCTGN_BASES = frozenset({
        "857d6dc8-ba1e-4839-8e96-a8a0136a2302",
        "ce96b767-c569-48b8-a998-d8009b0143c7",
    })
    FATTY_BOLGER_TBR_HERO_NAMES = frozenset({
        "小胖博哲",
        "小胖博尔杰",
        "Fatty Bolger",
    })
    FATTY_BOLGER_TBR_HERO_OCTGN_BASES = frozenset({
        "7adc49c0-640d-4934-89c5-312ab584b77c",
    })
    BILL_THE_PONY_ALLY_NAMES = frozenset({
        "小马比尔",
        "Bill the Pony",
    })
    BILL_THE_PONY_ALLY_OCTGN_BASES = frozenset({
        "ff7b0b9d-f8ae-4464-9db3-7205c5ae4db7",
    })
    FARMER_MAGGOT_ALLY_NAMES = frozenset({
        "农夫马嘎",
        "农夫马戈特",
        "Farmer Maggot",
    })
    FARMER_MAGGOT_ALLY_OCTGN_BASES = frozenset({
        "9d8ccd1a-48d3-4123-bcca-3c0ab88347ec",
    })
    SAM_GAMGEE_HERO_NAMES = frozenset({
        "山姆·詹吉",
        "山姆·甘姆吉",
        "Sam Gamgee",
    })
    SAM_GAMGEE_HERO_OCTGN_BASES = frozenset({
        "ae774680-c6e9-49eb-96b8-fcdebe90b49d",
        "4124136c-8c86-4f86-830c-94c8c76df161",
    })
    GIMLI_HERO_NAMES = frozenset({"吉姆利", "金雳", "Gimli"})
    GIMLI_SANDS_OF_HARAD_CODE = "16001"
    GIMLI_SANDS_OF_HARAD_OCTGN_BASE = "053357f5-4192-4a35-bf2c-7f48d584f292"
    GIMLI_SANDS_OF_HARAD_IMAGE_ID = "9eb45361-6325-4e39-9029-9a8401d0d294"
    BALIN_HERO_NAMES = frozenset({"巴林", "Balin"})
    BARD_THE_BOWMAN_HERO_NAMES = frozenset({
        "神射手巴德",
        "神箭手巴德",
        "Bard the Bowman",
    })
    DAIN_IRONFOOT_AURA_HERO_NAMES = frozenset({'戴因·铁足', '丹恩·铁足', "Dáin Ironfoot", "Dain Ironfoot"})
    VISIONARY_LEADERSHIP_ATTACHMENT_NAMES = frozenset({"远见卓识", "Visionary Leadership"})
    DAIN_IRONFOOT_AURA_OCTGN_BASE = "51223bd0-ffd1-11df-a976-0801206c9005"
    LEGOLAS_HERO_NAMES = frozenset({"莱戈拉斯", "勒苟拉斯", "Legolas"})
    LEGOLAS_SANDS_OF_HARAD_CODE = "16002"
    LEGOLAS_SANDS_OF_HARAD_OCTGN_BASE = "8124f5ac-6f03-4629-96f5-b8775ec8a7c4"
    LEGOLAS_SANDS_OF_HARAD_IMAGE_ID = "b2df0294-f962-4e05-998a-d824d88cc13c"
    GREENWOOD_ARCHER_ALLY_NAMES = frozenset({
        "绿林弓手",
        "巨绿森弓箭手",
        "Greenwood Archer",
    })
    GREENWOOD_ARCHER_CODE = "16003"
    GREENWOOD_ARCHER_OCTGN_BASE = "d30fd38a-65fe-464d-acac-47d0f02deb05"
    EREBOR_GUARD_ALLY_NAMES = frozenset({
        "埃瑞博禁卫",
        "依鲁伯守卫",
        "Erebor Guard",
    })
    EREBOR_GUARD_CODE = "16004"
    EREBOR_GUARD_OCTGN_BASE = "18d5f7fd-9d6e-44aa-9463-3530278f8c51"
    HALFLING_BOUNDER_ALLY_NAMES = frozenset({
        "半身人边界守卫",
        "哈比人警卫",
        "Halfling Bounder",
    })
    HALFLING_BOUNDER_CODE = "16005"
    HALFLING_BOUNDER_OCTGN_BASE = "aa7cd6b1-910c-49cb-9606-771889d1dc77"
    VIGILANT_DUNADAN_ALLY_NAMES = frozenset({
        "警惕的登丹人",
        "Vigilant Dúnadan",
        "Vigilant Dunadan",
    })
    VIGILANT_DUNADAN_CODE = "16006"
    DWALIN_HERO_NAMES = frozenset({"杜瓦林", "德瓦林", "Dwalin"})
    DWALIN_ALLY_NAMES = frozenset({"杜瓦林", "德瓦林", "Dwalin"})
    BRAND_SON_OF_BAIN_HERO_NAMES = frozenset({'巴因之子布兰德', '巴恩之子布兰德', "Brand son of Bain"})
    THALIN_HERO_NAMES = frozenset({"沙林", "萨林"})
    ELEANOR_HERO_NAMES = frozenset({"埃莉诺"})
    DUNHERE_HERO_NAMES = frozenset({"敦赫雷", "督希尔"})
    DENETHOR_HERO_NAMES = frozenset({"德内梭尔", "迪奈瑟", "迪耐瑟", "Denethor"})
    CALDARA_HERO_NAMES = frozenset({'卡尔达拉', '卡尔达瑞', "Caldara"})
    GLORFINDEL_HERO_NAMES = frozenset({"格罗芬德尔", "葛罗芬戴尔"})
    ELROND_HERO_NAMES = frozenset({"埃尔隆德", "爱隆", "Elrond"})
    ELROND_ALLY_PAYMENT_SPHERES = frozenset({"领导", "战术", "精神"})
    HIRLUIN_HERO_NAMES = frozenset({"白肤希尔路因", "贺路恩", "Hirluin the Fair"})
    MIRLONDE_HERO_NAMES = frozenset({"米尔隆德", "弥尔隆德", "Mirlonde"})
    BERAVOR_HERO_NAMES = frozenset({"贝拉芙", "贝拉沃", "Beravor"})  # 贝拉沃为旧版印刷名
    BIFUR_HERO_NAMES = frozenset({"比弗", "毕佛", "Bifur"})
    BIFUR_ALLY_NAMES = frozenset({"比弗", "毕佛", "Bifur"})
    BIFUR_HERO_IMAGE_IDS = frozenset({
        "51223bd0-ffd1-11df-a976-0801207c9008",
    })
    MERRY_SPIRIT_HERO_NAMES = frozenset({"梅里", "梅利", "Merry"})
    MERRY_SPIRIT_HERO_OCTGN_BASES = frozenset({
        "03a7152b-e6af-4aaf-a064-7e814fc181d5",
    })
    MERRY_TACTICS_HERO_NAMES = frozenset({"梅丽", "梅里", "梅利", "Merry"})
    MERRY_TACTICS_HERO_OCTGN_BASES = frozenset({
        "12d51424-0edd-4977-9df1-5f6a7a5a96e1",
        "052b1f85-8b9c-4bb0-a735-bdbd5ac1b2c4",
    })
    INGOLD_HERO_NAMES = frozenset({"英戈尔德", "英格尔德", "Ingold"})
    FARAMIR_ALLY_NAMES = frozenset({
        '法拉米尔', '法拉墨',
    })  # 法拉墨为旧版印刷名
    FARAMIR_HERO_NAMES = frozenset({
        '法拉米尔', '法拉墨', "Faramir",
    })
    DAUGHTER_OF_NIMRODEL_ALLY_NAMES = frozenset({"宁洛德尔之女", "宁若戴尔河之女", "Daughter of the Nimrodel"})
    GALADHRIM_WEAVER_ALLY_NAMES = frozenset({"加拉兹编织者", "凯兰崔姆织女", "Galadhrim Weaver"})
    GALADHRIM_HEALER_ALLY_NAMES = frozenset({"加拉兹医者", "凯兰崔姆医者", "Galadhrim Healer"})
    GALADHRIM_MINSTREL_ALLY_NAMES = frozenset({"加拉兹吟游诗人", "凯兰崔姆乐手", "Galadhrim Minstrel"})
    GALADRIEL_HANDMAIDEN_ALLY_NAMES = frozenset({
        "凯兰崔尔的侍女",
        "加拉德瑞尔的侍女",
        "Galadriel's Handmaiden",
    })
    GALADHON_ARCHER_ALLY_NAMES = frozenset({"加拉松弓手", "加拉顿弓箭手", "Galadhon Archer"})
    LINDIR_ALLY_NAMES = frozenset({"林德", "林迪尔", "Lindir"})
    HENNETH_ANNUN_GUARD_ALLY_NAMES = frozenset({
        "汉那斯安南守卫",
        "Henneth Annûn Guard",
        "Henneth Annun Guard",
    })
    LONG_DEFEAT_ATTACHMENT_NAMES = frozenset({"长久的失败", "The Long Defeat"})
    ROAD_GOES_EVER_ON_ATTACHMENT_NAMES = frozenset({
        "大路长啊长",
        "旅途永不绝",
        "The Road Goes Ever On",
    })
    ROAD_GOES_EVER_ON_CODE = "16012"
    ROAD_GOES_EVER_ON_OCTGN_BASE = "0b229535-c078-4f47-b781-7e84de1f0eb5"
    STORM_COMES_NAMES = frozenset({
        "\u66b4\u98ce\u5c06\u4e34",
        "\u98ce\u66b4\u5c06\u81f3",
        "The Storm Comes",
    })
    STORM_COMES_CODE = "16013"
    STORM_COMES_OCTGN_BASE = "458b755e-1d93-4586-bbca-bab905f895c0"
    WAIT_NO_LONGER_EVENT_NAMES = frozenset({
        "\u4e0d\u518d\u7b49\u5f85",
        "Wait no Longer",
        "Wait No Longer",
    })
    WAIT_NO_LONGER_CODE = "17005"
    WAIT_NO_LONGER_OCTGN_BASE = "e73ef9d3-e2a1-400b-9ed7-bfdfd0a3bc2a"
    ANDRATH_GUARDSMAN_ALLY_NAMES = frozenset({
        "\u5b89\u5fb7\u62c9\u65af\u5b88\u62a4\u8005",
        "Andrath Guardsman",
    })
    ANDRATH_GUARDSMAN_CODE = "17002"
    ANDRATH_GUARDSMAN_OCTGN_BASE = "f5bf16fb-3244-40a4-8800-3e38c979c18c"
    YAZAN_ALLY_NAMES = frozenset({"\u4e9a\u8d5e", "\u96c5\u8d5e", "Yazan"})
    YAZAN_CODE = "17004"
    YAZAN_OCTGN_BASE = "1a71163f-eeb4-4992-bd9c-83349d93cd58"
    JUBAYR_ALLY_NAMES = frozenset({
        "\u80e1\u5df4\u4f9d",
        "\u6731\u62dc\u5c14",
        "Jubayr",
        "Jubair",
    })
    JUBAYR_CODE = "17006"
    JUBAYR_OCTGN_BASE = "ba6d53fd-277c-4796-814b-ada9ff23cafd"
    DWARF_PIPE_ATTACHMENT_NAMES = frozenset({
        "\u77ee\u4eba\u70df\u6597",
        "Dwarf Pipe",
    })
    DWARF_PIPE_CODE = "17007"
    DWARF_PIPE_OCTGN_BASE = "86e1977f-0a24-46c8-a7ee-d5d78410f1a3"
    KAHLIEL_HERO_NAMES = frozenset({
        "\u5361\u91cc\u827e\u5c14",
        "\u5361\u5217\u5c14",
        "Kahliel",
    })
    KAHLIEL_CODE = "17001"
    KAHLIEL_OCTGN_BASE = "ae49dd35-f2c7-4ab5-b34d-0cc8e5fb1f6e"
    SON_OF_ARNOR_ALLY_NAMES = frozenset({"阿尔诺之子", "亚尔诺之子"})  # 亚尔诺之子为旧版印刷
    SNOWBOURN_SCOUT_ALLY_NAMES = frozenset({"雪河斥候", "雪界河斥候"})  # 雪界河斥候为旧版印刷
    LONG_BEARD_ORC_SLAYER_ALLY_NAMES = frozenset({
        '长须奥克屠戮者', '长须屠兽者',
    })  # 长须屠兽者为旧版印刷名
    VETERAN_OF_NANDUHIRION_ALLY_NAMES = frozenset({
        '南都希瑞安的老兵', '南都布理安的老兵',
        "Veteran of Nanduhirion",
    })
    BROK_IRONFIST_ALLY_NAMES = frozenset({"布洛克·铁拳", "铁拳布洛克"})
    EREBOR_CRAFTSMAN_ALLY_NAMES = frozenset({'埃瑞博铁匠', "依鲁伯铁匠", "Erebor Hammersmith", "Erebor Craftsman"})
    IRON_HILLS_PROSPECTOR_ALLY_NAMES = frozenset({"铁丘陵矿工", '铁丘陵的矿工', "Iron Hills Prospector"})
    ERED_NIMRAIS_PROSPECTOR_ALLY_NAMES = frozenset({"埃瑞德宁莱斯勘探者", "伊瑞德尼姆拉斯勘探者", "Ered Nimrais Prospector"})
    RIVENDELL_MINSTREL_ALLY_NAMES = frozenset({'幽谷吟游诗人', '瑞文戴尔乐手', "Rivendell Minstrel"})
    HENAMARTH_RIVERSONG_ALLY_NAMES = frozenset({"赫纳玛斯·河曲", "Henamarth Riversong", "Henamarth River-song"})
    GLEOWINE_ALLY_NAMES = frozenset({'格利奥威奈', '葛理欧温', "Gléowine", "Gleowine"})
    AMBORN_TRAP_RETURN_ALLY_NAMES = frozenset({"安博恩", "安朋", "Anborn"})
    ERESTOR_ALLY_NAMES = frozenset({"埃瑞斯托", "伊瑞斯特", "Erestor"})
    ERESTOR_HERO_NAMES = frozenset({"埃瑞斯托", "伊瑞斯特", "Erestor"})
    GRIMA_HERO_NAMES = frozenset({"葛力马", "格里马", "Gríma", "Grima"})
    ZIGIL_MINER_ALLY_NAMES = frozenset({"齐吉尔矿工", "西吉尔矿工", "Zigil Miner"})
    EREBOR_RECORD_KEEPER_ALLY_NAMES = frozenset({'埃瑞博撰史人', '依伯鲁撰史人', "Erebor Record Keeper"})
    WARDEN_OF_HEALING_ALLY_NAMES = frozenset({"医护官", "Warden of Healing"})
    EREBOR_BATTLE_MASTER_ALLY_NAMES = frozenset({'埃瑞博战斗大师', "依鲁伯战斗大师", "Erebor Battle Master"})
    LOSSARNACH_WARRIOR_ALLY_NAMES = frozenset({'洛斯阿尔那赫战士', "罗萨那奇的战士", "Warrior of Lossarnach"})
    SWAN_KNIGHT_ALLY_NAMES = frozenset({'天鹅骑士', "Knights of the Swan"})
    ETHIR_SWORDSMAN_ALLY_NAMES = frozenset({"埃希尔剑士", "伊瑟剑士", "Ethir Swordsman"})
    ANFALAS_HERDSMAN_ALLY_NAMES = frozenset({'安法拉斯牧民', "Anfalas Herdsman"})
    FORLONG_ALLY_NAMES = frozenset({'佛朗', '佛龙', "Forlong"})
    FORLONG_REQUIRED_INFLUENCE_SPHERES = frozenset({
        "领导",
        "学识",
        "精神",
        "战术"
    })
    DUNEDAIN_HUNTER_ALLY_NAMES = frozenset({"杜内丹猎人", "登丹人猎手"})
    RANGER_ALLIANCE_ALLY_NAMES = frozenset({"卡多蓝游民", "卡多兰游侠", "Ranger Alliance"})
    ROSEL_BEL_HERO_NAMES = frozenset({"罗塞尔", "Rosabel"})
    SARN_FORD_SENTRY_ALLY_NAMES = frozenset({"萨恩渡口哨兵"})
    ANNUMINAS_GUARDIAN_ALLY_NAMES = frozenset({"安努米那斯守护者", "阿努米那斯守卫"})
    FORNOST_BOWMAN_ALLY_NAMES = frozenset({"佛诺斯特箭手", "佛诺斯特弓箭手", "Fornost Bowman"})
    SUMMON_THE_WANDERER_EVENT_NAMES = frozenset({"召唤游民", "召唤游侠"})
    TIRELESS_HUNTER_EVENT_NAMES = frozenset({"不倦的猎手"})
    PROFESSIONAL_TRACKER_EVENT_NAMES = frozenset({"职业追踪者"})
    ELF_GEM_SMITH_ALLY_NAMES = frozenset({"精灵宝石匠", "精灵珠宝匠", "Elf Gem Smith", "Elf Jewel Smith"})
    OROPHIN_ALLY_NAMES = frozenset({"欧洛芬", "Orophin"})
    WANDERING_ENT_ALLY_NAMES = frozenset({"游荡的树人", "游荡的恩特", "Wandering Ent"})
    BOOMING_ENT_ALLY_NAMES = frozenset({"激愤的恩特", "激愤的树人", "Booming Ent"})
    HEIR_OF_MARDIL_ATTACHMENT_NAMES = frozenset({"马迪尔的后裔", "Heir of Mardil"})
    HEIR_OF_VALANDIL_ATTACHMENT_NAMES = frozenset({"维蓝迪尔的后裔", "瓦兰迪尔的子嗣"})
    ATHELAS_ATTACHMENT_NAMES = frozenset({"阿塞拉斯", "阿夕拉斯"})
    SECRET_VIGIL_ATTACHMENT_NAMES = frozenset({"秘密监视"})
    HARBOR_MASTER_ALLY_NAMES = frozenset({"港务总管", "Harbor Master"})
    WARDEN_OF_THE_HAVENS_ALLY_NAMES = frozenset({
        "灰港守护者",
        "海港守望者",
        "Warden of the Havens",
    })
    WHITE_TOWER_WATCHMAN_ALLY_NAMES = frozenset({"白塔守卫", "White Tower Watchman"})
    BARLIMAN_BUTTERBUR_ALLY_NAMES = frozenset({
        "巴力曼·奶油伯",
        "麦曼·黄油菊",
        "Barliman Butterbur",
    })
    TALE_OF_TINUVIEL_EVENT_NAMES = frozenset({
        "缇努维尔的传说", "提努维尔的传说", "Tale of Tinúviel",
    })
    DORI_HERO_NAMES = frozenset({"朵力", "Dori"})
    EREBOR_BOOTS_ATTACHMENT_NAMES = frozenset({'埃瑞博靴子', '依伯鲁之靴', "Boots from Erebor"})
    HARDY_LEADERSHIP_ATTACHMENT_NAMES = frozenset({"坚毅的领袖", "Hardy Leadership"})
    RING_MAIL_ATTACHMENT_NAMES = frozenset({'锁环甲', "Ring Mail"})
    ELVEN_MAIL_ATTACHMENT_NAMES = frozenset({'精灵锁甲', "Elven Mail"})
    WARDEN_OF_ARNOR_ATTACHMENT_NAMES = frozenset({'阿尔诺看守者', '亚尔诺看守者', "Warden of Arnor"})
    LEAF_BROOCH_ATTACHMENT_NAMES = frozenset({'绿叶胸针', "Leaf Brooch"})
    LOVE_OF_TALES_ATTACHMENT_NAMES = frozenset({'喜爱的故事', "Love of Tales"})
    GANDALF_ALLY_NAMES = frozenset({'甘道夫', "Gandalf"})
    # 《追击风暴召唤者》1 号领导英雄迪耐瑟；布置：资源池 +2。
    DENETHOR_HERO_OCTGN_BASE = "4a76ad60-35d1-4657-ab4a-2f7f15cc7ab7"
    # 《前路黑暗》2 号英雄甘道夫；CSV 的图片链接为空时，Card.id 为「前路黑暗-2」。
    THE_ROAD_DARKENS_GANDALF_OCTGN_BASE = "3e055edf-4540-4cf1-94ce-afbf5fc28f82"
    THE_ROAD_DARKENS_GANDALF_CARD_ID = "前路黑暗-2"
    THE_ROAD_DARKENS_GANDALF_SERIES = frozenset({"前路黑暗", "The Road Darkens"})
    # 《前路黑暗》3 号领导盟友凯兰崔尔；中文 CSV 的展示名称为「凯兰崔尔」。
    THE_ROAD_DARKENS_GALADRIEL_ALLY_OCTGN_BASE = "3d09a998-9ad0-4b98-9c42-50c4226cc73b"
    THE_ROAD_DARKENS_GALADRIEL_ALLY_CARD_ID = "前路黑暗-3"
    THE_ROAD_DARKENS_GALADRIEL_ALLY_SERIES = frozenset({"前路黑暗", "The Road Darkens"})
    # 《前路黑暗》4 号战术盟友波罗莫。
    THE_ROAD_DARKENS_BOROMIR_ALLY_OCTGN_BASE = "36b26228-1c01-4064-a109-d110e00d8e4b"
    THE_ROAD_DARKENS_BOROMIR_ALLY_CARD_ID = "前路黑暗-4"
    THE_ROAD_DARKENS_BOROMIR_ALLY_SERIES = frozenset({"前路黑暗", "The Road Darkens"})
    # 《前路黑暗》5 号学识盟友爱隆。
    THE_ROAD_DARKENS_ELROND_ALLY_OCTGN_BASE = "638638f0-8177-410a-bce8-9e2bbf5ad81f"
    THE_ROAD_DARKENS_ELROND_ALLY_CARD_ID = "前路黑暗-5"
    THE_ROAD_DARKENS_ELROND_ALLY_SERIES = frozenset({"前路黑暗", "The Road Darkens"})
    # 《前路黑暗》6 号精神盟友比尔博·巴金斯。
    THE_ROAD_DARKENS_BILBO_ALLY_OCTGN_BASE = "e02cc17b-80e4-4a8f-ba55-1df74aac816d"
    THE_ROAD_DARKENS_BILBO_ALLY_CARD_ID = "前路黑暗-6"
    THE_ROAD_DARKENS_BILBO_ALLY_SERIES = frozenset({"前路黑暗", "The Road Darkens"})
    # 《前路黑暗》7 号中立事件阿尔诺炽焰。
    THE_ROAD_DARKENS_FIRE_OF_ARNOR_OCTGN_BASE = "8a5ff815-56cf-4d5f-a649-a111e736a9f1"
    THE_ROAD_DARKENS_FIRE_OF_ARNOR_CARD_ID = "前路黑暗-7"
    THE_ROAD_DARKENS_FIRE_OF_ARNOR_SERIES = frozenset({"前路黑暗", "The Road Darkens"})
    # 《前路黑暗》8 号中立附属甘道夫的手杖。
    THE_ROAD_DARKENS_GANDALFS_STAFF_OCTGN_BASE = "02e56cef-e78d-4dbd-bd4d-6ec43e4b1d2b"
    THE_ROAD_DARKENS_GANDALFS_STAFF_CARD_ID = "前路黑暗-8"
    THE_ROAD_DARKENS_GANDALFS_STAFF_SERIES = frozenset({"前路黑暗", "The Road Darkens"})
    # 《萨鲁曼的背叛》14 号中立唯一附属影疾。
    SHADOWFAX_ATTACHMENT_OCTGN_BASE = "9f61eee8-cff2-43ad-8f82-7c4efb5ed9b8"
    SHADOWFAX_ATTACHMENT_CARD_ID = "143014"
    SHADOWFAX_ATTACHMENT_NAMES = frozenset({"影疾", "捷影", "Shadowfax"})
    # 《前路黑暗》9 号中立附属巫师的烟斗。
    THE_ROAD_DARKENS_WIZARD_PIPE_OCTGN_BASE = "428256aa-e03e-4f57-9b38-b8f7b5f17578"
    THE_ROAD_DARKENS_WIZARD_PIPE_CARD_ID = "前路黑暗-9"
    THE_ROAD_DARKENS_WIZARD_PIPE_SERIES = frozenset({"前路黑暗", "The Road Darkens"})
    # 《前路黑暗》10 号远征附属魔戒远征队。
    THE_ROAD_DARKENS_FELLOWSHIP_OF_RING_OCTGN_BASE = "d5f09d24-be50-4958-b0d0-41b1ad09b7af"
    THE_ROAD_DARKENS_FELLOWSHIP_OF_RING_CARD_ID = "前路黑暗-10"
    THE_ROAD_DARKENS_FELLOWSHIP_OF_RING_SERIES = frozenset({"前路黑暗", "The Road Darkens"})
    GANDALF_TOPDECK_PAYMENT_SPHERES = frozenset({
        "领导", "学识", "战术", "精神",
        "Leadership", "Lore", "Tactics", "Spirit",
    })
    GREYFLOOD_WANDERER_ALLY_NAMES = frozenset({'灰水河漫游者', '灰泛河流浪者', "Greyflood Wanderer"})
    ANORIEN_HERALD_ALLY_NAMES = frozenset({"安诺瑞安传令官", "Anórien Herald", "Anorien Herald"})
    ANORIEN_HERALD_OCTGN_BASE = "8c6be9f6-f97c-40a9-88da-2f3ba7f640ed"
    GWAIHIR_MOTK_ALLY_NAMES = frozenset({"关赫", "格怀希尔", "Gwaihir"})
    GWAIHIR_MOTK_OCTGN_BASE = "2e7f22cc-218e-4dad-b4e4-00b2208713d9"
    O_LORIEN_ATTACHMENT_NAMES = frozenset({"啊，罗瑞恩！", "喔，罗瑞安！", "O Lórien!", "O Lorien!"})
    O_LORIEN_OCTGN_BASE = "b4d93180-0ebc-4a9b-9817-e761068570e3"
    SARUMAN_ALLY_NAMES = frozenset({'萨茹曼', "萨鲁曼", "Saruman"})
    ORTHANC_GUARD_ALLY_NAMES = frozenset({
        '欧尔桑克禁卫', "欧散克塔卫兵", "Orthanc Guard",
    })
    ISENGARD_MESSENGER_ALLY_NAMES = frozenset({
        '艾森加德信使', "欧散克塔信使", "Isengard Messenger",
    })
    WESTFOLD_OUTRIDER_ALLY_NAMES = frozenset({
        "西伏尔德先驱者", "西谷先驱者", "Westfold Outrider",
    })
    WESTFOLD_HORSE_BREEDER_ALLY_NAMES = frozenset({
        "西伏尔德饲马师", "西谷饲马人", "Westfold Horse-breeder",
    })
    ROHAN_WARHORSE_ATTACHMENT_NAMES = frozenset({
        "洛汗战马", "Rohan Warhorse",
    })
    HERUGRIM_ATTACHMENT_OCTGN_BASE = "9fce5e18-37dc-45c2-8398-df8b5018cb54"
    ROHERYN_ATTACHMENT_NAMES = frozenset({
        "洛赫林", "Roheryn",
    })
    ROHERYN_ATTACHMENT_OCTGN_BASE = "83febe3a-95e8-4c38-8de7-4c1b1d43c917"
    GOLDEN_SHIELD_ATTACHMENT_NAMES = frozenset({
        "金色的盾牌", "黄金盾牌", "Golden Shield",
    })
    GOLDEN_SHIELD_ATTACHMENT_OCTGN_BASE = "a87da7c6-14fe-4213-b66f-43836531e213"
    SILVER_LAMP_ATTACHMENT_NAMES = frozenset({
        "银灯", "Silver Lamp",
    })
    KEYS_OF_ORTHANC_ATTACHMENT_NAMES = frozenset({
        "欧尔桑克钥匙", "欧散克塔钥匙", "Keys of Orthanc",
    })
    LEGACY_OF_NUMENOR_EVENT_NAMES = frozenset({
        "努曼诺尔的遗赠", "Legacy of Númenor",
    })
    DEEP_KNOWLEDGE_EVENT_NAMES = frozenset({
        "深层知识", "Deep Knowledge",
    })
    VOICE_OF_ISENGARD_EVENT_NAMES = frozenset({
        "巫师的声音", "Voice of Isengard",
    })
    POWER_OF_ORTHANC_EVENT_NAMES = frozenset({
        "欧尔桑克的力量", "欧散克塔的力量", "Power of Orthanc",
    })
    PALANTIR_EVENT_NAMES = frozenset({
        "真知晶石", "Palantír",
    })
    THORIN_OAKENSHIELD_HERO_NAMES = frozenset({
        "索林·橡木盾", "Thorin Oakenshield",
    })
    NORI_HERO_NAMES = frozenset({"诺力", "Nori"})
    IDRAEN_HERO_NAMES = frozenset({"伊德拉恩", "Idraen"})
    HALDIR_LORIEN_HERO_NAMES = frozenset({"罗瑞恩的哈尔迪尔", "Haldir of Lórien", "Haldir of Lorien"})
    ORI_HERO_NAMES = frozenset({"欧力", "Ori"})
    BEORN_HERO_NAMES = frozenset({"比翁", "Beorn"})
    CELEBORN_HERO_NAMES = frozenset({"凯勒博恩", "Celeborn"})
    GALADRIEL_HERO_NAMES = frozenset({"凯兰崔尔", "加拉德瑞尔", "Galadriel"})
    EKENBRAND_HERO_NAMES = frozenset({"埃肯布兰德", "鄂肯布兰德", "Ekenbrand"})
    FILI_ALLY_NAMES = frozenset({"菲力", "Fili"})
    KILI_ALLY_NAMES = frozenset({"奇力", "Kili"})
    BOFUR_WEAPON_ALLY_NAMES = frozenset({"波佛", "Bofur"})  # 战术版，区别于精神版
    DORI_ALLY_NAMES = frozenset({"朵力", "Dori"})  # 盟友版，区别于英雄版
    CRAM_ATTACHMENT_NAMES = frozenset({"干粮", "Cram"})
    LEMBAS_ATTACHMENT_NAMES = frozenset({"兰巴斯", "Lembas"})
    SPARE_HOOD_CLOAK_NAMES = frozenset({"斗篷和兜帽", "Spare Hood and Cloak"})
    THRORS_KEY_ATTACHMENT_NAMES = frozenset({"索尔的钥匙", "Thror's Key"})
    THRORS_MAP_ATTACHMENT_NAMES = frozenset({"索尔的地图", "Thror's Map"})
    MOUNTAIN_KING_ATTACHMENT_NAMES = frozenset({
        "山下之王",
        "山下国王",
        "King Under the Mountain",
    })
    TREASURE_HUNTER_ATTACHMENT_NAMES = frozenset({
        "职业寻宝猎人",
        "职业宝藏猎人",
        "Burglar Baggins",
    })
    A_VERY_GOOD_TALE_EVENT_NAMES = frozenset({"精彩的故事", "A Very Good Tale"})
    FOE_HAMMER_EVENT_NAMES = frozenset({"敌击剑", "Foe-hammer"})
    ORCRIST_EVENT_NAMES = frozenset({"兽咬剑", "Orcrist"})
    LATE_ADVENTURER_EVENT_NAMES = frozenset({"迟来的冒险者", "Late Adventurer"})
    FEIGNED_VOICES_EVENT_NAMES = frozenset({"诱敌之声", "Feigned Voices"})
    PURSUING_THE_ENEMY_EVENT_NAMES = frozenset({"追击敌人", "Pursuing the Enemy"})
    MESSAGE_FROM_ELROND_EVENT_NAMES = frozenset({"埃尔隆德的来信", "爱隆的来信", "Message from Elrond"})
    NOISELESS_MOVEMENT_EVENT_NAMES = frozenset(
        {"无声的移动", "悄无声息", "Noiseless Movement"}
    )
    EXPECTING_MISCHIEF_EVENT_NAMES = frozenset({"早有准备", "Expecting Mischief"})
    BURGLAR_BAGGINS_EVENT_NAMES = frozenset({"飞贼巴金斯", "Burglar Baggins"})
    LUCKY_NUMBER_EVENT_NAMES = frozenset({"幸运数字", "Lucky Number"})
    HALFLING_DETERMINATION_EVENT_NAMES = frozenset(
        {"半身人的决心", "Halfling Determination"}
    )
    SMOKE_RINGS_EVENT_NAMES = frozenset({"烟圈", "Smoke Rings"})
    FRODOS_INTUITION_EVENT_NAMES = frozenset(
        {"佛罗多的直觉", "弗罗多的直觉", "Frodo's Intuition"}
    )
    STAY_ALERT_EVENT_NAMES = frozenset({"保持警惕", "Stay Alert"})
    STRENGTH_OF_ARMS_EVENT_NAMES = frozenset({"武装的力量", "Strength of Arms"})
    TRAINED_FOR_WAR_EVENT_NAMES = frozenset({'战前操练', "Trained for War"})
    AGAINST_THE_SHADOW_EVENT_NAMES = frozenset({'对抗魔影', "Against the Shadow"})
    THE_SHADOW_GIVES_WAY_EVENT_NAMES = frozenset({"魔影退散", "魔影让道", "The Shadow Gives Way"})
    ADVANCE_WARNING_EVENT_NAMES = frozenset({"预先警报", "Advance Warning"})
    WELL_WARNED_EVENT_NAMES = frozenset({"获得警讯", "Well Warned"})
    WELL_WARNED_CODE = "16008"
    ONE_AGAINST_HUNDRED_EVENT_NAMES = frozenset({"以一当百", "One Against One Hundred"})
    ONE_AGAINST_HUNDRED_CODE = "16009"
    STAND_TOGETHER_EVENT_NAMES = frozenset({'同心协力', "Stand Together"})
    PEACE_AND_THOUGHT_EVENT_NAMES = frozenset({'沉思与冥想', "Peace, and Thought", "Peace and Thought"})
    FOR_GONDOR_EVENT_NAMES = frozenset({"刚铎万岁！", "For Gondor!"})
    GONDORIAN_DISCIPLINE_EVENT_NAMES = frozenset({'刚铎军戒', "Gondorian Discipline"})
    MUTUAL_ACCORD_EVENT_NAMES = frozenset({'同德一心', "Mutual Accord", "One Purpose"})
    WEALTH_OF_GONDOR_EVENT_NAMES = frozenset({'刚铎的财富', "Wealth of Gondor"})
    GAINING_STRENGTH_EVENT_NAMES = frozenset({'聚集力量', "Gaining Strength"})
    GOOD_HARVEST_EVENT_NAMES = frozenset({"丰收", "A Good Harvest"})
    BEHIND_STRONG_WALLS_EVENT_NAMES = frozenset({"高墙掩护", "Behind Strong Walls"})
    TAKING_INITIATIVE_EVENT_NAMES = frozenset({'采取主动', "Taking Initiative"})
    TIMELY_AID_EVENT_NAMES = frozenset({'及时的援助', "Timely Aid"})
    SECRET_TREASURE_EVENT_NAMES = frozenset({
        "隐藏的宝藏",
        "隐秘的珍宝",
        "隐蔽的窖藏",
        "Hidden Cache",
    })
    RISK_SOME_LIGHT_EVENT_NAMES = frozenset({'照探前路', "Risk Some Light"})
    SWIFT_AND_SILENT_EVENT_NAMES = frozenset({"迅捷无声", "Swift and Silent"})
    SECRECY_THREAT_THRESHOLD = 20
    VALOR_THREAT_THRESHOLD = 40
    VALOR_TRIGGER_LABELS = (
        "英勇计划行动",
        "英勇任务行动",
        "英勇战斗行动",
        "英勇遭遇行动",
        "英勇恢复行动",
        "英勇行动",
        "英勇响应",
        "Valor Planning Action",
        "Valor Quest Action",
        "Valor Combat Action",
        "Valor Encounter Action",
        "Valor Refresh Action",
        "Valor Action",
        "Valor Response",
        "Valour Planning Action",
        "Valour Quest Action",
        "Valour Combat Action",
        "Valour Encounter Action",
        "Valour Refresh Action",
        "Valour Action",
        "Valour Response",
    )
    NON_VALOR_ACTION_LABELS = (
        "计划行动",
        "任务行动",
        "战斗行动",
        "遭遇行动",
        "恢复行动",
        "行动",
        "Planning Action",
        "Quest Action",
        "Combat Action",
        "Encounter Action",
        "Refresh Action",
        "Action",
    )
    DURINS_SONG_EVENT_NAMES = frozenset({'都林之歌', '都灵之歌', "Durin's Song"})
    KHAZAD_KHAZAD_EVENT_NAMES = frozenset({"卡扎德！卡扎德！", "凯萨德！凯萨德！", "Khazâd! Khazâd!"})
    UNTROUBLED_BY_DARKNESS_EVENT_NAMES = frozenset({'适于黑暗', "Untroubled by Darkness"})
    LIGHT_THE_BEACONS_EVENT_NAMES = frozenset({'点燃烽火', "Light the Beacons"})
    WATCHFUL_PEACE_EVENT_NAMES = frozenset({'警戒的和平', "A Watchful Peace"})
    SUMMONS_OF_MORIA_EVENT_NAMES = frozenset({"墨瑞亚的召唤", "摩瑞亚的召唤", "Summons of Moria"})
    ANCESTRAL_KNOWLEDGE_EVENT_NAMES = frozenset({"先祖的知识", "先祖的学识", "Ancestral Knowledge"})
    EVER_ONWARD_EVENT_NAMES = frozenset({'勇往直前', "Ever Onward"})
    FREE_TO_CHOOSE_EVENT_NAMES = frozenset({'命运选择', "Free to Choose"})
    ASTONISHING_SPEED_EVENT_NAMES = frozenset({"惊人的速度", "Astonishing Speed"})
    CHILDREN_OF_THE_SEA_EVENT_NAMES = frozenset({'大海的子民', "Children of the Sea"})
    RUMOUR_FROM_EARTH_EVENT_NAMES = frozenset({'大地的线索', "Rumour from the Earth"})
    NEEDFUL_TO_KNOW_EVENT_NAMES = frozenset({"必要信息", "Needful to Know"})
    RAVENS_OF_THE_MOUNTAIN_EVENT_NAMES = frozenset({
        "山中的渡鸦",
        "山中的渡乌",
        "Ravens of the Mountain",
    })
    TO_ME_KINFOLK_EVENT_NAMES = frozenset({
        "跟我来！同胞们，冲啊！",
        "To me! O my kinsfolk!",
    })
    SHADOW_OF_THE_PAST_EVENT_NAMES = frozenset({'往昔阴影', "过往黯影", "Shadow of the Past"})
    SNEAK_ATTACK_EVENT_NAMES = frozenset({"偷袭", "Sneak Attack"})
    STRAIGHT_SHOT_EVENT_NAMES = frozenset({'瞄准射击', "Straight Shot"})
    DESPERATE_ALLIANCE_EVENT_NAMES = frozenset({
        "绝地之盟",
        "绝境之盟",
        "Desperate Alliance",
    })
    BOFUR_QUEST_ACTION_ALLY_NAMES = frozenset({"波弗", "波佛", "Bofur"})
    EMERY_ALLY_NAMES = frozenset({"埃梅瑞", "艾莫瑞", "Emery"})
    VALIANT_SACRIFICE_EVENT_NAMES = frozenset({'英勇牺牲', "Valiant Sacrifice"})
    GRAVE_CAIRN_EVENT_NAMES = frozenset({"石冢", "Grave Cairn"})
    THE_END_COMES_EVENT_NAMES = frozenset({"末日来临", "The End Comes"})
    RENEWED_FRIENDSHIP_EVENT_NAMES = frozenset({'重拾的友谊', "Renewed Friendship"})
    STEADFAST_RESOLVE_EVENT_NAMES = frozenset({'坚定的决心', "Steadfast Resolve"})
    CRISIS_OF_KINGS_EVENT_NAMES = frozenset({'存亡之秋', "Crisis of the Kings"})
    KINGLY_MAJESTY_EVENT_NAMES = frozenset({
        '王者之气', '王者威势', "Kingly Majesty",
    })
    HOLD_YOUR_GROUND_EVENT_NAMES = frozenset({'坚守阵地！', "Hold Your Ground!"})
    HOLD_THE_LINE_EVENT_NAMES = frozenset({"坚守战线", "Hold the Line", "Hold the Line!"})
    INSPIRING_PRESENCE_EVENT_NAMES = frozenset({
        "振奋人心", "Inspiring Presence", "Inspiring Fury",
    })
    HOUR_OF_WRATH_EVENT_NAMES = frozenset({'横扫千军', "Hour of Wrath"})
    LORDS_OF_THE_ELDAR_EVENT_NAMES = frozenset({'艾尔达精灵贵族', "埃尔达领主", "Lords of the Eldar"})
    ELVEN_LIGHT_EVENT_NAMES = frozenset({'精灵之光', "Elven-light", "精灵的光芒"})
    EXPERT_SWORDSMANSHIP_EVENT_NAMES = frozenset({'精于剑术', "Expert Swordsmanship"})
    UNSEEN_STRIKE_EVENT_NAMES = frozenset({'无影之击', "Unseen Strike"})
    ARROWS_RAIN_EVENT_NAMES = frozenset({"箭雨", "Rain of Arrows"})
    AIRBORNE_INTERCEPTION_EVENT_NAMES = frozenset({
        "空中截击",
        "Airborne Interception",
    })
    FEINT_EVENT_NAMES = frozenset({"佯攻", "Feint"})
    HOBBIT_SENSE_EVENT_NAMES = frozenset({'哈比人的直觉', "霍比特人的直觉", "Hobbit-sense"})
    DAWN_TAKE_YOU_ALL_EVENT_NAMES = frozenset({"曙光会照到你们所有人", "Dawn Take You All"})
    LIGHT_IN_THE_DARK_EVENT_NAMES = frozenset({"黑暗中的光芒", "Light in the Dark"})
    QUICK_STRIKE_EVENT_NAMES = frozenset({'先发制人', "Quick Strike"})
    HANDS_UPON_THE_BOW_EVENT_NAMES = frozenset({"张弓搭箭", "Hands Upon the Bow"})
    THICKET_OF_SPEARS_EVENT_NAMES = frozenset({'密集的长矛', "Thicket of Spears"})
    CLOSE_QUARTERS_EVENT_NAMES = frozenset({
        "白刃战", "短兵相接", "Close Quarters",
    })
    LINHIR_CAPTAIN_ALLY_NAMES = frozenset({
        "林何舰长", "林希尔舰长", "Captain of Linhir", "Linhir Captain",
    })
    WINDFOLA_ATTACHMENT_NAMES = frozenset({
        "温佛拉", "追风驹", "Windfola",
    })
    IORETH_ALLY_NAMES = frozenset({
        "攸瑞丝", "伊奥瑞丝", "Ioreth",
    })
    SULIEN_ALLY_NAMES = frozenset({
        "苏莉恩", "苏利恩", "Súlien", "Sulien",
    })
    OUT_OF_SIGHT_EVENT_NAMES = frozenset({"视线之外", "Out of Sight"})
    ELBERETH_GILTHONIEL_EVENT_NAMES = frozenset({"唵，伊尔芙·吉丝！埃尔贝瑞丝！", "O Elbereth! Gilthoniel!", "A Elbereth! Gilthoniel!"})
    SMALL_TARGET_EVENT_NAMES = frozenset({'弱小的目标', "Small Target"})
    SWIFT_BLOW_EVENT_NAMES = frozenset({'迅猛一击', "Swift Blow"})
    ANCHOR_WATCH_EVENT_NAMES = frozenset({"锚更", "锚表", "Anchor Watch"})
    THE_EVENING_STAR_EVENT_NAMES = frozenset({"暮星", "The Evening Star", "Evening Star"})
    ELWINGS_FLIGHT_EVENT_NAMES = frozenset({
        "埃尔汶的飞翔",
        "爱尔温的飞翔",
        "Elwing's Flight",
        "Elwings Flight",
    })
    DOL_AMROTH_SOLDIER_ALLY_NAMES = frozenset({
        "多尔安罗斯士兵",
        "多阿姆洛斯士兵",
        "Dol Amroth Soldier",
    })
    SIDE_BY_SIDE_EVENT_NAMES = frozenset({'并肩作战'})
    GALADHRIM_GREETINGS_EVENT_NAMES = frozenset({"加拉兹民的问候", "Galadhrim Greetings"})
    CAMPFIRE_STORIES_EVENT_NAMES = frozenset({'篝火故事', "Campfire Stories", "Tales by the Campfire"})
    DAERONS_RUNES_EVENT_NAMES = frozenset({"代隆的符文", "Daeron's Runes"})
    WELL_EQUIPPED_EVENT_NAMES = frozenset({"装备精良", "Well Equipped"})
    MISTY_MOUNTAINS_EAGLES_ALLY_NAMES = frozenset({"迷雾山脉鹰群", "Misty Mountains Eagles"})
    WING_OF_VIGILANCE_ALLY_NAMES = frozenset({'守护之翼', "Wing of Vigilance"})
    BRUINEN_WATCHER_ALLY_NAMES = frozenset({"布茹伊能河哨兵", "布鲁南河的哨兵", "Bruinen Watcher"})
    ARWEN_UNDOMIEL_ALLY_NAMES = frozenset({"阿尔玟·乌多米尔", "亚玟·安多米尔", "Arwen Undómiel"})
    ARWEN_UNDOMIEL_HERO_NAMES = frozenset({"阿尔玟·乌多米尔", "亚玟·安多米尔", "Arwen Undómiel"})
    STRENGTH_OF_WILL_EVENT_NAMES = frozenset({'意志之力', "Strength of Will"})
    TEST_OF_WILL_EVENT_NAMES = frozenset({'意志的考验', "A Test of Will"})
    DONT_BE_HASTY_EVENT_NAMES = frozenset({"不要仓促行事！", "不要仓促行事", "Don't Be Hasty!"})
    HASTY_STROKE_EVENT_NAMES = frozenset({'仓促的攻击', '轻率出击', "Hasty Stroke"})
    DESPERATE_DEFENSE_EVENT_NAMES = frozenset({
        "孤注一掷的防御",
        "绝望的防御",
        "Desperate Defense",
    })
    STERNER_THAN_STEEL_EVENT_NAMES = frozenset({
        "比钢铁更坚强",
        "钢铁般的意志",
        "Sterner than Steel",
    })
    RALLYING_CRY_EVENT_NAMES = frozenset({'奋起战斗', "Rallying Cry", "Stand and Fight"})
    RALLYING_CALL_EVENT_NAMES = frozenset({'集结号', "Rallying Call"})
    WILL_OF_THE_WEST_EVENT_NAMES = frozenset({"西方的意志", "Will of the West"})
    FORTUNE_OR_FATE_EVENT_NAMES = frozenset({'运气或命运', "Fortune or Fate"})
    HOSPITAL_EVENT_NAMES = frozenset({'医院', '诊疗院', "Hospital"})
    JUSTICE_SHALL_BE_DONE_EVENT_NAMES = frozenset({
        '正义终将被实现', '正义终将实现', "Justice Shall Be Done",
    })
    DWARF_TOMB_EVENT_NAMES = frozenset({"矮人坟墓", "Dwarf Tomb"})
    STRIDERS_PATH_EVENT_NAMES = frozenset({"大步佬之路", "神行客之路", "Strider's Path"})
    SHORT_CUT_EVENT_NAMES = frozenset({"捷径", "Short Cut"})
    HEIR_OF_MARDIL_EVENT_NAMES = frozenset({'列王的后嗣', '国王的后代', "Heir of Mardil"})
    FRESH_TRACKS_EVENT_NAMES = frozenset({'新的足迹', "Fresh Tracks"})
    LORE_OF_IMLADRIS_EVENT_NAMES = frozenset({"伊姆拉缀斯的学识", "Lore of Imladris"})
    ELRONDS_COUNSEL_EVENT_NAMES = frozenset({"埃尔隆德的忠告", "Elrond's Counsel"})
    LORIENS_WEALTH_EVENT_NAMES = frozenset({"罗瑞恩的财富", "Lórien's Wealth"})
    MITHRANDIRS_ADVICE_EVENT_NAMES = frozenset({"米斯兰迪尔的提议", "Mithrandir's Advice"})
    BEORNS_HOSPITALITY_EVENT_NAMES = frozenset({'贝奥恩的款待', "Beorn's Hospitality"})
    WATERS_OF_NIMRODEL_EVENT_NAMES = frozenset({"宁洛德尔河之水", "宁若戴尔河之水", "Waters of Nimrodel"})
    NOWHERE_TO_BE_FOUND_EVENT_NAMES = frozenset({'来去无踪', "Nowhere to Be Found"})
    DISTANT_STARS_EVENT_NAMES = frozenset({'遥远的星辰', "Distant Stars"})
    KEEN_EYED_EYE_EVENT_NAMES = frozenset({'目光如炬', "Keen-eyed Eye"})
    FOREST_SNARE_ATTACHMENT_NAMES = frozenset({"森林罗网", "Forest Snare"})
    RANGER_SUPPLY_ATTACHMENT_NAMES = frozenset({'游侠的储备', "Ranger's Supply"})
    RANGER_SPIKES_ATTACHMENT_NAMES = frozenset({"尖兵刺桩", '游侠刺桩', "Ranger Spikes"})
    RANGER_SPIKES_THREAT_REDUCTION = 2
    BOOMED_AND_TRUMPETED_EVENT_NAMES = frozenset({'怒吼狂呼', '咆哮大吼', "Boomed and Trumpeted"})
    DEAFENING_BLAST_EVENT_NAMES = frozenset({'震耳的号角声', "Deafening Blast"})
    RENEWED_HOPE_EVENT_NAMES = frozenset({'重燃希望', "Renewed Hope"})
    RENEWED_HOPE_DISCOUNT = 2
    UNLIKELY_FRIENDSHIP_EVENT_NAMES = frozenset({
        "非比寻常的友谊",
        "Unlikely Friendship",
    })
    UNLIKELY_FRIENDSHIP_CODE = "16007"
    NOT_THIS_TIME_EVENT_NAMES = frozenset({'休想得逞！', '门户依然关闭！', "Not this time!"})
    ITHILIEN_PIT_ATTACHMENT_NAMES = frozenset({'伊希利恩坑洞', "伊西立安陷阱", "Ithilien Pit"})
    POISONED_STAKES_ATTACHMENT_NAMES = frozenset({'剧毒木桩', "Poisoned Stakes"})
    POISONED_STAKES_ROUND_DAMAGE = 2
    ANCIENT_MATHOM_ATTACHMENT_NAMES = frozenset({"古老的玛瑟姆", "Ancient Mathom"})
    PATH_OF_NEED_ATTACHMENT_NAMES = frozenset({"必选之路", "Path of Need"})
    EVER_MY_HEART_RISES_ATTACHMENT_NAMES = frozenset({'心情转好', "Ever My Heart Rises"})
    FAVOR_OF_THE_VALAR_ATTACHMENT_NAMES = frozenset({'主神的眷顾', '维拉的看重', "Favor of the Valar"})
    RADAGASTS_CUNNING_EVENT_NAMES = frozenset({'拉达加斯特的机敏', "Radagast's Cunning"})
    SECRET_PATHS_EVENT_NAMES = frozenset({"秘密路径", "Secret Paths"})
    HAIL_OF_STONES_EVENT_NAMES = frozenset({"落石", "Hail of Stones"})
    RIDE_THEM_DOWN_EVENT_NAMES = frozenset({"冲倒他们", "Ride Them Down"})
    HEAVY_STROKE_EVENT_NAMES = frozenset({'重击', "Heavy Stroke"})
    REAR_GUARD_EVENT_NAMES = frozenset({'殿后', "Rear Guard"})
    NOT_IDLE_EVENT_NAMES = frozenset({"我们可没闲着", "我们并非无所事事", "We Are Not Idle"})
    WE_ARE_NOT_IDLE_EVENT_NAMES = frozenset({"我们并不是在沉睡", '我们枕戈待旦', "We Do Not Sleep"})
    RIDE_TO_RUIN_EVENT_NAMES = frozenset({'骑向毁灭', "Ride to Ruin"})
    GILDORS_COUNSEL_EVENT_NAMES = frozenset({'吉尔多的忠告', "Gildor's Counsel"})
    INFIGHTING_EVENT_NAMES = frozenset({'内斗', "Infighting"})
    SECOND_BREAKFAST_EVENT_NAMES = frozenset({"第二顿早餐", "Second Breakfast"})
    REINFORCEMENTS_EVENT_NAMES = frozenset({'增援', "Reinforcements"})
    WARRIORS_OF_THE_WEST_EVENT_NAMES = frozenset({'西方的战士', "Warriors of the West", "Men of the West"})
    FOREST_PATROL_EVENT_NAMES = frozenset({"森林巡逻", "Forest Patrol"})
    NO_RETURN_EVENT_NAMES = frozenset({'有来无回', "No Return"})
    DUNEDAINS_MESSAGE_EVENT_NAMES = frozenset({'杜内丹人的口信', "Dunedain's Message"})
    QUICK_EARS_EVENT_NAMES = frozenset({'灵敏的听觉', '敏锐的耳朵', "Quick Ears"})
    LONG_BEARD_SENTINEL_ALLY_NAMES = frozenset({'长须哨兵', "Long-beard Sentinel"})
    HAMMER_STROKE_EVENT_NAMES = frozenset({"一夫当关", '众矢之的', "The Hammer-stroke"})
    PALANTIR_ATTACHMENT_NAMES = frozenset({'真知晶球', "Palantir", "Palantir of Orthanc"})
    ELF_SPEAR_ATTACHMENT_NAMES = frozenset({'精灵长矛', '精灵长枪', "Elf Spear"})
    SILVER_HARP_ATTACHMENT_NAMES = frozenset({'银色的竖琴', '银竖琴', "Silver Harp"})
    ELF_FRIEND_ATTACHMENT_NAMES = frozenset({'精灵之友', "Elf Friend"})
    PALANTIR_NAMED_TYPE_OPTIONS = (
        ("敌人", "敌军"),
        ("地区", "地区"),
        ("诡计", "阴谋 / 诡计"),
        ("目标", "目标"),
    )
    PALANTIR_TYPE_PICK_BUTTON_HEIGHT = 120
    PALANTIR_TYPE_PICK_FONT_SIZE = 28
    GANDALFS_SEARCH_EVENT_NAMES = frozenset({"甘道夫的查阅", "甘道夫的搜寻", "Gandalf's Search"})
    WORD_OF_COMMAND_EVENT_NAMES = frozenset({"命令之语", "真言术", "Word of Command"})
    FIRE_OF_ARNOR_EVENT_NAMES = frozenset({"阿尔诺炽焰", "阿诺尔之火", "Fire of Arnor"})
    GANDALFS_STAFF_ATTACHMENT_NAMES = frozenset({
        "甘道夫的手杖", "甘道夫之杖", "Gandalf's Staff",
    })
    WIZARD_PIPE_ATTACHMENT_NAMES = frozenset({
        "巫师烟斗", "巫师的烟斗", "Wizard Pipe",
    })
    FELLOWSHIP_OF_RING_ATTACHMENT_NAMES = frozenset({
        "魔戒同盟", "魔戒远征队", "The Fellowship of the Ring",
        "Fellowship of the Ring",
    })
    BEAUTIFUL_AND_DANGEROUS_EVENT_NAMES = frozenset({'美丽又危险', '美丽并且危险', "Beautiful and Dangerous"})
    STEWARD_OF_GONDOR_ATTACHMENT_NAMES = frozenset({'刚铎宰相', '刚铎摄政王', "Steward of Gondor"})
    NARYA_ATTACHMENT_NAMES = frozenset({"纳雅", "Narya"})
    NARVI_BELT_ATTACHMENT_NAMES = frozenset({'纳维的腰带', "Narvi's Belt"})
    HORN_OF_GONDOR_ATTACHMENT_NAMES = frozenset({"刚铎号角", "刚铎的号角", "Horn of Gondor"})
    BLADE_OF_GONDOLIN_ATTACHMENT_NAMES = frozenset({"刚多林剑", "贡多林之剑", "Blade of Gondolin"})
    RIVENDELL_BLADE_ATTACHMENT_NAMES = frozenset({"幽谷剑", '瑞文戴尔之剑', "Rivendell Blade"})
    RIVENDELL_BOW_ATTACHMENT_NAMES = frozenset({"幽谷弓", '瑞文戴尔之弓', "Rivendell Bow"})
    GREAT_YEW_BOW_ATTACHMENT_NAMES = frozenset({"巨大的紫杉木弓", "巨大的紫衫木弓", "Great Yew Bow"})
    BLACK_ARROW_ATTACHMENT_NAMES = frozenset({"黑色的羽箭", "Black Arrow"})
    BOW_OF_THE_GALADHRIM_ATTACHMENT_NAMES = frozenset({"加拉兹弓", "凯兰崔姆之弓", "Bow of the Galadhrim"})
    IMLADRIS_STEED_ATTACHMENT_NAMES = frozenset({'伊姆拉缀斯骏马', '伊姆拉崔骏马', "Imladris Steed"})
    SCOUT_BOW_ATTACHMENT_NAMES = frozenset({"尖兵弓", '游侠之弓', "Ranger Bow", "Scout Bow"})
    RANGER_SPEAR_ATTACHMENT_NAMES = frozenset({"尖兵长矛", "游侠长矛", "Ranger Spear"})
    RANGER_SPEAR_CARD_CODES = frozenset({"12145", "海盗之城-145"})
    RANGER_SPEAR_OCTGN_BASES = frozenset({"80a03178-4039-4b2c-a2c4-2d546aa9b0b2"})
    GRAPPLING_HOOK_ATTACHMENT_NAMES = frozenset({"爪钩", "Grappling Hook"})
    EXPLORERS_ALMANAC_ATTACHMENT_NAMES = frozenset({
        "探索者的星历",
        "探索者的航行日志",
        "Explorer's Almanac",
        "Explorers Almanac",
    })
    MARINERS_COMPASS_ATTACHMENT_NAMES = frozenset({
        "水手的指南针",
        "海员的指南针",
        "Mariner's Compass",
        "Mariners Compass",
    })
    LIGHT_OF_VALINOR_ATTACHMENT_NAMES = frozenset({'维林诺之光', '瓦林诺之光', "Light of Valinor"})
    RIVENDELL_BLADE_DEFENSE_PENALTY = 2
    KCELEBRANTS_GEM_ATTACHMENT_NAMES = frozenset({"凯勒布莉安的宝石", "凯勒布里安的宝石", "Celebrant's Gem"})
    RING_OF_BARAHIR_ATTACHMENT_NAMES = frozenset({'巴拉西尔之戒', "Ring of Barahir"})
    SONG_OF_KINGS_ATTACHMENT_NAMES = frozenset({'国王之歌', "Song of Kings"})
    SONG_OF_WISDOM_ATTACHMENT_NAMES = frozenset({'智慧之歌', "Song of Wisdom"})
    SONG_OF_TRAVEL_ATTACHMENT_NAMES = frozenset({"旅行之歌", "Song of Travel"})
    SONG_OF_EARENDIL_ATTACHMENT_NAMES = frozenset({'埃雅仁迪尔之歌', "埃兰迪尔之歌", "Song of Earendil"})
    SONG_OF_BATTLE_ATTACHMENT_NAMES = frozenset({'战争之歌', "Song of Battle"})
    BROKEN_SWORD_ATTACHMENT_NAMES = frozenset({"断剑", "断折的圣剑", "Sword that was Broken"})
    FAST_HITCH_ATTACHMENT_NAMES = frozenset({'绑紧的绳结', "Fast Hitch"})
    PARTING_GIFT_EVENT_NAMES = frozenset({'临别的礼物', "Parting Gift"})
    FOLLOW_ME_EVENT_NAMES = frozenset({'跟我来！', "Follow Me!"})
    TIGHTEN_OUR_BELTS_EVENT_NAMES = frozenset({'勒紧裤带', '勒紧我们的裤带', "Tighten Our Belts"})
    ISLAND_AMID_PERILS_EVENT_NAMES = frozenset({'险域中的孤岛', "Island Amid Perils"})
    IMRAHIL_HERO_NAMES = frozenset({"伊姆拉希尔亲王", "印拉希尔王子", "Prince Imrahil"})
    EOMER_HERO_NAMES = frozenset({"伊奥梅尔", "伊欧墨", "Éomer", "Eomer"})
    MABLUNG_HERO_NAMES = frozenset({"马伯龙", "玛布隆", "Mablung"})
    FIREFOOT_ATTACHMENT_NAMES = frozenset({"火蹄", "Firefoot"})
    CITADEL_PLATE_ATTACHMENT_NAMES = frozenset({'王城板甲', '都城铠甲', "Citadel Plate"})
    GONDORIAN_SHIELD_ATTACHMENT_NAMES = frozenset({"刚铎盾", '刚铎之盾', "Gondorian Shield"})
    DWARVEN_SHIELD_ATTACHMENT_NAMES = frozenset({"\u77ee\u4eba\u76fe", "\u77ee\u4eba\u4e4b\u76fe", "Dwarven Shield"})
    DWARVEN_SHIELD_CODE = "16010"
    MIRKWOOD_LONG_KNIFE_ATTACHMENT_NAMES = frozenset({
        "幽暗密林长刀",
        "黑森林长刀",
        "Mirkwood Long-knife",
        "Mirkwood Long Knife",
    })
    MIRKWOOD_LONG_KNIFE_CODE = "16011"
    MIRKWOOD_LONG_KNIFE_OCTGN_BASE = "565ab52d-0390-4f39-8e6d-7809444b53af"
    DWARF_AXE_ATTACHMENT_NAMES = frozenset({"矮人斧", "矮人战斧", "Dwarf Axe"})
    DWARROWDELF_AXE_ATTACHMENT_NAMES = frozenset({"矮人挖凿斧", "矮人故乡之斧", "Dwarrowdelf Axe"})
    KEEPING_COUNT_ATTACHMENT_NAMES = frozenset({'持续击杀', "Keeping Count"})
    GOOD_MEAL_ATTACHMENT_NAMES = frozenset({"美味的餐食", "Good Meal"})
    MIRUVOR_ATTACHMENT_NAMES = frozenset({"米茹沃", "米卢活", "Miruvor"})
    VILYA_ATTACHMENT_NAMES = frozenset({"维雅", "Vilya"})
    NENYA_ATTACHMENT_NAMES = frozenset({"南雅", "Nenya"})
    GREETING_THE_DAWN_ATTACHMENT_NAMES = frozenset({"迎接晨光"})
    CAPTAIN_OF_GONDOR_ATTACHMENT_NAMES = frozenset({"刚铎的统帅", "刚铎的将军", "Captain of Gondor"})
    CLOAK_OF_LORIEN_ATTACHMENT_NAMES = frozenset({
        "罗瑞安斗篷",
        "Cloak of Lórien",
        "Cloak of Lorien",
    })
    GALADRIEL_MIRROR_ATTACHMENT_NAMES = frozenset({
        "凯兰崔尔之镜",
        "加拉德瑞尔的水镜",
        "Mirror of Galadriel",
        "Galadriel's Mirror",
    })
    LEGACY_OF_DURIN_ATTACHMENT_NAMES = frozenset({'都林的遗产', '都灵的遗赠', "Legacy of Durin"})
    LORD_OF_MORTHOND_ATTACHMENT_NAMES = frozenset({"墨松德领主", "摩颂河领主", "Lord of Morthond"})
    SWORD_OF_MORTHOND_ATTACHMENT_NAMES = frozenset({'墨松德之剑', "摩颂河之剑", "Sword of Morthond"})
    PRINCE_OF_DOL_AMROTH_ATTACHMENT_NAMES = frozenset({"多阿姆洛斯亲王", "多尔安罗斯王", "Prince of Dol Amroth"})
    PRINCE_OF_DOL_AMROTH_CARD_CODES = frozenset({"12146", "海盗之城-146"})
    PRINCE_OF_DOL_AMROTH_OCTGN_BASES = frozenset({"3903cf5b-0ca8-4cd9-8943-490a7af601e5"})
    BOOK_OF_ELDACAR_ATTACHMENT_NAMES = frozenset({'埃尔达卡之书', '艾尔达卡之书', "Book of Eldacar"})
    MAP_OF_EARNIL_ATTACHMENT_NAMES = frozenset({'埃雅尼尔地图', '艾尼尔地图', "Map of Earnil"})
    ATANATAR_TOME_ATTACHMENT_NAMES = frozenset({"阿塔那塔典籍", "雅坦纳托典籍", "Tome of Atanatar", "Atanatar's Edicts"})
    RESOURCEFUL_ATTACHMENT_NAMES = frozenset({'资源丰富', "Resourceful"})
    GOOD_MEAL_EVENT_DISCOUNT = 2
    LADYS_FAVOR_ATTACHMENT_NAMES = frozenset({"夫人的眷顾", "女皇的信任", "Lady's Favor", "Ladies Favor"})
    DARK_KNOWLEDGE_ATTACHMENT_NAMES = frozenset({'黑暗知识', "Dark Knowledge"})
    WINGED_HELM_ATTACHMENT_NAMES = frozenset({
        '鸦翼头盔', '鸦翼盔', "Winged Helm", "Winged Helmet",
    })
    LIVERY_OF_THE_TOWER_ATTACHMENT_NAMES = frozenset({
        "白塔的制服", "白塔制服", "Livery of the Tower",
    })
    LIVERY_OF_THE_TOWER_ATTACHMENT_OCTGN_BASE = "6fa385cc-3430-44ce-9761-d0558e2b569d"
    WEATHER_STAINED_CLOAK_ATTACHMENT_NAMES = frozenset({
        "风吹雨淋的斗篷", "破旧的衣物", "Weather-stained Cloak",
    })
    SWORD_BEARER_ATTACHMENT_NAMES = frozenset({
        "佩剑侍从", "Sword-bearer", "Squire of the Sword",
    })
    DEFENDER_OF_THE_WEST_ATTACHMENT_NAMES = frozenset({
        "西方守护者", "Defender of the West",
    })
    STAR_SHAPED_BROOCH_ATTACHMENT_NAMES = frozenset({
        "星形别针", "星形领针", "Star-shaped Brooch", "Star Shaped Brooch",
    })
    BURNING_TORCH_ATTACHMENT_NAMES = frozenset({'燃烧的火把', '燃烧的木棍', "A Burning Brand", "Burning Brand"})
    STRENGTH_OF_EARTH_ATTACHMENT_NAMES = frozenset({'大地的力量', "Strength of the Earth"})
    UNEXPECTED_COURAGE_ATTACHMENT_NAMES = frozenset({'突来勇气', "Unexpected Courage"})
    HOBBIT_PONY_ATTACHMENT_NAMES = frozenset({
        '霍比特人小马', '哈比人小马', "Hobbit Pony",
    })
    HOBBIT_CLOAK_ATTACHMENT_NAMES = frozenset({
        '霍比特人斗篷', '哈比人斗篷', "Hobbit Cloak",
    })
    DAGGER_OF_WESTERNESSE_ATTACHMENT_NAMES = frozenset({
        '西方之地的匕首', '西方皇族的短剑', "Dagger of Westernesse",
    })
    HOBBIT_PIPE_ATTACHMENT_NAMES = frozenset({
        '霍比特人烟斗', '哈比人的烟斗', "Hobbit Pipe",
    })
    ELF_STONE_ATTACHMENT_NAMES = frozenset({
        '精灵宝石', "Elf-stone", "Elf Stone",
    })
    SELF_PRESERVATION_ATTACHMENT_NAMES = frozenset({"自我保护", "Self Preservation"})
    HEALING_HERBS_ATTACHMENT_NAMES = frozenset({"治疗草药", "治愈药草", "Healing Herbs"})
    ASFALOTH_ATTACHMENT_NAMES = frozenset({'阿斯法洛斯', "Asfaloth"})
    PROTECTOR_OF_LORIEN_ATTACHMENT_NAMES = frozenset({'罗瑞恩的保护者', "罗瑞安守护者", "Protector of Lorien"})
    SONG_OF_MOCKING_ATTACHMENT_NAMES = frozenset({"嘲弄之歌", "Song of Mocking"})
    VIGILANT_GUARD_ATTACHMENT_NAMES = frozenset({
        "警惕守护", "警惕禁卫", "Vigilant Guard",
    })
    PROTECTOR_OF_LORIEN_PHASE_LIMIT = 3
    TO_THE_SEA_ATTACHMENT_NAMES = frozenset({
        "向海！向海！",
        "向大海，向大海！",
        "To the Sea, to the Sea!",
        "To the Sea to the Sea",
    })
    BLOOD_OF_NUMENOR_ATTACHMENT_NAMES = frozenset({"努门诺尔的血统", "努曼诺尔的血统", "Blood of Numenor"})
    SWORD_OF_NUMENOR_ATTACHMENT_NAMES = frozenset({"努门诺尔剑", "努曼诺尔之剑", "Sword of Númenor", "Sword of Numenor"})
    RUNE_MASTER_ATTACHMENT_NAMES = frozenset({
        "符文大师", "Rune-master", "Runemaster",
    })
    GONDORIAN_FIRE_ATTACHMENT_NAMES = frozenset({'刚铎之火', "Gondorian Fire"})
    ARAGORN_HERO_NAMES = frozenset({"阿拉贡", "亚拉冈", "Aragorn"})
    EXPEDITION_ARAGORN_OCTGN_BASE = "f5178135-1485-43c5-9660-99232a4cdca8"
    FLAME_OF_THE_WEST_ARAGORN_OCTGN_BASE = "2e778689-9e3f-4da8-a473-781c777f6473"
    EXPEDITION_ARAGORN_OCTGN_BASES = frozenset({
        "f5178135-1485-43c5-9660-99232a4cdca8",
        "2e778689-9e3f-4da8-a473-781c777f6473",
    })
    EXPEDITION_ARAGORN_CARD_CODES = frozenset({
        "145001",
        "西方之炎-1",
    })
    THEODEN_EXPEDITION_OCTGN_BASE = "f3d88160-30f2-4b8b-9e83-c42e62851bbc"
    TREEBEARD_HERO_OCTGN_BASE = "94f6738a-c946-4415-9b38-0ce3a443fa33"
    GIMLI_ALLY_OCTGN_BASE = "6a9ff9c1-64d2-4ed5-a793-9e93e8d605b7"
    LEGOLAS_ALLY_OCTGN_BASE = "9825aa22-f197-4320-972d-e2487197d989"
    QUICKBEAM_ALLY_OCTGN_BASE = "f37fc371-5a6e-4a24-85a1-2afef11c6841"
    HAMA_ALLY_OCTGN_BASE = "adc3e6f6-da67-4cc1-9767-8e87dfc91b99"
    AROD_ATTACHMENT_OCTGN_BASE = "6646bb07-0b44-4051-94a7-39c600b38481"
    ENT_DRAUGHT_ATTACHMENT_OCTGN_BASE = "b86ba0f8-11f9-4694-b421-17b683bd4325"
    BILBO_BAGGINS_NAMES = frozenset({'比尔博·巴金斯', "Bilbo Baggins"})
    CIRDAN_SHIPWRIGHT_NAMES = frozenset({
        "造船者奇尔丹",
        "奇尔丹",
        "Cirdan the Shipwright",
        "Círdan the Shipwright",
    })
    GALDOR_HAVENS_HERO_NAMES = frozenset({
        "海港的加尔多",
        "灰港的加尔多",
        "加尔多",
        "Galdor of the Havens",
    })
    GALDOR_HAVENS_ALLY_NAMES = frozenset({
        "海港的加尔多",
        "灰港的加尔多",
        "Galdor of the Havens",
    })
    HALBARAD_HERO_NAMES = frozenset({"哈尔巴拉德", "贺尔巴拉", "Halbarad"})
    AMARTHIUL_HERO_NAMES = frozenset({"阿玛希尔", "阿玛希乌", "Amarthiul"})
    MITHLOND_SEA_WATCHER_NAMES = frozenset({
        "米斯泷德望海者",
        "米斯龙德望海者",
        "米斯泷德望海者",
        "Mithlond Sea-watcher",
        "Mithlond Sea Watcher",
    })
    SAILOR_OF_LUNE_ALLY_NAMES = frozenset({
        "路恩船员",
        "隆恩水手",
        "Sailor of Lune",
    })
    DUNADAN_MARK_CLASSIC_NAMES = frozenset({'杜内丹人的标记', "登丹人的标记", "Dunedain Mark"})
    DUNEDAIN_SIGNAL_NAMES = frozenset({"杜内丹人的记号", "登丹人的记号", "Dunedain Signal"})
    DUNAGORN_MARK_NAMES = DUNADAN_MARK_CLASSIC_NAMES | DUNEDAIN_SIGNAL_NAMES
    DUNADAN_MARK_CLASSIC_OCTGN_BASE = "51223bd0-ffd1-11df-a976-0801201c9002"
    DUNEDAIN_SIGNAL_OCTGN_BASE = "51223bd0-ffd1-11df-a976-0801206c9008"
    DUNEDAIN_WARNING_ATT_NAMES = frozenset({"杜内丹人的警告", "登丹人的警示", "Dunedain Warning"})
    DUNEDAIN_QUEST_ATT_NAMES = frozenset({'杜内丹人的探险', '登丹人的任务', "Dunedain Quest"})
    GATHER_INFORMATION_QUEST_NAMES = frozenset({
        "收集信息", "Gather Information",
    })
    DUNEDAIN_PACK_ATT_NAMES = frozenset({"杜内丹人的行囊", "登丹人的行囊", "Dunedain Cache"})
    BORN_ALOFT_ATTACHMENT_NAMES = frozenset({'空中重生', "Born Aloft"})
    SUPPORT_OF_THE_EAGLES_ATTACHMENT_NAMES = frozenset({'大鹰的支持', '巨鹰的支援', "Support of the Eagles"})
    GONDORIAN_SPEARMAN_ALLY_NAMES = frozenset({'刚铎长矛手', '刚铎长枪兵', "Gondorian Spearman"})
    CITADEL_SPEAR_ATTACHMENT_NAMES = frozenset({'王城长矛', '都城之矛', "Spear of the Citadel"})
    SPEAR_OF_THE_MARK_ATTACHMENT_NAMES = frozenset({"马克的长矛", "骠骑之枪", "Spear of the Mark"})
    MIGHTY_PROWESS_ATTACHMENT_NAMES = frozenset({'勇猛无比', "Mighty Prowess"})
    ERRAND_RIDER_ALLY_NAMES = frozenset({'信使骑手', '跑腿的信差', "Errand-rider"})
    DAMROD_ALLY_NAMES = frozenset({"达姆罗德", "丹姆拉", "Damrod"})
    CITADEL_CUSTODIAN_ALLY_NAMES = frozenset({'王城看守者', '都城守护者', "Citadel Custodian"})
    GUTHLAF_ALLY_NAMES = frozenset({"古斯拉夫", "Guthlaf"})
    ELROHIR_TWIN_NAMES = frozenset({"埃洛希尔", "爱罗希尔", "Elrohir"})
    ELLADAN_TWIN_NAMES = frozenset({"埃尔拉丹", '爱拉丹', "Elladan"})
    HAMA_HERO_NAMES = frozenset({'哈玛', '哈马', "Hama"})
    THEODEN_TACTICS_AURA_HERO_OCTGN_BASE = (
        "29d5feef-6165-4077-bb80-692526a8a924"
    )
    HAMA_RESPONSE_GAME_LIMIT = 3
    BEORN_ALLY_NAMES = frozenset({"贝奥恩", "比翁", "Beorn"})
    RADAGAST_ALLY_NAMES = frozenset({"拉达加斯特", "瑞达加斯特", "Radagast"})
    BEORNING_BEEKEEPER_ALLY_NAMES = frozenset({'贝奥恩养蜂人', "比翁养蜂人", "Beorning Beekeeper"})
    BEECHBONE_ALLY_NAMES = frozenset({'柏骨', "Beechbone"})
    DUNEDAIN_WATCHER_ALLY_NAMES = frozenset({'杜内丹守望者', '登丹人的守望者', "Dunedain Watcher"})
    WEATHER_HILLS_WATCHMAN_ALLY_NAMES = frozenset({
        "风云丘陵看守者",
        "风云丘守望者",
        "Weather Hills Watchman",
    })
    ITHILIEN_LOOKOUT_ALLY_NAMES = frozenset({
        "伊西利安远望者",
        "伊希利恩远望者",
        "Ithilien Lookout",
    })
    CELDUIN_TRAVELER_ALLY_NAMES = frozenset({
        "凯尔都因河旅人",
        "赛尔督因河旅者",
        "Celduin Traveler",
    })
    MINAS_TIRITH_LAMPWRIGHT_ALLY_NAMES = frozenset({"米那斯提力斯制灯匠", "Minas Tirith Lampwright"})
    MINAS_TIRITH_KNIGHT_ALLY_NAMES = frozenset({"米那斯提力斯骑士", "米那斯提力斯的骑士", "Knight of Minas Tirith"})
    LAMPWRIGHT_NAMED_TYPE_OPTIONS = (
        ('敌人', '敌军'),
        ('地区', '地区'),
        ("诡计", "阴谋 / 诡计"),
    )
    WESTFOLD_HORSE_TRAINER_ALLY_NAMES = frozenset({"西伏尔德驯马师", "西谷驯马师", "Westfold Horse-trainer"})
    RIDDERMARKS_FINEST_ALLY_NAMES = frozenset({"里德马克之冠", '骠骑国的骏马', "The Riddermark's Finest"})
    RAVENHILL_SCOUT_ALLY_NAMES = frozenset({"渡鸦岭斥候", "乌丘斥候", "Ravenhill Scout"})
    GILDOR_INGLORION_ALLY_NAMES = frozenset({'吉尔多·英格罗瑞安', '吉尔多·印格罗瑞安', "Gildor Inglorion"})
    IMLADRIS_STARGAZER_ALLY_NAMES = frozenset({"伊姆拉缀斯占星师", "伊姆拉崔占星师", "Imladris Stargazer"})
    MASTER_OF_THE_FORGE_ALLY_NAMES = frozenset({'铸炉大师', "Master of the Forge"})
    EDORAS_ESCORT_ALLY_NAMES = frozenset({"埃多拉斯护卫队", "伊多拉斯护卫队", "Escort from Edoras"})
    LINDON_NAVIGATOR_ALLY_NAMES = frozenset({"林顿领航员", "Lindon Navigator"})
    EOMUND_ALLY_NAMES = frozenset({"伊奥蒙德", "伊欧蒙德", "Eomund"})
    DEFENDER_OF_NAITH_ALLY_NAMES = frozenset({"耐斯防御者", "林心守卫", "Defender of the Naith"})
    EOTHAIN_ALLY_NAMES = frozenset({"伊奥泰因", "伊欧参", "Éothain", "Eothain"})
    LANDROVAL_ALLY_NAMES = frozenset({"蓝德洛瓦", "兰楚瓦", "Landroval"})
    DESCENDANT_OF_THORONDOR_ALLY_NAMES = frozenset({"梭隆多的后代", '鹰王索隆多的子嗣', "Descendant of Thorondor"})
    WINGED_STEWARD_VASSAL_ALLY_NAMES = frozenset({'风王的臣属', '风王的臣民', "Vassal of the Windlord"})
    ELFHELM_TDM_ALLY_NAMES = frozenset({"埃尔夫海尔姆", "艾海姆", "Elfhelm"})
    ELFHELM_THREAT_RESPONSE_SOURCES = frozenset({"quest_fail", "encounter_effect", "quest_effect"})
    SILVAN_TRACKER_ALLY_NAMES = frozenset({"西尔凡追踪者", "Silvan Tracker"})
    SILVAN_REFUGEE_ALLY_NAMES = frozenset({"西尔凡流亡者", "西尔凡难民", "Silvan Refugee"})
    NAITH_GUIDE_ALLY_NAMES = frozenset({"耐斯向导", "林心引路人", "Naith Guide"})
    NOT_A_STRANGER_ATT_NAMES = frozenset({'我并不是陌生人', "我并非陌生人", "I Am Not a Stranger"})
    LONGBEARD_MAPMAKER_ALLY_NAMES = frozenset({'长须制图者', "Longbeard Map-maker", "Longbeard Map-Maker"})
    HONOR_GUARD_ALLY_NAMES = frozenset({'荣誉禁卫', '荣誉守卫', "Honor Guard"})
    OSGILIAH_VETERAN_ALLY_NAMES = frozenset({
        '欧斯吉利亚斯老兵', '奥斯吉力亚斯老兵', "Osgiliath Veteran",
    })
    DERNDINGLE_WARRIOR_ALLY_NAMES = frozenset({
        '秘林谷战士', '德丁哥战士', "Derndingle Warrior",
    })
    CURIOUS_BUCKLANDERS_NAMES = frozenset({
        '好奇的白兰地鹿', '好奇的烈酒鹿', "Curious Bucklanders", "Curious Brandywine",
    })
    EAST_ROAD_RANGER_NAMES = frozenset({
        '东大道游民', '东大道游侠', "East Road Ranger", "East Road Wanderer",
    })
    SCOUTS_AHEAD_QUEST_NAMES = frozenset({
        '斥候在前', "Scouts Ahead",
    })
    BACK_ON_THE_PATH_QUEST_NAMES = frozenset({'原路返回', "Back on the Path"})
    DELAY_THE_ENEMY_QUEST_NAMES = frozenset({'拖延敌军', '拖延敌人', "Delay the Enemy"})
    APPEAL_FOR_AID_QUEST_NAMES = frozenset({'请求援助', "Appeal for Aid"})
    SPRINGHALL_PROTECTOR_ALLY_NAMES = frozenset({'涌泉厅维护者', '威灵厅保护者', "Springhall Protector"})
    BEORN_PATH_QUEST_NAME = '贝奥恩之路'
    DONT_LEAVE_PATH_QUEST_NAME = "千万不要离开正路！"
    DONT_LEAVE_PATH_QUEST_NAMES = frozenset({"千万不要离开正路！", "\"千万不要离开正路！\"", "A Chosen Path (Don't Leave the Path)"})
    UNGOLIANT_SPAWN_NAMES = frozenset(
        {'乌苟立安特的子嗣', "Ungoliant's Spawn"}
    )
    HILL_TROLL_NAMES = frozenset({"山区食人妖", "Hill Troll"})
    GOBLIN_SNIPER_NAMES = frozenset({"半兽人射手", "Goblin Sniper"})
    WARGS_NAMES = frozenset({"座狼", "Wargs"})
    TO_THE_RIVER_QUEST_NAMES = frozenset({"来到河边", "To the River..."})
    ANDUIN_PASSAGE_QUEST_NAMES = frozenset({"安都因河的航程", "Anduin Passage"})
    AMBUSH_ON_SHORE_QUEST_NAMES = frozenset({"岸上的伏击", "Ambush on the Shore"})
    DESPAIR_NAMES = frozenset({"陷入绝望", "Despair"})
    EVIL_STORM_NAMES = frozenset({"邪恶的风暴", "Evil Storm"})
    VICIOUS_MARAUDER_NAMES = frozenset({
        "邪恶的掠夺者",
        "邪恶的惊奇者",
        "Vicious Marauder",
    })
    VICIOUS_MARAUDER_OCTGN_BASE = "2362af02-42b8-43fe-b3b6-f11f011b3197"
    CORSAIR_INFILTRATOR_NAMES = frozenset({
        "海盗潜入者",
        "Corsair Infiltrator",
    })
    CORSAIR_INFILTRATOR_OCTGN_BASE = "d7885efa-3af6-48e6-8eb1-5750892fdb34"
    PURSUED_BY_SHADOW_NAMES = frozenset({"魔影追击", "Pursued by Shadow"})
    TREACHEROUS_FOG_NAMES = frozenset({"险恶的迷雾", "Treacherous Fog"})
    KING_SPIDER_NAMES = frozenset({'王蜘蛛', "King Spider"})
    FOREST_SPIDER_NAMES = frozenset({"森林蜘蛛", "Forest Spider"})
    DOL_GULDUR_ORC_NAMES = frozenset({'多古尔都奥克', "Dol Guldur Orc", "Dol Guldur Orcs"})
    EAST_BIGHT_PATROL_NAMES = frozenset({"东林弯巡逻队", "East Bight Patrol"})
    MARSH_ADDER_NAMES = frozenset({"沼泽蝰蛇", "Marsh Adder"})
    BLACK_FOREST_BATS_NAMES = frozenset({"漆黑森林蝙蝠", "Black Forest Bats"})
    HUMMERHORNS_NAMES = frozenset({"蜇刺毒蜂", "Hummerhorns"})
    EASTERN_CROWS_NAMES = frozenset({"东方的乌鸦", "东方的鸟鸦", "Eastern Crows"})
    UFTHAK_NAMES = frozenset({"乌夫沙克首领", "Chieftain Ufthak"})
    STORMCALLER_ELITE_NAMES = frozenset({
        "萨伊尔船长",
        "萨希尔船长",
        "Captain Sahír",
        "Captain Sahir",
        "娜阿西雅",
        "Na'asiyah",
    })
    STORMCALLER_ELITE_CARD_IDS = frozenset({
        "97bf780d-cd76-492f-a970-37f78273c5bb",
        "f0d30258-7122-4004-a689-9ff50a00b3af",
    })
    UMBAR_COAST_STORMCALLER_NAMES = frozenset({"风暴召唤者", "Stormcaller"})
    UMBAR_COAST_STORMCALLER_OCTGN_BASE = (
        "c45dc401-bb07-42ec-ba4c-ee9105a3f349"
    )
    CAPTAIN_SAHIR_NAMES = frozenset({
        "萨伊尔船长",
        "萨希尔船长",
        "Captain Sahír",
        "Captain Sahir",
    })
    CAPTAIN_SAHIR_OCTGN_BASE = "97bf780d-cd76-492f-a970-37f78273c5bb"
    CITY_OF_CORSAIRS_CAPTAIN_SAHIR_OCTGN_BASE = (
        "c354e410-e15a-4999-bb14-696b5e89cee9"
    )
    PLAYER_FLEET_SHIP_ORDER = (
        "逐梦者号",
        "黎明之星号",
        "纳瑞兰雅号",
        "银翼号",
    )
    PLAYER_FLEET_SHIP_ID_ORDER = (
        "07a8f60f-926c-4342-bf04-e3af9a24df10",
        "81632d10-625e-42f0-9e6b-09574a9ffbc3",
        "32db1af6-e214-4958-9ca4-bdf3a73758b5",
        "c9139d8c-0d9d-4d34-bfb5-8d40ca455bfc",
    )
    PLAYER_FLEET_SHIP_NAMES = frozenset({
        "逐梦者号",
        "Dream-chaser",
        "Dream Chaser",
        "黎明之星号",
        "Dawn Star",
        "纳瑞兰雅号",
        "Nárelenya",
        "Narelenya",
        "银翼号",
        "Silver Wing",
    })
    DREAM_CHASER_SHIP_NAMES = frozenset({
        "逐梦者号",
        "Dream-chaser",
        "Dream Chaser",
    })
    DAWN_STAR_SHIP_NAMES = frozenset({
        "黎明之星号",
        "Dawn Star",
    })
    DAWN_STAR_SHIP_OCTGN_BASE = "81632d10-625e-42f0-9e6b-09574a9ffbc3"
    NARELENYA_SHIP_NAMES = frozenset({
        "纳瑞兰雅号",
        "Nárelenya",
        "Narelenya",
    })
    NARELENYA_SHIP_OCTGN_BASE = "32db1af6-e214-4958-9ca4-bdf3a73758b5"
    PLAYER_FLEET_SHIP_IDS = frozenset(PLAYER_FLEET_SHIP_ID_ORDER)
    DOL_GULDUR_BEASTMASTER_NAMES = frozenset(
        {"多古尔都驯兽师", "Dol Guldur Beastmaster"}
    )
    ORC_ARSONIST_NAMES = frozenset({"半兽人纵火者", "Orc Arsonist"})
    SCOURGE_OF_MORDOR_NAMES = frozenset({"魔多的苦难", "Scourge of Mordor"})
    DRIVEN_BY_SHADOW_NAMES = frozenset({'魔影驱使', "Driven by Shadow"})
    BOARDING_PARTY_NAMES = frozenset({"登船小队", "Boarding Party"})
    BOARDING_PARTY_OCTGN_BASE = "28ba7c20-b660-40cb-9e2c-bf6ebaec723e"
    SLAVE_SHIP_NAMES = frozenset({"奴隶船", "Slave Ship"})
    SLAVE_SHIP_OCTGN_BASE = "0f7ce386-9510-4fd5-93aa-145190f8d1de"
    UMBAR_SLAVER_NAMES = frozenset({"昂巴奴隶", "Umbar Slaver"})
    UMBAR_SLAVER_OCTGN_BASE = "2fd16de8-3286-4003-9978-574738b2a337"
    UMBAR_ASSASSIN_NAMES = frozenset({"昂巴暗杀者", "Umbar Assassin"})
    UMBAR_ASSASSIN_OCTGN_BASE = "4823aae3-46ef-4a75-89f9-cbd3aa1b9085"
    ORC_RABBLE_NAMES = frozenset({"半兽人暴民", "Orc Rabble"})
    ORC_RABBLE_OCTGN_BASE = "4823aae3-46ef-4a75-89f9-cbd3aa1b9064"
    ORC_RABBLE_HALF_OCTGN_BASE = "4823aae3-46ef-4a75-89f9-cbd3aa1b9074"
    TAKING_ON_WATER_NAMES = frozenset({"船舱进水", "Taking on Water"})
    TAKING_ON_WATER_OCTGN_BASE = "783ecb94-e005-4ff7-8bc3-76196bba9249"
    CORSAIR_WARSHIP_NAMES = frozenset({"海盗战船", "Corsair Warship"})
    CORSAIR_WARSHIP_OCTGN_BASE = "a312e181-9209-4460-b6ba-c90c99e011fe"
    WINDS_OF_WRATH_NAMES = frozenset({"愤怒之风", "Winds of Wrath"})
    WINDS_OF_WRATH_OCTGN_BASE = "71d7dd78-a967-43dd-ab73-e46a5d4d4519"
    SUDDEN_STORMS_NAMES = frozenset({"突来暴风", "Sudden Storms"})
    SUDDEN_STORMS_OCTGN_BASE = "7691fb33-603e-4460-9f09-5f96bc05c2bb"
    DROWNED_DEAD_NAMES = frozenset({"淹死鬼", "Drowned Dead"})
    DROWNED_DEAD_OCTGN_BASE = "79d5dc52-cfee-49f9-8a3d-1d4a7a749af2"
    THRONGS_OF_UNFAITHFUL_NAMES = frozenset({
        "成群的不忠者",
        "成群的不思者",
        "Throngs of Unfaithful",
    })
    THRONGS_OF_UNFAITHFUL_OCTGN_BASE = "159f5a8f-1e1f-4282-9936-94b10e348176"
    SERVANT_OF_THE_DECEIVER_NAMES = frozenset({
        "欺诈者的仆从",
        "Servant of the Deceiver",
    })
    SERVANT_OF_THE_DECEIVER_OCTGN_BASE = "7a2f15bc-d05d-4891-9558-02396affc32b"
    SOULLESS_CADAVER_NAMES = frozenset({"无魂死尸", "Soulless Cadaver"})
    SOULLESS_CADAVER_OCTGN_BASE = "69a3ace6-7588-4ec4-91de-780b56502d8a"
    CURSE_OF_THE_DOWNFALLEN_NAMES = frozenset({"堕落者的诅咒", "Curse of the Downfallen"})
    CURSE_OF_THE_DOWNFALLEN_OCTGN_BASE = "ab5fec4c-b353-47e5-ae47-db7daf7f9088"
    LINGERING_MALEVOLENCE_NAMES = frozenset({"挥之不去的恶意", "Lingering Malevolence"})
    LINGERING_MALEVOLENCE_OCTGN_BASE = "b03a007c-42bc-433c-bd0e-efbf65a8696d"
    RUINS_OF_AGES_PAST_NAMES = frozenset({"古代废墟", "Ruins of Ages Past", "Ancient Ruins"})
    RUINS_OF_AGES_PAST_OCTGN_BASE = "92fec8c5-53e7-477a-9c47-8d97578bc1e7"
    STEEP_PLATEAU_NAMES = frozenset({"险峻的高原", "Steep Plateau"})
    STEEP_PLATEAU_OCTGN_BASE = "8830070b-b918-4b74-8dfb-dced3866e53b"
    AIMLESS_WANDERING_NAMES = frozenset({"漫无目的的游荡", "Aimless Wandering"})
    AIMLESS_WANDERING_OCTGN_BASE = "a8918c80-f092-4649-bb5c-9f7fde3cef75"
    MYSTERIOUS_FOG_NAMES = frozenset({"神秘的雾气", "Mysterious Fog"})
    MYSTERIOUS_FOG_OCTGN_BASE = "b0a1f83b-f1b0-4945-afc8-91a1c25063e2"
    FORBIDDEN_COAST_NAMES = frozenset({"禁闭海岸", "Forbidden Coast"})
    FORBIDDEN_COAST_OCTGN_BASE = "b1f8c611-99a3-4bab-9805-832853d52d4d"
    LUSH_JUNGLE_NAMES = frozenset({"茂密的丛林", "Lush Jungle"})
    LUSH_JUNGLE_OCTGN_BASE = "c7bb88f6-a6b7-44f3-8bc2-8c4be788e7da"
    DROWNED_GRAVES_NAMES = frozenset({"被淹没的坟墓", "Drowned Graves"})
    DROWNED_GRAVES_OCTGN_BASE = "d7ef4ad0-93f1-4c03-a04b-cdfe6339eb41"
    CURSED_CAVERNS_NAMES = frozenset({"被诅咒的洞穴", "Cursed Caverns"})
    CURSED_CAVERNS_OCTGN_BASES = frozenset({
        "6a3ae84d-6c74-4fbf-9254-b8c1d17c8384",
        "4be4e706-1bd2-455a-a712-833c1bbcea55",
    })
    DROWNED_CAVE_NAMES = frozenset({"淹没的洞穴", "Drowned Cave"})
    DROWNED_CAVE_OCTGN_BASE = "b59afed1-a224-4793-b149-0a6aadc9ba7e"
    TWISTING_HOLLOW_NAMES = frozenset({"曲折的海穴", "Twisting Hollow"})
    TWISTING_HOLLOW_OCTGN_BASES = frozenset({
        "6a3ae84d-6c74-4fbf-9254-b8c1d17c8384",
        "c95b4c6a-3dd0-4f11-8620-df7af65c11bc",
    })
    UNDERSEA_GROTTO_NAMES = frozenset({"水下石窟", "Undersea Grotto"})
    UNDERSEA_GROTTO_OCTGN_BASES = frozenset({
        "58f1cb80-d8de-4f96-98d0-0fdf61988da3",
    })
    DARK_ABYSS_NAMES = frozenset({"黑暗深渊", "Dark Abyss"})
    DARK_ABYSS_OCTGN_BASES = frozenset({
        "58f1cb80-d8de-4f96-98d0-0fdf61988da3",
        "9c6798e3-9c5a-4609-b499-e6b1d6ae567b",
    })
    DROWNED_RUINS_SHRINE_TO_MORGOTH_NAMES = frozenset({
        "魔苟斯祭坛",
        "窟苟斯祭坛",
        "Shrine to Morgoth",
    })
    DROWNED_RUINS_SHRINE_TO_MORGOTH_OCTGN_BASE = (
        "011a4573-f21a-4897-9b23-8b64391f5136"
    )
    DROWNED_RUINS_CAVE_EEL_NAMES = frozenset({
        "穴鳗",
        "Cave Eel",
    })
    DROWNED_RUINS_CAVE_EEL_OCTGN_BASE = (
        "34bfcbad-5557-48bc-8abd-3ac472ee4d3b"
    )
    DROWNED_RUINS_INTO_THE_ABYSS_NAMES = frozenset({
        "坚入深渊",
        "Into the Abyss",
    })
    DROWNED_RUINS_INTO_THE_ABYSS_OCTGN_BASE = (
        "54014fda-7adc-49b0-96ac-656e849eaf7b"
    )
    DROWNED_RUINS_ANCIENT_DEPTHS_NAMES = frozenset({
        "远古的深海",
        "Ancient Depths",
    })
    DROWNED_RUINS_ANCIENT_DEPTHS_OCTGN_BASE = (
        "a9321cbd-897f-4a77-a548-85ad00112273"
    )
    DROWNED_RUINS_POWERFUL_UNDERTOW_NAMES = frozenset({
        "强大的暗流",
        "Powerful Undertow",
    })
    DROWNED_RUINS_POWERFUL_UNDERTOW_OCTGN_BASE = (
        "befbd3c9-45b3-4e68-b19e-5981a6571835"
    )
    DROWNED_RUINS_TANGLING_AND_GRASPING_NAMES = frozenset({
        "纠结和缠绕",
        "Tangling and Grasping",
    })
    DROWNED_RUINS_TANGLING_AND_GRASPING_OCTGN_BASE = (
        "c0d85826-dc42-4ac8-851c-4ed671917e9d"
    )
    DROWNED_RUINS_SEA_SCORPION_NAMES = frozenset({
        "海蝎",
        "Sea-Scorpion",
        "Sea Scorpion",
    })
    DROWNED_RUINS_SEA_SCORPION_OCTGN_BASE = (
        "c49ec585-e306-4fda-a55b-497cb0fd1174"
    )
    WATER_LOGGED_HALLS_NAMES = frozenset({
        "水浸的大厅",
        "水漫的大厅",
        "Water-Logged Halls",
        "Waterlogged Halls",
    })
    WATER_LOGGED_HALLS_OCTGN_BASES = frozenset({
        "9c6798e3-9c5a-4609-b499-e6b1d6ae567b",
        "cb3ca114-400e-4abf-87e6-124b75e92cb4",
    })
    SUNKEN_TEMPLE_NAMES = frozenset({"沉没的神庙", "Sunken Temple"})
    SUNKEN_TEMPLE_OCTGN_BASES = frozenset({
        "4be4e706-1bd2-455a-a712-833c1bbcea55",
        "cb3ca114-400e-4abf-87e6-124b75e92cb4",
    })
    FLOODED_RUINS_NAMES = frozenset({"被水漫的废墟", "Flooded Ruins"})
    FLOODED_RUINS_OCTGN_BASE = "fb5e0cb9-914f-49ee-8e21-534e89bc5533"
    HAVENS_BURN_NAMES = frozenset({"海港在燃烧", "The Havens Burn"})
    HAVENS_BURN_OCTGN_BASE = "6302e603-fade-43b3-a6b3-235af412fdfa"
    SEA_WARD_TOWER_NAMES = frozenset({"海岸监视塔", "Sea-ward Tower", "Sea-ward tower"})
    SEA_WARD_TOWER_OCTGN_BASE = "434cf78e-2e8e-4d79-9662-eb50e33bc3f9"
    TOWER_OF_THE_GULL_NAMES = frozenset({"鸥之塔", "Tower of the Gull"})
    TOWER_OF_THE_GULL_OCTGN_BASE = "d198aa74-21ec-462a-ac1a-f9c2fcf526cb"
    TOWER_OF_THE_HERON_NAMES = frozenset({"鹭之塔", "Tower of the Heron"})
    TOWER_OF_THE_HERON_OCTGN_BASE = "52b5e077-36f2-4c13-89fb-4f7b3d73463b"
    THE_BEACON_NAMES = frozenset({"烽火台", "The Beacon"})
    THE_BEACON_OCTGN_BASE = "f410480c-ac41-431a-8db1-693746a7570e"
    DOL_AMROTH_WARSHIP_NAMES = frozenset({"多尔安罗斯战舰", "Dol Amroth Warship"})
    DOL_AMROTH_WARSHIP_OCTGN_BASE = "b585f28e-7612-4f07-83f7-0fed8d391e3d"
    MITHLOND_HARBOR_NAMES = frozenset({"米斯龙德港", "Mithlond Harbor"})
    MITHLOND_HARBOR_OCTGN_BASE = "766735f8-3b17-4b50-aee3-a869837f986c"
    COBAS_HAVEN_NAMES = frozenset({"科巴斯港", "Cobas Haven"})
    COBAS_HAVEN_OCTGN_BASE = "c46688bb-24ac-4ee9-a23e-2bd481e8e56c"
    BELFALAS_ISLET_NAMES = frozenset({"贝尔法拉斯岛", "Belfalas Islet"})
    BELFALAS_ISLET_OCTGN_BASE = "d9bde488-6391-492f-b3df-6dd6dbe4f9e9"
    SOUTHERN_BELFALAS_LOCATION_NAMES = frozenset({
        "南贝尔法拉斯湾", "Southern Belfalas",
    })
    SOUTHERN_BELFALAS_LOCATION_OCTGN_BASE = "797a1805-f347-4eb3-9f77-5c9e6b6a850f"
    COAST_OF_UMBAR_QUEST_NAMES = frozenset({"昂巴的海岸", "The Coast of Umbar"})
    CORSAIR_WATERS_LOCATION_NAMES = frozenset({
        "海盗水域", "Corsair Waters",
    })
    CORSAIR_WATERS_LOCATION_OCTGN_BASE = "a3173fc4-1ddb-470d-ae12-a35e24ea63a3"
    BURNING_WATCHTOWER_NAMES = frozenset({"燃烧的瞭望塔", "Burning Watchtower"})
    BURNING_WATCHTOWER_OCTGN_BASE = "f57aeb37-4f19-4e5f-ae34-326c3b8b62f2"
    BURNING_PIERS_NAMES = frozenset({"燃烧的码头", "Burning Piers", "Burning Wharf"})
    BURNING_PIERS_OCTGN_BASE = "c69c1fe9-076a-4a5b-8388-9f653015d84d"
    PILLAGED_SHIP_NAMES = frozenset({"被掠夺的船", "Pillaged Ship"})
    PILLAGED_SHIP_OCTGN_BASE = "a961751d-414b-4a53-9da4-5708f8b7bb1f"
    UMBAR_HARBOR_NAMES = frozenset({"昂巴海港", "Umbar Harbor"})
    UMBAR_HARBOR_OCTGN_BASE = "5c0a0aea-af43-441b-9d7f-86a1dfd09f00"
    CITY_OF_CORSAIRS_LOCATION_NAMES = frozenset({"海盗之城", "City of Corsairs"})
    CITY_OF_CORSAIRS_LOCATION_OCTGN_BASE = "06badf71-8e85-479e-8ddf-1dec7a40e325"
    SHATTERED_MONUMENT_NAMES = frozenset({"被毁坏的纪念碑", "The Shattered Monument"})
    UMBAR_PATROL_NAMES = frozenset({"昂巴巡逻", "Umbar Patrol"})
    UMBAR_PATROL_OCTGN_BASE = "7f0e7969-bca4-48a3-99b5-ba055bd376af"
    STREETS_OF_UMBAR_NAMES = frozenset({"昂巴的街道", "Streets of Umbar"})
    STREETS_OF_UMBAR_OCTGN_BASE = "dd394c76-c2b9-4b1c-8948-a056de97b779"
    FOES_OF_ECTHELION_NAMES = frozenset({"爱克西力昂的劲敌", "Foes of Ecthelion"})
    FOES_OF_ECTHELION_OCTGN_BASE = "720b2482-f69c-4a88-8352-721d854704d2"
    FURY_AND_MALICE_NAMES = frozenset({"愤怒和怨恨", "Fury and Malice"})
    FURY_AND_MALICE_OCTGN_BASE = "b4769083-91fb-4e9c-9736-17e8c28ff971"
    BLACK_SERPENTS_TAIL_NAMES = frozenset({"黑蛇之尾", "Serpent's Tail"})
    BLACK_SERPENTS_TAIL_OCTGN_BASE = "86e8724b-32cb-4d63-b918-5754d39d2249"
    CORSAIRS_ASSAULT_1A_NAMES = frozenset({"海盗的强袭", "The Corsairs' Assault"})
    OUTMANEUVER_THE_ENEMY_1A_NAMES = frozenset({"机动制敌", "Outmaneuver the Enemy"})
    BATTLE_IN_THE_BAY_NAMES = frozenset({"海湾鏖战", "Battle in the Bay"})
    BREAK_THROUGH_THE_FLEET_NAMES = frozenset({
        "突破舰队！",
        "突破舰队",
        "Break Through the Fleet!",
        "Break Through the Fleet",
    })
    CORSAIRS_ASSAULT_1B_NAMES = frozenset({"海盗的强袭", "The Corsairs' Assault"})
    BURNING_DREAM_CHASER_LOCATION_NAMES = frozenset({"逐梦者号", "Dream-chaser", "Dream Chaser"})
    BURNING_DREAM_CHASER_LOCATION_OCTGN_BASE = "c792fec6-5840-4aba-8dcd-36b1632d58fa"
    SAHIRS_RAVAGER_NAMES = frozenset({
        "萨伊尔的破坏者",
        "萨希尔的破坏者",
        "Sahír's Ravager",
        "Sahir's Ravager",
    })
    SAHIRS_RAVAGER_OCTGN_BASE = "4893d105-fada-4bee-a4bb-d26463b97b63"
    CORSAIR_ARSONIST_NAMES = frozenset({"海盗纵火者", "Corsair Arsonist"})
    CORSAIR_ARSONIST_OCTGN_BASE = "cedfcb4c-9f28-46c6-b6ec-7986be79f7b1"
    NAASIYAH_OCTGN_BASE = "f0d30258-7122-4004-a689-9ff50a00b3af"
    MISTY_MOUNTAIN_GOBLINS_NAMES = frozenset({'狼骑兵'})
    MISTY_MOUNTAINS_ORC_NAMES = frozenset({"迷雾山脉半兽人"})
    MISTY_MOUNTAINS_ORC_OCTGN_BASE = "51223bd0-ffd1-11df-a976-0801200c9111"
    MASSING_AT_NIGHT_NAMES = frozenset({"夜幕中的集结", "Massing at Night"})
    ENEMY_VICTORY_VALUES = {
        "山区食人妖": 4,
        "Hill Troll": 4,
        "乌夫沙克首领": 4,
        "Chieftain Ufthak": 4,
        "金鸢尾原野": 3,
        "Gladden Fields": 3,
        "沼泽蝰蛇": 3,
        "Marsh Adder": 3,
        "蜇刺毒蜂": 5,
        "Hummerhorns": 5,
        "纳乌尔路赫": 7,
        "Naurlhûg": 7,
        "Naurlhug": 7,
        "深水潜伏者": 5,
        "Lurker of the Depths": 5,
        "萨伊尔船长": 6,
        "萨希尔船长": 6,
        "Captain Sahír": 6,
        "神庙守卫": 4,
        "Temple Guardian": 4,
        "Captain Sahir": 6,
        "海盗战船": 6,
        "Corsair Warship": 6,
        "娜阿西雅": 4,
        "Na'asiyah": 4,
    }
    GREAT_FOREST_WEB_NAMES = frozenset({'密布蛛网的森林', "Great Forest Web"})
    OLD_FOREST_ROAD_NAMES = frozenset({"老密林路", "Old Forest Road"})
    FOREST_GATE_NAMES = frozenset({"森林之门", "Forest Gate"})
    BROWN_LANDS_NAMES = frozenset({"褐地", "The Brown Lands"})
    THE_EAST_BIGHT_LOCATION_NAMES = frozenset({"东林弯", "The East Bight"})
    BANKS_OF_THE_ANDUIN_NAMES = frozenset({"安都因河岸", "Banks of the Anduin"})
    GLADDEN_FIELDS_NAMES = frozenset({"金鸢尾原野", "Gladden Fields"})
    MOUNTAINS_OF_MIRKWOOD_NAMES = frozenset(
        {"黑森林山脉", "Mountains of Mirkwood"}
    )
    ENCHANTED_STREAM_NAMES = frozenset({'魔法小溪', "Enchanted Stream"})
    EYES_OF_THE_FOREST_NAMES = frozenset(
        {"森林中的眼睛", "Eyes of the Forest"}
    )
    CAUGHT_IN_WEB_NAMES = frozenset({"身限蛛网", "Caught in a Web"})
    CAUGHT_IN_WEB_READY_COST = 2
    WANDERING_TOOK_ALLY_NAMES = frozenset({        "漫游的图克", "流浪的图克", "Wandering Took",
})
    RIDER_OF_MARK_ALLY_NAMES = frozenset({"马克骑手", "骠骑兵", "Rider of the Mark"})
    BLUE_MOUNTAIN_TRADER_ALLY_NAMES = frozenset({"蓝山商人", "Blue Mountain Trader"})
    BOMBUR_ALLY_NAMES = frozenset({"邦伯", "庞伯", "Bombur"})
    TDM_BOROMIR_HERO_NAMES = frozenset({        "波洛米尔", "波罗莫", "Boromir",
})
    KEEN_EYED_TOOK_ALLY_NAMES = frozenset({        "目光锐利的图克", "Keen-eyed Took",
})
    LORIEN_GUIDE_ALLY_NAMES = frozenset({'罗瑞恩向导', "罗瑞安引路人", "Lorien Guide"})
    NORTHERN_TRACKER_ALLY_NAMES = frozenset({'北方的追踪者', "Northern Tracker"})
    NORTHERN_WANDERER_ALLY_NAMES = frozenset({
        "北方的游民", "北方的游侠", "Northern Wanderer", "Northern Ranger",
    })
    CALPHON_OBJECTIVE_ALLY_NAMES = frozenset({"卡冯", "Calphon"})
    CALPHON_OBJECTIVE_ALLY_OCTGN_BASE = "24a816f2-172f-4945-8d9b-21eb7fb68263"
    LONGBEARD_ELDER_ALLY_NAMES = frozenset({'长须长者', "Longbeard Elder"})
    WEST_ROAD_TRAVELLER_ALLY_NAMES = frozenset({'西大道旅人', '西大道的旅人', "West Road Traveller"})
    LAMEDON_HUNTER_ALLY_NAMES = frozenset({"拉梅顿猎人", "拉密顿猎人", "Lamedon Hunter"})
    ITHILIEN_TRACKER_ALLY_NAMES = frozenset({'伊希利恩追踪者', "伊西立安追踪者", "Ithilien Tracker"})
    ITHILIEN_ARCHER_ALLY_NAMES = frozenset({'伊希利恩弓手', "伊西立安弓箭手", "Ithilien Archer"})
    GUARDIAN_OF_ITHILIEN_ALLY_NAMES = frozenset({"伊西利安守卫", "伊希利恩守卫", "Guardian of Ithilien"})
    GUARDIAN_OF_ITHILIEN_CARD_CODES = frozenset({"12144", "海盗之城-144"})
    GUARDIAN_OF_ITHILIEN_OCTGN_BASES = frozenset({"dba87655-87e0-44b6-ba45-caf86ce723eb"})
    MASTER_OF_LORE_ALLY_NAMES = frozenset({"学识大师", "饱读历史的学者", "Master of Lore"})
    MASTER_OF_LORE_HAND_PLAY_DISCOUNT = 1
    MASTER_OF_LORE_CARD_TYPE_OPTIONS = (
        ("盟友", "盟友"),
        ("附属", "附属"),
        ("事件", "事件"),
    )
    TREASURE_HUNTER_CARD_TYPE_OPTIONS = (
        ("盟友", "盟友"),
        ("附属", "附属"),
        ("事件", "事件"),
        ("任务", "任务"),
        ("约定", "约定"),
        ("英雄", "英雄"),
    )
    PELARGIR_MESSENGER_ALLY_NAMES = frozenset({"佩拉基尔使者", "佩拉格使者", "Envoy of Pelargir"})
    PELARGIR_SHIP_CAPTAIN_ALLY_NAMES = frozenset({"佩拉基尔船长", "佩拉格船长", "Pelargir Ship Captain"})
    CITADEL_SQUIRE_ALLY_NAMES = frozenset({'王城侍从', '都城侍从', "Squire of the Citadel"})
    PELARGIR_SHIPWRIGHT_ALLY_NAMES = frozenset({"佩拉基尔船木工", "佩拉格船木工", "Pelargir Shipwright"})
    MIRKWOOD_RUNNER_ALLY_NAMES = frozenset({"黑森林信使", '幽暗密林斥候', "Mirkwood Runner", "Mirkwood Scout"})
    TROLLSHAW_SCOUT_ALLY_NAMES = frozenset({'食人妖之地的斥候', '食人妖森林斥候', "Trollshaw Scout"})
    STAGING_REPLACE_MAX_DEPTH = 20
    STAGING_AREA_TYPES = frozenset({
        '敌人', '地区', "目标", "目标-盟友", "目标|盟友", "船|目标",
        "支线探险", "遭遇支线探险", "Side Quest",
    })
    TREACHERY_TYPES = term_variants("诡计")
    STARTING_HAND_SIZE = 6
    PHASE_ORDER = ("1.1", "2.1", "3.1", "4.1", "5.1", "6.1", "7.1")
    PHASE_LABELS = {
        "1.1": "资源环节开始",
        "2.1": "计划环节开始",
        "3.1": "探险环节开始",
        "4.1": "游历环节开始",
        "5.1": "遭遇环节开始",
        "6.1": "战斗环节开始",
        "7.1": "恢复环节开始",
    }

    @property
    def PLAYER_COUNT(self) -> int:
        return self._player_count

    @property
    def hand_cards(self) -> list:
        return self._players[self._active_player_index].hand_cards

    @hand_cards.setter
    def hand_cards(self, value: list):
        self._players[self._active_player_index].hand_cards = value

    @property
    def ally_cards(self) -> list:
        return self._players[self._active_player_index].ally_cards

    @ally_cards.setter
    def ally_cards(self, value: list):
        self._players[self._active_player_index].ally_cards = value

    @property
    def discard_cards(self) -> list:
        return self._players[self._active_player_index].discard_cards

    @discard_cards.setter
    def discard_cards(self, value: list):
        self._players[self._active_player_index].discard_cards = value

    @property
    def _attachments(self) -> dict:
        return self._players[self._active_player_index].attachments

    @_attachments.setter
    def _attachments(self, value: dict):
        self._players[self._active_player_index].attachments = value

    @property
    def _hero_resources(self) -> dict:
        return self._players[self._active_player_index].hero_resources

    @_hero_resources.setter
    def _hero_resources(self, value: dict):
        self._players[self._active_player_index].hero_resources = value

    @property
    def threat_level(self) -> int:
        return self._players[self._active_player_index].threat_level

    @threat_level.setter
    def threat_level(self, value: int):
        self._players[self._active_player_index].threat_level = value
        if hasattr(self, "threat_dial"):
            self.threat_dial.set_threat_level(value)

    @property
    def _mulligan_used(self) -> bool:
        return self._players[self._active_player_index].mulligan_used

    @_mulligan_used.setter
    def _mulligan_used(self, value: bool):
        self._players[self._active_player_index].mulligan_used = value

    def __init__(self):
        super().__init__()
        self._stdout_tee = _StdoutTee(sys.__stdout__)
        sys.stdout = self._stdout_tee
        self._game_log_dialog: GameLogDialog | None = None
        self._player_count = 1
        self._active_player_index = 0
        self._turn_player_index: int | None = None
        self._players = [PlayerState(index=i) for i in range(self.MAX_PLAYERS)]
        self._char_owner: dict[str, int] = {}
        self._game_started = False
        self.hand_widgets: list = []
        self.staging_cards: list = []
        self.staging_widgets: list = []
        self.hero_widgets: list = []
        self.ally_widgets: list = []
        self.engagement_widgets: list = []
        self._engagement_host_widgets: dict = {}
        self._staging_host_widgets: dict = {}
        self._field_player_blocks: dict[int, QWidget] = {}
        self.encounter_discard_cards: list = []
        self._uncharted_location_deck: list = []
        self._grotto_location_deck: list = []
        self._grotto_deck_enabled = False
        self._night_fire_side_quest_deck: list = []
        self._night_fire_side_quest_deck_enabled = False
        self._hrogars_hill_damage = 0
        self._hrogars_hill_1b_forced_round = -1
        self._island_map_cells: list[dict] = []
        self._island_map_position: tuple[int, int] | None = None
        self.pirate_deck_cards: list = []
        self.pirate_discard_cards: list = []
        self._pirate_deck_enabled = False
        self.evil_creature_deck_cards: list = []
        self.evil_creature_discard_cards: list = []
        self._evil_creature_deck_enabled = False
        self._q08_2b_location_progress_round: dict[str, int] = {}
        self._dark_woods_progress_round: dict[str, int] = {}
        self._urdug_engaged_ids: set[str] = set()
        self._deep_ravine_progress_round = -1
        self._deep_ravine_progress_count = 0
        self._things_in_the_deep_encounter_switched = False
        self._city_of_corsairs_second_encounter_cards: list = []
        self._city_of_corsairs_retired_encounter_cards: list = []
        self._city_of_corsairs_encounter_switched = False
        self.heading_index = 0
        self.heading_controller_index: int | None = None
        self._resource_actions_active = False
        self._syncing_hero_resources = False
        self._planning_active = False
        self._planning_player_index = 0
        self._quest_assign_active = False
        self._quest_assign_player_index = 0
        self._staging_active = False
        self._staging_player_index = 0
        self._quest_staging_reveal_reduction = 0
        self._quest_staging_reveal_minimum_zero = False
        self._warden_of_arnor_first_location_pending = True
        self._adventure_begin_actions_active = False
        self._quest_assign_actions_active = False
        self._quest_resolve_actions_active = False
        self._player_actions_active = False
        self._travel_active = False
        self._travel_chosen = False
        self._travel_actions_active = False
        self._voluntary_engage_active = False
        self._engage_player_index = 0
        self._engage_chosen_this_turn = False
        self._engage_count_this_turn = 0
        self._engage_check_active = False
        self._engage_check_player_index = 0
        self._engage_check_awaiting_choice = False
        self._engage_check_pending_candidates: list = []
        self._encounter_engage_actions_active = False
        self._encounter_actions_active = False
        self._skip_encounter_phase_to_combat = False
        self._combat_active = False
        self._combat_shadow_distributed = False
        self._combat_actions_active = False
        self._empty_combat_auto_skip_pending = False
        self._enemy_attack_active = False
        self._enemy_attack_player_index = 0
        self._enemy_attack_ctx: dict | None = None
        self._pending_forced_enemy_attacks: list[dict] = []
        self._temporary_surge_card_ids: set[str] = set()
        self._mirkwood_spider_phase_ready_blocked_ids: set[str] = set()
        self._giant_spider_no_ready_ids: set[str] = set()
        self._heavy_snow_player_effect_no_ready_ids: set[str] = set()
        self._phase_enemy_no_damage_ids: set[str] = set()
        self._mirkwood_patrol_shadow_no_ready_ids: set[str] = set()
        self._mirkwood_spider_first_seen_this_phase = False
        self._dol_guldur_orcs_first_seen_this_phase = False
        self._enemy_attack_substep_window_active = False
        self._enemies_attacked_this_round: set[str] = set()
        self._andrath_guardsman_blocked_attacks: set[tuple[str, int]] = set()
        self._yazan_response_used_this_phase: set[str] = set()
        self._jubayr_response_used_this_phase: set[str] = set()
        self._player_attack_active = False
        self._player_attack_player_index = 0
        self._player_attack_ctx: dict | None = None
        self._player_attack_substep_window_active = False
        self._player_attacked_by: dict[int, set[str]] = {}
        self._dain_ironfoot_aura_was_active = False
        self._hon_boromir_aura_was_active = False
        self._visionary_leadership_aura_was_active = False
        self._combat_player_attacks_done = False
        self._refresh_active = False
        self._refresh_substep = ""
        self._refresh_actions_active = False
        self._refresh_core_applied = False
        self._aragorn_refresh_used_players: set[int] = set()
        self._hama_response_uses = 0
        self._quest_when_revealed_resolved_index = -1
        self._truly_lost_3c_resolved = False
        self.first_player_index = 0
        self._first_player_chosen = False
        self._experience_mode: str | None = None  # "beginner" | "expert"
        self._experience_mode_chosen = False
        self._elimination_threat_delta: dict[int, int] = {}
        self._eliminated_players: set[int] = set()
        self._justice_shall_be_done_eliminate_at_round_end: set[int] = set()
        self._game_lost = False
        self._game_won = False
        self._shadow_cards: dict = {}
        self._extra_shadow_cards: dict = {}  # 额外的魔影卡牌（如多古尔都驭兽师效果）
        self._bonus_extra_shadow_cards: dict[str, list] = {}  # 同一敌军上的第 2+ 张额外魔影
        self._black_serpents_tail_pending_shadows: dict[str, list] = {}
        self._black_serpents_tail_engagement_penalty_ids: set[str] = set()
        self._orc_hunting_party_low_engagement_ids: set[str] = set()
        self._shadow_revealed: set[str] = set()
        self._extra_shadow_revealed: set[str] = set()
        self._wolf_rider_shadow_return_ids: set[str] = set()
        self._wolf_rider_shadow_return_cards: list = []
        self._facedown_attachment_ids: set[str] = set()
        self._adventure_phase_active = False
        self._questing_ids: set[str] = set()
        self._questing_readied: set[str] = set()
        self._questing_ids_this_player: set[str] = set()
        self._promoted_ally_ids: set[str] = set()  # 佩剑侍从提升的盟友
        self._captain_sahir_ally_action_used_rounds: dict[str, int] = {}
        self._naasiyah_ally_action_used_rounds: dict[str, int] = {}
        self._active_side_quest_id = ""
        self._player_side_quest_progress: dict[str, int] = {}
        self._declared_defender_ids: set[str] = set()
        self._defender_readied: set[str] = set()
        self._phase_willpower_bonus: dict[str, int] = {}
        self._phase_willpower_penalty: dict[str, int] = {}
        self._round_willpower_penalty: dict[str, int] = {}
        self._phase_attack_bonus: dict[str, int] = {}
        self._gimli_defense_response_used_this_phase: set[str] = set()
        self._legolas_quest_response_used_this_phase: set[str] = set()
        self._round_attack_bonus: dict[str, int] = {}
        self._lurker_damage_this_round: dict[str, int] = {}
        self._cold_drake_damage_this_round: dict[str, int] = {}
        self._dagnir_non_quest_damage_this_round: dict[str, int] = {}
        self._cold_drake_set_aside_damage = 0
        self._stormcaller_damage_this_round: dict[str, int] = {}
        self._round_char_willpower_bonus: dict[str, int] = {}
        self._round_char_attack_bonus: dict[str, int] = {}
        self._round_char_defense_bonus: dict[str, int] = {}
        self._round_granted_vigilant: set[str] = set()
        self._granted_vigilant_char_ids: set[str] = set()
        self._phase_defense_bonus: dict[str, int] = {}
        self._phase_enemy_attack_penalty: dict[str, int] = {}
        self._phase_enemy_defense_bonus: dict[str, int] = {}
        self._phase_enemy_defense_penalty: dict[str, int] = {}
        self._phase_close_quarters_draw_counts: dict[int, int] = {}
        self._phase_sphere_bonus: dict[str, list[str]] = {}
        self._phase_granted_gondor_trait_ids: set[str] = set()
        self._phase_granted_rohan_trait_ids: set[str] = set()
        self._sneak_attack_returns: dict[str, str] = {}
        self._beorn_shuffle_returns: dict[str, str] = {}
        self._children_of_sea_shuffle_returns: dict[str, str] = {}
        self._imrahil_combat_action_returns: dict[str, str] = {}
        self._desperate_alliance_returns: dict[str, dict[str, int | str]] = {}
        self._to_me_kinsfolk_returns: dict[str, dict[str, int | str]] = {}
        self._bofur_quest_returns: set[str] = set()
        self._ranger_alliance_returns: dict[str, str] = {}
        self._reinforcements_ally_returns: list = []
        self._good_meal_event_discount: dict[int, frozenset[str]] = {}
        self._renewed_hope_discount_player: int | None = None
        self._phase_master_of_lore_discount: dict[int, str] = {}
        self._phase_to_the_sea_discount: dict[int, int] = {}
        self._phase_dol_amroth_soldier_tactics_discount: dict[int, int] = {}
        self._heir_of_valandil_discount: dict[int, int] = {}
        self._narelenya_ally_discount_used_players: set[int] = set()
        self._theoden_ally_discount_used_players: set[int] = set()
        self._undersea_grotto_ally_discount_used_this_round = False
        self._active_location_flip_blocked_this_round = False
        self._phase_harvest_sphere_by_player: dict[int, str] = {}
        self._phase_quest_battle_granted = False
        self._phase_spirit_willpower_as_defense_active = False
        self._quest_commit_stat_override: dict[str, str] = {}
        self._visionary_leadership_aura_was_active = False
        self._outlands_aura_counts: dict[int, tuple[int, int, int, int]] = {}
        self._vilya_free_play: bool = False
        self._vilya_free_play_player: int | None = None
        self._book_of_eldacar_play_event = None
        self._map_of_earnil_play_event = None
        self._atanatar_tome_play_event = None
        self._shadow_man_event_lock_round = 0
        self._feint_blocked_attacks: set[tuple[str, int]] = set()
        # 埃尔隆德的来信：本回合结束时待洗回的卡牌 (card_id, owner_player_idx, target_player_idx)
        self._message_from_elrond_tracked: list[tuple[object, int, int]] = []
        # 格怀希尔(MotK)：本回合结束时待返回手牌的巨鹰盟友 {ally_id: controller_idx}
        self._gwaihir_motk_round_end_returns: dict[str, int] = {}
        self._expecting_mischief_pending: bool = False
        self._unseen_strike_attacker_ids: set[str] = set()
        self._thicket_player_blocks: set[int] = set()
        self._hobbit_sense_no_attack_players: set[int] = set()
        self._faramir_staging_bonus_counts: dict[str, int] = {}
        self._side_by_side_multi_defense_players: set[int] = set()
        self._quest_fail_threat_blocked: bool = False
        self._quest_phase_skipped: bool = False
        self._skip_next_adventure_phase: bool = False
        self._lost_armory_used: bool = False
        self._ancient_treasury_used: bool = False
        self._noiseless_movement_enemy_engagement_bonus: int = 0
        self._phase_threat_excluded_card_ids: set[str] = set()
        self._ride_them_down_target_enemy_id = ""
        self._saruman_out_of_play_map: dict[str, str] = {}  # target_id → saruman_card_id
        self._isengard_messenger_round_uses: dict[str, int] = {}  # ally_id → count（每回合限2次）
        self._fresh_tracks_ignored_enemy_ids: set[str] = set()
        self._staging_unattached_attachments: list = []
        self._ranger_spikes_skip_engage_ids: set[str] = set()
        self._pippin_engagement_blocks: set[tuple[str, int]] = set()
        self._umbar_patrol_no_voluntary_engage_until_round_end = False
        self._phase_staging_threat_bonus: dict[str, int] = {}
        self._phase_staging_area_threat_bonus: int = 0
        self._phase_ithilien_tracker_pending_count: int = 0
        self._phase_ithilien_tracker_zero_enemy_ids: set[str] = set()
        self._driven_by_shadow_surge_pending = False
        self._belegost_servants_hazard_surge_used = False
        self._lampwright_named_encounter_type: str | None = None
        self._eowyn_action_used_chars: set[str] = set()
        self._fotw_eowyn_action_used: bool = False
        self._fotw_eowyn_setup_applied: set[str] = set()
        self._denethor_setup_applied: set[str] = set()
        self._denethor_move_action_used_chars: set[str] = set()
        self._fotw_beregond_response_used_rounds: set[str] = set()
        self._fatty_bolger_action_used_chars: set[str] = set()
        self._beorn_action_used_players: set[int] = set()
        self._glorfindel_action_used_players: set[int] = set()
        self._beravor_action_used_chars: set[str] = set()
        self._erestor_action_used_chars: set[str] = set()
        self._bifur_action_used_chars: set[str] = set()
        self._arwen_hero_action_used_chars: set[str] = set()
        self._haldir_action_used_chars: set[str] = set()
        self._galadriel_action_used_chars: set[str] = set()
        self._entered_play_this_round_ally_ids: set[str] = set()
        self._ally_entries_this_round = 0
        self._storm_comes_first_ally_free_used_players: set[int] = set()
        self._players_who_engaged_this_round: set[int] = set()
        self._grima_discount_pending: dict[int, bool] = {}
        self._o_lorien_discount_pending: dict[int, bool] = {}
        self._grima_action_used_chars: set[str] = set()
        self._wandering_took_action_used_chars: set[str] = set()
        self._rider_of_mark_action_used_chars: set[str] = set()
        self._blue_mountain_trader_action_used_chars: set[str] = set()
        self._imrahil_response_used_this_round: bool = False
        self._imrahil_combat_action_used_chars: set[str] = set()
        self._eomer_response_used_this_round: bool = False
        self._hama_ally_action_used_this_round: set[str] = set()
        self._hama_ally_phase_end_discard_ids: set[str] = set()
        self._landroval_response_used: bool = False
        self._rallying_call_return_to_hand_active: bool = False
        self._caldara_action_used: bool = False
        self._galdor_havens_action_used_chars: set[str] = set()
        self._protector_of_lorien_phase_uses: dict[str, int] = {}
        self._blood_of_numenor_phase_uses: dict[str, int] = {}
        self._gondorian_fire_phase_uses: dict[str, int] = {}
        self._gandalf_topdeck_phase_used_chars: set[str] = set()
        self._treebeard_action_phase_uses: dict[str, int] = {}
        self._kahliel_action_used_this_phase: set[str] = set()
        # 仅在甘道夫临时将牌库顶牌视作手牌、并支付该牌费用的调用栈内存在。
        self._gandalf_topdeck_play_context: dict | None = None
        self._frodo_damage_threat_used_this_phase: bool = False
        self._mablung_engage_resource_used_this_phase: set[str] = set()
        self._heroes_spent_resources_this_round: set[str] = set()
        self._tighten_our_belts_played_this_round: bool = False
        self._heavy_stroke_used_players: set[int] = set()
        self._boromir_ready_action_used_chars: set[str] = set()
        self._song_of_mocking_redirects: dict[str, str] = {}
        self._we_are_not_idle_active: bool = False
        self._phase_skip_engagement_checks: bool = False
        self._light_the_beacons_active: bool = False
        self._hour_of_wrath_hero_ids: set[str] = set()
        self._hour_of_wrath_player_ids: set[int] = set()
        self._naith_guide_quest_hero_ids: set[str] = set()
        self._linhir_captain_quest_char_ids: set[str] = set()
        self._wingfoot_pending: dict[str, str] = {}  # hero_id → named type
        self._wingfoot_used_hero_ids: set[str] = set()
        self._swift_and_silent_played_players: set[int] = set()
        self._noiseless_movement_played_players: set[int] = set()
        # 绿叶胸针：每位玩家本回合已使用隐匿1加成的派系集合
        self._leaf_brooch_sphere_used: dict[int, set[str]] = {}
        self.current_location_card = None
        self.current_location_progress = 0
        self.current_location_panel = None
        self._current_location_attachments_dialog = None
        self._location_attachments: dict[str, list] = {}
        self._thrors_key_processed_location_ids: set[str] = set()
        self._thrors_key_staging_sync_initialized: bool = False
        self._entangled_enemy_ids: set[str] = set()
        self._guarded_objective_attachment_ids: set[str] = set()
        self._belfalas_islet_active_location_id: str = ""
        self._belfalas_islet_set_aside_enemy_id: str = ""
        self.encounter_set_aside_cards: list = []
        self._spiders_of_mirkwood_2a_resolved: bool = False
        self._disappearance_1b_resolved: bool = False
        self._terror_of_dead_2a_resolved: bool = False
        self._terror_of_dead_2b_skip_planning: bool = False
        self._terror_of_dead_2b_no_hero_resources: bool = False
        self._shadow_host_3a_resolved: bool = False
        self._dol_guldur_orcs_2c_resolved: bool = False
        self._carried_away_3a_resolved: bool = False
        self._forest_of_great_fear_4a_resolved: bool = False
        self._formidable_opponent_ids: set[str] = set()
        self._escape_taur_nu_fuin_4c_resolved: bool = False
        self._stormcaller_area_card = None
        self._stormcaller_second_quest_meta: list[dict] = []
        self._stormcaller_quest_faces: list[dict] = []
        self._stormcaller_quest_index: int = 0
        self._stormcaller_quest_progress: int = 0
        self._stormcaller_area_extra_cards: list = []
        self._enemy_attachments: dict[str, list] = {}
        self._quest_attachments: dict[str, list] = {}
        self._player_threat_attachments: dict[int, list] = {}
        self._field_widgets: dict[str, PlayerCardWidget] = {}
        self._attachment_widgets: dict[str, PlayerCardWidget] = {}
        self._destroyed_characters: set[str] = set()
        self._destroyed_enemies: set[str] = set()
        self._victory_display_cards: list = []
        self._victory_display_vp = 0
        self._havens_burn_underneath_cards: list = []
        self._havens_burn_dialog = None
        self._suppress_burning_destroy_check = False
        self._suppress_destroy_check = False
        self._when_revealed_resolution_depth = 0
        self._pending_the_end_comes_departed: list = []
        self.round_number = 0
        self._phase_step = ""
        self.current_phase = "未开始"
        self.debug_mode = False
        self.initUI()

    def initUI(self):
        """创建UI界面"""
        self.setWindowTitle("魔戒 LCG")
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        self.resize(1200, 700)
        self.setMinimumSize(1000, 650)

        # 中央控件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

 # ==================== 自定义窗口标题栏 ====================
        title_bar = TitleBarWidget(self, "魔戒 LCG")
        top_layout = title_bar.tool_layout

        # 标题栏按钮
        start_button = QPushButton('开始游戏')
        debug_button = QPushButton("Debug OFF")
        self.debug_button = debug_button
        phase_button = QPushButton("下一阶段")
        cancel_button = QPushButton("取消")
        cancel_button.setEnabled(False)
        cancel_button.setToolTip("从存档恢复到进入当前大环节前的界面")
        self.cancel_phase_button = cancel_button
        debug_choose_button = QPushButton('调试:选择卡牌')
        flow_zoom_button = QPushButton('流程放大')
        flow_zoom_button.setToolTip("弹出窗口放大显示阶段流程条")
        log_button = QPushButton('日志')
        log_button.setToolTip("查看游戏运行日志（终端输出）")
        victory_button = QPushButton("胜利区 (0/0)")
        victory_button.setToolTip("查看胜利点计数区（同弃牌堆浏览）")
        set_aside_button = QPushButton("放置一旁 (0)")
        set_aside_button.setToolTip("查看所有放置一旁的卡牌（同弃牌堆浏览）")

        player_count_label = QLabel("玩家数")
        player_count_label.setObjectName("titleHint")
        player_count_label.setStyleSheet("font-size: 12px; color: #cccccc;")
        self.player_count_spin = QSpinBox()
        self.player_count_spin.setRange(1, self.MAX_PLAYERS)
        self.player_count_spin.setValue(1)
        self.player_count_spin.setToolTip("1-4 人模式；多人时每位玩家分别加载卡组")

        self._player_tab_buttons: list[QPushButton] = []
        self._player_tab_group = QButtonGroup(self)
        self._player_tab_group.setExclusive(True)
        player_tabs_widget = QWidget()
        player_tabs_layout = QHBoxLayout(player_tabs_widget)
        player_tabs_layout.setContentsMargins(0, 0, 0, 0)
        player_tabs_layout.setSpacing(4)
        for i in range(self.MAX_PLAYERS):
            btn = QPushButton(f"玩家{i + 1}")
            btn.setCheckable(True)
            btn.setVisible(False)
            btn.setFixedHeight(28)
            btn.clicked.connect(lambda _, idx=i: self._set_active_player(idx))
            self._player_tab_group.addButton(btn, i)
            self._player_tab_buttons.append(btn)
            player_tabs_layout.addWidget(btn)
        self._player_tab_buttons[0].setChecked(True)

        self._active_player_hint = QLabel("")
        self._active_player_hint.setStyleSheet(
            "font-size: 11px; color: #cccccc;"
        )
        self._active_player_hint.setVisible(False)

        # 阶段标签：保留供 _set_phase_label 写入（tooltip/日志用），不再显示，
        # 由下方 PhaseFlowBar 流程条取代
        self.phase_label = QLabel("未开始")
        self.phase_label.setVisible(False)

        # 添加到顶部布局
        top_layout.addWidget(start_button)
        top_layout.addWidget(phase_button)
        top_layout.addWidget(cancel_button)
        top_layout.addWidget(debug_button)
        top_layout.addWidget(debug_choose_button)
        top_layout.addWidget(flow_zoom_button)
        top_layout.addWidget(log_button)
        top_layout.addWidget(victory_button)
        top_layout.addWidget(set_aside_button)
        top_layout.addWidget(player_count_label)
        top_layout.addWidget(self.player_count_spin)
        top_layout.addWidget(player_tabs_widget)
        top_layout.addWidget(self._active_player_hint)
        top_layout.addStretch()

        self.start_button = start_button
        self.next_phase_button = phase_button
        self.victory_button = victory_button
        self.set_aside_button = set_aside_button
        start_button.clicked.connect(self._on_game_start)
        phase_button.clicked.connect(self._on_next_phase)
        cancel_button.clicked.connect(self._on_cancel_phase)
        debug_button.clicked.connect(self._toggle_debug)
        flow_zoom_button.clicked.connect(self._show_phase_flow_zoom)
        log_button.clicked.connect(self._show_game_log)
        victory_button.clicked.connect(self._show_victory_display_dialog)
        set_aside_button.clicked.connect(self._show_set_aside_dialog)
        self._refresh_set_aside_button()
        self._phase_flow_zoom_dialog: PhaseFlowZoomDialog | None = None

        main_layout.addWidget(title_bar)

        # ==================== 阶段流程导航栏====================
        self.phase_flow_bar = PhaseFlowBar(self)
        self.phase_flow_bar.skip_toggled.connect(self._on_flow_skip_toggled)
        self.phase_flow_bar.flow_view_changed.connect(self._on_phase_flow_view_changed)
        main_layout.addWidget(self.phase_flow_bar)
        self._flow_auto_skipping = False
        self._flow_auto_skip_node: str | None = None
        self._phase_flow_bar_refreshing = False
        self._update_phase_flow_bar()

        # ==================== 主体布局 ====================
        body_layout = QHBoxLayout()

        # ==================== 左侧布局 - 4行卡牌栏 ====================
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        self.card_bars = []
        self._card_row_scrollers: list[_CardRowHorizontalScroller] = []
        self._card_row_scroll_areas: list[QScrollArea] = []

        for i in range(4):
            # 创建每一行
            empty_row = QFrame()
            empty_row.setStyleSheet("background-color: #f0f0f0; border: 2px solid #ccc;")
            empty_row.setFixedHeight(170)
            empty_layout = QVBoxLayout(empty_row)
            empty_layout.setContentsMargins(10, 5, 10, 5)
            empty_layout.setSpacing(5)

            # 卡图容器
            container = QWidget()
            container.setStyleSheet("background: transparent;")
            card_bar = QHBoxLayout(container)
            card_bar.setContentsMargins(0, 0, 0, 0)
            card_bar.setSpacing(8)
            self.card_bars.append(card_bar)

            # 滚动区域
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
            scroll.viewport().setStyleSheet("background: transparent;")
            scroll.setWidget(container)
            self._card_row_scrollers.append(
                _CardRowHorizontalScroller(scroll, container)
            )
            self._card_row_scroll_areas.append(scroll)

            # 滚动条
            scrollbar = scroll.horizontalScrollBar()
            scrollbar.setFixedHeight(14)
            scrollbar.setStyleSheet("""
                QScrollBar:horizontal {
                    background: #f0f0f0;
                    height: 14px;
                    margin: 0px;
                    border: none;
                }
                QScrollBar::handle:horizontal {
                    background: #999999;
                    min-width: 30px;
                    border-radius: 6px;
                }
                QScrollBar::add-line:horizontal,
                QScrollBar::sub-line:horizontal {
                    width: 0px;
                    background: none;
                    border: none;
                }
                QScrollBar::add-page:horizontal,
                QScrollBar::sub-page:horizontal {
                    background: #dcdcdc;
                }
            """)

            # 左右滚动按钮
            btn_left = QPushButton("◀")
            btn_left.setFixedSize(24, 14)
            btn_left.setStyleSheet("QPushButton { background: #dcdcdc; border: 1px solid #aaa; }")
            btn_left.clicked.connect(lambda _, sb=scrollbar: sb.setValue(sb.value() - 150))

            btn_right = QPushButton("▶")
            btn_right.setFixedSize(24, 14)
            btn_right.setStyleSheet("QPushButton { background: #dcdcdc; border: 1px solid #aaa; }")
            btn_right.clicked.connect(lambda _, sb=scrollbar: sb.setValue(sb.value() + 150))

            scroll_row = QHBoxLayout()
            scroll_row.setContentsMargins(0, 0, 0, 0)
            scroll_row.setSpacing(4)
            scroll_row.addWidget(btn_left)
            scroll_row.addWidget(scrollbar)
            scroll_row.addWidget(btn_right)

            # 第3行特殊布局 - 威胁转移（向对面，拖拽到顶部）
            if i == 0:
                self.threat_dial = ThreatDialWidget(self.threat_level)
                row1_container = QHBoxLayout()
                row1_container.setSpacing(10)
                row1_container.setAlignment(Qt.AlignTop)
                row1_container.addWidget(self.threat_dial, 0, Qt.AlignTop | Qt.AlignLeft)
                row1_container.addWidget(scroll, 1)
                empty_layout.addLayout(row1_container)
            elif i == self.FIELD_ROW_INDEX:
                self._field_player_switch_btn = QToolButton()
                self._field_player_switch_btn.setToolButtonStyle(
                    Qt.ToolButtonTextUnderIcon
                )
                self._field_player_switch_btn.setFixedSize(92, 136)
                self._field_player_switch_btn.setVisible(False)
                self._field_player_switch_btn.setToolTip(
                    '切换查看的玩家（手牌、牌库、弃牌堆、威胁）\n'
                    "顺序：玩家1 → 玩家2 → 玩家3 → 玩家4"
                )
                self._field_player_switch_btn.clicked.connect(
                    self._on_field_player_switch_clicked
                )
                token_pix = QPixmap(str(FIRST_PLAYER_TOKEN))
                self._first_player_token_icon = QIcon()
                if not token_pix.isNull():
                    self._first_player_token_icon = QIcon(
                        token_pix.scaled(
                            40,
                            40,
                            Qt.KeepAspectRatio,
                            Qt.SmoothTransformation,
                        )
                    )
                field_row_container = QHBoxLayout()
                field_row_container.setSpacing(8)
                field_row_container.addWidget(self._field_player_switch_btn)
                # 《前路黑暗》甘道夫：牌库顶常驻公开区。
                # 固定在切换查看玩家按钮右侧，不占用场上角色的横向滚动区。
                self._gandalf_deck_top_panel = QFrame()
                self._gandalf_deck_top_panel.setObjectName("gandalfDeckTopPanel")
                self._gandalf_deck_top_panel.setFixedSize(92, 136)
                self._gandalf_deck_top_panel.setToolTip(
                    "甘道夫·牌库顶\n单击卡图可放大查看。"
                )
                self._gandalf_deck_top_panel.setStyleSheet(
                    "QFrame#gandalfDeckTopPanel {"
                    "border: 1px solid #8b6f2a; background: #fff9e6; "
                    "border-radius: 5px; }"
                )
                gandalf_panel_layout = QVBoxLayout(self._gandalf_deck_top_panel)
                gandalf_panel_layout.setContentsMargins(3, 2, 3, 2)
                gandalf_panel_layout.setSpacing(1)
                self._gandalf_deck_top_title = QLabel("甘道夫·牌库顶")
                self._gandalf_deck_top_title.setAlignment(Qt.AlignCenter)
                self._gandalf_deck_top_title.setFixedHeight(18)
                self._gandalf_deck_top_title.setStyleSheet(
                    "border: none; color: #624d16; font-size: 10px; font-weight: bold;"
                )
                gandalf_panel_layout.addWidget(self._gandalf_deck_top_title)
                self._gandalf_deck_top_card_layout = QVBoxLayout()
                self._gandalf_deck_top_card_layout.setContentsMargins(0, 0, 0, 0)
                self._gandalf_deck_top_card_layout.setSpacing(0)
                self._gandalf_deck_top_card_layout.setAlignment(Qt.AlignCenter)
                gandalf_panel_layout.addLayout(self._gandalf_deck_top_card_layout)
                self._gandalf_deck_top_panel.setVisible(False)
                field_row_container.addWidget(
                    self._gandalf_deck_top_panel, 0, Qt.AlignTop
                )
                field_row_container.addWidget(scroll, stretch=1)
                empty_layout.addLayout(field_row_container)
            else:
                empty_layout.addWidget(scroll)

            empty_layout.addLayout(scroll_row)

            # 添加整列到左侧布局
            left_layout.addWidget(empty_row)

        # ==================== 右侧布局 - 4x2网格 ====================
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        for row in range(4):
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)

            for col in range(2):
                cell = QFrame()
                cell.setFixedHeight(170)
                cell.setStyleSheet("border: 1px solid #ccc; background-color: white;")
                cell_layout = QVBoxLayout(cell)

                # 1-1：当前地区
                if row == 0 and col == 0:
                    cell_layout.setContentsMargins(0, 0, 0, 0)
                    self.area_1_1_layout = QVBoxLayout()
                    self.area_1_1_layout.setAlignment(
                        Qt.AlignHCenter | Qt.AlignTop
                    )
                    self.area_1_1_placeholder = QLabel("区域 1-1")
                    self.area_1_1_placeholder.setAlignment(Qt.AlignCenter)
                    self.area_1_1_placeholder.setMinimumSize(140, 160)
                    self.area_1_1_placeholder.setStyleSheet(
                        "border: 1px dashed #888; background-color: #f9f9f9;"
                    )
                    self.area_1_1_layout.addWidget(self.area_1_1_placeholder)
                    cell_layout.addLayout(self.area_1_1_layout)
                # 1-2：任务模块（场景.py）
                elif row == 0 and col == 1:
                    self.task_widget = 任务()
                    self.task_widget.quest_stage_completed.connect(
                        self._on_quest_stage_completed
                    )
                    self.task_widget.progress_changed.connect(
                        self._on_main_quest_progress_changed
                    )
                    self._bind_quest_stage_hooks()
                    cell_layout.setContentsMargins(0, 0, 0, 0)
                    cell_layout.addWidget(
                        self.task_widget, alignment=Qt.AlignHCenter | Qt.AlignTop
                    )
                # 2-1：遭遇卡抽取器
                elif row == 1 and col == 0:
                    self.encounter_drawer = CardDrawer(max_height=158)
                    cell_layout.setContentsMargins(0, 0, 0, 0)
                    cell_layout.addWidget(
                        self.encounter_drawer, alignment=Qt.AlignHCenter | Qt.AlignTop
                    )
                    self.encounter_drawer.deck_loaded.connect(
                        self._sync_quest_chain_from_encounter
                    )
                    if self.encounter_drawer.deck_path:
                        self._sync_quest_chain_from_encounter(
                            self.encounter_drawer.deck_path
                        )
                # 2-2：遭遇弃牌堆（单击查看）
                elif row == 1 and col == 1:
                    cell_layout.setContentsMargins(4, 2, 4, 2)
                    self.encounter_discard_panel = DiscardPilePanel(
                        title="遭遇弃牌堆",
                        card_kind="encounter",
                    )
                    self.encounter_discard_panel.clicked.connect(
                        self._show_encounter_discard_pile_dialog
                    )
                    cell_layout.addWidget(self.encounter_discard_panel)
                    self.pirate_discard_btn = QPushButton("弃牌堆2 (0)")
                    self.pirate_discard_btn.setToolTip("单独查看海盗牌组的弃牌堆")
                    self.pirate_discard_btn.setStyleSheet(
                        "QPushButton { background-color: #fff2e6; border: 1px solid #cc8a4d; "
                        "padding: 3px 8px; font-size: 11px; color: #8a4b12; }"
                        "QPushButton:hover { background-color: #ffe2c2; }"
                    )
                    self.pirate_discard_btn.setCursor(Qt.PointingHandCursor)
                    self.pirate_discard_btn.clicked.connect(
                        self._show_pirate_discard_pile_dialog
                    )
                    cell_layout.addWidget(self.pirate_discard_btn)
                    self.evil_creature_discard_btn = QPushButton("Evil discard (0)")
                    self.evil_creature_discard_btn.setToolTip("View the Q08.1 evil creature discard pile")
                    self.evil_creature_discard_btn.setCursor(Qt.PointingHandCursor)
                    self.evil_creature_discard_btn.clicked.connect(
                        self._show_evil_creature_discard_pile_dialog
                    )
                    cell_layout.addWidget(self.evil_creature_discard_btn)
                    self._refresh_encounter_discard_pile()
                # 3-2：航向卡
                elif row == 2 and col == 1:
                    cell_layout.setContentsMargins(4, 2, 4, 2)
                    self.area_3_2_layout = QVBoxLayout()
                    self.area_3_2_layout.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
                    self.heading_title_label = QLabel("航向：未设置")
                    self.heading_title_label.setAlignment(Qt.AlignCenter)
                    self.heading_title_label.setStyleSheet(
                        "border: none; font-weight: bold; color: #244b8f;"
                    )
                    self.heading_image_label = QLabel("区域 3-2\n航向")
                    self.heading_image_label.setAlignment(Qt.AlignCenter)
                    self.heading_image_label.setMinimumSize(140, 132)
                    self.heading_image_label.setStyleSheet(
                        "border: 1px dashed #888; background-color: #f9f9f9;"
                    )
                    self.area_3_2_layout.addWidget(self.heading_title_label)
                    self.area_3_2_layout.addWidget(self.heading_image_label)
                    self.grotto_deck_panel = DiscardPilePanel(
                        title="石窟牌库",
                        card_kind="encounter",
                    )
                    self.grotto_deck_panel.setMinimumSize(140, 158)
                    self.grotto_deck_panel.setToolTip(
                        "显示石窟牌库顶牌的石窟面；单击可放大查看。"
                        "海底面在翻面前不可查看。"
                    )
                    self.grotto_deck_panel.clicked.connect(
                        self._show_grotto_deck_top_dialog
                    )
                    self.area_3_2_layout.addWidget(self.grotto_deck_panel)
                    self.night_fire_side_quest_deck_panel = DiscardPilePanel(
                        title="支线探险牌组",
                        card_kind="encounter",
                    )
                    self.night_fire_side_quest_deck_panel.setMinimumSize(140, 158)
                    self.night_fire_side_quest_deck_panel.setToolTip(
                        "霍加堡 1A：8 张遭遇支线探险洗混后面朝下放置。"
                    )
                    self.night_fire_side_quest_deck_panel.clicked.connect(
                        self._show_night_fire_side_quest_deck_dialog
                    )
                    self.area_3_2_layout.addWidget(
                        self.night_fire_side_quest_deck_panel
                    )
                    self.island_map_button = QPushButton("岛屿地图")
                    self.island_map_button.setMinimumSize(140, 76)
                    self.island_map_button.setStyleSheet(
                        "QPushButton { background-color: #e8f2df; border: 2px solid #537b3a; "
                        "border-radius: 8px; padding: 8px; font-size: 18px; font-weight: bold; }"
                        "QPushButton:hover { background-color: #d7ebc8; }"
                    )
                    self.island_map_button.clicked.connect(self._show_island_map_dialog)
                    self.island_map_position_label = QLabel("当前位置：未选择")
                    self.island_map_position_label.setAlignment(Qt.AlignCenter)
                    self.island_map_position_label.setStyleSheet(
                        "border: none; color: #355326; font-weight: bold;"
                    )
                    self.area_3_2_layout.addWidget(self.island_map_button)
                    self.area_3_2_layout.addWidget(self.island_map_position_label)
                    cell_layout.addLayout(self.area_3_2_layout)
                    self.heading_title_label.setVisible(False)
                    self.heading_image_label.setVisible(False)
                    self.grotto_deck_panel.setVisible(False)
                    self.night_fire_side_quest_deck_panel.setVisible(False)
                    self.island_map_button.setVisible(False)
                    self.island_map_position_label.setVisible(False)
                    self._refresh_heading_display()
                # 4-1：玩家卡抽取器
                elif row == 3 and col == 0:
                    self.player_drawer = PlayerCardDrawer(max_height=158, adaptive=True)
                    cell_layout.setContentsMargins(0, 0, 0, 0)
                    cell_layout.addWidget(
                        self.player_drawer, alignment=Qt.AlignHCenter | Qt.AlignTop
                    )
                    self.player_drawer.deck_loaded.connect(self._on_player_deck_loaded)
                    self.player_drawer.deck_state_changed.connect(
                        self._refresh_gandalf_deck_top_panel
                    )
                    self.player_drawer.next_phase_requested.connect(
                        self._on_next_phase
                    )
                    self.player_drawer.setToolTip(
                        "双击牌库：进入下一阶段\n"
                        "弹窗：切换焦点后自动确认"
                    )
                    self._refresh_field_row()
                # 4-2：玩家弃牌堆（单击查看）
                elif row == 3 and col == 1:
                    cell_layout.setContentsMargins(4, 2, 4, 2)
                    self.discard_panel = DiscardPilePanel()
                    self.discard_panel.clicked.connect(self._show_discard_pile_dialog)
                    cell_layout.addWidget(self.discard_panel)
                    # 精灵之光：弃牌堆打出按钮
                    self.elven_light_discard_btn = QPushButton("打出 精灵之光")
                    self.elven_light_discard_btn.setStyleSheet(
                        "QPushButton { background-color: #e8f0ff; border: 1px solid #6688cc; "
                        "padding: 3px 8px; font-size: 11px; color: #224488; }"
                        "QPushButton:hover { background-color: #d0e0ff; }"
                    )
                    self.elven_light_discard_btn.setCursor(Qt.PointingHandCursor)
                    self.elven_light_discard_btn.setVisible(False)
                    self.elven_light_discard_btn.clicked.connect(
                        self._on_elven_light_discard_btn
                    )
                    cell_layout.addWidget(self.elven_light_discard_btn)
                    self._refresh_discard_pile()
                else:
                    image_label = QLabel(f"区域 {row+1}-{col+1}")
                    image_label.setAlignment(Qt.AlignCenter)
                    image_label.setMinimumSize(140, 160)
                    image_label.setStyleSheet("border: 1px dashed #888; background-color: #f9f9f9;")

                    cell_layout.addWidget(image_label)
                row_layout.addWidget(cell)

            right_layout.addWidget(row_widget)

        # ==================== 组合主体布局 ====================
        body_layout.addWidget(left_widget)
        body_layout.addWidget(right_widget)
        main_layout.addLayout(body_layout)
        self._card_hover_preview = _CardHoverPreviewController(self)

    def _player_state(self, player_index: int) -> PlayerState:
        return self._players[player_index]

    def _player_engagement(self, player_index: int) -> list:
        return self._players[player_index].engagement_cards

    def _all_engagement_cards(self) -> list:
        cards: list = []
        for idx in range(self.PLAYER_COUNT):
            cards.extend(self._players[idx].engagement_cards)
        seen = {getattr(card, "id", "") for card in cards}
        for card in self.staging_cards:
            if not (
                self._is_lurker_of_the_depths_card(card)
                or (self._is_cold_drake_attacks_2b_active() and self._is_cold_drake_card(card))
            ):
                continue
            card_id = getattr(card, "id", "") or ""
            if card_id and card_id in seen:
                continue
            cards.append(card)
            if card_id:
                seen.add(card_id)
        for card in self.staging_cards:
            if not self._dagnir_counts_as_engaged_with_each_player(card):
                continue
            card_id = getattr(card, "id", "") or ""
            if card_id and card_id in seen:
                continue
            cards.append(card)
            if card_id:
                seen.add(card_id)
        return cards

    def _engagement_cards_for(self, player_index: int) -> list:
        """兼容旧代码：单人模式返回全局交战列表。"""
        return self._player_engagement(player_index)

    @property
    def engagement_cards(self) -> list:
        if self.PLAYER_COUNT <= 1:
            return self._players[0].engagement_cards
        return self._all_engagement_cards()

    @engagement_cards.setter
    def engagement_cards(self, value: list):
        if self.PLAYER_COUNT <= 1:
            self._players[0].engagement_cards = value
        else:
            self._players[0].engagement_cards = list(value)

    def _player_color(self, player_index: int) -> str | None:
        if self.PLAYER_COUNT <= 1:
            return None
        if 0 <= player_index < len(self.PLAYER_COLORS):
            return self.PLAYER_COLORS[player_index]
        return None

    def _player_drawer_for(self, player_index: int) -> PlayerCardDrawer | None:
        if self.PLAYER_COUNT <= 1 and hasattr(self, "player_drawer"):
            return self.player_drawer
        state = self._players[player_index]
        if state.drawer is not None:
            return state.drawer
        if player_index == self._active_player_index and hasattr(self, "player_drawer"):
            return self.player_drawer
        return None

    def _copy_drawer_state(self, src: PlayerCardDrawer, dst: PlayerCardDrawer):
        dst.deck_text = src.deck_text
        dst.deck_path = src.deck_path
        dst.deck_heroes = list(src.deck_heroes)
        dst.deck_spec = src.deck_spec
        dst.deck_series = src.deck_series
        dst.cards = list(src.cards)
        dst.drawn_ids = set(src.drawn_ids)
        dst.deck_stack = list(src.deck_stack)
        dst.current_card = src.current_card
        dst.show_card_back()

    def _sync_visible_drawer_from_player(self, player_index: int):
        if not hasattr(self, "player_drawer"):
            return
        src = self._player_drawer_for(player_index)
        if src is None:
            return
        self._copy_drawer_state(src, self.player_drawer)
        self._refresh_gandalf_deck_top_panel()

    def _sync_player_drawer_from_visible(self, player_index: int):
        if not hasattr(self, "player_drawer"):
            return
        dst = self._players[player_index].drawer
        if dst is None:
            return
        self._copy_drawer_state(self.player_drawer, dst)

    # ==================== 环节存档 / 取消回档 ====================
    # 进入每个大环节前自动写入 save.rb；点击「取消」时读取并回档到该环节开始前。
    _CHECKPOINT_SCALAR_FIELDS = (
        "round_number", "_phase_step", "current_phase",
        "first_player_index", "_first_player_chosen",
        "_experience_mode", "_experience_mode_chosen",
        "_active_player_index", "_turn_player_index",
        "_game_lost", "_game_won",
        "_resource_actions_active",
        "_planning_active", "_planning_player_index",
        "_quest_assign_active", "_quest_assign_player_index",
        "_staging_active", "_staging_player_index",
        "_quest_staging_reveal_reduction",
        "_quest_staging_reveal_minimum_zero",
        "_adventure_begin_actions_active",
        "_quest_assign_actions_active",
        "_quest_resolve_actions_active",
        "_player_actions_active",
        "_travel_active", "_travel_chosen", "_travel_actions_active",
        "_voluntary_engage_active", "_engage_player_index", "_engage_chosen_this_turn",
        "_engage_count_this_turn",
        "_engage_check_active", "_engage_check_player_index",
        "_engage_check_awaiting_choice", "_engage_check_pending_candidates",
        "_encounter_engage_actions_active",
        "_encounter_actions_active",
        "_skip_encounter_phase_to_combat",
        "_combat_active", "_combat_shadow_distributed", "_combat_actions_active",
        "_combat_player_attacks_done",
        "_enemy_attack_active", "_enemy_attack_player_index",
        "_pending_forced_enemy_attacks",
        "_enemy_attack_substep_window_active", "_enemies_attacked_this_round",
        "_andrath_guardsman_blocked_attacks",
        "_yazan_response_used_this_phase",
        "_jubayr_response_used_this_phase",
        "_player_attack_active", "_player_attack_player_index",
        "_player_attack_substep_window_active", "_player_attacked_by",
        "_refresh_active", "_refresh_substep", "_refresh_actions_active",
        "_refresh_core_applied", "_aragorn_refresh_used_players",
        "_heroes_spent_resources_this_round",
        "_tighten_our_belts_played_this_round",
        "_hama_response_uses",
        "_quest_when_revealed_resolved_index",
        "_truly_lost_3c_resolved",
        "_adventure_phase_active",
        "_questing_ids", "_questing_readied", "_questing_ids_this_player",
        "_active_side_quest_id", "_player_side_quest_progress",
        "_declared_defender_ids", "_defender_readied",
        "_phase_willpower_bonus", "_phase_willpower_penalty",
        "_round_willpower_penalty",
        "_phase_attack_bonus", "_round_attack_bonus",
        "_gimli_defense_response_used_this_phase",
        "_legolas_quest_response_used_this_phase",
        "_lurker_damage_this_round", "_stormcaller_damage_this_round",
        "_cold_drake_damage_this_round",
        "_cold_drake_set_aside_damage",
        "_round_char_willpower_bonus", "_round_char_attack_bonus",
        "_round_char_defense_bonus", "_round_granted_vigilant",
        "_noiseless_movement_enemy_engagement_bonus",
        "_black_serpents_tail_engagement_penalty_ids",
        "_orc_hunting_party_low_engagement_ids",
        "_phase_defense_bonus",
        "_phase_enemy_attack_penalty",
        "_phase_enemy_defense_bonus",
        "_phase_enemy_defense_penalty",
        "_phase_close_quarters_draw_counts",
        "_phase_sphere_bonus",
        "_phase_granted_gondor_trait_ids",
        "_phase_granted_rohan_trait_ids",
        "_sneak_attack_returns", "_beorn_shuffle_returns",
        "_children_of_sea_shuffle_returns",
        "_imrahil_combat_action_returns",
        "_desperate_alliance_returns",
        "_to_me_kinsfolk_returns",
        "_bofur_quest_returns",
        "_good_meal_event_discount",
        "_renewed_hope_discount_player",
        "_phase_master_of_lore_discount",
        "_phase_to_the_sea_discount",
        "_phase_dol_amroth_soldier_tactics_discount",
        "_heir_of_valandil_discount",
        "_narelenya_ally_discount_used_players",
        "_theoden_ally_discount_used_players",
        "_undersea_grotto_ally_discount_used_this_round",
        "_active_location_flip_blocked_this_round",
        "_phase_harvest_sphere_by_player",
        "_phase_quest_battle_granted",
        "_phase_spirit_willpower_as_defense_active",
        "_quest_commit_stat_override",
        "_outlands_aura_counts",
        "_feint_blocked_attacks", "_expecting_mischief_pending",
        "_unseen_strike_attacker_ids", "_thicket_player_blocks",
        "_hobbit_sense_no_attack_players", "_faramir_staging_bonus_counts",
        "_side_by_side_multi_defense_players",
        "_linhir_captain_quest_char_ids",
        "_quest_fail_threat_blocked", "_quest_phase_skipped",
        "_phase_threat_excluded_card_ids", "_saruman_out_of_play_map",
        "_isengard_messenger_round_uses",
        "_fresh_tracks_ignored_enemy_ids",
        "_staging_unattached_attachments", "_ranger_spikes_skip_engage_ids",
        "_pippin_engagement_blocks",
        "_umbar_patrol_no_voluntary_engage_until_round_end",
        "_phase_staging_threat_bonus",
        "_phase_staging_area_threat_bonus",
        "_phase_ithilien_tracker_pending_count",
        "_phase_ithilien_tracker_zero_enemy_ids",
        "_driven_by_shadow_surge_pending",
        "_mirkwood_spider_phase_ready_blocked_ids",
        "_giant_spider_no_ready_ids",
        "_heavy_snow_player_effect_no_ready_ids",
        "_phase_enemy_no_damage_ids",
        "_mirkwood_patrol_shadow_no_ready_ids",
        "_mirkwood_spider_first_seen_this_phase",
        "_dol_guldur_orcs_first_seen_this_phase",
        "_belegost_servants_hazard_surge_used",
        "_lampwright_named_encounter_type",
        "_eowyn_action_used_chars", "_beravor_action_used_chars",
        "_fotw_eowyn_action_used", "_fotw_eowyn_setup_applied",
        "_denethor_setup_applied",
        "_denethor_move_action_used_chars",
        "_fotw_beregond_response_used_rounds",
        "_fatty_bolger_action_used_chars",
        "_erestor_action_used_chars",
        "_bifur_action_used_chars",
        "_arwen_hero_action_used_chars",
        "_haldir_action_used_chars",
        "_galadriel_action_used_chars",
        "_entered_play_this_round_ally_ids",
        "_ally_entries_this_round",
        "_storm_comes_first_ally_free_used_players",
        "_players_who_engaged_this_round",
        "_grima_discount_pending",
        "_o_lorien_discount_pending",
        "_grima_action_used_chars",
        "_wandering_took_action_used_chars",
        "_rider_of_mark_action_used_chars",
        "_blue_mountain_trader_action_used_chars",
        "_beorn_action_used_players", "_glorfindel_action_used_players",
        "_protector_of_lorien_phase_uses",
        "_blood_of_numenor_phase_uses",
        "_gondorian_fire_phase_uses",
        "_gandalf_topdeck_phase_used_chars",
        "_treebeard_action_phase_uses",
        "_kahliel_action_used_this_phase",
        "_galdor_havens_action_used_chars",
        "_boromir_ready_action_used_chars",
        "_captain_sahir_ally_action_used_rounds",
        "_naasiyah_ally_action_used_rounds",
        "_imrahil_combat_action_used_chars",
        "_song_of_mocking_redirects",
        "_we_are_not_idle_active",
        "_phase_skip_engagement_checks",
        "_noiseless_movement_enemy_engagement_bonus",
        "_light_the_beacons_active",
        "_hour_of_wrath_hero_ids", "_hour_of_wrath_player_ids",
        "_shadow_revealed", "_extra_shadow_revealed",
        "_wolf_rider_shadow_return_ids", "_wolf_rider_shadow_return_cards",
        "_promoted_ally_ids", "_char_owner", "_eliminated_players", "_elimination_threat_delta",
        "_justice_shall_be_done_eliminate_at_round_end",
        "_destroyed_characters", "_destroyed_enemies",
        "_victory_display_cards", "_victory_display_vp",
        "_havens_burn_underneath_cards",
        "encounter_set_aside_cards",
        "_spiders_of_mirkwood_2a_resolved",
        "_disappearance_1b_resolved",
        "_terror_of_dead_2a_resolved",
        "_terror_of_dead_2b_skip_planning",
        "_terror_of_dead_2b_no_hero_resources",
        "_shadow_host_3a_resolved",
        "_dol_guldur_orcs_2c_resolved",
        "_carried_away_3a_resolved",
        "staging_cards", "encounter_discard_cards", "_uncharted_location_deck",
        "_grotto_location_deck", "_grotto_deck_enabled",
        "_night_fire_side_quest_deck", "_night_fire_side_quest_deck_enabled",
        "_hrogars_hill_damage", "_hrogars_hill_1b_forced_round",
        "_island_map_cells", "_island_map_position",
        "pirate_deck_cards", "pirate_discard_cards", "_pirate_deck_enabled",
        "evil_creature_deck_cards", "evil_creature_discard_cards", "_evil_creature_deck_enabled",
        "_q08_2b_location_progress_round",
        "_dark_woods_progress_round",
        "_things_in_the_deep_encounter_switched",
        "_city_of_corsairs_second_encounter_cards",
        "_city_of_corsairs_retired_encounter_cards",
        "_city_of_corsairs_encounter_switched",
        "heading_index", "heading_controller_index",
        "current_location_card", "current_location_progress",
        "_location_attachments", "_guarded_objective_attachment_ids",
        "_belfalas_islet_active_location_id", "_belfalas_islet_set_aside_enemy_id",
        "_enemy_attachments", "_quest_attachments", "_player_threat_attachments",
        "_shadow_cards", "_extra_shadow_cards", "_bonus_extra_shadow_cards",
        "_black_serpents_tail_pending_shadows",
        "_facedown_attachment_ids", "_entangled_enemy_ids",
        "_forest_of_great_fear_4a_resolved", "_formidable_opponent_ids",
        "_escape_taur_nu_fuin_4c_resolved",
    )

    def _player_drawer_snapshot(self, drawer) -> dict:
        return {
            "deck_text": drawer.deck_text,
            "deck_path": drawer.deck_path,
            "deck_series": drawer.deck_series,
            "deck_spec": drawer.deck_spec,
            "deck_heroes": list(drawer.deck_heroes),
            "cards": list(drawer.cards),
            "drawn_ids": set(drawer.drawn_ids),
            "deck_stack": list(drawer.deck_stack),
            "current_card": drawer.current_card,
        }

    def _apply_player_drawer_snapshot(self, drawer, snap: dict):
        drawer.deck_text = snap.get("deck_text")
        drawer.deck_path = snap.get("deck_path")
        drawer.deck_series = snap.get("deck_series")
        drawer.deck_spec = snap.get("deck_spec")
        drawer.deck_heroes = list(snap.get("deck_heroes") or [])
        drawer.cards = list(snap.get("cards") or [])
        drawer.drawn_ids = set(snap.get("drawn_ids") or set())
        drawer.deck_stack = list(snap.get("deck_stack") or [])
        drawer.current_card = snap.get("current_card")
        drawer.show_card_back()
        self._refresh_gandalf_deck_top_panel()

    def _encounter_drawer_snapshot(self) -> dict:
        d = self.encounter_drawer
        return {
            "deck_path": d.deck_path,
            "deck_series": d.deck_series,
            "cards": list(d.cards),
            "drawn_ids": set(d.drawn_ids),
            "current_card": d.current_card,
            "setup_cards": list(getattr(d, "setup_cards", [])),
            "special_cards": list(getattr(d, "special_cards", [])),
        }

    def _apply_encounter_drawer_snapshot(self, snap: dict):
        d = self.encounter_drawer
        d.deck_path = snap.get("deck_path")
        d.deck_series = snap.get("deck_series")
        d.cards = list(snap.get("cards") or [])
        d.setup_cards = list(snap.get("setup_cards") or [])
        d.special_cards = list(snap.get("special_cards") or [])
        d.drawn_ids = set(snap.get("drawn_ids") or set())
        d.current_card = snap.get("current_card")
        d.show_card_back()

    def _build_checkpoint_dict(self, target_step: str) -> dict:
        game = {
            field: getattr(self, field, None)
            for field in self._CHECKPOINT_SCALAR_FIELDS
        }
        players = []
        for idx in range(self.MAX_PLAYERS):
            st = self._players[idx]
            drawer = self._player_drawer_for(idx)
            players.append({
                "index": st.index,
                "hand_cards": list(st.hand_cards),
                "ally_cards": list(st.ally_cards),
                "discard_cards": list(st.discard_cards),
                "encounter_set_aside_cards": list(st.encounter_set_aside_cards),
                "removed_from_game_cards": list(st.removed_from_game_cards),
                "engagement_cards": list(st.engagement_cards),
                "attachments": {k: list(v) for k, v in st.attachments.items()},
                "hero_resources": dict(st.hero_resources),
                "threat_level": st.threat_level,
                "initial_threat_level": st.initial_threat_level,
                "mulligan_used": st.mulligan_used,
                "deck_path": st.deck_path,
                "deck_text": st.deck_text,
                "drawer": self._player_drawer_snapshot(drawer) if drawer else None,
            })
        game["players"] = players
        game["encounter_drawer"] = self._encounter_drawer_snapshot()
        if hasattr(self, "task_widget"):
            game["task"] = {
                "task_index": self.task_widget.task_index,
                "progress_count": self.task_widget.progress_count,
            }
        else:
            game["task"] = None
        game["stormcaller_area"] = {
            "card": self._stormcaller_area_card,
            "second_quest_meta": list(self._stormcaller_second_quest_meta or []),
            "quest_faces": list(getattr(self, "_stormcaller_quest_faces", []) or []),
            "quest_index": int(getattr(self, "_stormcaller_quest_index", 0) or 0),
            "quest_progress": int(getattr(self, "_stormcaller_quest_progress", 0) or 0),
            "extra_cards": list(getattr(self, "_stormcaller_area_extra_cards", []) or []),
        }
        game["markers"] = {
            "player": export_player_marker_cache(),
            "encounter": export_encounter_marker_cache(),
        }
        return {
            "version": CHECKPOINT_VERSION,
            "target_step": target_step,
            "player_count": self._player_count,
            "game": _jsonify(game),
        }

    def _write_checkpoint_file(self, payload: dict) -> bool:
        try:
            tmp_path = CHECKPOINT_PATH.with_suffix(".rb.tmp")
            text = CHECKPOINT_HEADER + "\n" + json.dumps(
                payload, ensure_ascii=False
            )
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp_path, CHECKPOINT_PATH)
            return True
        except OSError as exc:
            print(f"写入失败：{exc}")
            return False

    def _read_checkpoint_file(self) -> dict | None:
        if not CHECKPOINT_PATH.is_file():
            return None
        try:
            with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
                lines = [ln for ln in f.readlines() if not ln.lstrip().startswith("#")]
            text = '、'.join(lines).strip()
            if not text:
                return None
            return json.loads(text)
        except (OSError, ValueError) as exc:
            print(f"读取失败：{exc}")
            return None

    def _clear_checkpoint_file(self):
        try:
            if CHECKPOINT_PATH.is_file():
                CHECKPOINT_PATH.unlink()
        except OSError as exc:
            print(f"清除失败：{exc}")
        if hasattr(self, "cancel_phase_button"):
            self.cancel_phase_button.setEnabled(False)

    def _save_phase_checkpoint(self, target_step: str, allow_cancel: bool = True):
        """进入大环节前自动写入 save.rb（覆盖旧档）。
        allow_cancel=False 时仍写入存档，但禁用「取消」按钮（如 3.3 探查完成后，
        遭遇牌已随机翻开，不允许回档重抽）。
        """
        if not self._game_started:
            return
        # 跳过游戏开始后的首个 1.1（此前为准备阶段，回档无意义并且会卡住推进）
        if not self._phase_step:
            return
        self._sync_hero_resources_from_widgets()
        if self.PLAYER_COUNT > 1:
            self._sync_player_drawer_from_visible(self._active_player_index)
        payload = self._build_checkpoint_dict(target_step)
        if self._write_checkpoint_file(payload):
            if hasattr(self, "cancel_phase_button"):
                self.cancel_phase_button.setEnabled(
                    allow_cancel and not self._game_lost and not self._game_won
                )
            if self.debug_mode:
                tag = "（禁止取消）" if not allow_cancel else ""
                print(f"自动存档 -> save.rb（{target_step}）{tag}")

    def _lock_phase_cancel(self, reason: str = ""):
        """锁定「取消」回档：一旦从遭遇牌库抽取或翻面魔影即终止本环节回档。
        敌军阶段揭示魔影或抽取遭遇牌后，回档等同于重抽随机遭遇牌，
        因此禁用「取消」按钮（进入下一大环节时会随存档自动恢复可用）。"""
        if hasattr(self, "cancel_phase_button") and self.cancel_phase_button.isEnabled():
            self.cancel_phase_button.setEnabled(False)
            if self.debug_mode and reason:
                print(f"已锁定“取消”回档：{reason}")

    def _on_cancel_phase(self):
        if not self._game_started:
            self._warn("提示", '游戏尚未开始，无可取消的环节。')
            return
        if self._game_lost or self._game_won:
            self._warn("提示", "本局已结束，无法取消回档。")
            return
        self._restore_game_checkpoint()

    def _restore_game_checkpoint(self):
        payload = self._read_checkpoint_file()
        if not payload or not payload.get("game"):
            self._warn('无可用存档', 'save.rb 不存在或已损坏，无法取消回档。')
            return
        target_step = payload.get("target_step", "")
        if self._question(
            "取消回档",
            f"将恢复到进入 {target_step} 前的界面，是否继续？",
            default_yes=False,
        ) != QMessageBox.Yes:
            return
        game = _unjsonify(payload["game"])
        self._player_count = int(payload.get("player_count", self._player_count))
        self._apply_checkpoint(game, target_step)
        print(f"已取消并回档到进入 {target_step} 前的界面")
        # 回档及重开弹窗全部结束后，下一轮事件循环再强制重载一次所有显示区域，
        # 修复回档瞬间卡牌错乱（数据正确但布局/渲染未完全刷新）的问题。
        QTimer.singleShot(0, self._rebuild_all_display_regions)

    def _apply_checkpoint(self, game: dict, target_step: str = ""):
        self._suppress_destroy_check = True
        try:
            for row in (
                self.STAGING_ROW_INDEX,
                self.ENGAGEMENT_ROW_INDEX,
                self.FIELD_ROW_INDEX,
                self.HAND_ROW_INDEX,
            ):
                self._clear_card_bar(row)
            self._clear_location_display()
            self._field_widgets.clear()
            self._attachment_widgets.clear()
            self._engagement_host_widgets.clear()
            self._staging_host_widgets.clear()
            self.hero_widgets.clear()
            self.ally_widgets.clear()
            self.staging_widgets.clear()
            self.engagement_widgets.clear()
            self.hand_widgets.clear()

            markers = game.get("markers") or {}
            restore_player_marker_cache(markers.get("player") or {})
            restore_encounter_marker_cache(markers.get("encounter") or {})

            for fld in self._CHECKPOINT_SCALAR_FIELDS:
                if fld in game:
                    setattr(self, fld, game[fld])
            # 战斗结算上下文不持久化：回档后回到环节起点的空状态
            self._enemy_attack_ctx = None
            self._player_attack_ctx = None
            self._clear_defender_commit_state()
            self._engage_check_pending_candidates = list(
                game.get("_engage_check_pending_candidates") or []
            )

            for idx, pdata in enumerate(game.get("players") or []):
                if idx >= self.MAX_PLAYERS:
                    break
                st = self._players[idx]
                st.index = pdata.get("index", idx)
                st.hand_cards = list(pdata.get("hand_cards") or [])
                st.ally_cards = list(pdata.get("ally_cards") or [])
                st.discard_cards = list(pdata.get("discard_cards") or [])
                st.encounter_set_aside_cards = list(
                    pdata.get("encounter_set_aside_cards") or []
                )
                st.removed_from_game_cards = list(
                    pdata.get("removed_from_game_cards") or []
                )
                st.engagement_cards = list(pdata.get("engagement_cards") or [])
                st.attachments = {
                    k: list(v) for k, v in (pdata.get("attachments") or {}).items()
                }
                st.hero_resources = dict(pdata.get("hero_resources") or {})
                st.threat_level = pdata.get("threat_level", 0)
                st.initial_threat_level = pdata.get("initial_threat_level", 0)
                st.mulligan_used = pdata.get("mulligan_used", False)
                st.deck_path = pdata.get("deck_path")
                st.deck_text = pdata.get("deck_text")
                drawer = self._player_drawer_for(idx)
                snap = pdata.get("drawer")
                if drawer is not None and snap:
                    self._apply_player_drawer_snapshot(drawer, snap)

            enc_snap = game.get("encounter_drawer")
            if enc_snap:
                self._apply_encounter_drawer_snapshot(enc_snap)
            if self.PLAYER_COUNT > 1:
                self._sync_visible_drawer_from_player(self._active_player_index)

            task = game.get("task")
            if task and hasattr(self, "task_widget"):
                self.task_widget.task_index = task.get("task_index", 0)
                self.task_widget.progress_count = task.get("progress_count", 0)
            stormcaller_area = game.get("stormcaller_area") or {}
            self._stormcaller_area_card = stormcaller_area.get("card")
            self._stormcaller_second_quest_meta = list(
                stormcaller_area.get("second_quest_meta") or []
            )
            self._stormcaller_quest_faces = list(
                stormcaller_area.get("quest_faces") or []
            )
            if "quest_index" in stormcaller_area:
                self._stormcaller_quest_index = int(
                    stormcaller_area.get("quest_index") or 0
                )
            else:
                self._stormcaller_quest_index = 1
            self._stormcaller_quest_progress = int(
                stormcaller_area.get("quest_progress") or 0
            )
            self._stormcaller_area_extra_cards = list(
                stormcaller_area.get("extra_cards") or []
            )

            # 重建界面
            self._rebuild_all_display_regions()
        finally:
            self._suppress_destroy_check = False
        if hasattr(self, "next_phase_button"):
            self.next_phase_button.setEnabled(True)
        if hasattr(self, "cancel_phase_button"):
            self.cancel_phase_button.setEnabled(
                CHECKPOINT_PATH.is_file()
                and not self._game_lost
                and not self._game_won
            )
        self._resettle_restored_phase(target_step)

    def _rebuild_all_display_regions(self):
        """重建所有显示区域：探险/交战/场上/手牌/弃牌/当前地区/任务图/转盘/标记。
        回档应用一次，并在回档（含重开弹窗）结束后用 QTimer 再强制重载一次，
        修复回档瞬间卡牌错乱（数据正确但布局/渲染未完全刷新）的问题。"""
        self._refresh_staging_row(self.staging_cards)
        self._refresh_engagement_row()
        self._refresh_field_row()
        self._refresh_hand_row(self.hand_cards)
        self._refresh_gandalf_deck_top_panel()
        self._refresh_discard_pile()
        self._refresh_encounter_discard_pile()
        self._refresh_victory_display_button()
        self._refresh_set_aside_button()
        self._refresh_current_location_display()
        self._refresh_heading_display()
        if hasattr(self, "task_widget"):
            self.task_widget.load_current_task()
            self.task_widget._update_progress_display()
        if hasattr(self, "threat_dial"):
            self.threat_dial.set_threat_level(self.threat_level)
        self._update_quest_dial_badges()
        self._update_player_tab_styles()
        self._set_phase_label(self.current_phase)
        if self._experience_mode_chosen:
            self._apply_experience_mode_settings()

    def _heading_status_text(self, heading_index: int) -> str:
        labels = {
            1: "正确航向",
            2: "偏离航向",
            3: "偏离航向",
            4: "偏离航向 + 最差航向",
        }
        return labels.get(int(heading_index or 0), "未设置")

    def _heading_image_path(self, heading_index: int) -> Path:
        image_dir = Path(__file__).resolve().parent / "cards" / "images"
        for filename in (
            f"Heading{heading_index}.jpg",
            f"Heading({heading_index}).jpg",
        ):
            path = image_dir / filename
            if path.is_file():
                return path
        return image_dir / f"Heading{heading_index}.jpg"

    def _current_heading(self) -> int:
        try:
            heading_index = int(getattr(self, "heading_index", 0) or 0)
        except (TypeError, ValueError):
            heading_index = 0
        return max(0, min(4, heading_index))

    def _set_heading(
        self,
        heading_index: int = 1,
        controller_index: int | None = None,
        *,
        log: bool = True,
    ):
        try:
            heading_index = int(heading_index)
        except (TypeError, ValueError):
            heading_index = 1
        heading_index = max(1, min(4, heading_index))
        self.heading_index = heading_index
        self.heading_controller_index = controller_index
        controller_text = (
            f"玩家 {controller_index + 1}"
            if isinstance(controller_index, int) and controller_index >= 0
            else "未知玩家"
        )
        if log:
            print(
                f"航向设置：{controller_text} 控制逐梦者号，"
                f"Heading{heading_index}（{self._heading_status_text(heading_index)}）"
            )
        self._refresh_heading_display()
        if hasattr(self, "_field_widgets"):
            self._refresh_field_row()
        if hasattr(self, "staging_cards"):
            self._sync_all_staging_threat_passives()
            self._update_quest_dial_badges()

    def _adjust_heading(self, delta: int, reason: str = "", *, log: bool = True) -> bool:
        current = self._current_heading()
        if current <= 0:
            self._set_heading(
                1, getattr(self, "heading_controller_index", None), log=log
            )
            current = 1
        if delta < 0 and current <= 1:
            if log:
                print("航向改正：当前已为 Heading1（正确航向），无法再次改正。")
            return False
        if delta > 0 and current >= 4:
            if log:
                print("航向改偏：当前已为 Heading4（最差航向），无法再次改偏。")
            return False
        next_heading = max(1, min(4, current + delta))
        self._set_heading(
            next_heading, getattr(self, "heading_controller_index", None), log=log
        )
        action = "改正" if delta < 0 else "改偏"
        reason_text = f"（{reason}）" if reason else ""
        if log:
            print(
                f"航向{action}{reason_text}：Heading{current} → Heading{next_heading}"
                f"（{self._heading_status_text(next_heading)}）"
            )
        return True

    def _correct_heading(self, reason: str = "", *, log: bool = True) -> bool:
        return self._adjust_heading(-1, reason, log=log)

    def _deviate_heading(self, reason: str = "", *, log: bool = True) -> bool:
        return self._adjust_heading(1, reason, log=log)

    def _is_island_map_scenario(self) -> bool:
        return (self._encounter_series() or "").strip() == self.ISLAND_MAP_SERIES

    def _is_drowned_ruins_scenario(self) -> bool:
        return (self._encounter_series() or "").strip() in {
            "沉没的废墟",
            "The Drowned Ruins",
        }

    def _island_map_cell(self, row: int, col: int) -> dict | None:
        if not (0 <= row < self.ISLAND_MAP_ROWS and 0 <= col < self.ISLAND_MAP_COLS):
            return None
        for cell in self._island_map_cells:
            if cell.get("row") == row and cell.get("col") == col:
                return cell
        return None

    def _island_map_display_card(self, cell: dict | None):
        if not cell:
            return None
        return cell.get("hidden_card") if cell.get("face_up") else cell.get("front_card")

    def _island_map_cell_for_card(self, card) -> dict | None:
        if card is None:
            return None
        card_id = getattr(card, "id", "") or ""
        for cell in self._island_map_cells:
            shown = self._island_map_display_card(cell)
            if shown is card or (card_id and getattr(shown, "id", "") == card_id):
                return cell
        return None

    def _is_island_map_card(self, card) -> bool:
        return self._island_map_cell_for_card(card) is not None

    def _island_map_adjacent_cells(self) -> list[dict]:
        if self._island_map_position is None:
            return []
        row, col = self._island_map_position
        cells = []
        for direction in ("上", "下", "左", "右"):
            cell = self._island_map_neighbor(row, col, direction)
            if cell is not None:
                cells.append(cell)
        return cells

    def _is_jagged_cliffs_location(self, card) -> bool:
        if card is None:
            return False
        base_id = self._card_octgn_base_id(card)
        if base_id == "1877b069-7015-4172-b5df-bbfd235b976f":
            return True
        name = (getattr(card, "name", "") or "").strip()
        canonical = CARD_NAME_ALIASES.get(name, "")
        return name in {"锯齿悬崖", "Jagged Cliffs"} or canonical in {
            "锯齿悬崖",
            "Jagged Cliffs",
        }

    def _jagged_cliffs_right_blocked_cell(self) -> dict | None:
        if self._island_map_position is None:
            return None
        current = self._island_map_cell(*self._island_map_position)
        current_card = self._island_map_display_card(current)
        if not self._is_jagged_cliffs_location(current_card):
            return None
        row, col = self._island_map_position
        return self._island_map_neighbor(row, col, "右")

    def _jungle_path_blocked_cells(self) -> list[dict]:
        if self._island_map_position is None:
            return []
        current = self._island_map_cell(*self._island_map_position)
        current_card = self._island_map_display_card(current)
        if not self._is_jungle_path_location(current_card):
            return []
        row, col = self._island_map_position
        return [
            cell for cell in (
                self._island_map_neighbor(row, col, "上"),
                self._island_map_neighbor(row, col, "下"),
            )
            if cell is not None
        ]

    def _island_map_cell_counts_as_staging(self, cell: dict | None) -> bool:
        if cell is None:
            return False
        blocked = self._jagged_cliffs_right_blocked_cell()
        if blocked is not None and cell is blocked:
            return False
        return all(cell is not blocked_cell for blocked_cell in self._jungle_path_blocked_cells())

    def _island_map_cells_within_distance(self, distance: int) -> list[dict]:
        if self._island_map_position is None:
            return []
        row, col = self._island_map_position
        distance = max(0, int(distance or 0))
        cells = []
        for cell in getattr(self, "_island_map_cells", []) or []:
            cell_row = int(cell.get("row", -99))
            cell_col = int(cell.get("col", -99))
            if (cell_row, cell_col) == (row, col):
                continue
            if abs(cell_row - row) + abs(cell_col - col) <= distance:
                if self._island_map_cell_counts_as_staging(cell):
                    cells.append(cell)
        return cells

    def _island_map_travel_target_cells(self) -> list[dict]:
        if self._island_map_position is None:
            return []
        current = self._island_map_cell(*self._island_map_position)
        current_card = self._island_map_display_card(current)
        if self._is_winding_caverns_location(current_card):
            return self._island_map_cells_within_distance(3)
        return [
            cell for cell in self._island_map_adjacent_cells()
            if self._island_map_cell_counts_as_staging(cell)
        ]

    def _island_map_virtual_staging_locations(self) -> list:
        return [
            self._island_map_display_card(cell)
            for cell in self._island_map_adjacent_cells()
            if self._island_map_cell_counts_as_staging(cell)
        ]

    def _island_map_staging_display_locations(self) -> list:
        """1B 中显示在探查区、但仍固定留在地图坐标上的相邻地区。"""
        if not self._is_searching_island_1b_quest_active():
            return []
        return self._island_map_virtual_staging_locations()

    def _flip_island_map_cell(self, cell: dict, *, resolve_forced: bool) -> list[str]:
        if cell.get("face_up"):
            return []
        cell["face_up"] = True
        cell["progress"] = 0
        hidden = cell.get("hidden_card")
        clear_encounter_marker_state_for_card(hidden)
        lines = [f"岛屿地图：翻开「{getattr(hidden, 'name', '未知地区')}」。"]
        if resolve_forced and hidden is not None:
            lines.extend(self._resolve_uncharted_location_flipped(hidden))
            if self._is_jagged_cliffs_location(hidden):
                lines.extend(self._resolve_jagged_cliffs_flipped(cell, hidden))
            if self._is_temple_halls_location(hidden):
                lines.extend(self._resolve_temple_halls_flipped(hidden))
            if self._is_grotto_entrance_location(hidden):
                lines.extend(self._resolve_grotto_entrance_flipped(hidden))
            if self._is_jungle_path_location(hidden):
                lines.extend(self._resolve_jungle_path_flipped(hidden))
        return lines

    def _peek_island_map_cell_back_forced(self, cell: dict, source_name: str) -> str:
        hidden = cell.get("hidden_card")
        front = cell.get("front_card")
        front_name = getattr(front, "name", "失落的岛屿") or "失落的岛屿"
        hidden_name = getattr(hidden, "name", "未知反面") if hidden is not None else "未知反面"
        cell["peeked"] = True
        image_path = (getattr(hidden, "image_path", "") or "").strip() if hidden else ""
        if image_path and Path(image_path).is_file():
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                dlg = CardImageZoomDialog(pixmap, self)
                dlg.setWindowTitle(f"查看反面 · {hidden_name} - 单击关闭")
                dlg.exec_()
        row = int(cell.get("row", 0)) + 1
        col = int(cell.get("col", 0)) + 1
        return (
            f"强制 · {source_name}：查看相邻「{front_name}」"
            f"（第 {row} 行第 {col} 列）的反面「{hidden_name}」。"
        )

    def _resolve_jagged_cliffs_flipped(self, cell: dict, location_card) -> list[str]:
        """锯齿悬崖：被激活翻面时，查看每个相邻失落的岛屿反面。"""
        source_name = getattr(location_card, "name", "锯齿悬崖") or "锯齿悬崖"
        row = int(cell.get("row", 0))
        col = int(cell.get("col", 0))
        notes: list[str] = []
        for direction in ("上", "下", "左", "右"):
            neighbor = self._island_map_neighbor(row, col, direction)
            if neighbor is None or neighbor.get("face_up"):
                continue
            front = neighbor.get("front_card")
            if not self._is_lost_island_proxy(front):
                continue
            notes.append(self._peek_island_map_cell_back_forced(neighbor, source_name))
        if not notes:
            return [f"强制 · {source_name}：没有相邻的面朝上「失落的岛屿」可查看反面。"]
        return notes

    def _jungle_path_exhaust_options_for_player(self, player_index: int) -> list[CharacterPickOption]:
        return [
            opt for opt in self._ready_character_pick_options(player_index)
            if int(getattr(opt, "attack", 0) or 0) >= 2
        ]

    def _resolve_jungle_path_flipped(self, location_card) -> list[str]:
        """丛林小径：被激活翻面时，每位玩家横置一名攻击至少 2 的角色。"""
        source_name = getattr(location_card, "name", "丛林小径") or "丛林小径"
        notes: list[str] = []
        for player_idx in range(self.PLAYER_COUNT):
            if player_idx in getattr(self, "_eliminated_players", set()):
                continue
            player_tag = self._player_tag(player_idx) or f"玩家 {player_idx + 1}"
            options = self._jungle_path_exhaust_options_for_player(player_idx)
            if not options:
                notes.append(
                    f"强制 · {source_name}：{player_tag}没有重整且攻击至少 2 的角色可横置。"
                )
                continue
            if len(options) == 1:
                chosen_id = options[0].char_id
            else:
                dlg = CharacterImagePickDialog(
                    self,
                    f"强制 · {source_name}",
                    f"{player_tag}必须横置一名攻击至少 2 的角色：",
                    options,
                    mode="single",
                    highlight_stat="attack",
                    mandatory=True,
                )
                dlg.exec_()
                chosen_id = dlg.selected_id() or options[0].char_id
            self._set_host_exhausted(chosen_id, True)
            char_name = self._character_display_name(chosen_id)
            notes.append(
                f"强制 · {source_name}：{player_tag}横置「{char_name}」。"
            )
        return notes

    def _choose_island_map_start_cell(self, top_left: dict, bottom_left: dict) -> tuple[int, int]:
        options = []
        for cell, label in ((top_left, "左上角"), (bottom_left, "左下角")):
            card = self._island_map_display_card(cell)
            options.append(CharacterPickOption(
                char_id=f"{cell['row']},{cell['col']}", label=f"{label} · {card.name}",
                image_path=getattr(card, "image_path", "") or "",
                attack=self._card_threat_value(card),
                defense=self._location_progress_required(card), health=0,
            ))
        choice = CharacterImagePickDialog(
            self, "搜寻小岛 1A · 选择起始地区",
            "左上角和左下角地区已翻面（不结算强制效果）。请选择起始激活地区：",
            options, mode="single", highlight_stat="defense", mandatory=True,
        )
        choice.exec_()
        selected = choice.selected_id() or "0,0"
        return tuple(int(part) for part in selected.split(",", 1))

    def _island_map_neighbor(self, row: int, col: int, direction: str) -> dict | None:
        """取得地图相邻格；右方恒为朝神庙列（列号增加）。"""
        offsets = {
            "上": (-1, 0), "up": (-1, 0),
            "下": (1, 0), "down": (1, 0),
            "左": (0, -1), "left": (0, -1),
            "右": (0, 1), "right": (0, 1),
        }
        offset = offsets.get((direction or "").strip().lower())
        if offset is None:
            return None
        return self._island_map_cell(row + offset[0], col + offset[1])

    def _set_island_map_position(self, row: int, col: int) -> bool:
        if self._island_map_cell(row, col) is None:
            return False
        self._island_map_position = (row, col)
        self._refresh_island_map_entry()
        print(f"岛屿地图：当前位置设为第 {row + 1} 行第 {col + 1} 列")
        return True

    def _can_travel_on_island_map(self, row: int, col: int) -> bool:
        if not self._travel_active or self._travel_chosen or self._island_map_position is None:
            return False
        current = self._island_map_cell(*self._island_map_position)
        current_card = self._island_map_display_card(current)
        needed = self._location_progress_required(current_card)
        if needed <= 0 or int(current.get("progress", 0) or 0) < needed:
            return False
        return self._island_map_cell(row, col) in self._island_map_travel_target_cells()

    def _finish_island_map_location_logically(self, card, lines: list[str]):
        lines.extend(self._try_curious_bucklanders_play_from_hand_response(card))
        lines.extend(self._resolve_ancient_mathom_explored_responses(card))
        lines.extend(self._resolve_elf_stone_explored_responses(card))
        lines.extend(self._resolve_ever_my_heart_rises_explored_responses(card))
        lines.extend(self._resolve_location_explored_responses(card))
        lines.extend(self._discard_location_attachments(getattr(card, "id", "") or ""))

    def _ready_controlled_gate_key_for_travel_cost(self):
        for player_idx in range(getattr(self, "PLAYER_COUNT", 0)):
            if player_idx in getattr(self, "_eliminated_players", set()):
                continue
            state = self._players[player_idx]
            for attachments in getattr(state, "attachments", {}).values():
                for att in attachments:
                    if not self._is_gate_key_objective(att):
                        continue
                    att_id = getattr(att, "id", "") or ""
                    widget = self._attachment_widgets.get(att_id)
                    if widget is None or not widget.is_exhausted():
                        return att
            for att in getattr(self, "_player_threat_attachments", {}).get(player_idx, []):
                if not self._is_gate_key_objective(att):
                    continue
                att_id = getattr(att, "id", "") or ""
                widget = self._attachment_widgets.get(att_id)
                if widget is None or not widget.is_exhausted():
                    return att
        return None

    def _pay_temple_of_the_deceived_travel_cost(self, target_cell: dict | None) -> str | None:
        if target_cell is None:
            return ""
        target_card = self._island_map_display_card(target_cell)
        if not self._is_temple_of_the_deceived_location(target_card):
            return ""
        gate_key = self._ready_controlled_gate_key_for_travel_cost()
        if gate_key is None:
            return None
        self._set_attachment_exhausted(getattr(gate_key, "id", "") or "", True)
        gate_key_name = getattr(gate_key, "name", "") or "大门钥匙"
        return f"探索 · 欺诈者神庙：横置「{gate_key_name}」以探索本地区。"

    def _travel_on_island_map(self, row: int, col: int) -> bool:
        if not self._can_travel_on_island_map(row, col):
            self._inform("4.2 岛屿地图游历", "当前地区进度未满，或目标地区不相邻。")
            return False
        old_position = tuple(self._island_map_position)
        old_cell = self._island_map_cell(*self._island_map_position)
        new_cell = self._island_map_cell(row, col)
        old_card = self._island_map_display_card(old_cell)
        gate_key_note = self._pay_temple_of_the_deceived_travel_cost(new_cell)
        if gate_key_note is None:
            self._inform(
                "4.2 岛屿地图游历",
                "「欺诈者神庙」的探索费用未支付：需要横置玩家控制的「大门钥匙」。",
            )
            return False
        lines = [f"当前地区「{old_card.name}」在离开时探索完毕。"]
        if gate_key_note:
            lines.append(gate_key_note)
        self._finish_island_map_location_logically(old_card, lines)
        old_cell["progress"] = 0
        old_cell["explored"] = True
        self._island_map_position = (row, col)
        if not new_cell.get("face_up"):
            lines.extend(self._flip_island_map_cell(new_cell, resolve_forced=True))
        new_card = self._island_map_display_card(new_cell)
        self.current_location_card = new_card
        self.current_location_progress = int(new_cell.get("progress", 0) or 0)
        lines.extend(
            self._resolve_island_watcher_right_explore_forced(
                old_position,
                (row, col),
            )
        )
        self._travel_chosen = True
        self._refresh_current_location_display()
        self._refresh_heading_display()
        self._refresh_staging_row(self.staging_cards)
        self._inform("4.2 岛屿地图游历", "\n".join(lines))
        self._start_next_pending_forced_enemy_attack()
        return True

    def _activate_island_map_lost_island_action(self, row: int, col: int) -> bool:
        cell = self._island_map_cell(row, col)
        if cell is None or cell.get("face_up") or cell.get("front_kind") != "lost_island":
            return False
        if cell not in self._island_map_adjacent_cells():
            self._inform("行动 · 失落的岛屿", "该地区当前不与激活地区相邻，因此不视为在场景区。")
            return False
        if not self._is_player_action_window_active():
            self._inform("行动 · 失落的岛屿", "当前不是玩家行动窗口，不能触发此行动。")
            return False
        progress = int(cell.get("progress", 0) or 0)
        if progress < 4:
            self._inform(
                "行动 · 失落的岛屿",
                f"该地区只有 {progress} 枚进度，不能移除4枚查看反面。",
            )
            return False
        cell["progress"] = progress - 4
        cell["peeked"] = True
        hidden = cell.get("hidden_card")
        hidden_name = getattr(hidden, "name", "未知反面") or "未知反面"
        image_path = (getattr(hidden, "image_path", "") or "").strip()
        if image_path and Path(image_path).is_file():
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                dlg = CardImageZoomDialog(pixmap, self)
                dlg.setWindowTitle(f"查看反面 · {hidden_name} - 单击关闭")
                dlg.exec_()
        self._inform(
            "行动 · 失落的岛屿",
            f"移除4枚进度标记（{progress} → {progress - 4}），查看反面：{hidden_name}\n\n"
            "只是查看反面，不翻面，也不触发翻面强制效果。",
        )
        self._refresh_heading_display()
        return True

    def _refresh_grotto_deck_panel(self) -> bool:
        """区域 3-2：只公开石窟牌库顶牌的石窟面。"""
        if not hasattr(self, "grotto_deck_panel"):
            return False
        visible = bool(
            getattr(self, "_grotto_deck_enabled", False)
            and self._is_drowned_ruins_scenario()
        )
        self.grotto_deck_panel.setVisible(visible)
        if not visible:
            return False
        deck = list(getattr(self, "_grotto_location_deck", []) or [])
        top_card = deck[0] if deck else None
        self.grotto_deck_panel.set_title("石窟牌库")
        self.grotto_deck_panel.set_count(len(deck))
        self.grotto_deck_panel.set_top_card(
            top_card,
            series=self._encounter_series(),
        )
        return True

    def _refresh_night_fire_side_quest_deck_panel(self) -> bool:
        if not hasattr(self, "night_fire_side_quest_deck_panel"):
            return False
        visible = bool(
            getattr(self, "_night_fire_side_quest_deck_enabled", False)
            and getattr(self, "_night_fire_side_quest_deck", None)
        )
        self.night_fire_side_quest_deck_panel.setVisible(visible)
        if not visible:
            return False
        deck = list(getattr(self, "_night_fire_side_quest_deck", []) or [])
        panel = self.night_fire_side_quest_deck_panel
        panel.set_title("支线探险牌组")
        panel.set_count(len(deck))
        panel._clear_top_slot()
        back = QLabel("面朝下")
        back.setAlignment(Qt.AlignCenter)
        back.setMinimumSize(78, 100)
        back.setStyleSheet(
            "border: 1px solid #555; background-color: #3c3f46; "
            "color: white; font-size: 13px; font-weight: bold;"
        )
        panel._top_slot.addWidget(back, alignment=Qt.AlignHCenter)
        return True

    def _show_night_fire_side_quest_deck_dialog(self):
        deck = list(getattr(self, "_night_fire_side_quest_deck", []) or [])
        if not deck:
            self._warn("支线探险牌组", "支线探险牌组为空。")
            return
        self._inform(
            "支线探险牌组",
            f"支线探险牌组共有 {len(deck)} 张，当前全部面朝下。",
        )

    def _show_grotto_deck_top_dialog(self):
        deck = getattr(self, "_grotto_location_deck", None) or []
        if not deck:
            self._warn("石窟牌库", "石窟牌库为空。")
            return
        top_card = deck[0]
        image_path = (getattr(top_card, "image_path", "") or "").strip()
        if image_path and Path(image_path).is_file():
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                dlg = CardImageZoomDialog(pixmap, self)
                dlg.setWindowTitle(
                    f"石窟牌库顶牌 · {getattr(top_card, 'name', '石窟地区')} - 单击关闭"
                )
                dlg.exec_()
                return
        self._inform(
            "石窟牌库顶牌",
            f"石窟面：{getattr(top_card, 'name', '石窟地区')}\n\n"
            "海底面在翻面前不可查看。",
        )

    def _refresh_island_map_entry(self):
        if not hasattr(self, "island_map_button"):
            return
        visible = self._is_island_map_scenario() and bool(self._island_map_cells)
        self.island_map_button.setVisible(visible)
        self.island_map_position_label.setVisible(visible)
        if not visible:
            return
        position = self._island_map_position
        if position is None:
            text = "当前位置：未选择"
        else:
            text = f"当前位置：第 {position[0] + 1} 行，第 {position[1] + 1} 列"
        self.island_map_position_label.setText(text)

    def _show_island_map_dialog(self):
        if not self._island_map_cells:
            self._warn("岛屿地图", "岛屿地图尚未布置。")
            return
        IslandMapDialog(self, self).exec_()

    def _refresh_heading_display(self):
        if not hasattr(self, "heading_title_label") or not hasattr(
            self, "heading_image_label"
        ):
            return
        self._refresh_island_map_entry()
        grotto_visible = self._refresh_grotto_deck_panel()
        if grotto_visible:
            if hasattr(self, "night_fire_side_quest_deck_panel"):
                self.night_fire_side_quest_deck_panel.setVisible(False)
            self.heading_title_label.setVisible(False)
            self.heading_image_label.setVisible(False)
            return
        night_fire_side_quest_visible = self._refresh_night_fire_side_quest_deck_panel()
        if night_fire_side_quest_visible:
            self.heading_title_label.setVisible(False)
            self.heading_image_label.setVisible(False)
            return
        island_map_visible = self._is_island_map_scenario() and bool(self._island_map_cells)
        if island_map_visible:
            if hasattr(self, "night_fire_side_quest_deck_panel"):
                self.night_fire_side_quest_deck_panel.setVisible(False)
            self.heading_title_label.setVisible(False)
            self.heading_image_label.setVisible(False)
            return
        heading_index = int(getattr(self, "heading_index", 0) or 0)
        if heading_index <= 0:
            self.heading_title_label.setText("航向：未设置")
            self.heading_image_label._hover_card_pixmap = None
            self.heading_image_label.clear()
            self.heading_image_label.setText("")
            self.heading_title_label.setVisible(False)
            self.heading_image_label.setVisible(False)
            return
        self.heading_title_label.setVisible(True)
        self.heading_image_label.setVisible(True)
        status_text = self._heading_status_text(heading_index)
        controller_index = getattr(self, "heading_controller_index", None)
        controller_suffix = (
            f"（玩家 {controller_index + 1}）"
            if isinstance(controller_index, int) and controller_index >= 0
            else ""
        )
        self.heading_title_label.setText(
            f"Heading{heading_index}：{status_text}{controller_suffix}"
        )
        image_path = self._heading_image_path(heading_index)
        reader = QImageReader(str(image_path))
        reader.setAutoTransform(True)
        pix = QPixmap.fromImage(reader.read())
        if pix.isNull():
            self.heading_image_label._hover_card_pixmap = None
            self.heading_image_label.clear()
            self.heading_image_label.setText(f"Heading{heading_index}\n{status_text}")
            return
        self.heading_image_label._hover_card_pixmap = pix
        self.heading_image_label._hover_card_face_up = True
        self.heading_image_label.setPixmap(
            pix.scaled(140, 132, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def _resettle_restored_phase(self, target_step: str = ""):
        """回档后重新开启对应环节的行动窗口，使其可交互。
        存档有两种与计划环节相关的情形：
        - 进入 2.1 前保存（target_step="2.1"，数据 _phase_step="1.1"）：在 2.2
          特殊行动窗口按「取消」属于此情形，应撤销未计划环节打出的卡牌，
          并把 _phase_step 置回 "2.1" 重开全新的特殊行动窗口。
        - 进入 3.1 前保存（数据 _phase_step="2.1"，标签仍停留在 2.2）：在探险
          环节按「取消」属于此情形，重开计划环节使其可继续打牌。
        仅重开「无副作用」的计划环节窗口（只设标记、选先手提示，不抽牌、不涨
        威胁），避免重结算。"""
        if self._game_lost or self._game_won:
            return
        if target_step == "6.7 玩家攻击":
            # 回档到玩家攻击开始前：清除残留攻击上下文并重开玩家攻击过程。
            self._phase_step = "6.1"
            self._player_attack_ctx = None
            self._player_attack_substep_window_active = False
            self._player_attack_active = False
            self._start_player_attack_process()
            return
        if target_step == "2.1" or self._phase_step == "2.1":
            self._phase_step = "2.1"
            self._planning_active = False
            self._start_planning_phase()

    def _update_player_tab_styles(self):
        if not hasattr(self, "_player_tab_buttons"):
            return
        if self.PLAYER_COUNT <= 1:
            for btn in self._player_tab_buttons:
                btn.setVisible(False)
            self._update_field_player_switch_btn()
            return
        for idx, btn in enumerate(self._player_tab_buttons):
            if idx >= self.PLAYER_COUNT:
                btn.setVisible(False)
                continue
            btn.setVisible(True)
            parts = []
            if idx == self._active_player_index:
                parts.append("background-color: #0078d4; color: white; font-weight: bold;")
            else:
                parts.append("background-color: #e8e8e8; color: #333;")
            if idx == self.first_player_index and self.PLAYER_COUNT > 1:
                parts.append("border: 2px solid #FFD700;")
            elif idx == self._turn_player_index and self.PLAYER_COUNT > 1:
                parts.append("border: 2px dashed #004488;")
            else:
                parts.append("border: 1px solid #aaa;")
            btn.setStyleSheet(" ".join(parts))
        self._update_field_player_switch_btn()

    def _update_field_player_switch_btn(self):
        """第 3 行左侧：多人局显示大号切换玩家按钮。"""
        if not hasattr(self, "_field_player_switch_btn"):
            return
        btn = self._field_player_switch_btn
        if self.PLAYER_COUNT <= 1:
            btn.setVisible(False)
            return
        btn.setVisible(True)
        idx = self._active_player_index
        player_no = idx + 1
        btn.setText(f"玩家{player_no}\n切换 ▼")
        show_token = (
            self._first_player_chosen
            and idx == self.first_player_index
        )
        if show_token and hasattr(self, "_first_player_token_icon"):
            btn.setIcon(self._first_player_token_icon)
            btn.setIconSize(QSize(40, 40))
        else:
            btn.setIcon(QIcon())
        color = self._player_color(idx) or "#0078d4"
        border = "2px solid #aaa;"
        if idx == self._turn_player_index:
            border = "3px dashed #004488;"
        btn.setStyleSheet(
            "QToolButton {"
            f"background-color: {color};"
            "color: white;"
            "font-size: 18px;"
            "font-weight: bold;"
            f"border: {border}"
            "border-radius: 8px;"
            "padding: 4px 2px;"
            "}"
            "QToolButton:hover {"
            "border: 3px solid white;"
            "}"
            "QToolButton:pressed {"
            "padding-top: 6px;"
            "}"
        )

    def _on_field_player_switch_clicked(self):
        """第 2 行按钮：按玩家 A→B→C… 顺序循环切换查看的玩家。"""
        if self.PLAYER_COUNT <= 1:
            return
        next_idx = (self._active_player_index + 1) % self.PLAYER_COUNT
        self._set_active_player(next_idx)

    def _maybe_prompt_experience_mode_selection(self) -> None:
        """回合 1 之 1.1：首次选择新手 / 熟练模式（仅一局）。"""
        if self._experience_mode_chosen:
            return
        if self.round_number != 1 or self._phase_step != "1.1":
            return
        dlg = ExperienceModePickDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            mode = "beginner"
        else:
            mode = dlg.picked_mode() or "beginner"
        self._experience_mode = mode
        self._experience_mode_chosen = True
        self._apply_experience_mode_settings()
        print(f"游戏模式：{'熟练' if mode == 'expert' else '新手'}")

    def _apply_experience_mode_settings(self) -> None:
        """按当前模式配置流程条：熟练=宏观流程+行动节点全标红（计划环节除外）；新手=详细主流程。"""
        bar = getattr(self, "phase_flow_bar", None)
        if bar is None:
            return
        expert = self._experience_mode == "expert"
        bar.set_expert_mode(expert)
        bar.apply_action_skip_preset(expert)
        if expert:
            bar.set_flow_variant("expert_macro", from_game=False)
        else:
            bar.reset_view_to_main()
        self._update_phase_flow_bar()

    def _maybe_prompt_first_player_selection(self) -> None:
        """回合 1 之 1.1：多人局首次选择起始玩家（仅一次）。"""
        if self._first_player_chosen or self.PLAYER_COUNT <= 1:
            return
        if self.round_number != 1 or self._phase_step != "1.1":
            return
        dlg = FirstPlayerPickDialog(
            self,
            self.PLAYER_COUNT,
            self.PLAYER_COLORS,
        )
        if dlg.exec_() != QDialog.Accepted:
            picked = 0
        else:
            picked = dlg.picked_index()
            if picked is None:
                picked = 0
        self.first_player_index = picked
        self._first_player_chosen = True
        self._set_active_player(picked)
        print(f"起始玩家：玩家 {picked + 1}")
        self._inform(
            "起始玩家",
            f"已选择玩家 {picked + 1} 为本局起始玩家。\n\n"
            "起始玩家标记将显示在该玩家切换按钮内。",
        )

    def _maybe_show_havens_burn_tip(self) -> None:
        """回合 1 之 1.1：若「海港在燃烧」在场景区，提示双击查看下方卡牌。"""
        if self.round_number != 1 or self._phase_step != "1.1":
            return
        if self._havens_burn_in_staging() is None:
            return
        self._inform(
            "海港在燃烧 · 提示",
            "你可以双击场景区中的「海港在燃烧」\n"
            "来查看面朝下叠放在其下方的卡牌。\n\n"
            "当下方卡牌数量达到玩家数 + 3 时，\n"
            "游戏将以失败告终。",
        )

    def _player_zone_card_counter(self, player_index: int) -> Counter:
        """统计该玩家手牌/弃牌/场上（盟友与附属）各卡牌 ID 的数量。"""
        state = self._players[player_index]
        out: Counter = Counter()
        for card in state.hand_cards:
            if getattr(card, "id", ""):
                out[card.id] += 1
        for card in state.discard_cards:
            if getattr(card, "id", ""):
                out[card.id] += 1
        for ally in state.ally_cards:
            if getattr(ally, "id", ""):
                out[ally.id] += 1
        for att_list in state.attachments.values():
            for att in att_list:
                if getattr(att, "id", ""):
                    out[att.id] += 1
        return out

    def _deck_remaining_count(
        self, drawer: PlayerCardDrawer | None, player_index: int | None = None
    ) -> int:
        """牌库剩余张数 = 牌组总数 − 已开出牌库的张数（按 ID 计数）。"""
        if drawer is None or not drawer.cards:
            return 0
        if player_index is None:
            player_index = self._active_player_index
        deck_counter = Counter(c.id for c in drawer.cards)
        if 0 <= player_index < self.PLAYER_COUNT:
            out_counter = self._player_zone_card_counter(player_index)
            return sum(
                max(0, deck_counter[cid] - out_counter.get(cid, 0))
                for cid in deck_counter
            )
        return len([c for c in drawer.cards if c.id not in drawer.drawn_ids])

    def _is_encounter_keyword_player_card(self, card) -> bool:
        """玩家牌「遭遇」关键词：不可进玩家牌组；展示后不可取消；离场后移出游戏。"""
        if card is None:
            return False
        text = (getattr(card, "Text_Effect", "") or "")
        fields = (
            getattr(card, "Encounter", ""),
            getattr(card, "encounter", ""),
            getattr(card, "遭遇", ""),
        )
        if any((str(value).strip() for value in fields)):
            return True
        normalized = re.sub(r"\s+", "", text)
        return (
            normalized.startswith("遭遇.")
            or normalized.startswith("遭遇。")
            or "遭遇.涌现" in normalized
            or "遭遇.涌动" in normalized
            or re.search(r"(?:^|\s)Encounter\.", text, re.I | re.M) is not None
        )

    def _remove_player_card_from_drawer(self, drawer, card) -> None:
        if drawer is None or card is None:
            return
        cid = getattr(card, "id", "") or ""
        drawer.cards = [c for c in drawer.cards if getattr(c, "id", "") != cid]
        drawer.deck_stack = [
            c for c in drawer.deck_stack if getattr(c, "id", "") != cid
        ]
        if cid:
            drawer.drawn_ids.discard(cid)

    def _set_aside_encounter_keyword_player_cards(
        self, player_index: int
    ) -> tuple[bool, str]:
        """布置时：最多 3 张「遭遇」玩家牌场外放置，不计入玩家牌库。"""
        drawer = self._player_drawer_for(player_index)
        if drawer is None:
            return True, ""
        state = self._players[player_index]
        found: list = []
        seen_ids: set[str] = set()
        for zone_cards in (list(drawer.cards), list(drawer.deck_stack)):
            for card in zone_cards:
                cid = getattr(card, "id", "") or ""
                if cid in seen_ids:
                    continue
                if self._is_encounter_keyword_player_card(card):
                    found.append(card)
                    seen_ids.add(cid)
        if not found:
            return True, ""
        if len(found) > 3:
            names = "、".join(getattr(c, "name", "?") for c in found)
            msg = (
                f"玩家 {player_index + 1} 的牌组含 {len(found)} 张"
                f"「遭遇」关键词玩家牌（{names}）。\n\n"
                "布置时每位玩家最多只能在场外放置 3 张，"
                "且这些牌不能加入玩家牌组。"
            )
            self._warn("遭遇关键词", msg)
            return False, msg
        for card in found:
            self._remove_player_card_from_drawer(drawer, card)
        state.encounter_set_aside_cards = list(found)
        self._refresh_set_aside_button()
        names = "、".join(getattr(c, "name", "?") for c in found)
        tag = self._player_tag(player_index) or f"玩家 {player_index + 1}"
        note = (
            f"{tag} 布置：场外放置 {len(found)} 张「遭遇」玩家牌"
            f"（{names}），不计入玩家牌组。"
        )
        print(note)
        return True, note

    def _set_aside_all_encounter_keyword_player_cards(self) -> bool:
        for idx in range(self.PLAYER_COUNT):
            ok, _ = self._set_aside_encounter_keyword_player_cards(idx)
            if not ok:
                return False
        if self.PLAYER_COUNT > 1:
            self._sync_visible_drawer_from_player(self._active_player_index)
        return True

    def _remove_encounter_keyword_player_card_from_game(
        self, card, owner_index: int | None = None, *, reason: str = "离场"
    ) -> bool:
        if not self._is_encounter_keyword_player_card(card):
            return False
        if owner_index is None:
            owner_index = self._character_owner_index(getattr(card, "id", "") or "")
        if owner_index is None or owner_index < 0 or owner_index >= self.PLAYER_COUNT:
            owner_index = self._active_player_index
        state = self._players[owner_index]
        cid = getattr(card, "id", "") or ""
        if cid:
            self._char_owner.pop(cid, None)
            self._remove_player_card_from_drawer(
                self._player_drawer_for(owner_index), card
            )
        clear_marker_state_for_card(card)
        if card not in state.removed_from_game_cards:
            state.removed_from_game_cards.append(card)
        name = getattr(card, "name", "?")
        tag = self._player_tag(owner_index) or f"玩家 {owner_index + 1}"
        print(f"{tag}「{name}」具有「遭遇」关键词，{reason}后移出游戏")
        return True

    def _encounter_keyword_set_aside_targets_for_effect(
        self, event_card, player_index: int
    ) -> list:
        """事件效果可将对应的场外「遭遇」玩家牌洗入遭遇牌库。"""
        if event_card is None or player_index < 0:
            return []
        text = (getattr(event_card, "Text_Effect", "") or "")
        if not (
            (getattr(event_card, "type", "") or "").strip() == "事件"
            and ("行动" in text or "Action" in text)
            and "放置在一旁" in text
            and "洗入" in text
            and "遭遇" in text
        ):
            return []
        state = self._players[player_index]
        candidates = []
        compact = re.sub(r"\s+", "", text)
        for card in state.encounter_set_aside_cards:
            if not self._is_encounter_keyword_player_card(card):
                continue
            names = {
                (getattr(card, "name", "") or "").strip(),
                CARD_NAME_ALIASES.get((getattr(card, "name", "") or "").strip(), ""),
            }
            names = {n for n in names if n}
            if any(n in text or n in compact for n in names):
                candidates.append(card)
        return candidates

    def _pick_encounter_keyword_set_aside_target(
        self, event_card, player_index: int
    ):
        candidates = self._encounter_keyword_set_aside_targets_for_effect(
            event_card, player_index
        )
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        def stat(value) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        options = [
            CharacterPickOption(
                char_id=getattr(card, "id", "") or str(i),
                label=getattr(card, "name", "?"),
                image_path=getattr(card, "image_path", "") or "",
                attack=stat(getattr(card, "Attack", 0)),
                defense=stat(getattr(card, "Defense", 0)),
                health=stat(getattr(card, "Health", 0)),
            )
            for i, card in enumerate(candidates)
        ]
        dlg = CharacterImagePickDialog(
            self,
            f"行动 · {event_card.name}",
            "选择一张场外「遭遇」玩家牌洗入遭遇牌库：",
            options,
            mode="single",
        )
        if dlg.exec_() != QDialog.Accepted:
            return None
        picked = dlg.selected_id()
        for card in candidates:
            if getattr(card, "id", "") == picked:
                return card
        return None

    def _shuffle_set_aside_encounter_keyword_card_into_encounter_deck(
        self, card, player_index: int
    ) -> str:
        if card is None:
            return "未选择场外「遭遇」玩家牌"
        if not hasattr(self, "encounter_drawer"):
            return "遭遇牌库未初始化"
        state = self._players[player_index]
        if card not in state.encounter_set_aside_cards:
            return f"「{getattr(card, 'name', '?')}」已不在场外区"
        state.encounter_set_aside_cards.remove(card)
        self._refresh_set_aside_button()
        clear_marker_state_for_card(card)
        self.encounter_drawer.drawn_ids.discard(getattr(card, "id", "") or "")
        self.encounter_drawer.cards.append(card)
        self.encounter_drawer.shuffle_deck()
        deck_n = len(self.encounter_drawer.cards)
        tag = self._player_tag(player_index) or f"玩家 {player_index + 1}"
        return (
            f"{tag} 将场外「{getattr(card, 'name', '?')}」"
            f"洗入遭遇牌库（牌库 {deck_n} 张）"
        )

    def _player_controlled_title(self, base: str) -> str:
        if self.PLAYER_COUNT <= 1:
            return base
        return f"玩家{self._active_player_index + 1} 路 {base}"

    def _player_tag(self, player_index: int, *, short: bool = False) -> str:
        """多人局返回玩家编号标记，单人局返回空串。"""
        if self.PLAYER_COUNT <= 1:
            return ""
        return f"P{player_index + 1}" if short else f"玩家{player_index + 1}"

    def _update_player_controlled_captions(self):
        """切换操控玩家时更新牌库/弃牌/威胁等区域的玩家标识。"""
        player_no = self._active_player_index + 1
        drawer = self._player_drawer_for(self._active_player_index)
        remaining = self._deck_remaining_count(
            drawer, self._active_player_index
        )
        threat = self._player_threat(self._active_player_index)
        if hasattr(self, "player_drawer"):
            if self.PLAYER_COUNT > 1:
                self.player_drawer.setToolTip(
                    f"玩家 {player_no} 牌库（剩余 {remaining} 张）\n"
                    "双击：进入下一阶段"
                )
            else:
                self.player_drawer.setToolTip(
                    f"牌库（剩余 {remaining} 张）\n"
                    "双击：进入下一阶段\n弹窗：切换焦点后自动确认"
                )
        if hasattr(self, "threat_dial"):
            if self.PLAYER_COUNT > 1:
                self.threat_dial.setToolTip(f"玩家 {player_no} 威胁等级：{threat}")
            else:
                self.threat_dial.setToolTip(f"威胁等级：{threat}")
        if hasattr(self, "_active_player_hint"):
            if self.PLAYER_COUNT > 1 and self._game_started:
                hand_n = len(self.hand_cards)
                _ = len(self.discard_cards)
                if not self._phase_step:
                    self._active_player_hint.setText(
                        f"起始手牌 · {self._player_tag(self._active_player_index)}"
                        f" · 手牌{hand_n}"
                    )
                else:
                    self._active_player_hint.setText(
                        f"查看：玩家 {player_no} · "
                    )
                self._active_player_hint.setVisible(True)
            else:
                self._active_player_hint.setVisible(False)

    def _refresh_active_player_views(self):
        """切换操控玩家：刷新手牌、牌库、弃牌堆、威胁转盘（场上/交战区不变）。"""
        if hasattr(self, "threat_dial"):
            self.threat_dial.set_threat_level(self.threat_level)
        self._refresh_hand_row(self.hand_cards)
        self._refresh_discard_pile()
        if self.PLAYER_COUNT > 1:
            self._sync_visible_drawer_from_player(self._active_player_index)
        self._refresh_gandalf_deck_top_panel()
        self._update_player_controlled_captions()

    def _set_active_player(self, player_index: int):
        if player_index < 0 or player_index >= self.PLAYER_COUNT:
            return
        self._active_player_index = player_index
        if hasattr(self, "_player_tab_buttons"):
            if 0 <= player_index < len(self._player_tab_buttons):
                self._player_tab_buttons[player_index].setChecked(True)
        self._refresh_active_player_views()
        self._update_player_tab_styles()
        QTimer.singleShot(0, self._maybe_scroll_field_row_to_active_player)

    def _player_field_card_count(self, player_index: int) -> int:
        """指定玩家场上英雄、盟友、角色附属的总张数。"""
        if player_index < 0 or player_index >= self.PLAYER_COUNT:
            return 0
        drawer = self._player_drawer_for(player_index)
        hero_count = len(getattr(drawer, "deck_heroes", []) or []) if drawer else 0
        state = self._players[player_index]
        ally_count = len(state.ally_cards)
        attachment_count = sum(len(cards) for cards in state.attachments.values())
        return hero_count + ally_count + attachment_count

    def _maybe_scroll_field_row_to_active_player(self):
        """多人局切换玩家后，必要时将场上行横向滚动到该玩家区块。"""
        if self.PLAYER_COUNT <= 1:
            return
        threshold = int(self.FIELD_AUTO_SCROLL_CARD_THRESHOLD)
        if threshold <= 0:
            return
        player_index = self._active_player_index
        if self._player_field_card_count(player_index) < threshold:
            return
        if not hasattr(self, "_card_row_scroll_areas"):
            return
        if self.FIELD_ROW_INDEX >= len(self._card_row_scroll_areas):
            return
        scroll = self._card_row_scroll_areas[self.FIELD_ROW_INDEX]
        block = getattr(self, "_field_player_blocks", {}).get(player_index)
        if scroll is None or block is None:
            return
        bar = scroll.horizontalScrollBar()
        target = max(bar.minimum(), min(block.x(), bar.maximum()))
        bar.setValue(target)

    def _current_turn_player_index(self) -> int | None:
        if self._quest_assign_active:
            return self._quest_assign_player_index
        if self._voluntary_engage_active:
            return self._engage_player_index
        if self._engage_check_active:
            return self._engage_check_player_index
        if self._enemy_attack_active:
            return self._enemy_attack_player_index
        if self._player_attack_active:
            return self._player_attack_player_index
        if self._resource_actions_active and hasattr(self, "_resource_player_index"):
            return self._resource_player_index
        if self._planning_active:
            return self._planning_player_index
        return None

    def _acting_player_index(self) -> int:
        """当前打出/支付费用所归属的玩家（回合顺位或查看 Tab）。"""
        if self._planning_active:
            return self._active_player_index
        turn_idx = self._current_turn_player_index()
        if turn_idx is not None:
            return turn_idx
        return self._active_player_index

    def _auto_switch_to_turn_player(self):
        turn_idx = self._current_turn_player_index()
        if turn_idx is None:
            self._turn_player_index = None
            self._update_player_tab_styles()
            return
        self._turn_player_index = turn_idx
        if self.PLAYER_COUNT > 1:
            self._set_active_player(turn_idx)
        else:
            self._update_player_tab_styles()

    def _require_active_turn_player(self, action_label: str) -> bool:
        turn_idx = self._current_turn_player_index()
        if turn_idx is None or self.PLAYER_COUNT <= 1:
            return True
        if self._active_player_index != turn_idx:
            self._inform(
                action_label,
                f"当前轮到玩家 {turn_idx + 1} 行动。\n"
                f"请先切换到玩家 {turn_idx + 1}。",
            )
            return False
        return True

    def _reset_all_player_states(self):
        self._char_owner.clear()
        self._promoted_ally_ids.clear()
        self._desperate_alliance_returns.clear()
        for idx in range(self.MAX_PLAYERS):
            state = self._players[idx]
            state.hand_cards.clear()
            state.ally_cards.clear()
            state.discard_cards.clear()
            state.encounter_set_aside_cards.clear()
            state.removed_from_game_cards.clear()
            state.engagement_cards.clear()
            state.attachments.clear()
            state.hero_resources.clear()
            state.threat_level = 0
            state.initial_threat_level = 0
            state.mulligan_used = False
            state.deck_path = None
            if state.drawer is not None:
                state.drawer.deleteLater()
                state.drawer = None
        self._refresh_set_aside_button()

    def _prefix_player_deck_ids(self, drawer: PlayerCardDrawer, player_index: int):
        """克隆牌组时为每位玩家的卡牌 ID 加前缀，避免同牌组多玩家 ID 冲突。"""
        prefix = f"p{player_index}_"

        def fix_id(card):
            if card and card.id and not str(card.id).startswith(prefix):
                card.id = prefix + str(card.id)

        for card in drawer.cards:
            fix_id(card)
        for hero in drawer.deck_heroes:
            fix_id(hero)
            self._char_owner[hero.id] = player_index

    def _remove_engagement_card(self, card_id: str):
        for idx in range(self.PLAYER_COUNT):
            self._players[idx].engagement_cards = [
                c for c in self._players[idx].engagement_cards if c.id != card_id
            ]

    def _wrap_with_player_border(self, widget: QWidget, player_index: int) -> QWidget:
        if self.PLAYER_COUNT <= 1:
            return widget
        color = self._player_color(player_index)
        if not color:
            return widget
        frame = QFrame()
        frame.setObjectName("playerBorderFrame")
        frame.setFrameShape(QFrame.NoFrame)
        frame.setAttribute(Qt.WA_StyledBackground, True)
        frame.setStyleSheet(
            "QFrame#playerBorderFrame {"
            f"border: 3px solid {color};"
            "border-radius: 6px;"
            "background-color: rgba(0, 0, 0, 0);"
            "}"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)
        layout.addWidget(widget)
        return frame

    def _load_deck_dialog_for_player(
        self,
        player_index: int,
        initial_text: str | None = None,
        *,
        allow_fellowship: bool = False,
    ) -> str | None:
        """弹出卡组对话框；取消则返回 None。"""
        dialog = DeckListDialog(
            self,
            initial_text=initial_text,
            allow_fellowship=allow_fellowship,
        )
        dialog.setWindowTitle(f"玩家 {player_index + 1} 加载卡组")
        if dialog.exec_() != QDialog.Accepted:
            return None
        return dialog.get_text()

    def _validate_unique_heroes_across_players(self) -> str:
        """团队展开：同名独特英雄不可由多名玩家同时使用。"""
        seen: dict[str, tuple[int, str]] = {}
        conflicts: list[str] = []
        for idx in range(self.PLAYER_COUNT):
            drawer = self._player_drawer_for(idx)
            if drawer is None:
                continue
            for hero in drawer.deck_heroes:
                if not _is_unique_card(hero):
                    continue
                canon = _canonical_card_title(hero.name)
                if canon in seen:
                    prev_idx, prev_name = seen[canon]
                    conflicts.append(
                        f"「{hero.name}」（玩家{idx + 1}）与"
                        f"「{prev_name}」（玩家{prev_idx + 1}）"
                    )
                else:
                    seen[canon] = (idx, hero.name)
        if not conflicts:
            return ""
        lines = "\n".join(f"路 {line}" for line in conflicts)
        return (
            "以下独特英雄在团队中重复，无法开始游戏：\n\n"
            f"{lines}\n\n"
            '规则：团队同时只能有一名同名独特英雄在场。'
        )

    def _clear_multiplayer_drawers(self):
        for idx in range(self.MAX_PLAYERS):
            state = self._players[idx]
            if state.drawer is not None:
                state.drawer.deleteLater()
            state.drawer = None
            state.deck_path = None
            state.deck_text = None

    def _setup_multiplayer_decks(
        self,
        first_text: str | None = None,
        preset_texts: list[str] | None = None,
    ) -> bool:
        """为每位玩家分别加载独立牌组，并校验独特英雄不重复。"""
        if preset_texts is not None and len(preset_texts) != self.PLAYER_COUNT:
            self._warn(
                "队伍加载失败",
                "队伍牌组数量与自动设置的玩家数不一致。",
            )
            return False
        initial_text = getattr(self.player_drawer, "deck_text", None)
        for idx in range(self.PLAYER_COUNT):
            if preset_texts is not None:
                text = preset_texts[idx]
            elif idx == 0 and first_text is not None:
                text = first_text
            else:
                text = self._load_deck_dialog_for_player(idx, initial_text)
            if text is None:
                print(f"游戏开始已取消：玩家 {idx + 1} 未加载牌组")
                self._clear_multiplayer_drawers()
                return False
            drawer = PlayerCardDrawer(parent=None, max_height=158)
            if not drawer.load_deck_from_text(text, silent=True):
                self._warn("提示", f"玩家 {idx + 1} 卡组加载失败：")
                self._clear_multiplayer_drawers()
                return False
            self._prefix_player_deck_ids(drawer, idx)
            drawer.deck_state_changed.connect(self._refresh_gandalf_deck_top_panel)
            state = self._players[idx]
            state.drawer = drawer
            state.deck_path = drawer.deck_path
            # RingsDB URL 会转换为 Main Deck 文本，存转换后的文本便于下位玩家继续编辑
            state.deck_text = drawer.deck_text or text
            initial_text = drawer.deck_text or text
            hero_names = '、'.join(h.name for h in drawer.deck_heroes) or '无'
            print(
                f"玩家 {idx + 1} 卡组：主牌 {len(drawer.cards)} 张，"
                f"英雄 {len(drawer.deck_heroes)} 名（{hero_names}）"
            )
        conflict = self._validate_unique_heroes_across_players()
        if conflict:
            self._warn("独特英雄冲突", conflict)
            self._clear_multiplayer_drawers()
            return False
        self._sync_visible_drawer_from_player(self._active_player_index)
        return True

    def _show_fellowship_missing_cards_warning(self, fellowship) -> None:
        groups: list[str] = []
        for player_index, deck in enumerate(fellowship.decks):
            if not deck.missing_cards:
                continue
            missing_lines = "\n".join(
                f"  路 {missing}" for missing in deck.missing_cards
            )
            groups.append(
                f"玩家 {player_index + 1} · {deck.name}\n{missing_lines}"
            )
        if not groups:
            return
        self._warn(
            "RingsDB 队伍：已跳过缺失卡牌",
            "以下卡牌未能匹配到本地 魔戒玩家牌.csv，已跳过：\n\n"
            + "\n\n".join(groups),
        )

    def _setup_player_decks_for_game(self) -> bool:
        """加载首副牌；若为 Fellowship，则自动装载整队并调整玩家数。"""
        original_count = self.PLAYER_COUNT
        initial_text = getattr(self.player_drawer, "deck_text", None)
        first_text = self._load_deck_dialog_for_player(
            0,
            initial_text,
            allow_fellowship=True,
        )
        if first_text is None:
            print("游戏开始已取消：玩家 1 未加载牌组")
            return False

        try:
            from CardViewer import (
                is_ringsdb_fellowship_source,
                ringsdb_fellowship_to_deck_texts,
            )
        except ImportError as exc:
            self._warn("RingsDB 队伍加载失败", str(exc))
            return False

        if not is_ringsdb_fellowship_source(first_text):
            if self.PLAYER_COUNT > 1:
                return self._setup_multiplayer_decks(first_text=first_text)
            return self.player_drawer.load_deck_from_text(first_text)

        try:
            QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
            QApplication.processEvents()
            try:
                fellowship = ringsdb_fellowship_to_deck_texts(
                    first_text,
                    skip_missing=True,
                )
            finally:
                QApplication.restoreOverrideCursor()
        except Exception as exc:
            self._warn("RingsDB 队伍加载失败", str(exc))
            return False

        player_count = len(fellowship.decks)
        if not 1 <= player_count <= self.MAX_PLAYERS:
            self._warn(
                "RingsDB 队伍加载失败",
                f"队伍包含 {player_count} 副牌；本游戏只支持 1-{self.MAX_PLAYERS} 名玩家。",
            )
            return False

        self._player_count = player_count
        self.player_count_spin.setValue(player_count)
        deck_texts = [deck.deck_text for deck in fellowship.decks]
        if player_count > 1:
            loaded = self._setup_multiplayer_decks(preset_texts=deck_texts)
        else:
            loaded = self.player_drawer.load_deck_from_text(
                deck_texts[0], silent=True
            )
        if not loaded:
            self._clear_multiplayer_drawers()
            self._player_count = original_count
            self.player_count_spin.setValue(original_count)
            return False

        print(
            f"已从 RingsDB 加载队伍「{fellowship.name}」："
            f"{player_count} 名玩家"
        )
        self._show_fellowship_missing_cards_warning(fellowship)
        return True

    def _sync_card_row_scroller_filters(self, row_index: int) -> None:
        if row_index < 0 or row_index >= len(self._card_row_scrollers):
            return
        self._card_row_scrollers[row_index].sync_child_filters()

    def _clear_card_bar(self, row_index: int):
        bar = self.card_bars[row_index]
        while bar.count():
            item = bar.takeAt(0)
            widget = item.widget()
            if widget:
                self._persist_widget_marker_states(widget)
                widget.deleteLater()

    def _widget_is_facedown_attachment(self, widget) -> bool:
        card = getattr(widget, "current_card", None)
        card_id = (getattr(card, "id", "") or "") if card is not None else ""
        return bool(card_id) and card_id in self._facedown_attachment_ids

    def _persist_widget_marker_states(self, widget):
        """刷新行之前保存卡牌控件上的标记状态。"""
        if hasattr(widget, "persist_marker_state"):
            if not self._widget_is_facedown_attachment(widget):
                widget.persist_marker_state()
            return
        for child in widget.findChildren(PlayerCardWidget):
            if not self._widget_is_facedown_attachment(child):
                child.persist_marker_state()
        for child in widget.findChildren(EncounterCardWidget):
            if not self._widget_is_facedown_attachment(child):
                child.persist_marker_state()

    def _build_character_group(
        self,
        card,
        *,
        show_resource_pool: bool = False,
        show_willpower_badge: bool = False,
        attachments_map: dict | None = None,
        hero_resources: dict | None = None,
        owner_color: str | None = None,
    ):
        """角色卡图+附属卡横向组合（红线连接宿主与附属）。"""
        if attachments_map is None:
            attachments_map = self._attachments
        if hero_resources is None:
            hero_resources = self._hero_resources
        group = CharacterGroupWidget()

        wp_bonus, hp_bonus, atk_bonus = self._compute_attachment_passive_bonuses(
            card, attachments_map
        )
        dain_wp, dain_atk = self._dain_ironfoot_aura_bonuses_for(card)
        broken_sword_wp = self._broken_sword_willpower_bonus_for(card)
        outlands_atk = self._outlands_attack_aura_bonus_for(card)
        outlands_def = self._outlands_defense_aura_bonus_for(card)
        outlands_wp = self._outlands_willpower_aura_bonus_for(card)
        outlands_hp = self._outlands_health_aura_bonus_for(card)
        eaad_denethor_wp = self._eaad_denethor_willpower_modifier_for(card)
        shipwright_wp = self._pelargir_shipwright_willpower_bonus_for(card)
        theoden_wp = self._theoden_tactics_willpower_bonus_for(card)
        sailor_wp = self._sailor_of_lune_willpower_bonus_for(card)
        star_brooch_wp = self._star_shaped_brooch_willpower_bonus_for(card, attachments_map)
        ingold_wp = self._ingold_willpower_bonus_for(card)
        rosabel_wp = self._rosabel_willpower_bonus_for_hero(card)
        voyage_ship_wp = self._voyage_departure_ship_willpower_bonus_for(card)
        ghan_buri_ghan_wp = self._ghan_buri_ghan_active_location_willpower_bonus_for(card)
        cursed_mists_wp = self._voyage_cursed_mists_ally_willpower_penalty_for(card)
        dol_amroth_warship_bonus = self._dol_amroth_warship_on_course_bonus_for(card)
        wp_bonus += (
            dain_wp + broken_sword_wp + outlands_wp + eaad_denethor_wp
            + shipwright_wp + theoden_wp + sailor_wp + star_brooch_wp
            + ingold_wp + rosabel_wp + voyage_ship_wp + ghan_buri_ghan_wp
            + cursed_mists_wp + dol_amroth_warship_bonus
        )
        silver_wing_atk = self._silver_wing_hero_attack_bonus_for(card)
        oin_atk = self._oin_hero_attack_bonus_for(card)
        lush_jungle_atk = self._lush_jungle_attack_modifier_for(card)
        temple_halls_penalty = self._temple_halls_active_stat_penalty()
        atk_bonus += (
            dain_atk
            + outlands_atk
            + silver_wing_atk
            + oin_atk
            + dol_amroth_warship_bonus
        )
        hp_bonus += (
            self._hardy_leadership_health_bonus_for(card)
            + self._bill_the_pony_hobbit_health_bonus_for(card)
            + outlands_hp
            + dol_amroth_warship_bonus
        )
        eagles_atk, eagles_def = self._misty_mountains_eagles_facedown_stat_bonus(
            card, attachments_map
        )
        atk_bonus += eagles_atk
        twin_def = self._elrohir_twin_defense_bonus(card)
        twin_atk = self._elladan_twin_attack_bonus(card)
        ebm_atk = self._erebor_battle_master_attack_bonus_for(card)
        merry_atk = self._merry_tactics_attack_bonus_for(card)
        mithlond_atk = self._mithlond_sea_watcher_attack_bonus_for(card)
        hon_boromir_atk = self._hon_boromir_gondor_ally_attack_bonus_for(card)
        faramir_atk = self._faramir_hero_staging_attack_bonus_for(card)
        fornost_atk = self._fornost_bowman_attack_bonus_for(card)
        booming_ent_atk = self._booming_ent_attack_bonus_for(card)
        att_def = self._compute_attachment_defense_bonus(card, attachments_map)
        annuminas_def = self._annuminas_guardian_defense_bonus_for(card)

        host_widget = PlayerCardWidget(
            card_name=card.name,
            series=getattr(card, "series", "") or self._encounter_series(),
            max_height=self.FIELD_CARD_HEIGHT,
            show_resource_pool=show_resource_pool,
            show_willpower_badge=False,
            show_field_stat_overlay=True,
        )
        host_widget.bind_game_card(card)
        if owner_color:
            host_widget.set_owner_border(owner_color)
        if show_resource_pool:
            game_count = int(hero_resources.get(card.id, 0))
            host_widget.set_resource_count(game_count)
            hero_resources[card.id] = game_count
            host_widget.stats_changed.connect(self._sync_hero_resources_from_widgets)
        host_widget.clicked.connect(
            lambda cid=card.id, w=host_widget: self._on_field_character_quest_click(cid, w)
        )
        if show_resource_pool and self._is_effectively_hero(card):
            host_widget.play_requested.connect(
                lambda cid=card.id: self._on_field_hero_action_requested(cid)
            )
        if show_willpower_badge:
            host_widget.stats_changed.connect(self._update_quest_dial_badges)
        host_widget.stats_changed.connect(
            lambda cid=card.id: self._maybe_destroy_character(cid)
        )
        host_widget.stats_changed.connect(self._refresh_booming_ent_attack_passives)
        host_widget.stats_changed.connect(self._refresh_merry_tactics_hero_attack_passives)
        host_widget.exhaust_changed.connect(
            lambda exhausted, cid=card.id: self._on_field_host_exhaust_changed(
                cid, exhausted
            )
        )
        if self._has_gimli_damage_attack_passive(card):
            host_widget.set_passive_attack_per_damage(1)
        host_widget.set_passive_willpower_bonus(wp_bonus)
        host_widget.set_passive_health_bonus(hp_bonus)
        host_widget.set_passive_attack_bonus(
            atk_bonus + twin_atk + ebm_atk + merry_atk + mithlond_atk
            + hon_boromir_atk + faramir_atk + fornost_atk
            + booming_ent_atk + lush_jungle_atk + temple_halls_penalty
        )
        host_widget.set_passive_defense_bonus(
            eagles_def + twin_def + att_def + outlands_def + annuminas_def
            + temple_halls_penalty
        )
        group.set_host(host_widget)

        for att_card in attachments_map.get(card.id, []):
            if isinstance(att_card, PlayerCard):
                att_widget = PlayerCardWidget(
                    card_name=att_card.name,
                    series=getattr(att_card, "series", "") or self._encounter_series(),
                    max_height=self.FIELD_CARD_HEIGHT,
                    show_resource_pool=self._is_keeping_count_attachment(att_card),
                )
                att_widget.bind_game_card(att_card)
                if self._is_keeping_count_attachment(att_card):
                    att_widget.stats_changed.connect(
                        self._refresh_keeping_count_host_passives
                    )
            else:
                att_widget = EncounterCardWidget(
                    card_name=att_card.name,
                    series=self._encounter_series(),
                    show_threat_badge=False,
                    max_height=self.FIELD_CARD_HEIGHT,
                )
                att_widget.bind_game_card(att_card)
            if getattr(att_card, "id", "") in self._facedown_attachment_ids:
                att_widget.set_face_down(True)
            if owner_color:
                att_widget.set_owner_border(owner_color)
            group.add_attachment(att_widget)
            if getattr(att_card, "id", ""):
                self._attachment_widgets[att_card.id] = att_widget
            if not hasattr(att_widget, "play_requested"):
                continue
            if self._has_steward_of_gondor_resource_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_unexpected_courage_ready_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_cram_discard_ready_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_spare_hood_cloak_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_thrors_map_quest_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_nenya_quest_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_fast_hitch_ready_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_healing_herbs_heal_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_athelas_heal_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_self_preservation_heal_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_asfaloth_progress_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_song_of_mocking_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_protector_of_lorien_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_blood_of_numenor_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_gondorian_fire_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_dunadan_mark_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_dunedain_pack_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_born_aloft_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_good_meal_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_to_the_sea_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_miruvor_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_vilya_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_gandalfs_staff_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_shadowfax_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_wizard_pipe_swap_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_galadriel_mirror_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_narya_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_book_of_eldacar_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_map_of_earnil_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_atanatar_tome_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_great_yew_bow_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_scout_bow_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_palantir_planning_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_support_of_the_eagles_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_narvi_belt_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_heir_of_valandil_planning_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._has_taking_on_water_action(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._is_mountain_king_attachment(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )
            elif self._is_gate_key_objective(att_card):
                att_widget.play_requested.connect(
                    lambda aid=att_card.id: self._on_field_attachment_action_requested(aid)
                )

        return group, host_widget

    def _character_effective_type(self, card) -> str:
        """返回角色有效类型（佩剑侍从提升的盟友视为英雄）。"""
        char_id = (getattr(card, "id", "") or "").strip()
        if char_id and char_id in getattr(self, "_promoted_ally_ids", set()):
            return '英雄'
        return (getattr(card, "type", "") or "").strip()

    def _is_effectively_hero(self, card) -> bool:
        """角色是否有效为英雄（含佩剑侍从提升）。"""
        return self._character_effective_type(card) == '英雄'

    def _is_effectively_ally(self, card) -> bool:
        """角色是否有效为盟友（排除佩剑侍从提升的）。"""
        return self._character_effective_type(card) == '盟友'

    def _has_fotw_imrahil_hero_type_passive(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() not in ("盟友", "Ally"):
            return False
        text = (getattr(card, "Text_Effect", "") or "")
        if not text:
            return False
        compact = "".join(text.split())
        lower = text.casefold()
        chinese_match = (
            "弃牌堆中有英雄" in compact
            and "失去盟友牌类型" in compact
            and "获得英雄牌类型" in compact
        )
        english_match = (
            "hero in your discard pile" in lower
            and "loses the ally card type" in lower
            and "gains the hero card type" in lower
        )
        return chinese_match or english_match

    def _player_discard_has_printed_hero(self, player_index: int) -> bool:
        state = self._players[player_index]
        for card in getattr(state, "discard_cards", []) or []:
            if (getattr(card, "type", "") or "").strip() in ("英雄", "Hero"):
                return True
        return False

    def _sync_promoted_ally_ids(self) -> bool:
        promoted: set[str] = set()
        if not hasattr(self, "_players"):
            return False
        for player_idx in range(self.PLAYER_COUNT):
            state = self._players[player_idx]
            discard_has_hero = self._player_discard_has_printed_hero(player_idx)
            for ally in getattr(state, "ally_cards", []) or []:
                ally_id = (getattr(ally, "id", "") or "").strip()
                if not ally_id:
                    continue
                for att in getattr(state, "attachments", {}).get(ally_id, []):
                    if self._is_sword_bearer_attachment(att):
                        promoted.add(ally_id)
                        break
                if (
                    discard_has_hero
                    and self._has_fotw_imrahil_hero_type_passive(ally)
                ):
                    promoted.add(ally_id)
        old = set(getattr(self, "_promoted_ally_ids", set()) or set())
        if old == promoted:
            return False
        self._promoted_ally_ids = promoted
        for player_idx in range(self.PLAYER_COUNT):
            state = self._players[player_idx]
            for ally in getattr(state, "ally_cards", []) or []:
                if getattr(ally, "id", "") in promoted:
                    state.hero_resources.setdefault(ally.id, 0)
        return True

    def _is_hero_on_field(self, character_id: str) -> bool:
        for idx in range(self.PLAYER_COUNT):
            drawer = self._player_drawer_for(idx)
            if drawer and any(h.id == character_id for h in drawer.deck_heroes):
                return True
        # 佩剑侍从：提升的盟友也视为英雄
        if character_id in getattr(self, "_promoted_ally_ids", set()):
            return True
        return False

    def _refresh_host_group(self, _: str):
        """刷新指定宿主角色及其附属显示："""
        self._refresh_field_row()

    def _refresh_discard_top_dependent_passives(self):
        """弃牌堆顶变化后，重算依赖弃牌堆顶类型的场上被动。"""
        if not hasattr(self, "_field_widgets"):
            return
        if self._sync_promoted_ally_ids():
            self._refresh_field_row()
            return
        for char_id, widget in list(self._field_widgets.items()):
            card = self._field_character_card(char_id)
            if card is None:
                continue
            owner_idx = self._char_owner.get(char_id, self._active_player_index)
            attachments_map = self._players[owner_idx].attachments
            self._apply_host_attachment_passives(card, widget, attachments_map)
        self._update_quest_dial_badges()

    def _refresh_field_row(self):
        """在第 3 行显示英雄与盟友（同行，附属在宿主旁）。"""
        if not hasattr(self, "player_drawer"):
            return
        self._sync_promoted_ally_ids()
        self._ensure_expedition_aragorn_first_player_control()
        self._sync_outlands_aura_counts()
        self._clear_card_bar(self.FIELD_ROW_INDEX)
        self.hero_widgets.clear()
        self.ally_widgets.clear()
        self._field_widgets.clear()
        self._attachment_widgets.clear()
        self._field_player_blocks.clear()
        in_adventure = self._adventure_phase_active
        for player_idx in range(self.PLAYER_COUNT):
            drawer = self._player_drawer_for(player_idx)
            if drawer is None:
                continue
            state = self._players[player_idx]
            owner_color = self._player_color(player_idx)
            player_field = QWidget()
            player_row = QHBoxLayout(player_field)
            player_row.setContentsMargins(0, 0, 0, 0)
            player_row.setSpacing(4)
            player_row.setAlignment(Qt.AlignBottom)
            for hero in drawer.deck_heroes:
                self._char_owner[hero.id] = player_idx
                group, host_widget = self._build_character_group(
                    hero,
                    show_resource_pool=True,
                    show_willpower_badge=in_adventure,
                    attachments_map=state.attachments,
                    hero_resources=state.hero_resources,
                    owner_color=owner_color,
                )
                player_row.addWidget(group)
                self.hero_widgets.append(host_widget)
                self._field_widgets[hero.id] = host_widget
            for ally in state.ally_cards:
                self._char_owner[ally.id] = player_idx
                show_radagast_pool = self._has_radagast_resource_pool_passive(ally)
                is_promoted = ally.id in self._promoted_ally_ids
                is_captain_sahir_ally = self._is_captain_sahir_objective_ally_card(
                    ally
                )
                is_naasiyah_ally = self._is_naasiyah_objective_ally_card(
                    ally
                )
                show_pool = (
                    show_radagast_pool
                    or is_promoted
                    or is_captain_sahir_ally
                    or is_naasiyah_ally
                )
                group, host_widget = self._build_character_group(
                    ally,
                    show_resource_pool=show_pool,
                    show_willpower_badge=in_adventure,
                    attachments_map=state.attachments,
                    hero_resources=state.hero_resources,
                    owner_color=owner_color,
                )
                if is_captain_sahir_ally:
                    self._refresh_captain_sahir_ally_bonus(ally)
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                    host_widget.stats_changed.connect(
                        lambda c=ally: self._refresh_captain_sahir_ally_bonus(c)
                    )
                if is_naasiyah_ally:
                    self._refresh_naasiyah_ally_combat_bonus(ally)
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                    host_widget.stats_changed.connect(
                        lambda c=ally: self._refresh_naasiyah_ally_combat_bonus(c)
                    )
                if self._is_hama_ally_card(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_faramir_willpower_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_errand_rider_resource_transfer_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_damrod_threat_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_daughter_of_nimrodel_heal_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_warden_of_healing_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_henamarth_peek_deck_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_gleowine_draw_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_amborn_trap_return_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_erestor_discard_draw_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_zigil_miner_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_erebor_record_keeper_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_beorn_attack_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_wandering_took_transfer_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_rider_of_mark_transfer_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_blue_mountain_trader_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_bombur_location_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_keen_eyed_took_return_discard_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_westfold_horse_trainer_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_westfold_outrider_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_bofur_weapon_search_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_riddermarks_finest_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_ravenhill_scout_progress_move_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_gildor_inglorion_deck_swap_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_calphon_deck_bottom_swap_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_imladris_stargazer_deck_peek_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_master_of_the_forge_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_beorning_beekeeper_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_longbeard_mapmaker_willpower_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_ithilien_tracker_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_master_of_lore_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_radagast_heal_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                if self._has_ghan_buri_ghan_travel_action(ally):
                    host_widget.play_requested.connect(
                        lambda cid=ally.id: self._on_field_hero_action_requested(cid)
                    )
                player_row.addWidget(group)
                self.ally_widgets.append(host_widget)
                self._field_widgets[ally.id] = host_widget
            if player_row.count() > 0:
                wrapped = self._wrap_with_player_border(player_field, player_idx)
                self._field_player_blocks[player_idx] = wrapped
                self.card_bars[self.FIELD_ROW_INDEX].addWidget(wrapped)
        self._apply_field_character_state()
        self._note_dain_ironfoot_aura_state()
        self._note_hon_boromir_aura_state()
        self._note_visionary_leadership_aura_state()
        self._refresh_twin_sibling_passives()
        self._check_eaad_denethor_zero_willpower_discard()
        self.card_bars[self.FIELD_ROW_INDEX].addStretch()
        self._sync_card_row_scroller_filters(self.FIELD_ROW_INDEX)
        self._reapply_player_attack_badges_if_active()
        self._sync_eowyn_action_limit_markers()
        self._sync_fotw_eowyn_action_limit_markers()
        self._sync_fotw_beregond_response_limit_markers()
        self._sync_fatty_bolger_action_limit_markers()
        self._sync_glorfindel_action_limit_markers()
        self._sync_beravor_action_limit_markers()
        self._sync_galadriel_action_limit_markers()
        self._sync_erestor_action_limit_markers()
        self._sync_bifur_action_limit_markers()
        self._sync_beorn_action_limit_markers()
        self._sync_wandering_took_action_limit_markers()
        self._sync_rider_of_mark_action_limit_markers()
        self._sync_boromir_action_limit_markers()
        self._sync_galdor_havens_action_limit_markers()
        self._refresh_gandalf_deck_top_panel()

    def _apply_field_character_state(self):
        """恢复报名后的横置状态（报名后已重整的角色保持重整）。"""
        for char_id in self._questing_ids:
            if char_id not in self._questing_readied:
                self._set_host_exhausted(char_id, True)
        for char_id in self._declared_defender_ids:
            if char_id not in self._defender_readied:
                self._set_host_exhausted(char_id, True)

    def _clear_defender_commit_state(self) -> None:
        self._declared_defender_ids.clear()
        self._defender_readied.clear()

    def _set_host_exhausted(
        self,
        char_id: str,
        exhausted: bool,
        *,
        card_effect: bool = True,
    ) -> bool:
        """横置/重整宿主角角色（英雄/盟友），不联动其附属。"""
        widget = self._field_widgets.get(char_id)
        if widget is None:
            return False
        if (
            not exhausted
            and char_id in getattr(self, "_giant_spider_no_ready_ids", set())
        ):
            print(
                f"巨大蜘蛛：本回合禁止重置角色「{self._character_display_name(char_id)}」。"
            )
            return False
        if (
            not exhausted
            and self._is_stone_of_erech_location(getattr(self, "current_location_card", None))
            and self._stone_of_erech_low_willpower(char_id)
        ):
            print(f"伊瑞赫之石：意志低于2的角色「{self._character_display_name(char_id)}」不能重置。")
            return False
        if (
            not exhausted
            and card_effect
            and char_id in getattr(self, "_heavy_snow_player_effect_no_ready_ids", set())
        ):
            print(
                f"大暴雪：本回合角色「{self._character_display_name(char_id)}」"
                "不能因玩家卡牌效果重置。"
            )
            return False
        if (
            not exhausted
            and card_effect
            and self._twisting_hollow_blocks_card_effect_readying(char_id)
        ):
            loc = self._twisting_hollow_active_location()
            loc_name = getattr(loc, "name", "曲折的海穴")
            char_name = self._character_display_name(char_id)
            print(
                f"{loc_name}：阻止卡牌效果重置角色「{char_name}」。"
            )
            return False
        if (
            not exhausted
            and char_id in getattr(self, "_mirkwood_patrol_shadow_no_ready_ids", set())
        ):
            print(
                f"幽暗密林巡逻队暗影：本回合禁止重置角色「{self._character_display_name(char_id)}」。"
            )
            return False
        if (
            not exhausted
            and card_effect
            and char_id in getattr(
                self, "_mirkwood_spider_phase_ready_blocked_ids", set()
            )
        ):
            print(
                f"幽暗密林的蜘蛛 2B：本回合禁止重置角色「{self._character_display_name(char_id)}」。"
            )
            return False
        if exhausted == widget.is_exhausted():
            return True
        widget.set_exhausted(exhausted)
        return True

    def _set_play_card_exhausted(self, card_id: str, exhausted: bool):
        """横置/重整场上的玩家卡、船-目标或玩家控制的场景区目标。"""
        widget = self._field_widgets.get(card_id)
        if widget is not None:
            if exhausted != widget.is_exhausted():
                widget.set_exhausted(exhausted)
            return
        card = next(
            (
                staging_card
                for staging_card in getattr(self, "staging_cards", [])
                if (getattr(staging_card, "id", "") or "") == card_id
            ),
            None,
        )
        if card is None:
            card = self._enemy_card_by_id(card_id)
        if card is None or not (
            self._is_ship_objective_card(card)
            or self._is_player_controlled_staging_objective(card)
        ):
            return
        widget = self._encounter_widget_for_card(card)
        if widget is None or not hasattr(widget, "set_exhausted"):
            return
        if exhausted == widget.is_exhausted():
            return
        widget.set_exhausted(exhausted)

    def _on_field_host_exhaust_changed(self, char_id: str, exhausted: bool):
        """场上角色横置/重整（含 Debug 菜单）后同步报名单与相关响应。"""
        if exhausted:
            for note in self._resolve_tiny_exhaust_forced(char_id):
                print(note)
            self._try_arwen_undomiel_exhaust_response(char_id)
            self._questing_readied.discard(char_id)
            self._defender_readied.discard(char_id)
            self._resolve_taking_on_water_forced_on_exhaust(char_id)
        else:
            self._on_character_readied(char_id)
            if char_id in self._questing_ids:
                self._questing_readied.add(char_id)
            if char_id in self._declared_defender_ids:
                self._defender_readied.add(char_id)
            for note in self._resolve_weighed_down_ready_forced(char_id):
                print(note)
        card = self._field_character_card(char_id)
        if card is None or not self._is_dain_ironfoot_aura_hero(card):
            return
        self._refresh_dain_ironfoot_aura_passives()

    def _resolve_taking_on_water_forced_on_exhaust(self, host_id: str) -> None:
        host_card = self._character_card_by_id(host_id)
        if host_card is None or not self._is_ship_objective_card(host_card):
            return
        if host_id in getattr(self, "_destroyed_enemies", set()):
            return
        for att in list(self._taking_on_water_attachments_on_host(host_id)):
            if host_id in getattr(self, "_destroyed_enemies", set()):
                break
            destroyed = self._deal_damage_to_enemy(host_card, 2)
            note = (
                f"强制 · 「{att.name}」：所附属的船目标「{host_card.name}」"
                "横置时，受到 2 点伤害"
            )
            if destroyed:
                note += "并被摧毁"
            print(note)

    def _set_attachment_exhausted(self, att_id: str, exhausted: bool):
        """横置/重整附属区卡，不联动对价。"""
        widget = self._attachment_widgets.get(att_id)
        if widget is not None:
            widget.set_exhausted(exhausted)

    def _card_trait_text(self, card) -> str:
        row = load_player_row_by_name(
            card.name, series=getattr(card, "series", None)
        )
        return (row.get("属性") or "") if row else ""

    def _player_race_and_traits_text(self, card) -> str:
        name = (getattr(card, "name", "") or "").strip()
        row = load_player_row_by_name(
            name,
            series=getattr(card, "series", None),
        )
        if not row and name:
            index = build_player_name_index(_read_player_csv_rows())
            row = lookup_card_row_by_name_any_series(index, name)
        if not row:
            text = (getattr(card, "Text_Effect", "") or "")
            return self._card_trait_text(card) + text
        return (row.get("属性") or "") + (row.get('种族') or "")

    def _is_kahliel_hero_card(self, card) -> bool:
        """精确识别《猛犸巨象》的卡里艾尔（Kahliel，17001）。"""
        if card is None or (getattr(card, "type", "") or "").strip() != "英雄":
            return False
        card_code = str(
            getattr(card, "code", "")
            or getattr(card, "card_code", "")
            or getattr(card, "Code", "")
            or ""
        ).strip()
        if (
            card_code == self.KAHLIEL_CODE
            or self._card_octgn_base_id(card).casefold()
            == self.KAHLIEL_OCTGN_BASE
        ):
            return True
        name = (getattr(card, "name", "") or "").strip()
        return name in self.KAHLIEL_HERO_NAMES or (
            CARD_NAME_ALIASES.get(name, "") in self.KAHLIEL_HERO_NAMES
        )

    def _is_harad_character_card(self, card) -> bool:
        """哈拉德角色：英雄或盟友，按本地 CSV 属性/种族字段识别。"""
        if card is None or (getattr(card, "type", "") or "").strip() not in {
            "英雄", "盟友"
        }:
            return False
        traits = self._player_race_and_traits_text(card)
        return "哈拉德" in traits or "Harad" in traits

    def _is_harad_ally_card(self, card) -> bool:
        return (
            card is not None
            and (getattr(card, "type", "") or "").strip() == "盟友"
            and self._is_harad_character_card(card)
        )

    def _kahliel_hero_for_player(self, player_index: int):
        for hero in self._heroes_controlled_by_player(player_index):
            if self._is_kahliel_hero_card(hero):
                return hero
        return None

    def _is_andrath_guardsman_ally_card(self, card) -> bool:
        """精确识别《猛犸巨象》的安德拉斯守护者（17002）。"""
        if card is None or (getattr(card, "type", "") or "").strip() != "盟友":
            return False
        card_code = str(
            getattr(card, "code", "")
            or getattr(card, "card_code", "")
            or getattr(card, "Code", "")
            or ""
        ).strip()
        if (
            card_code == self.ANDRATH_GUARDSMAN_CODE
            or self._card_octgn_base_id(card).casefold()
            == self.ANDRATH_GUARDSMAN_OCTGN_BASE
        ):
            return True
        name = (getattr(card, "name", "") or "").strip()
        return name in self.ANDRATH_GUARDSMAN_ALLY_NAMES or (
            CARD_NAME_ALIASES.get(name, "") in self.ANDRATH_GUARDSMAN_ALLY_NAMES
        )

    def _is_yazan_ally_card(self, card) -> bool:
        """精确识别《猛犸巨象》的亚赞（Yazan，17004）。"""
        if card is None or (getattr(card, "type", "") or "").strip() != "盟友":
            return False
        card_code = str(
            getattr(card, "code", "")
            or getattr(card, "card_code", "")
            or getattr(card, "Code", "")
            or ""
        ).strip()
        if (
            card_code == self.YAZAN_CODE
            or self._card_octgn_base_id(card).casefold() == self.YAZAN_OCTGN_BASE
        ):
            return True
        name = (getattr(card, "name", "") or "").strip()
        return name in self.YAZAN_ALLY_NAMES or (
            CARD_NAME_ALIASES.get(name, "") in self.YAZAN_ALLY_NAMES
        )

    def _is_treefolk_character_card(self, card) -> bool:
        """检查卡牌是否为树人类型的角色（英雄或盟友）。"""
        card_type = (getattr(card, "type", "") or "").strip()
        if card_type not in ('英雄', '盟友'):
            return False
        return "树人" in self._player_race_and_traits_text(card)

    def _is_treefolk_player_card(self, card) -> bool:
        """树人会议可搜寻的树人卡牌。"""
        return "树人" in self._player_race_and_traits_text(card)

    def _is_boomed_and_trumpeted_event(self, card) -> bool:
        """检查卡牌是否为「怒吼狂呼」事件。"""
        if (getattr(card, "type", "") or "").strip() != '事件':
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.BOOMED_AND_TRUMPETED_EVENT_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.BOOMED_AND_TRUMPETED_EVENT_NAMES:
            return True
        text = (getattr(card, "Text_Effect", "") or "")
        return (
            '响应' in text
            and "树人" in text
            and "伤害" in text
            and '重置' in text
            and '攻击' in text
        )

    def _is_dwarf_hero_card(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != '英雄':
            return False
        return "矮人" in self._player_race_and_traits_text(card)

    def _card_octgn_base_id(self, card) -> str:
        """卡牌 OCTGN / 图片链接 UUID（不含 .jpg 中 copy 后缀）。"""
        cid = (getattr(card, "id", "") or "").strip()
        if not cid:
            return ""
        base = cid.split("::", 1)[0].split("#")[0]
        if base.lower().endswith(".jpg"):
            base = base[:-4]
        if base.lower().endswith(".b"):
            base = base[:-2]
        return base

    def _is_the_road_darkens_gandalf_hero(self, card) -> bool:
        """精确识别《前路黑暗》编号 2 的英雄甘道夫。"""
        if (getattr(card, "type", "") or "").strip() != "英雄":
            return False
        card_id = (getattr(card, "id", "") or "").strip()
        normalized_id = card_id.split("#", 1)[0]
        if (
            normalized_id == self.THE_ROAD_DARKENS_GANDALF_CARD_ID
            or normalized_id.endswith("_" + self.THE_ROAD_DARKENS_GANDALF_CARD_ID)
        ):
            return True
        base_id = self._card_octgn_base_id(card)
        if (
            base_id == self.THE_ROAD_DARKENS_GANDALF_OCTGN_BASE
            or base_id.endswith("_" + self.THE_ROAD_DARKENS_GANDALF_OCTGN_BASE)
        ):
            return True
        name = (getattr(card, "name", "") or "").strip()
        series = (getattr(card, "series", "") or "").strip()
        return name in {"甘道夫", "Gandalf"} and series in self.THE_ROAD_DARKENS_GANDALF_SERIES

    def _is_the_road_darkens_galadriel_ally(self, card) -> bool:
        """精确识别《前路黑暗》编号 3 的领导盟友凯兰崔尔。"""
        if (getattr(card, "type", "") or "").strip() != "盟友":
            return False
        card_id = (getattr(card, "id", "") or "").strip()
        normalized_id = card_id.split("#", 1)[0]
        if (
            normalized_id == self.THE_ROAD_DARKENS_GALADRIEL_ALLY_CARD_ID
            or normalized_id.endswith(
                "_" + self.THE_ROAD_DARKENS_GALADRIEL_ALLY_CARD_ID
            )
        ):
            return True
        base_id = self._card_octgn_base_id(card)
        if (
            base_id == self.THE_ROAD_DARKENS_GALADRIEL_ALLY_OCTGN_BASE
            or base_id.endswith(
                "_" + self.THE_ROAD_DARKENS_GALADRIEL_ALLY_OCTGN_BASE
            )
        ):
            return True
        name = (getattr(card, "name", "") or "").strip()
        series = (getattr(card, "series", "") or "").strip()
        return (
            name in {"凯兰崔尔", "加拉德瑞尔", "Galadriel"}
            and series in self.THE_ROAD_DARKENS_GALADRIEL_ALLY_SERIES
        )

    def _is_the_road_darkens_boromir_ally(self, card) -> bool:
        """精确识别《前路黑暗》编号 4 的战术盟友波罗莫。"""
        if (getattr(card, "type", "") or "").strip() != "盟友":
            return False
        card_id = (getattr(card, "id", "") or "").strip()
        normalized_id = card_id.split("#", 1)[0]
        if (
            normalized_id == self.THE_ROAD_DARKENS_BOROMIR_ALLY_CARD_ID
            or normalized_id.endswith(
                "_" + self.THE_ROAD_DARKENS_BOROMIR_ALLY_CARD_ID
            )
        ):
            return True
        base_id = self._card_octgn_base_id(card)
        if (
            base_id == self.THE_ROAD_DARKENS_BOROMIR_ALLY_OCTGN_BASE
            or base_id.endswith(
                "_" + self.THE_ROAD_DARKENS_BOROMIR_ALLY_OCTGN_BASE
            )
        ):
            return True
        name = (getattr(card, "name", "") or "").strip()
        series = (getattr(card, "series", "") or "").strip()
        return (
            name in {"波罗莫", "波洛米尔", "Boromir"}
            and series in self.THE_ROAD_DARKENS_BOROMIR_ALLY_SERIES
        )

    def _is_the_road_darkens_elrond_ally(self, card) -> bool:
        """精确识别《前路黑暗》编号 5 的学识盟友爱隆。"""
        if (getattr(card, "type", "") or "").strip() != "盟友":
            return False
        card_id = (getattr(card, "id", "") or "").strip()
        normalized_id = card_id.split("#", 1)[0]
        if (
            normalized_id == self.THE_ROAD_DARKENS_ELROND_ALLY_CARD_ID
            or normalized_id.endswith(
                "_" + self.THE_ROAD_DARKENS_ELROND_ALLY_CARD_ID
            )
        ):
            return True
        base_id = self._card_octgn_base_id(card)
        if (
            base_id == self.THE_ROAD_DARKENS_ELROND_ALLY_OCTGN_BASE
            or base_id.endswith(
                "_" + self.THE_ROAD_DARKENS_ELROND_ALLY_OCTGN_BASE
            )
        ):
            return True
        name = (getattr(card, "name", "") or "").strip()
        series = (getattr(card, "series", "") or "").strip()
        return (
            name in {"爱隆", "埃尔隆德", "Elrond"}
            and series in self.THE_ROAD_DARKENS_ELROND_ALLY_SERIES
        )

    def _is_the_road_darkens_bilbo_ally(self, card) -> bool:
        """精确识别《前路黑暗》编号 6 的精神盟友比尔博·巴金斯。"""
        if (getattr(card, "type", "") or "").strip() not in ("盟友", "Ally"):
            return False
        card_id = (getattr(card, "id", "") or "").strip()
        normalized_id = card_id.split("#", 1)[0]
        if (
            normalized_id == self.THE_ROAD_DARKENS_BILBO_ALLY_CARD_ID
            or normalized_id.endswith(
                "_" + self.THE_ROAD_DARKENS_BILBO_ALLY_CARD_ID
            )
        ):
            return True
        base_id = self._card_octgn_base_id(card)
        if (
            base_id == self.THE_ROAD_DARKENS_BILBO_ALLY_OCTGN_BASE
            or base_id.endswith(
                "_" + self.THE_ROAD_DARKENS_BILBO_ALLY_OCTGN_BASE
            )
        ):
            return True
        name = (getattr(card, "name", "") or "").strip()
        series = (getattr(card, "series", "") or "").strip()
        return (
            name in {"比尔博·巴金斯", "Bilbo Baggins"}
            and series in self.THE_ROAD_DARKENS_BILBO_ALLY_SERIES
        )

    def _is_the_road_darkens_fire_of_arnor_event(self, card) -> bool:
        """精确识别《前路黑暗》编号 7 的中立事件阿尔诺炽焰。"""
        if (getattr(card, "type", "") or "").strip() not in ("事件", "Event"):
            return False
        card_id = (getattr(card, "id", "") or "").strip()
        normalized_id = card_id.split("#", 1)[0]
        if (
            normalized_id == self.THE_ROAD_DARKENS_FIRE_OF_ARNOR_CARD_ID
            or normalized_id.endswith(
                "_" + self.THE_ROAD_DARKENS_FIRE_OF_ARNOR_CARD_ID
            )
        ):
            return True
        base_id = self._card_octgn_base_id(card)
        if (
            base_id == self.THE_ROAD_DARKENS_FIRE_OF_ARNOR_OCTGN_BASE
            or base_id.endswith(
                "_" + self.THE_ROAD_DARKENS_FIRE_OF_ARNOR_OCTGN_BASE
            )
        ):
            return True
        name = (getattr(card, "name", "") or "").strip()
        canonical = CARD_NAME_ALIASES.get(name, "")
        series = (getattr(card, "series", "") or "").strip()
        return (
            (name in self.FIRE_OF_ARNOR_EVENT_NAMES
             or canonical in self.FIRE_OF_ARNOR_EVENT_NAMES)
            and series in self.THE_ROAD_DARKENS_FIRE_OF_ARNOR_SERIES
        )

    def _is_the_road_darkens_gandalfs_staff_attachment(self, card) -> bool:
        """精确识别《前路黑暗》编号 8 的中立附属甘道夫的手杖。"""
        if (getattr(card, "type", "") or "").strip() not in (
            "附属", "Attachment"
        ):
            return False
        card_id = (getattr(card, "id", "") or "").strip()
        normalized_id = card_id.split("#", 1)[0]
        if (
            normalized_id == self.THE_ROAD_DARKENS_GANDALFS_STAFF_CARD_ID
            or normalized_id.endswith(
                "_" + self.THE_ROAD_DARKENS_GANDALFS_STAFF_CARD_ID
            )
        ):
            return True
        base_id = self._card_octgn_base_id(card)
        if (
            base_id == self.THE_ROAD_DARKENS_GANDALFS_STAFF_OCTGN_BASE
            or base_id.endswith(
                "_" + self.THE_ROAD_DARKENS_GANDALFS_STAFF_OCTGN_BASE
            )
        ):
            return True
        name = (getattr(card, "name", "") or "").strip()
        canonical = CARD_NAME_ALIASES.get(name, "")
        series = (getattr(card, "series", "") or "").strip()
        return (
            (name in self.GANDALFS_STAFF_ATTACHMENT_NAMES
             or canonical in self.GANDALFS_STAFF_ATTACHMENT_NAMES)
            and series in self.THE_ROAD_DARKENS_GANDALFS_STAFF_SERIES
        )

    def _is_shadowfax_attachment(self, card) -> bool:
        """精确识别《萨鲁曼的背叛》14 号中立唯一附属影疾。"""
        if (getattr(card, "type", "") or "").strip() not in ("附属", "Attachment"):
            return False
        card_id = (getattr(card, "id", "") or "").strip()
        normalized_id = card_id.split("#", 1)[0]
        if normalized_id in (self.SHADOWFAX_ATTACHMENT_CARD_ID, "影疾") or normalized_id.endswith(
            "_" + self.SHADOWFAX_ATTACHMENT_CARD_ID
        ):
            return True
        base_id = self._card_octgn_base_id(card)
        if base_id == self.SHADOWFAX_ATTACHMENT_OCTGN_BASE or base_id.endswith(
            "_" + self.SHADOWFAX_ATTACHMENT_OCTGN_BASE
        ):
            return True
        name = (getattr(card, "name", "") or "").strip()
        canonical = CARD_NAME_ALIASES.get(name, "")
        return name in self.SHADOWFAX_ATTACHMENT_NAMES or canonical in self.SHADOWFAX_ATTACHMENT_NAMES

    def _is_the_road_darkens_wizard_pipe_attachment(self, card) -> bool:
        """精确识别《前路黑暗》编号 9 的中立附属巫师的烟斗。"""
        if (getattr(card, "type", "") or "").strip() not in (
            "附属", "Attachment"
        ):
            return False
        card_id = (getattr(card, "id", "") or "").strip()
        normalized_id = card_id.split("#", 1)[0]
        if (
            normalized_id == self.THE_ROAD_DARKENS_WIZARD_PIPE_CARD_ID
            or normalized_id.endswith(
                "_" + self.THE_ROAD_DARKENS_WIZARD_PIPE_CARD_ID
            )
        ):
            return True
        base_id = self._card_octgn_base_id(card)
        if (
            base_id == self.THE_ROAD_DARKENS_WIZARD_PIPE_OCTGN_BASE
            or base_id.endswith(
                "_" + self.THE_ROAD_DARKENS_WIZARD_PIPE_OCTGN_BASE
            )
        ):
            return True
        name = (getattr(card, "name", "") or "").strip()
        canonical = CARD_NAME_ALIASES.get(name, "")
        series = (getattr(card, "series", "") or "").strip()
        return (
            (name in self.WIZARD_PIPE_ATTACHMENT_NAMES
             or canonical in self.WIZARD_PIPE_ATTACHMENT_NAMES)
            and series in self.THE_ROAD_DARKENS_WIZARD_PIPE_SERIES
        )

    def _is_the_road_darkens_fellowship_of_ring_attachment(self, card) -> bool:
        """精确识别《前路黑暗》编号 10 的远征附属魔戒远征队。"""
        if (getattr(card, "type", "") or "").strip() not in (
            "附属", "Attachment"
        ):
            return False
        card_id = (getattr(card, "id", "") or "").strip()
        normalized_id = card_id.split("#", 1)[0]
        if (
            normalized_id == self.THE_ROAD_DARKENS_FELLOWSHIP_OF_RING_CARD_ID
            or normalized_id.endswith(
                "_" + self.THE_ROAD_DARKENS_FELLOWSHIP_OF_RING_CARD_ID
            )
        ):
            return True
        base_id = self._card_octgn_base_id(card)
        if (
            base_id == self.THE_ROAD_DARKENS_FELLOWSHIP_OF_RING_OCTGN_BASE
            or base_id.endswith(
                "_" + self.THE_ROAD_DARKENS_FELLOWSHIP_OF_RING_OCTGN_BASE
            )
        ):
            return True
        name = (getattr(card, "name", "") or "").strip()
        canonical = CARD_NAME_ALIASES.get(name, "")
        series = (getattr(card, "series", "") or "").strip()
        return (
            (name in self.FELLOWSHIP_OF_RING_ATTACHMENT_NAMES
             or canonical in self.FELLOWSHIP_OF_RING_ATTACHMENT_NAMES)
            and series in self.THE_ROAD_DARKENS_FELLOWSHIP_OF_RING_SERIES
        )

    def _gandalf_deck_top_hero_for_active_player(self):
        drawer = self._player_drawer_for(self._active_player_index)
        if drawer is None:
            return None
        for hero in drawer.deck_heroes:
            if (
                self._is_the_road_darkens_gandalf_hero(hero)
                and self._is_character_in_play(hero.id)
            ):
                return hero
        return None

    def _gandalf_revealed_deck_top_card(self):
        if self._gandalf_deck_top_hero_for_active_player() is None:
            return None
        drawer = self._player_drawer_for(self._active_player_index)
        if drawer is None:
            return None
        # 不调用 peek_deck_top：牌库耗尽时它会尝试重建牌库，而展示本身不应改变状态。
        stack = getattr(drawer, "deck_stack", []) or []
        return stack[0] if stack else None

    def _refresh_gandalf_deck_top_panel(self):
        """刷新场上行左侧的甘道夫公开牌库顶面板。"""
        while self._gandalf_deck_top_card_layout.count():
            item = self._gandalf_deck_top_card_layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.deleteLater()

        top_card = self._gandalf_revealed_deck_top_card()
        if top_card is None:
            self._gandalf_deck_top_panel.setVisible(False)
            return

        card_widget = PlayerCardWidget(
            card_name=top_card.name,
            series=getattr(top_card, "series", "") or DEFAULT_DECK_SERIES,
            max_height=98,
            restore_markers=False,
        )
        card_widget.bind_game_card(top_card)
        card_widget.setToolTip("牌库顶牌（单击放大查看）")
        card_widget.clicked.connect(self._show_gandalf_deck_top_dialog)
        self._gandalf_deck_top_card_layout.addWidget(
            card_widget, alignment=Qt.AlignCenter
        )
        self._gandalf_deck_top_panel.setVisible(True)

    def _show_gandalf_deck_top_dialog(self):
        top_card = self._gandalf_revealed_deck_top_card()
        if top_card is None:
            self._refresh_gandalf_deck_top_panel()
            return
        image_path = (getattr(top_card, "image_path", "") or "").strip()
        if image_path and Path(image_path).is_file():
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                dialog = CardImageZoomDialog(pixmap, self)
                dialog.setWindowTitle(f"甘道夫·牌库顶 · {top_card.name} - 单击关闭")
                dialog.exec_()
                return
        self._inform("甘道夫·牌库顶", f"牌库顶牌：{top_card.name}")

    def _encounter_row_for_card(self, card) -> dict | None:
        """按图片/OCTGN id 优先回查本地遭遇 CSV，避免跨遭遇组同名误配。"""
        if card is None or not ENCOUNTER_CSV.is_file():
            return None
        base_id = self._card_octgn_base_id(card)
        name = (getattr(card, "name", "") or "").strip()
        current_series = (self._encounter_series() or "").strip()
        name_matches: list[dict] = []
        try:
            with open(ENCOUNTER_CSV, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    row_base = _image_id_stem(row.get("图片链接") or "")
                    if base_id and row_base == base_id:
                        return row
                    row_name = (row.get("卡牌名称") or row.get("英文名称") or "").strip()
                    row_en = (row.get("英文名称") or "").strip()
                    if name and name in {row_name, row_en}:
                        name_matches.append(row)
        except OSError:
            return None
        if current_series:
            for row in name_matches:
                if (row.get("系列") or "").strip() == current_series:
                    return row
        return name_matches[0] if name_matches else None

    def _encounter_row_for_base_id(self, base_id: str, *, prefer_back: bool = False) -> dict | None:
        """按同一张双面遭遇卡的 base id 回查 CSV，可选择优先背面。"""
        base_id = (base_id or "").strip()
        if not base_id or not ENCOUNTER_CSV.is_file():
            return None
        current_series = (self._encounter_series() or "").strip()
        front_row = None
        back_row = None
        try:
            with open(ENCOUNTER_CSV, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    row_base = _image_id_stem(row.get("图片链接") or "")
                    normalized = row_base[:-2] if row_base.lower().endswith(".b") else row_base
                    if normalized != base_id:
                        continue
                    if current_series and (row.get("系列") or "").strip() != current_series:
                        continue
                    if row_base.lower().endswith(".b"):
                        back_row = row
                    else:
                        front_row = row
        except OSError:
            return None
        if prefer_back:
            return back_row or front_row
        return front_row or back_row

    def _encounter_row_for_base_id_any_series(
        self, base_id: str, *, prefer_back: bool = False
    ) -> dict | None:
        """按 OCTGN 基础 id 跨遭遇组回查卡牌面。"""
        base_id = (base_id or "").strip()
        if not base_id or not ENCOUNTER_CSV.is_file():
            return None
        front_row = None
        back_row = None
        try:
            with open(ENCOUNTER_CSV, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    row_base = _image_id_stem(row.get("图片链接") or "")
                    normalized = row_base[:-2] if row_base.lower().endswith(".b") else row_base
                    if normalized != base_id:
                        continue
                    if row_base.lower().endswith(".b"):
                        back_row = row
                    else:
                        front_row = row
        except OSError:
            return None
        if prefer_back:
            return back_row or front_row
        return front_row or back_row

    def _encounter_back_row_for_any_series(self, card) -> dict | None:
        """地图卡可能来自其他遭遇组；按 OCTGN 基础 id 跨系列寻找真实反面。"""
        base_id = self._card_octgn_base_id(card)
        if not base_id or not ENCOUNTER_CSV.is_file():
            return None
        front_row = None
        back_row = None
        try:
            with open(ENCOUNTER_CSV, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    row_base = _image_id_stem(row.get("图片链接") or "")
                    normalized = row_base[:-2] if row_base.lower().endswith(".b") else row_base
                    if normalized != base_id:
                        continue
                    if row_base.lower().endswith(".b"):
                        back_row = row
                    else:
                        front_row = row
        except OSError:
            return None
        front_name = (getattr(card, "name", "") or "").strip()
        # O8D 中 Lost Island / Temple 是公开正面；返回同一基础 id 的另一面。
        for row in (back_row, front_row):
            if row and (row.get("卡牌名称") or row.get("英文名称") or "").strip() != front_name:
                return row
        return back_row or front_row

    def _lost_island_template_row(self) -> dict | None:
        if not ENCOUNTER_CSV.is_file():
            return None
        current_series = (self._encounter_series() or "").strip()
        fallback = None
        try:
            with open(ENCOUNTER_CSV, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    names = {
                        (row.get("卡牌名称") or "").strip(),
                        (row.get("英文名称") or "").strip(),
                    }
                    if not ({"失落的岛屿", "Lost Island"} & names):
                        continue
                    if fallback is None:
                        fallback = row
                    if current_series and (row.get("系列") or "").strip() == current_series:
                        return row
        except OSError:
            return None
        return fallback

    def _is_lost_island_template_card(self, card) -> bool:
        name = (getattr(card, "name", "") or "").strip()
        canonical = CARD_NAME_ALIASES.get(name, "")
        return name in {"失落的岛屿", "Lost Island"} or canonical in {
            "失落的岛屿",
            "Lost Island",
        }

    def _is_lost_island_proxy(self, card) -> bool:
        return bool(getattr(card, "_uncharted_proxy", False))

    def _uncharted_hidden_card(self, card):
        return getattr(card, "_uncharted_hidden_card", None)

    def _is_uncharted_location_back(self, card) -> bool:
        if card is None or self._is_lost_island_proxy(card):
            return False
        if (getattr(card, "type", "") or "").strip() != "地区":
            return False
        if self._is_lost_island_template_card(card):
            return False
        parts = [getattr(card, "Keywords", "") or ""]
        row = self._encounter_row_for_card(card)
        if row:
            parts.append(row.get("关键字") or "")
        return any(text_contains(part, "未知") for part in parts if part)

    def _grotto_location_face(self, card) -> str:
        """返回双面石窟地区当前面：grotto / underwater / 空。"""
        if card is None or (getattr(card, "type", "") or "").strip() != "地区":
            return ""
        marked = (getattr(card, "_grotto_face", "") or "").strip().lower()
        if marked in {"grotto", "underwater"}:
            return marked
        if (getattr(card, "series", "") or "").strip() not in {
            "沉没的废墟",
            "The Drowned Ruins",
        }:
            return ""
        keywords = (getattr(card, "Keywords", "") or "").strip()
        if text_contains(keywords, "石窟"):
            return "grotto"
        if text_contains(keywords, "水下") or text_contains(keywords, "海底"):
            return "underwater"
        return ""

    def _is_grotto_location_card(self, card) -> bool:
        return bool(self._grotto_location_face(card))

    @staticmethod
    def _grotto_row_victory_value(row: dict | None) -> int:
        raw = ((row or {}).get("胜利") or "").strip()
        match = re.search(r"\d+", raw)
        return int(match.group(0)) if match else 0

    def _prepare_grotto_location_pair(
        self,
        front_card,
        front_row: dict,
        back_row: dict,
    ):
        """为一张实体双面地区保存另一面的数据，但不向玩家公开反面。"""
        physical_id = getattr(front_card, "id", "") or _image_id_stem(
            front_row.get("图片链接") or ""
        )
        back_card = EncounterCard.from_csv_row(back_row)
        back_card.id = physical_id
        front_card.id = physical_id
        front_victory = self._grotto_row_victory_value(front_row)
        back_victory = self._grotto_row_victory_value(back_row)
        setattr(front_card, "_grotto_face", "grotto")
        setattr(front_card, "_grotto_victory", front_victory)
        setattr(
            front_card,
            "_grotto_other_face_data",
            {
                "card": asdict(back_card),
                "face": "underwater",
                "victory": back_victory,
            },
        )
        return front_card

    def _grotto_other_face_card(self, card):
        payload = getattr(card, "_grotto_other_face_data", None)
        if not isinstance(payload, dict) or not isinstance(payload.get("card"), dict):
            return None
        current_face = self._grotto_location_face(card)
        other = EncounterCard(**dict(payload["card"]))
        other.id = getattr(card, "id", "") or other.id
        other_face = (payload.get("face") or "").strip().lower()
        if other_face not in {"grotto", "underwater"}:
            other_face = "underwater" if current_face == "grotto" else "grotto"
        setattr(other, "_grotto_face", other_face)
        setattr(other, "_grotto_victory", int(payload.get("victory", 0) or 0))
        setattr(
            other,
            "_grotto_other_face_data",
            {
                "card": asdict(card),
                "face": current_face,
                "victory": int(getattr(card, "_grotto_victory", 0) or 0),
            },
        )
        return other

    def _shuffle_grotto_location_deck(self) -> None:
        """使用独立的系统随机源洗混石窟牌库。"""
        deck = getattr(self, "_grotto_location_deck", None)
        if isinstance(deck, list) and len(deck) > 1:
            random.SystemRandom().shuffle(deck)
        self._refresh_grotto_deck_panel()

    def _setup_grotto_location_deck_from_special_cards(self) -> int:
        """布置石窟牌库，并将魔苟斯祭坛以石窟面放置一旁。"""
        if not self._is_drowned_ruins_scenario() or not hasattr(self, "encounter_drawer"):
            return 0
        self._grotto_deck_enabled = True
        prepared: list = []
        source = getattr(self.encounter_drawer, "special_cards", None)
        if isinstance(source, list):
            kept = []
            for card in source:
                if self._grotto_location_face(card) != "grotto":
                    kept.append(card)
                    continue
                base_id = self._card_octgn_base_id(card)
                front_row = self._encounter_row_for_base_id(base_id)
                back_row = self._encounter_row_for_base_id(base_id, prefer_back=True)
                if (
                    not front_row
                    or not back_row
                    or (back_row.get("关键字") or "").strip() not in {"水下", "海底"}
                ):
                    kept.append(card)
                    print(f"石窟牌库：未找到「{card.name}」对应的海底面，保留在 Special。")
                    continue
                clear_encounter_marker_state_for_card(card)
                prepared.append(
                    self._prepare_grotto_location_pair(card, front_row, back_row)
                )
                self.encounter_drawer.drawn_ids.discard(getattr(card, "id", "") or "")
            self.encounter_drawer.special_cards = kept

        if prepared:
            self._grotto_location_deck.extend(prepared)
            self._shuffle_grotto_location_deck()

        shrine = None
        setup_cards = getattr(self.encounter_drawer, "setup_cards", None)
        if isinstance(setup_cards, list):
            for card in list(setup_cards):
                if not self._is_shrine_to_morgoth_location(card):
                    continue
                base_id = self._card_octgn_base_id(card)
                front_row = self._encounter_row_for_base_id(base_id)
                back_row = self._encounter_row_for_base_id(base_id, prefer_back=True)
                if front_row and back_row:
                    shrine = self._prepare_grotto_location_pair(card, front_row, back_row)
                    clear_encounter_marker_state_for_card(shrine)
                    setup_cards.remove(card)
                    self.encounter_drawer.drawn_ids.discard(getattr(card, "id", "") or "")
                    self.encounter_set_aside_cards.append(shrine)
                break
        self._refresh_set_aside_button()
        self._refresh_heading_display()
        shrine_note = "；魔苟斯祭坛已放置一旁" if shrine is not None else "；未找到魔苟斯祭坛"
        print(
            f"石窟牌库：以石窟面朝上洗混 {len(prepared)} 张双面地区"
            f"（牌库共 {len(self._grotto_location_deck)} 张）{shrine_note}。"
        )
        return len(prepared)

    def _draw_grotto_location_card(self):
        """从石窟牌库顶取一张；牌库中始终保持石窟面朝上。"""
        deck = getattr(self, "_grotto_location_deck", None)
        if not deck:
            return None
        card = deck.pop(0)
        if self._grotto_location_face(card) != "grotto":
            other = self._grotto_other_face_card(card)
            if other is not None and self._grotto_location_face(other) == "grotto":
                card = other
        clear_encounter_marker_state_for_card(card)
        self._refresh_grotto_deck_panel()
        return card

    def _add_grotto_location_from_deck_to_staging(self):
        card = self._draw_grotto_location_card()
        if card is None:
            self._warn("石窟牌库", "石窟牌库为空，无法将顶牌加入探查区。")
            return None
        if self._card_cannot_enter_staging_area(card):
            note = self._set_aside_instead_of_staging(card, source="石窟牌库")
            print(f"石窟牌库：{note}")
            return None
        self.staging_cards.append(card)
        self._move_thrors_key_from_heroes_to_location(card)
        self._refresh_staging_row(self.staging_cards)
        print(f"石窟牌库：「{card.name}」以石窟面朝上加入探查区。")
        return card

    def _grotto_1b_underwater_victory_count(self) -> int:
        return sum(
            1
            for card in (getattr(self, "_victory_display_cards", []) or [])
            if self._grotto_location_face(card) == "underwater"
        )

    def _underwater_active_grotto_location(self):
        current = getattr(self, "current_location_card", None)
        return current if self._grotto_location_face(current) == "underwater" else None

    def _is_underwater_location_card(self, card) -> bool:
        if card is None or (getattr(card, "type", "") or "").strip() != "地区":
            return False
        if self._is_drowned_cave_location(card):
            return True
        if self._grotto_location_face(card) == "underwater":
            return True
        row = self._encounter_row_for_card(card)
        parts = [
            (getattr(card, "Keywords", "") or "").strip(),
            ((row or {}).get("关键字") or "").strip(),
        ]
        return any(
            text_contains(part, "水下") or text_contains(part, "海底")
            for part in parts
            if part
        )

    def _active_underwater_location(self):
        current = getattr(self, "current_location_card", None)
        return current if self._is_underwater_location_card(current) else None

    def _active_location_flip_blocked_note(self, card=None) -> str:
        if not getattr(self, "_active_location_flip_blocked_this_round", False):
            return ""
        current = getattr(self, "current_location_card", None)
        if current is None:
            return ""
        if card is not None and getattr(card, "id", "") != getattr(current, "id", ""):
            return ""
        current_name = getattr(current, "name", "当前地区") or "当前地区"
        return f"本回合不能将当前地区「{current_name}」翻面。"

    def _underwater_active_location_play_block_title(self) -> str:
        if self._is_sahirs_betrayal_2b_quest_active():
            return "萨伊尔的背叛 2B"
        if self._is_grotto_1b_quest_active():
            return "石窟 1B"
        return "水下地区"

    def _underwater_active_location_blocks_playing_card(self, card) -> bool:
        if not (
            self._is_grotto_1b_quest_active()
            or self._is_sahirs_betrayal_2b_quest_active()
        ):
            return False
        current = getattr(self, "current_location_card", None)
        if self._grotto_location_face(current) != "underwater":
            return False
        return (getattr(card, "type", "") or "").strip() in {"盟友", "附属"}

    def _grotto_1b_blocks_playing_card(self, card) -> bool:
        return self._underwater_active_location_blocks_playing_card(card)

    def _is_cursed_caverns_location(self, card) -> bool:
        """被诅咒的洞穴（石窟面）。"""
        if card is None or (getattr(card, "type", "") or "").strip() != "地区":
            return False
        name = (getattr(card, "name", "") or "").strip()
        canonical = CARD_NAME_ALIASES.get(name, "")
        if name in self.CURSED_CAVERNS_NAMES or canonical in self.CURSED_CAVERNS_NAMES:
            return True
        base_id = self._card_octgn_base_id(card)
        return (
            base_id in self.CURSED_CAVERNS_OCTGN_BASES
            and self._grotto_location_face(card) == "grotto"
        )

    def _is_drowned_cave_location(self, card) -> bool:
        """淹没的洞穴（水下地区）。"""
        if card is None or (getattr(card, "type", "") or "").strip() != "地区":
            return False
        base_id = self._card_octgn_base_id(card)
        if base_id == self.DROWNED_CAVE_OCTGN_BASE:
            return True
        name = (getattr(card, "name", "") or "").strip()
        canonical = CARD_NAME_ALIASES.get(name, "")
        return name in self.DROWNED_CAVE_NAMES or canonical in self.DROWNED_CAVE_NAMES

    def _drowned_cave_active_location(self):
        current = getattr(self, "current_location_card", None)
        return current if self._is_drowned_cave_location(current) else None

    def _resolve_cursed_caverns_after_travel(self, card) -> list[str]:
        if not self._is_cursed_caverns_location(card):
            return []
        blocked_note = self._active_location_flip_blocked_note(card)
        if blocked_note:
            return [f"响应 · 被诅咒的洞穴：{blocked_note}"]
        if (
            self._question(
                "响应 · 被诅咒的洞穴",
                "游历到「被诅咒的洞穴」后，是否将其翻至水下面？",
                default_yes=False,
            )
            != QMessageBox.Yes
        ):
            return ["响应 · 被诅咒的洞穴：未翻至水下面。"]
        return [
            "响应 · 被诅咒的洞穴：游历到此后，将其翻至水下面。"
        ] + self._flip_grotto_location(card)

    def _draw_player_deck_bottom_card(
        self, player_index: int, *, source: str
    ) -> tuple[object | None, str]:
        if not (0 <= player_index < self.PLAYER_COUNT):
            return None, f"{source}：未找到玩家，未补牌。"
        blocked = self._player_draw_blocked_reason()
        if blocked:
            return None, f"{source}：玩家 {player_index + 1} 未补牌（{blocked}）。"
        drawer = self._player_drawer_for(player_index)
        player_no = player_index + 1
        if drawer is None:
            return None, f"{source}：玩家 {player_no} 牌组未初始化，未补牌。"
        if hasattr(drawer, "_ensure_deck_stack"):
            drawer._ensure_deck_stack()
        deck_stack = getattr(drawer, "deck_stack", [])
        if not deck_stack:
            return None, f"{source}：玩家 {player_no} 牌组为空，未补牌。"
        bottom_card = deck_stack.pop()
        if hasattr(drawer, "drawn_ids"):
            bottom_id = getattr(bottom_card, "id", "") or ""
            if bottom_id:
                drawer.drawn_ids.add(bottom_id)
        self._players[player_index].hand_cards.append(bottom_card)
        if player_index == self._active_player_index:
            self._refresh_hand_row(self._players[player_index].hand_cards)
        return (
            bottom_card,
            f"{source}：玩家 {player_no} 从牌组底端补 1 张牌「{bottom_card.name}」。",
        )

    def _resolve_cursed_caverns_active_explored_response(self, card) -> list[str]:
        if not self._is_cursed_caverns_location(card):
            return []
        lines = [
            "响应 · 被诅咒的洞穴：作为激活地区被探索完毕后，"
            "每位玩家可以上升 2 点威胁以从其牌组底端补 1 张牌。"
        ]
        for offset in range(self.PLAYER_COUNT):
            player_idx = (self.first_player_index + offset) % self.PLAYER_COUNT
            if player_idx in self._eliminated_players:
                continue
            player_tag = self._player_tag(player_idx) or f"玩家 {player_idx + 1}"
            blocked = self._player_draw_blocked_reason()
            drawer = self._player_drawer_for(player_idx)
            if blocked:
                lines.append(f"  {player_tag}不能补牌（{blocked}），未上升威胁。")
                continue
            if drawer is None:
                lines.append(f"  {player_tag}牌组未初始化，未上升威胁。")
                continue
            if hasattr(drawer, "_ensure_deck_stack"):
                drawer._ensure_deck_stack()
            if not getattr(drawer, "deck_stack", []):
                lines.append(f"  {player_tag}牌组为空，未上升威胁。")
                continue
            if (
                self._question(
                    "响应 · 被诅咒的洞穴",
                    f"{player_tag}：是否上升 2 点威胁，从牌组底端补 1 张牌？",
                    default_yes=False,
                )
                != QMessageBox.Yes
            ):
                lines.append(f"  {player_tag}未选择上升威胁。")
                continue
            before = self._player_threat(player_idx)
            raised = self._raise_threat(
                2,
                player_index=player_idx,
                elfhelm_source="encounter_effect",
            )
            after = self._player_threat(player_idx)
            if raised <= 0:
                lines.append(f"  {player_tag}威胁未上升，未补牌。")
                continue
            drawn, note = self._draw_player_deck_bottom_card(
                player_idx,
                source="响应 · 被诅咒的洞穴",
            )
            lines.append(f"  {player_tag}威胁 {before}→{after}。")
            lines.append("  " + note)
            if self._game_lost:
                break
        print("响应（被诅咒的洞穴探索完毕）：" + "、".join(lines[1:]))
        return lines

    def _is_twisting_hollow_location(self, card) -> bool:
        """曲折的海穴（水下面）。"""
        if card is None or (getattr(card, "type", "") or "").strip() != "地区":
            return False
        name = (getattr(card, "name", "") or "").strip()
        canonical = CARD_NAME_ALIASES.get(name, "")
        if name in self.TWISTING_HOLLOW_NAMES or canonical in self.TWISTING_HOLLOW_NAMES:
            return True
        base_id = self._card_octgn_base_id(card)
        return (
            base_id in self.TWISTING_HOLLOW_OCTGN_BASES
            and self._grotto_location_face(card) == "underwater"
        )

    def _twisting_hollow_active_location(self):
        current = getattr(self, "current_location_card", None)
        return current if self._is_twisting_hollow_location(current) else None

    def _resolve_drowned_cave_active_undead_return(self) -> list[str]:
        cave = self._drowned_cave_active_location()
        if cave is None:
            return []
        lines: list[str] = []
        cave_name = getattr(cave, "name", "淹没的洞穴") or "淹没的洞穴"
        for player_idx in range(self.PLAYER_COUNT):
            for enemy_card in list(self._player_engagement(player_idx)):
                if not self._is_undead_enemy_card(enemy_card):
                    continue
                ok, note = self._return_engaged_enemy_to_staging(
                    getattr(enemy_card, "id", "") or ""
                )
                if ok:
                    lines.append(
                        f"淹没的洞穴：当前地区「{cave_name}」使亡灵敌军不能被交锋，{note}。"
                    )
                else:
                    lines.append(
                        f"淹没的洞穴：当前地区「{cave_name}」使亡灵敌军不能被交锋，"
                        f"但将「{getattr(enemy_card, 'name', '亡灵敌军')}」返回场景区失败：{note}"
                    )
        return lines

    def _twisting_hollow_blocks_card_effect_readying(self, char_id: str) -> bool:
        if self._twisting_hollow_active_location() is None:
            return False
        if not char_id or not self._is_character_in_play(char_id):
            return False
        card = self._character_card_by_id(char_id)
        return card is not None

    def _mandatory_discard_character_for_player(
        self,
        player_index: int,
        *,
        title: str,
        prompt: str,
    ) -> list[str]:
        """强制：玩家弃除一名自己控制的存活角色。"""
        player_no = player_index + 1
        options = self._alive_character_pick_options_for_player(player_index)
        if not options:
            return [f"玩家 {player_no}：没有可弃除的角色。"]
        if len(options) == 1:
            char_id = options[0].char_id
        else:
            dlg = CharacterImagePickDialog(
                self,
                title,
                prompt,
                options,
                mode="single",
                highlight_stat="health",
                mandatory=True,
            )
            if dlg.exec_() != QDialog.Accepted:
                char_id = options[0].char_id
            else:
                char_id = dlg.selected_id() or options[0].char_id
        name = self._character_display_name(char_id)
        if not self._discard_character_from_play(char_id):
            return [f"玩家 {player_no}：未能弃除「{name}」。"]
        return [f"玩家 {player_no}：弃除角色「{name}」。"]

    def _resolve_twisting_hollow_quest_phase_end(self) -> None:
        hollow = self._twisting_hollow_active_location()
        if hollow is None:
            return
        lines = [
            "强制 · 曲折的海穴：任务阶段结束时，每位玩家必须弃除一名角色。"
        ]
        for offset in range(self.PLAYER_COUNT):
            player_idx = (self.first_player_index + offset) % self.PLAYER_COUNT
            if player_idx in self._eliminated_players:
                continue
            lines.extend(
                self._mandatory_discard_character_for_player(
                    player_idx,
                    title="强制 · 曲折的海穴",
                    prompt=(
                        f"玩家 {player_idx + 1}：选择并弃除一名你控制的角色："
                    ),
                )
            )
            if self._game_lost:
                break
        blocked_note = self._active_location_flip_blocked_note(hollow)
        if not self._game_lost and blocked_note:
            lines.append(f"  {blocked_note}")
        elif (
            not self._game_lost
            and self._question(
                "强制 · 曲折的海穴",
                "\n".join(lines) + "\n\n是否将「曲折的海穴」翻至石窟面？",
                default_yes=False,
            )
            == QMessageBox.Yes
        ):
            lines.extend(self._flip_grotto_location(hollow))
        elif not self._game_lost:
            lines.append("未将「曲折的海穴」翻至石窟面。")
        self._update_quest_dial_badges()
        detail = "\n".join(lines)
        self._inform("曲折的海穴 · 强制", detail)
        print("曲折的海穴强制：\n" + detail)

    def _is_undersea_grotto_location(self, card) -> bool:
        """水下石窟（石窟面）；同一实体的水下面不应被误判。"""
        if card is None or (getattr(card, "type", "") or "").strip() != "地区":
            return False
        name = (getattr(card, "name", "") or "").strip()
        canonical = CARD_NAME_ALIASES.get(name, "")
        if name in self.UNDERSEA_GROTTO_NAMES or canonical in self.UNDERSEA_GROTTO_NAMES:
            return True
        base_id = self._card_octgn_base_id(card)
        return (
            base_id in self.UNDERSEA_GROTTO_OCTGN_BASES
            and self._grotto_location_face(card) == "grotto"
        )

    def _undersea_grotto_active_location(self):
        current = getattr(self, "current_location_card", None)
        return current if self._is_undersea_grotto_location(current) else None

    def _undersea_grotto_ally_hand_play_discount(self, card) -> int:
        if self._undersea_grotto_active_location() is None:
            return 0
        if (getattr(card, "type", "") or "").strip() != "盟友":
            return 0
        if getattr(self, "_undersea_grotto_ally_discount_used_this_round", False):
            return 0
        return 1

    def _resolve_undersea_grotto_after_travel(self, card) -> list[str]:
        if not self._is_undersea_grotto_location(card):
            return []
        blocked_note = self._active_location_flip_blocked_note(card)
        if blocked_note:
            return [f"响应 · 水下石窟：{blocked_note}"]
        if (
            self._question(
                "响应 · 水下石窟",
                "游历到「水下石窟」后，是否将其翻至水下面？",
                default_yes=False,
            )
            != QMessageBox.Yes
        ):
            return ["响应 · 水下石窟：未翻至水下面。"]
        return [
            "响应 · 水下石窟：游历到此后，将其翻至水下面。"
        ] + self._flip_grotto_location(card)

    def _is_dark_abyss_location(self, card) -> bool:
        """黑暗深渊（水下面）；同一实体的石窟面不应被误判。"""
        if card is None or (getattr(card, "type", "") or "").strip() != "地区":
            return False
        name = (getattr(card, "name", "") or "").strip()
        canonical = CARD_NAME_ALIASES.get(name, "")
        if name in self.DARK_ABYSS_NAMES or canonical in self.DARK_ABYSS_NAMES:
            return True
        base_id = self._card_octgn_base_id(card)
        return (
            base_id in self.DARK_ABYSS_OCTGN_BASES
            and self._grotto_location_face(card) == "underwater"
        )

    def _dark_abyss_active_location(self):
        current = getattr(self, "current_location_card", None)
        return current if self._is_dark_abyss_location(current) else None

    def _dark_abyss_blocks_character_attacks(self) -> bool:
        return self._dark_abyss_active_location() is not None

    def _dark_abyss_blocks_character_attack_id(self, char_id: str) -> bool:
        if not self._dark_abyss_blocks_character_attacks():
            return False
        if not char_id or not self._is_character_in_play(char_id):
            return False
        return self._character_card_by_id(char_id) is not None

    def _resolve_dark_abyss_quest_phase_end(self) -> None:
        abyss = self._dark_abyss_active_location()
        if abyss is None:
            return
        lines = [
            "强制 · 黑暗深渊：任务阶段结束时，对每名在场的角色造成 1 点伤害。"
        ]
        damage_lines = self._deal_damage_to_each_character_in_play(
            1,
            source="强制 · 黑暗深渊",
        )
        lines.extend(f"  {line}" for line in damage_lines)
        blocked_note = self._active_location_flip_blocked_note(abyss)
        if not self._game_lost and blocked_note:
            lines.append(f"  {blocked_note}")
        elif (
            not self._game_lost
            and self._question(
                "强制 · 黑暗深渊",
                "\n".join(lines) + "\n\n是否将「黑暗深渊」翻至石窟面？",
                default_yes=False,
            )
            == QMessageBox.Yes
        ):
            lines.extend(self._flip_grotto_location(abyss))
        elif not self._game_lost:
            lines.append("未将「黑暗深渊」翻至石窟面。")
        self._update_quest_dial_badges()
        detail = "\n".join(lines)
        self._inform("黑暗深渊 · 强制", detail)
        print("黑暗深渊强制：\n" + detail)

    def _is_water_logged_halls_location(self, card) -> bool:
        """水浸的大厅（石窟面）；同一实体的水下面不应被误判。"""
        if card is None or (getattr(card, "type", "") or "").strip() != "地区":
            return False
        name = (getattr(card, "name", "") or "").strip()
        canonical = CARD_NAME_ALIASES.get(name, "")
        if (
            name in self.WATER_LOGGED_HALLS_NAMES
            or canonical in self.WATER_LOGGED_HALLS_NAMES
        ):
            return True
        base_id = self._card_octgn_base_id(card)
        return (
            base_id in self.WATER_LOGGED_HALLS_OCTGN_BASES
            and self._grotto_location_face(card) == "grotto"
        )

    def _water_logged_halls_active_location(self):
        current = getattr(self, "current_location_card", None)
        return current if self._is_water_logged_halls_location(current) else None

    def _water_logged_halls_undead_enemy_threat_modifier(self, card) -> int:
        if self._water_logged_halls_active_location() is None:
            return 0
        if not self._is_undead_enemy_card(card):
            return 0
        return -1

    def _resolve_water_logged_halls_after_travel(self, card) -> list[str]:
        if not self._is_water_logged_halls_location(card):
            return []
        blocked_note = self._active_location_flip_blocked_note(card)
        if blocked_note:
            self._sync_all_staging_threat_passives()
            return [f"响应 · 水浸的大厅：{blocked_note}"]
        if (
            self._question(
                "响应 · 水浸的大厅",
                "游历到「水浸的大厅」后，是否将其翻至水下面？",
                default_yes=False,
            )
            != QMessageBox.Yes
        ):
            self._sync_all_staging_threat_passives()
            return ["响应 · 水浸的大厅：未翻至水下面。"]
        lines = [
            "响应 · 水浸的大厅：游历到此后，将其翻至水下面。"
        ] + self._flip_grotto_location(card)
        self._sync_all_staging_threat_passives()
        return lines

    def _is_sunken_temple_location(self, card) -> bool:
        """沉没的神庙（水下面）；同一实体的石窟面不应被误判。"""
        if card is None or (getattr(card, "type", "") or "").strip() != "地区":
            return False
        name = (getattr(card, "name", "") or "").strip()
        canonical = CARD_NAME_ALIASES.get(name, "")
        if name in self.SUNKEN_TEMPLE_NAMES or canonical in self.SUNKEN_TEMPLE_NAMES:
            return True
        base_id = self._card_octgn_base_id(card)
        return (
            base_id in self.SUNKEN_TEMPLE_OCTGN_BASES
            and self._grotto_location_face(card) == "underwater"
        )

    def _sunken_temple_active_location(self):
        current = getattr(self, "current_location_card", None)
        return current if self._is_sunken_temple_location(current) else None

    def _attachment_text_blank_by_sunken_temple(self, card) -> bool:
        if self._sunken_temple_active_location() is None:
            return False
        if (getattr(card, "type", "") or "").strip() not in {"附属", "Attachment"}:
            return False
        att_id = (getattr(card, "id", "") or "").strip()
        if not att_id:
            return False
        if self._attachment_host_id(att_id):
            return True
        return any(
            (getattr(att, "id", "") or "").strip() == att_id
            for att in getattr(self, "_staging_unattached_attachments", []) or []
        )

    def _resolve_sunken_temple_quest_phase_end(self) -> None:
        temple = self._sunken_temple_active_location()
        if temple is None:
            return
        lines = [
            "强制 · 沉没的神庙：任务阶段结束时，"
            "从每名目标-盟友和每名英雄的资源池中弃除 1 枚资源标记。"
        ]
        targets = self._hero_and_objective_ally_resource_targets()
        removed_any = False
        if not targets:
            lines.append("  场上没有英雄或目标-盟友。")
        for char_id, owner_idx, target_card in targets:
            removed, before, after = self._remove_one_resource_from_character_pool(
                char_id,
                owner_idx,
            )
            target_name = getattr(target_card, "name", self._character_display_name(char_id))
            tag = self._player_tag(owner_idx) or f"玩家 {owner_idx + 1}"
            if removed:
                removed_any = True
                lines.append(f"  {tag}「{target_name}」资源 {before}→{after}。")
            else:
                lines.append(f"  {tag}「{target_name}」没有资源标记可弃除。")
        if removed_any:
            self._sync_hero_resources_from_widgets()

        blocked_note = self._active_location_flip_blocked_note(temple)
        if blocked_note:
            lines.append(f"  {blocked_note}")
        elif (
            self._question(
                "强制 · 沉没的神庙",
                "\n".join(lines) + "\n\n是否将「沉没的神庙」翻至石窟面？",
                default_yes=False,
            )
            == QMessageBox.Yes
        ):
            lines.extend(self._flip_grotto_location(temple))
        else:
            lines.append("未将「沉没的神庙」翻至石窟面。")
        self._update_quest_dial_badges()
        detail = "\n".join(lines)
        self._inform("沉没的神庙 · 强制", detail)
        print("沉没的神庙强制：\n" + detail)

    def _resolve_drowned_cave_quest_phase_end(self) -> None:
        cave = self._drowned_cave_active_location()
        if cave is None:
            return
        lines = [
            "强制 · 淹没的洞穴：如果任务阶段结束时淹没的洞穴处于激活地区，"
            "每位玩家必须弃除牌组顶端的十张牌。"
        ]
        for offset in range(self.PLAYER_COUNT):
            player_idx = (self.first_player_index + offset) % self.PLAYER_COUNT
            if player_idx in self._eliminated_players:
                continue
            player_tag = self._player_tag(player_idx) or f"玩家 {player_idx + 1}"
            drawer = self._player_drawer_for(player_idx)
            if drawer is None:
                lines.append(f"  {player_tag}：牌组未初始化，未弃除卡牌。")
                continue
            if hasattr(drawer, "_ensure_deck_stack"):
                drawer._ensure_deck_stack()
            discarded = self._discard_player_deck_top_cards(player_idx, 10)
            if not discarded:
                lines.append(f"  {player_tag}：牌组为空，未弃除卡牌。")
                continue
            lines.append(
                f"  {player_tag}：弃除牌组顶端 {len(discarded)} 张（"
                + "、".join(getattr(card, "name", "?") or "?" for card in discarded)
                + "）。"
            )
            for discarded_card in discarded:
                response_note = self._try_secret_treasure_deck_discard_response(
                    discarded_card,
                    player_idx,
                )
                if response_note:
                    lines.append(f"    {response_note}")
        if (
            self._question(
                "强制 · 淹没的洞穴",
                "\n".join(lines) + "\n\n是否将「淹没的洞穴」返回场景区？",
                default_yes=False,
            )
            == QMessageBox.Yes
        ):
            return_note = self._return_current_location_to_staging_preserving_progress()
            if return_note:
                lines.append(f"  {return_note}")
        else:
            lines.append("  「淹没的洞穴」留在当前地区。")
        detail = "\n".join(lines)
        self._inform("强制 · 淹没的洞穴", detail)
        print("淹没的洞穴强制：\n" + detail)

    def _resolve_active_underwater_location_quest_phase_end_forced(self) -> str:
        current = self._active_underwater_location()
        if current is None:
            return "当前没有水下激活地区。"
        if self._twisting_hollow_active_location() is not None:
            self._resolve_twisting_hollow_quest_phase_end()
        elif self._dark_abyss_active_location() is not None:
            self._resolve_dark_abyss_quest_phase_end()
        elif self._sunken_temple_active_location() is not None:
            self._resolve_sunken_temple_quest_phase_end()
        elif self._drowned_cave_active_location() is not None:
            self._resolve_drowned_cave_quest_phase_end()
        elif self._drowned_ruins_shrine_to_morgoth_underwater_active_location() is not None:
            self._resolve_shrine_to_morgoth_quest_phase_end()
        else:
            current_name = getattr(current, "name", "水下地区") or "水下地区"
            return (
                f"当前地区「{current_name}」是水下地区，"
                "但未找到对应的任务阶段结束强制效果。"
            )
        current_name = getattr(current, "name", "水下地区") or "水下地区"
        return f"立即结算当前水下地区「{current_name}」的任务阶段结束强制效果。"

    def _resolve_grotto_1b_location_explored(self, card) -> str:
        if not self._is_grotto_1b_quest_active() or not self._is_grotto_location_card(card):
            return ""
        added = self._add_grotto_location_from_deck_to_staging()
        if added is None:
            return "强制 · 石窟 1B：双面地区探索完毕，但石窟牌库为空。"
        return (
            f"强制 · 石窟 1B：双面地区「{card.name}」探索完毕后，"
            f"将石窟牌库顶牌「{added.name}」以石窟面朝上加入探查区。"
        )

    def _flip_grotto_location(
        self,
        card,
        *,
        remove_progress: bool = True,
    ) -> list[str]:
        """按指示翻转石窟/海底地区；默认翻面时移除其上的全部进度。"""
        other = self._grotto_other_face_card(card)
        if other is None:
            return [f"「{getattr(card, 'name', '地区')}」没有可用的另一面。"]
        is_current = (
            getattr(self, "current_location_card", None) is not None
            and getattr(self.current_location_card, "id", "") == getattr(card, "id", "")
        )
        blocked_note = self._active_location_flip_blocked_note(card) if is_current else ""
        if blocked_note:
            print(f"石窟地区翻面受阻：{blocked_note}")
            return [blocked_note]
        old_progress = (
            int(getattr(self, "current_location_progress", 0) or 0)
            if is_current
            else self._location_placed_progress(card)
        )
        clear_encounter_marker_state_for_card(card)
        clear_encounter_marker_state_for_card(other)
        replaced_staging = False
        for idx, staged in enumerate(list(getattr(self, "staging_cards", []) or [])):
            if staged is card or getattr(staged, "id", "") == getattr(card, "id", ""):
                self.staging_cards[idx] = other
                replaced_staging = True
                break
        if is_current:
            self.current_location_card = other
            self.current_location_progress = 0 if remove_progress else old_progress
            self._refresh_current_location_display()
        elif replaced_staging and not remove_progress and old_progress > 0:
            set_encounter_marker_progress_for_card(other, old_progress)
        if replaced_staging:
            self._refresh_staging_row(self.staging_cards)
        face_name = "海底面" if self._grotto_location_face(other) == "underwater" else "石窟面"
        progress_note = (
            f"移除其上的全部进度标记（{old_progress} 枚）"
            if remove_progress
            else f"不移除进度标记（保留 {old_progress} 枚）"
        )
        note = (
            f"将「{getattr(card, 'name', '地区')}」翻至{face_name}"
            f"「{other.name}」，{progress_note}。"
        )
        print(f"石窟地区翻面：{note}")
        return [note]

    def _shuffle_grotto_location_into_deck(self, card) -> bool:
        if self._grotto_location_face(card) != "grotto":
            return False
        clear_encounter_marker_state_for_card(card)
        deck = getattr(self, "_grotto_location_deck", None)
        if deck is None:
            self._grotto_location_deck = []
            deck = self._grotto_location_deck
        deck.append(card)
        self._shuffle_grotto_location_deck()
        if hasattr(self, "encounter_drawer"):
            self.encounter_drawer.drawn_ids.discard(getattr(card, "id", "") or "")
        print(
            f"石窟牌库：石窟面「{card.name}」未进入遭遇弃牌堆，"
            f"已洗回石窟牌库（{len(deck)} 张）。"
        )
        return True

    def _route_grotto_location_out_of_play(self, card) -> bool:
        face = self._grotto_location_face(card)
        if not face:
            return False
        clear_encounter_marker_state_for_card(card)
        if self._is_drowned_ruins_shrine_to_morgoth_location(card):
            vp = self._encounter_card_victory_value(card)
            self._add_to_victory_display(card, vp)
            print(
                f"魔苟斯祭坛：「{card.name}」未进入遭遇弃牌堆，"
                f"已加入胜利区（{vp} 胜利点）。"
            )
            return True
        if face == "grotto":
            return self._shuffle_grotto_location_into_deck(card)
        vp = self._encounter_card_victory_value(card)
        self._add_to_victory_display(card, vp)
        print(
            f"石窟牌库：海底面「{card.name}」未进入遭遇弃牌堆，"
            f"已加入胜利区（{vp} 胜利点）。"
        )
        return True

    def _grotto_location_explored_note(self, card, *, from_staging: bool) -> str:
        area = "（探查区）" if from_staging else ""
        face = self._grotto_location_face(card)
        if self._is_drowned_ruins_shrine_to_morgoth_location(card):
            vp = self._encounter_card_victory_value(card)
            clear_encounter_marker_state_for_card(card)
            self._add_to_victory_display(card, vp)
            return (
                f"  地区「{card.name}」探索完毕{area}，移除全部进度并加入胜利区"
                f"（{vp} 胜利点，团队合计 {self._victory_display_vp}）"
            )
        if face == "grotto":
            self._shuffle_grotto_location_into_deck(card)
            return (
                f"  地区「{card.name}」探索完毕{area}，移除全部进度并以石窟面"
                "洗回石窟牌库"
            )
        vp = self._encounter_card_victory_value(card)
        clear_encounter_marker_state_for_card(card)
        self._add_to_victory_display(card, vp)
        return (
            f"  地区「{card.name}」探索完毕{area}，移除全部进度并加入胜利区"
            f"（{vp} 胜利点，团队合计 {self._victory_display_vp}）"
        )

    def _create_lost_island_proxy(self, hidden_card):
        row = self._lost_island_template_row() or self._encounter_row_for_card(hidden_card)
        if row:
            proxy = EncounterCard.from_csv_row(row)
        else:
            proxy = hidden_card
        hidden_base = self._card_octgn_base_id(hidden_card) or getattr(hidden_card, "id", "")
        copy_suffix = ""
        hidden_id = getattr(hidden_card, "id", "") or ""
        if "#" in hidden_id:
            copy_suffix = "#" + hidden_id.split("#", 1)[1].split("::", 1)[0]
        proxy.id = f"{hidden_base}{copy_suffix}::lost-island"
        proxy.name = "失落的岛屿"
        proxy.Category = "地区"
        proxy.Area = "1"
        proxy.type = "地区"
        proxy.Threat = "2"
        proxy.Threat_Level = "2"
        proxy.Defense = ""
        proxy.Health = ""
        proxy.Progress = ""
        proxy.Keywords = "未知"
        proxy.Text_Effect = (
            "未知。不受玩家牌附属。强制：在失落的岛屿成为激活地区后，将其翻面。"
            "行动：从失落的岛屿上移除4枚进度标记以查看其反面。"
        )
        setattr(proxy, "_uncharted_proxy", True)
        setattr(proxy, "_uncharted_hidden_card", hidden_card)
        return proxy

    def _remove_lost_island_templates_from_encounter_deck(self) -> int:
        if self._is_island_map_scenario():
            return 0
        if not hasattr(self, "encounter_drawer"):
            return 0
        removed = 0
        for attr in ("cards", "special_cards"):
            source = getattr(self.encounter_drawer, attr, None)
            if not isinstance(source, list):
                continue
            kept = []
            for card in source:
                if (
                    self._is_lost_island_template_card(card)
                    and not self._is_lost_island_proxy(card)
                    and self._uncharted_hidden_card(card) is None
                ):
                    removed += 1
                    self.encounter_drawer.drawn_ids.discard(getattr(card, "id", "") or "")
                    continue
                kept.append(card)
            setattr(self.encounter_drawer, attr, kept)
        if removed:
            print(f"未知：移除 {removed} 张失落的岛屿模板，改用运行时代理")
        return removed

    def _setup_uncharted_location_deck_from_encounter_deck(self) -> int:
        """布置未知牌库：将遭遇文件中的未知双面地区抽离并洗混。"""
        if self._is_island_map_scenario():
            return 0
        if not hasattr(self, "encounter_drawer"):
            return 0
        hidden_cards = []
        for attr in ("cards", "setup_cards", "special_cards"):
            source = getattr(self.encounter_drawer, attr, None)
            if not isinstance(source, list):
                continue
            kept = []
            for card in source:
                if self._is_uncharted_location_back(card):
                    clear_encounter_marker_state_for_card(card)
                    hidden_cards.append(card)
                    self.encounter_drawer.drawn_ids.discard(getattr(card, "id", "") or "")
                else:
                    kept.append(card)
            setattr(self.encounter_drawer, attr, kept)
        if not hidden_cards:
            return 0
        deck = getattr(self, "_uncharted_location_deck", None)
        if deck is None:
            self._uncharted_location_deck = []
            deck = self._uncharted_location_deck
        deck.extend(hidden_cards)
        random.shuffle(deck)
        print(
            f"未知：布置未知牌库，将 {len(hidden_cards)} 张未知地区"
            f"以失落的岛屿面朝上洗入未知牌库（共 {len(deck)} 张）"
        )
        return len(hidden_cards)

    def _draw_uncharted_location_proxy(self):
        """从未知牌库抽取一张，以失落的岛屿正面代理返回。"""
        deck = getattr(self, "_uncharted_location_deck", None)
        if not deck:
            return None
        hidden = deck.pop(0)
        clear_encounter_marker_state_for_card(hidden)
        proxy = self._create_lost_island_proxy(hidden)
        print(
            f"未知：从未知牌库抽取 1 张，以「失落的岛屿」加入流程"
            f"（未知牌库剩余 {len(deck)} 张）"
        )
        return proxy

    def _add_uncharted_location_from_deck_to_staging(self):
        """从未知牌库将一张失落的岛屿代理加入探查区。"""
        proxy = self._draw_uncharted_location_proxy()
        if proxy is None:
            self._warn("未知牌库", "未知牌库为空，无法加入失落的岛屿。")
            return None
        if proxy not in self.staging_cards:
            self.staging_cards.append(proxy)
            self._move_thrors_key_from_heroes_to_location(proxy)
            self._refresh_staging_row(self.staging_cards)
        return proxy

    def _shuffle_uncharted_location_into_deck(self, card) -> bool:
        hidden = self._uncharted_hidden_card(card) or card
        if hidden is None or not self._is_uncharted_location_back(hidden):
            return False
        clear_encounter_marker_state_for_card(card)
        if hidden is not card:
            clear_encounter_marker_state_for_card(hidden)
        deck = getattr(self, "_uncharted_location_deck", None)
        if deck is None:
            self._uncharted_location_deck = []
            deck = self._uncharted_location_deck
        deck.append(hidden)
        random.shuffle(deck)
        self.encounter_drawer.drawn_ids.discard(getattr(hidden, "id", "") or "")
        self.encounter_drawer.drawn_ids.discard(getattr(card, "id", "") or "")
        print(
            f"未知：双面地区「{hidden.name}」未进入遭遇弃牌堆，"
            f"以失落的岛屿面朝上洗回未知牌库（{len(deck)} 张）"
        )
        return True

    def _resolve_uncharted_flip_for_active_location(
        self, proxy_card, *, carried_progress: int = 0
    ) -> list[str]:
        hidden = self._uncharted_hidden_card(proxy_card)
        if hidden is None:
            return []
        old_progress = max(0, int(carried_progress))
        clear_encounter_marker_state_for_card(proxy_card)
        clear_encounter_marker_state_for_card(hidden)
        self.set_current_location(hidden, carried_progress=0)
        notes = [
            f"强制 · 失落的岛屿成为激活地区：翻面为「{hidden.name}」。",
            f"移除其上的全部进度标记（{old_progress} 枚）。",
        ]
        notes.extend(self._resolve_uncharted_location_flipped(hidden))
        notes.extend(
            self._resolve_explore_island_1b_unknown_became_active_location(hidden)
        )
        return notes

    def _resolve_uncharted_location_flipped(self, location_card) -> list[str]:
        """未知地区真正翻面后的扩展钩子；具体反面强制效果逐张接入。"""
        if self._is_cursed_temple_location(location_card):
            return ["被诅咒的神庙已翻面：其激活地区持续效果立即生效。"]
        if self._is_lush_jungle_location(location_card):
            return self._resolve_lush_jungle_flipped(location_card)
        if self._is_drowned_graves_location(location_card):
            return self._resolve_drowned_graves_flipped(location_card)
        if self._is_forbidden_coast_location(location_card):
            return self._resolve_forbidden_coast_flipped(location_card)
        if self._is_shrine_to_morgoth_location(location_card):
            return self._resolve_shrine_to_morgoth_flipped(location_card)
        return []

    def _on_lost_island_action_click(self, card):
        if not self._is_lost_island_proxy(card) or card not in self.staging_cards:
            return
        if not self._is_player_action_window_active():
            return
        placed = self._location_placed_progress(card)
        hidden = self._uncharted_hidden_card(card)
        if placed < 4:
            self._warn(
                "行动 · 失落的岛屿",
                f"「失落的岛屿」上只有 {placed} 枚进度，不能移除 4 枚查看反面。",
            )
            return
        removed = self._remove_progress_from_location(card, 4)
        setattr(card, "_uncharted_peeked", True)
        hidden_name = getattr(hidden, "name", "未知反面") if hidden is not None else "未知反面"
        self._refresh_staging_row(self.staging_cards)
        body = (
            f"从「失落的岛屿」上移除 {removed} 枚进度标记。\n\n"
            f"查看其反面：{hidden_name}\n\n"
            "注意：只是查看反面，不触发翻面时效果。"
        )
        image_path = (getattr(hidden, "image_path", "") or "").strip() if hidden else ""
        if image_path and Path(image_path).is_file():
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                dlg = CardImageZoomDialog(pixmap, self)
                dlg.setWindowTitle(f"查看反面 · {hidden_name} - 单击关闭")
                dlg.exec_()
        self._inform("行动 · 失落的岛屿", body)
        print(f"行动（失落的岛屿）：移除 {removed} 进度，查看「{hidden_name}」")

    def _staging_uncharted_location_cards(self) -> list:
        candidates = list(getattr(self, "staging_cards", []) or [])
        candidates.extend(self._island_map_staging_display_locations())
        result = []
        seen_ids = set()
        for card in candidates:
            card_id = getattr(card, "id", "") or ""
            if card_id and card_id in seen_ids:
                continue
            cell = self._island_map_cell_for_card(card)
            if cell is not None and cell.get("face_up"):
                continue
            if not (
                self._is_lost_island_proxy(card)
                or self._is_uncharted_location_back(card)
            ):
                continue
            result.append(card)
            if card_id:
                seen_ids.add(card_id)
        return result

    def _peek_uncharted_location_back(
        self,
        card,
        *,
        title: str,
        source_label: str,
    ) -> str:
        hidden = self._uncharted_hidden_card(card) or card
        if hidden is None:
            return f"{source_label}：未找到可查看的未知地区反面。"
        hidden_name = getattr(hidden, "name", "未知反面") or "未知反面"
        card_name = getattr(card, "name", "未知地区") or "未知地区"
        body = (
            f"{source_label}：查看「{card_name}」的反面：{hidden_name}\n\n"
            "注意：只是查看反面，不触发翻面时效果。"
        )
        image_path = (getattr(hidden, "image_path", "") or "").strip()
        if image_path and Path(image_path).is_file():
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                dlg = CardImageZoomDialog(pixmap, self)
                dlg.setWindowTitle(f"查看反面 · {hidden_name} - 单击关闭")
                dlg.exec_()
        self._inform(title, body)
        return f"{source_label}：查看「{card_name}」的反面「{hidden_name}」。"

    def _is_objective_ally_type(self, card) -> bool:
        return (getattr(card, "type", "") or "").strip() in (
            "目标-盟友",
            "目标|盟友",
            "Objective Ally",
        )

    def _is_stormcaller_elite_card(self, card) -> bool:
        base_id = self._card_octgn_base_id(card)
        if base_id in self.STORMCALLER_ELITE_CARD_IDS:
            return True
        name = (getattr(card, "name", "") or "").strip()
        canonical = CARD_NAME_ALIASES.get(name, "")
        return name in self.STORMCALLER_ELITE_NAMES or canonical in self.STORMCALLER_ELITE_NAMES

    def _is_stormcaller_elite_enemy_face(self, card) -> bool:
        return (
            self._is_stormcaller_elite_card(card)
            and (getattr(card, "type", "") or "").strip() == "敌人"
        )

    def _is_stormcaller_elite_objective_ally_face(self, card) -> bool:
        return self._is_stormcaller_elite_card(card) and self._is_objective_ally_type(card)

    def _is_calphon_objective_ally_card(self, card) -> bool:
        if card is None:
            return False
        base_id = self._card_octgn_base_id(card)
        if base_id == self.CALPHON_OBJECTIVE_ALLY_OCTGN_BASE:
            return True
        name = (getattr(card, "name", "") or "").strip()
        canonical = CARD_NAME_ALIASES.get(name, "")
        return (
            name in self.CALPHON_OBJECTIVE_ALLY_NAMES
            or canonical in self.CALPHON_OBJECTIVE_ALLY_NAMES
        )

    def _field_character_card(self, char_id: str):
        widget = self._field_widgets.get(char_id)
        if widget is not None:
            bound = getattr(widget, "current_card", None)
            if bound is not None:
                return bound
        return self._character_card_by_id(char_id)

    def _card_name_matches_any(self, card, names: set[str] | frozenset[str]) -> bool:
        name = (getattr(card, "name", "") or "").strip()
        if name in names:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        return canonical in names

    def _player_controls_named_character(
        self, player_index: int, names: set[str] | frozenset[str]
    ) -> bool:
        if player_index in self._eliminated_players:
            return False
        for hero in self._heroes_controlled_by_player(player_index):
            if self._card_name_matches_any(hero, names):
                return True
        for ally in self._players[player_index].ally_cards:
            ally_id = getattr(ally, "id", "") or ""
            if not ally_id or ally_id in self._destroyed_characters:
                continue
            if not self._is_character_in_play(ally_id):
                continue
            if not self._is_character_alive(ally_id):
                continue
            if self._card_name_matches_any(ally, names):
                return True
        return False

    def _is_dwarf_character_card(self, card) -> bool:
        """英雄或盟友且具矮人属性。"""
        if (getattr(card, "type", "") or "").strip() not in ('英雄', "盟友"):
            return False
        return "矮人" in self._player_race_and_traits_text(card)

    def _is_dwarf_ally_card(self, card) -> bool:
        """矮人盟友（不含英雄）。"""
        if (getattr(card, "type", "") or "").strip() != "盟友":
            return False
        return "矮人" in self._player_race_and_traits_text(card)

    def _is_erebor_battle_master_ally_card(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != "盟友":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.EREBOR_BATTLE_MASTER_ALLY_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.EREBOR_BATTLE_MASTER_ALLY_NAMES:
            return True
        text = (getattr(card, "Text_Effect", "") or "")
        return (
            "矮人盟友" in text
            and '攻击力' in text
            and ('其他' in text or "每名" in text)
        )

    def _other_dwarf_ally_count_for_owner(
        self, owner_idx: int, *, exclude_id: str = ""
    ) -> int:
        """某玩家场上存活的其他矮人盟友数量。"""
        count = 0
        for ally in self._players[owner_idx].ally_cards:
            if ally.id == exclude_id:
                continue
            if ally.id in self._destroyed_characters:
                continue
            if ally.id not in self._field_widgets:
                continue
            if not self._is_dwarf_ally_card(ally):
                continue
            count += 1
        return count

    def _erebor_battle_master_attack_bonus_for(self, card) -> int:
        """埃瑞博战斗大师：每名其他矮人盟友 +1 攻（FAQ 澄清：无上限）。"""
        if not self._is_erebor_battle_master_ally_card(card):
            return 0
        if card.id in self._destroyed_characters:
            return 0
        owner_idx = self._char_owner.get(card.id, self._active_player_index)
        return self._other_dwarf_ally_count_for_owner(
            owner_idx, exclude_id=card.id
        )

    def _is_mithlond_sea_watcher_ally_card(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != "盟友":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.MITHLOND_SEA_WATCHER_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.MITHLOND_SEA_WATCHER_NAMES:
            return True
        text = (getattr(card, "Text_Effect", "") or "")
        return (
            ("米斯" in name or "Mithlond" in name)
            and "弃牌堆顶端" in text
            and "盟友" in text
            and "攻击力" in text
            and "远攻" in text
        )

    def _is_elf_gem_smith_ally_card(self, card) -> bool:
        """检查卡牌是否为精灵宝石匠盟友。"""
        if (getattr(card, "type", "") or "").strip() != "盟友":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.ELF_GEM_SMITH_ALLY_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.ELF_GEM_SMITH_ALLY_NAMES:
            return True
        text = (getattr(card, "Text_Effect", "") or "")
        return (
            "诺多" in text
            and "工匠" in text
            and "手牌" in text
            and "弃除" in text
            and "放置进场" in text
        )

    def _player_discard_top_type_is(self, player_index: int, card_type: str) -> bool:
        if player_index < 0 or player_index >= len(self._players):
            return False
        pile = self._players[player_index].discard_cards
        if not pile:
            return False
        top = pile[-1]
        return (getattr(top, "type", "") or "").strip() == card_type

    def _mithlond_sea_watcher_passive_active(self, card) -> bool:
        if not self._is_mithlond_sea_watcher_ally_card(card):
            return False
        if card.id in self._destroyed_characters:
            return False
        owner_idx = self._char_owner.get(card.id, self._active_player_index)
        return self._player_discard_top_type_is(owner_idx, "盟友")

    def _mithlond_sea_watcher_attack_bonus_for(self, card) -> int:
        """米斯龙德望海者：己方弃牌堆顶为盟友时 +2 攻。"""
        return 2 if self._mithlond_sea_watcher_passive_active(card) else 0

    def _is_warden_of_the_havens_ally_card(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != "盟友":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.WARDEN_OF_THE_HAVENS_ALLY_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.WARDEN_OF_THE_HAVENS_ALLY_NAMES:
            return True
        text = (getattr(card, "Text_Effect", "") or "")
        return (
            "弃牌堆顶端" in text
            and "附属" in text
            and "防御力" in text
            and "警戒" in text
        )

    def _warden_of_the_havens_passive_active(self, card) -> bool:
        if not self._is_warden_of_the_havens_ally_card(card):
            return False
        if card.id in self._destroyed_characters:
            return False
        owner_idx = self._char_owner.get(card.id, self._active_player_index)
        return self._player_discard_top_type_is(owner_idx, "附属")

    def _warden_of_the_havens_defense_bonus_for(self, card) -> int:
        """灰港守护者：己方弃牌堆顶为附属时 +2 防。"""
        return 2 if self._warden_of_the_havens_passive_active(card) else 0

    def _annuminas_guardian_defense_bonus_for(self, card) -> int:
        """安努米那斯守护者：每有一个与你交锋的敌军，+1【防御力】。"""
        if not self._has_annuminas_guardian_defense_passive(card):
            return 0
        owner_idx = self._char_owner.get(card.id, self._active_player_index)
        return len(self._engaged_enemies_for_player(owner_idx))

    def _refresh_annuminas_guardian_defense_passives(self) -> None:
        """交锋状态变化后刷新安努米那斯守护者防御力面板加成。"""
        if not self._field_widgets:
            return
        for char_id, widget in list(self._field_widgets.items()):
            card = self._field_character_card(char_id)
            if card is None:
                continue
            if not self._is_annuminas_guardian_ally_card(card):
                continue
            owner_idx = self._char_owner.get(char_id, self._active_player_index)
            attachments_map = self._players[owner_idx].attachments
            self._apply_host_attachment_passives(
                card, widget, attachments_map
            )

    def _fornost_bowman_attack_bonus_for(self, card) -> int:
        """佛诺斯特箭手：每有一个与你交锋的敌军，+1【攻击力】。"""
        if not self._has_fornost_bowman_attack_passive(card):
            return 0
        owner_idx = self._char_owner.get(card.id, self._active_player_index)
        return len(self._engaged_enemies_for_player(owner_idx))

    def _refresh_fornost_bowman_attack_passives(self) -> None:
        """交锋状态变化后刷新佛诺斯特箭手攻击力面板加成。"""
        if not self._field_widgets:
            return
        for char_id, widget in list(self._field_widgets.items()):
            card = self._field_character_card(char_id)
            if card is None:
                continue
            if not self._is_fornost_bowman_ally_card(card):
                continue
            owner_idx = self._char_owner.get(char_id, self._active_player_index)
            attachments_map = self._players[owner_idx].attachments
            self._apply_host_attachment_passives(
                card, widget, attachments_map
            )

    def _refresh_merry_tactics_hero_attack_passives(self) -> None:
        """哈比人英雄数量变化后刷新战术版梅丽攻击力面板加成。"""
        if not self._field_widgets:
            return
        for char_id, widget in list(self._field_widgets.items()):
            card = self._field_character_card(char_id)
            if card is None or not self._is_tactics_merry_hero_card(card):
                continue
            owner_idx = self._char_owner.get(char_id, self._active_player_index)
            attachments_map = self._players[owner_idx].attachments
            self._apply_host_attachment_passives(
                card, widget, attachments_map
            )

    def _star_shaped_brooch_willpower_bonus_for(self, card, attachments_map: dict | None = None) -> int:
        """星形别针：若附属英雄的玩家与敌军交锋，则+1意志力。"""
        card_id = (getattr(card, "id", "") or "").strip()
        if not card_id:
            return 0
        owner_idx = self._char_owner.get(card_id, self._active_player_index)
        if attachments_map is None:
            attachments_map = self._players[owner_idx].attachments
        for att in attachments_map.get(card_id, []):
            if self._attachment_text_blank_by_sunken_temple(att):
                continue
            if self._is_star_shaped_brooch_attachment(att):
                if self._engaged_enemies_for_player(owner_idx):
                    return 1
                break
        return 0

    def _character_has_star_shaped_brooch(self, char_id: str) -> bool:
        """检查角色是否装备了星形别针（用于意志力不可降低判断）。"""
        owner_idx = self._char_owner.get(char_id, self._active_player_index)
        attachments_map = self._players[owner_idx].attachments
        for att in attachments_map.get(char_id, []):
            if self._attachment_text_blank_by_sunken_temple(att):
                continue
            if self._is_star_shaped_brooch_attachment(att):
                return True
        return False

    def _refresh_star_shaped_brooch_willpower_passives(self) -> None:
        """交锋状态变化后刷新星形别针意志力面板加成。"""
        if not self._field_widgets:
            return
        for char_id, widget in list(self._field_widgets.items()):
            card = self._field_character_card(char_id)
            if card is None:
                continue
            card_id = getattr(card, "id", "")
            owner_idx = self._char_owner.get(card_id, self._active_player_index)
            attachments_map = self._players[owner_idx].attachments
            has_brooch = False
            for att in attachments_map.get(card_id, []):
                if self._is_star_shaped_brooch_attachment(att):
                    has_brooch = True
                    break
            if has_brooch:
                self._apply_host_attachment_passives(
                    card, widget, attachments_map
                )

    def _is_sailor_of_lune_ally_card(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != "盟友":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.SAILOR_OF_LUNE_ALLY_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.SAILOR_OF_LUNE_ALLY_NAMES:
            return True
        text = (getattr(card, "Text_Effect", "") or "")
        return (
            "弃牌堆顶端" in text
            and "事件" in text
            and "意志力" in text
            and ("不受伤害" in text or "Cannot be damaged" in text)
        )

    def _sailor_of_lune_passive_active(self, card) -> bool:
        if not self._is_sailor_of_lune_ally_card(card):
            return False
        if card.id in self._destroyed_characters:
            return False
        owner_idx = self._char_owner.get(card.id, self._active_player_index)
        return self._player_discard_top_type_is(owner_idx, "事件")

    def _sailor_of_lune_willpower_bonus_for(self, card) -> int:
        """路恩船员：己方弃牌堆顶为事件时 +1 意志。"""
        return 1 if self._sailor_of_lune_passive_active(card) else 0

    def _sailor_of_lune_prevents_damage(self, char_id: str) -> bool:
        card = self._character_card_by_id(char_id)
        return (
            card is not None
            and char_id in self._questing_ids
            and self._sailor_of_lune_passive_active(card)
        )

    def _character_cannot_attack_or_defend(self, card) -> bool:
        """不能攻击或防御（如埃瑞博撰史人㭁凯兰崔姆织女等）。"""
        if card is None:
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.EREBOR_RECORD_KEEPER_ALLY_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.EREBOR_RECORD_KEEPER_ALLY_NAMES:
            return True
        text = (getattr(card, "Text_Effect", "") or "")
        if '不能攻击或防徭' in text:
            return True
        return "cannot attack or defend" in text.lower()

    def _is_dain_ironfoot_aura_hero(self, card) -> bool:
        """重返幽暗密林丹恩·铁足：未横置时矮人 +1 攻 +1 意（非凯尔·都铎防御版）。"""
        if (getattr(card, "type", "") or "").strip() != '英雄':
            return False
        if self._card_octgn_base_id(card) == self.DAIN_IRONFOOT_AURA_OCTGN_BASE:
            return True
        name = (getattr(card, "name", "") or "").strip()
        canonical = CARD_NAME_ALIASES.get(name, "")
        if (
            name not in self.DAIN_IRONFOOT_AURA_HERO_NAMES
            and canonical not in self.DAIN_IRONFOOT_AURA_HERO_NAMES
        ):
            return False
        text = (getattr(card, "Text_Effect", "") or "")
        return (
            "未横置" in text
            and "矮人角色" in text
            and '攻击力' in text
            and '意志力' in text
        )

    def _dain_ironfoot_aura_active(self) -> bool:
        """场上是否存在存活且未横置的丹恩·铁足（光环版）。"""
        for char_id, widget in self._field_widgets.items():
            if not self._is_character_alive(char_id):
                continue
            card = self._field_character_card(char_id)
            if card is None or not self._is_dain_ironfoot_aura_hero(card):
                continue
            if not widget.is_exhausted():
                return True
        return False

    def _dain_ironfoot_aura_bonuses_for(self, card) -> tuple[int, int]:
        """丹恩·铁足光环：矮人角色 +1 意志 / +1 攻击。"""
        if not self._dain_ironfoot_aura_active():
            return 0, 0
        if not self._is_dwarf_character_card(card):
            return 0, 0
        return 1, 1

    def _refresh_dain_ironfoot_aura_passives(self) -> None:
        """戴因横置/重整或场上角色重建后，刷新全场矮人面板加成。"""
        if not self._field_widgets:
            return
        was_active = bool(getattr(self, "_dain_ironfoot_aura_was_active", False))
        now_active = self._dain_ironfoot_aura_active()
        for char_id, widget in list(self._field_widgets.items()):
            card = self._field_character_card(char_id)
            if card is None:
                continue
            owner_idx = self._char_owner.get(char_id, self._active_player_index)
            attachments_map = self._players[owner_idx].attachments
            self._apply_host_attachment_passives(
                card, widget, attachments_map
            )
        if was_active != now_active:
            if now_active:
                print(
                    "戴因·铁足（未横置）：矮人角色获得 +1【攻击力】与 +1【意志力】"
                )
            else:
                print(
                    "戴因·铁足横置：矮人角色不再获得 +1【攻击力】与 +1【意志力】"
                )
        self._dain_ironfoot_aura_was_active = now_active

    def _has_hon_boromir_gondor_ally_attack_passive(self, card) -> bool:
        """刚铎领主版波洛米尔：资源≥1 时刚铎盟友 +1 攻。"""
        if (getattr(card, "type", "") or "").strip() != '英雄':
            return False
        text = (getattr(card, "Text_Effect", "") or "")
        return (
            "刚铎盟友" in text
            and '资源' in text
            and '攻击力' in text
        )

    def _hon_boromir_gondor_ally_aura_active(self) -> bool:
        """场上领主版波洛米尔资源池至少 1 枚。"""
        for char_id, widget in self._field_widgets.items():
            if not self._is_character_alive(char_id):
                continue
            card = self._field_character_card(char_id)
            if card is None or not self._has_hon_boromir_gondor_ally_attack_passive(
                card
            ):
                continue
            if widget.resource_count() >= 1:
                return True
            owner_idx = self._char_owner.get(char_id, self._active_player_index)
            if self._players[owner_idx].hero_resources.get(char_id, 0) >= 1:
                return True
        return False

    def _hon_boromir_gondor_ally_attack_bonus_for(self, card) -> int:
        if (getattr(card, "type", "") or "").strip() != "盟友":
            return 0
        if card.id in self._destroyed_characters:
            return 0
        if not self._character_has_gondor_trait(card):
            return 0
        if not self._hon_boromir_gondor_ally_aura_active():
            return 0
        return 1

    def _is_visionary_leadership_attachment(self, card) -> bool:
        name = (getattr(card, "name", "") or "").strip()
        if name in self.VISIONARY_LEADERSHIP_ATTACHMENT_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        return canonical in self.VISIONARY_LEADERSHIP_ATTACHMENT_NAMES

    def _has_visionary_leadership_willpower_passive(self, card) -> bool:
        """远见卓识：刚铎英雄资源≥1 时，刚铎角色 +1 意志力。"""
        if self._attachment_text_blank_by_sunken_temple(card):
            return False
        if not self._is_visionary_leadership_attachment(card):
            return False
        text = (getattr(card, "Text_Effect", "") or "")
        return "刚铎" in text and '资源' in text and '意志力' in text

    def _visionary_leadership_aura_active(self) -> bool:
        """场上是否存在资源≥1 且附有远见卓识的刚铎英雄。"""
        for char_id, widget in self._field_widgets.items():
            if not self._is_character_alive(char_id):
                continue
            card = self._field_character_card(char_id)
            if card is None or (getattr(card, "type", "") or "").strip() != '英雄':
                continue
            if not self._character_has_gondor_trait(card, char_id):
                continue
            if widget.resource_count() < 1:
                continue
            if not any(
                self._has_visionary_leadership_willpower_passive(att)
                for att in self._character_attachments(char_id)
            ):
                continue
            return True
        return False

    def _visionary_leadership_gondor_willpower_bonus_for(self, card) -> int:
        """远见卓识：刚铎角色 +1 意志力。"""
        if not self._visionary_leadership_aura_active():
            return 0
        char_id = getattr(card, "id", "") or ""
        if not char_id or not self._is_character_alive(char_id):
            return 0
        if not self._character_has_gondor_trait(card, char_id):
            return 0
        return 1

    def _is_spear_of_the_mark_attachment(self, card) -> bool:
        name = (getattr(card, "name", "") or "").strip()
        if name in self.SPEAR_OF_THE_MARK_ATTACHMENT_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        return canonical in self.SPEAR_OF_THE_MARK_ATTACHMENT_NAMES

    def _has_spear_of_the_mark_attack_passive(self, card) -> bool:
        """马克的长矛：附属到一名洛汗角色。"""
        if not self._is_spear_of_the_mark_attachment(card):
            return False
        text = (getattr(card, "Text_Effect", "") or "")
        return "洛汗" in text and '攻击力' in text

    def _is_ranger_spear_attachment(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != "附属":
            return False
        card_id = (getattr(card, "id", "") or "").strip()
        normalized_id = card_id.split("::", 1)[0].split("#", 1)[0]
        if normalized_id in self.RANGER_SPEAR_CARD_CODES:
            return True
        if self._card_octgn_base_id(card) in self.RANGER_SPEAR_OCTGN_BASES:
            return True
        name = (getattr(card, "name", "") or "").strip()
        if name in self.RANGER_SPEAR_ATTACHMENT_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        return canonical in self.RANGER_SPEAR_ATTACHMENT_NAMES

    def _has_ranger_spear_attack_passive(self, card) -> bool:
        return self._is_ranger_spear_attachment(card)

    def _enemy_has_any_attachment(self, enemy_card) -> bool:
        enemy_id = (getattr(enemy_card, "id", "") or "").strip()
        if not enemy_id:
            return False
        return bool(getattr(self, "_enemy_attachments", {}).get(enemy_id, []))

    def _ranger_spear_count_for_character(self, char_id: str) -> int:
        if not char_id or not self._is_character_alive(char_id):
            return 0
        card = self._character_card_by_id(char_id)
        if card is None or not self._character_has_ranger_trait(card):
            return 0
        return sum(
            1
            for att in self._character_attachments(char_id)
            if self._has_ranger_spear_attack_passive(att)
        )

    def _ranger_spear_attached_enemy_extra_bonus_for(
        self, char_id: str, enemy_card
    ) -> int:
        """游侠长矛：攻击带附属敌军时，原本 +1 攻击改为 +2。"""
        if enemy_card is None or not self._enemy_has_any_attachment(enemy_card):
            return 0
        return self._ranger_spear_count_for_character(char_id)

    def _spear_of_the_mark_attack_bonus_for(
        self,
        char_id: str,
        enemy_card,
        *,
        from_staging: bool,
    ) -> int:
        """马克的长矛：默认 +1；攻击场景区敌军为 +2。"""
        if not char_id or not self._is_character_alive(char_id):
            return 0
        card = self._character_card_by_id(char_id)
        if card is None or not self._character_has_rohan_trait(card, char_id):
            return 0
        if not any(
            self._has_spear_of_the_mark_attack_passive(att)
            for att in self._character_attachments(char_id)
        ):
            return 0
        if from_staging and enemy_card is not None:
            return 2 if enemy_card in self._staging_enemy_cards() else 1
        return 1

    def _refresh_hon_boromir_gondor_ally_attack_passives(self) -> None:
        """波洛米尔资源变化或场上角色重建后，刷新刚铎盟友攻击加成。"""
        if not self._field_widgets:
            return
        was_active = bool(getattr(self, "_hon_boromir_aura_was_active", False))
        now_active = self._hon_boromir_gondor_ally_aura_active()
        for char_id, widget in list(self._field_widgets.items()):
            card = self._field_character_card(char_id)
            if card is None:
                continue
            owner_idx = self._char_owner.get(char_id, self._active_player_index)
            attachments_map = self._players[owner_idx].attachments
            self._apply_host_attachment_passives(
                card, widget, attachments_map
            )
        if was_active != now_active:
            if now_active:
                print(
                    '波洛米尔（资源≥1）：刚铎盟友获得 +1【攻击力】'
                )
            else:
                print(
                    '波洛米尔资源池为空：刚铎盟友不再获得 +1【攻击力】'
                )
        self._hon_boromir_aura_was_active = now_active

    def _refresh_visionary_leadership_passives(self) -> None:
        """远见卓识资源变化或场上角色重建后，刷新刚铎意志力加成。"""
        if not self._field_widgets:
            return
        was_active = bool(
            getattr(self, "_visionary_leadership_aura_was_active", False)
        )
        now_active = self._visionary_leadership_aura_active()
        for char_id, widget in list(self._field_widgets.items()):
            card = self._field_character_card(char_id)
            if card is None:
                continue
            owner_idx = self._char_owner.get(char_id, self._active_player_index)
            attachments_map = self._players[owner_idx].attachments
            self._apply_host_attachment_passives(
                card, widget, attachments_map
            )
        if was_active != now_active:
            if now_active:
                print("远见卓识（资源≥1）：刚铎角色获得 +1【意志力】")
            else:
                print("远见卓识资源池为空：刚铎角色不再获得 +1【意志力】")
        self._visionary_leadership_aura_was_active = now_active

    def _is_faramir_hero_card(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != '英雄':
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.FARAMIR_HERO_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        return canonical in self.FARAMIR_HERO_NAMES

    def _has_faramir_staging_attack_passive(self, card) -> bool:
        """强韧的伊西利安法拉米尔：场景区每有一名敌军 +1 攻。"""
        if not self._is_faramir_hero_card(card):
            return False
        text = (getattr(card, "Text_Effect", "") or "")
        return (
            "场景区" in text
            and '敌军' in text
            and '攻击' in text
            and ('每有' in text or '每名' in text)
        )

    def _faramir_hero_staging_attack_bonus_for(self, card) -> int:
        if not self._has_faramir_staging_attack_passive(card):
            return 0
        char_id = getattr(card, "id", "") or ""
        if not char_id or char_id in self._destroyed_characters:
            return 0
        if not self._is_character_in_play(char_id):
            return 0
        return len(self._staging_enemy_cards())

    def _refresh_faramir_hero_attack_passives(self) -> None:
        """场景区敌军数量变化后，刷新法拉米尔英雄攻击加成。"""
        if not self._field_widgets:
            return
        staging_count = len(self._staging_enemy_cards())
        for char_id, widget in list(self._field_widgets.items()):
            card = self._field_character_card(char_id)
            if card is None or not self._has_faramir_staging_attack_passive(card):
                continue
            prev = self._faramir_staging_bonus_counts.get(char_id, -1)
            if staging_count != prev:
                if staging_count > 0:
                    print(
                        f"法拉米尔：场景区 {staging_count} 名敌军"
                        f" →+{staging_count}【攻击力】"
                    )
                elif prev > 0:
                    print("法拉米尔：场景区无敌军，攻击加成消失")
                self._faramir_staging_bonus_counts[char_id] = staging_count
            owner_idx = self._char_owner.get(char_id, self._active_player_index)
            attachments_map = self._players[owner_idx].attachments
            self._apply_host_attachment_passives(
                card, widget, attachments_map
            )

    def _character_has_outlands_trait(
        self, card, char_id: str | None = None
    ) -> bool:
        if "边境" in self._player_race_and_traits_text(card):
            return True
        cid = (char_id or getattr(card, "id", "") or "").strip()
        if not cid:
            return False
        owner_idx = self._char_owner.get(cid, self._active_player_index)
        attachments = self._players[owner_idx].attachments.get(cid, [])
        return any(
            self._is_sword_of_morthond_attachment(att)
            or (
                self._has_prince_of_dol_amroth_outlands_grant(att)
                and (getattr(att, "id", "") or "") not in self._facedown_attachment_ids
                and not self._attachment_text_blank_by_sunken_temple(att)
            )
            for att in attachments
        )

    def _is_outlands_character_card(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() not in ('英雄', "盟友"):
            return False
        if getattr(card, "id", "") in self._destroyed_characters:
            return False
        return self._character_has_outlands_trait(card)

    def _is_lossarnach_warrior_ally_card(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != "盟友":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.LOSSARNACH_WARRIOR_ALLY_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        return canonical in self.LOSSARNACH_WARRIOR_ALLY_NAMES

    def _has_lossarnach_warrior_outlands_defense_passive(self, card) -> bool:
        if not self._is_lossarnach_warrior_ally_card(card):
            return False
        text = (getattr(card, "Text_Effect", "") or "")
        return "边境" in text and "防御力" in text

    def _is_swan_knight_ally_card(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != "盟友":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.SWAN_KNIGHT_ALLY_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        return canonical in self.SWAN_KNIGHT_ALLY_NAMES

    def _has_swan_knight_outlands_attack_passive(self, card) -> bool:
        if not self._is_swan_knight_ally_card(card):
            return False
        text = (getattr(card, "Text_Effect", "") or "")
        return "边境" in text and '攻击力' in text

    def _is_ethir_swordsman_ally_card(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != "盟友":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.ETHIR_SWORDSMAN_ALLY_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        return canonical in self.ETHIR_SWORDSMAN_ALLY_NAMES

    def _has_ethir_swordsman_outlands_willpower_passive(self, card) -> bool:
        if not self._is_ethir_swordsman_ally_card(card):
            return False
        text = (getattr(card, "Text_Effect", "") or "")
        return "边境" in text and '意志力' in text

    def _is_anfalas_herdsman_ally_card(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != "盟友":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.ANFALAS_HERDSMAN_ALLY_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        return canonical in self.ANFALAS_HERDSMAN_ALLY_NAMES

    def _has_anfalas_herdsman_outlands_health_passive(self, card) -> bool:
        if not self._is_anfalas_herdsman_ally_card(card):
            return False
        text = (getattr(card, "Text_Effect", "") or "")
        return "边境" in text and '生命值' in text

    def _count_outlands_aura_sources_for_player(
        self, player_index: int, predicate
    ) -> int:
        if player_index in self._eliminated_players:
            return 0
        count = 0
        for ally in self._players[player_index].ally_cards:
            if ally.id in self._destroyed_characters:
                continue
            if not self._is_character_alive(ally.id):
                continue
            if predicate(ally):
                count += 1
        return count

    def _sync_outlands_aura_counts(self) -> None:
        """每位玩家边缘光环源数量：(抵御意志/生命)，供面板加成查表。"""
        counts: dict[int, tuple[int, int, int, int]] = {}
        for player_idx in range(self.PLAYER_COUNT):
            if player_idx in self._eliminated_players:
                counts[player_idx] = (0, 0, 0, 0)
                continue
            counts[player_idx] = (
                self._count_outlands_aura_sources_for_player(
                    player_idx,
                    self._has_lossarnach_warrior_outlands_defense_passive,
                ),
                self._count_outlands_aura_sources_for_player(
                    player_idx,
                    self._has_swan_knight_outlands_attack_passive,
                ),
                self._count_outlands_aura_sources_for_player(
                    player_idx,
                    self._has_ethir_swordsman_outlands_willpower_passive,
                ),
                self._count_outlands_aura_sources_for_player(
                    player_idx,
                    self._has_anfalas_herdsman_outlands_health_passive,
                ),
            )
        self._outlands_aura_counts = counts

    def _outlands_aura_bonus_for(self, card, stat_index: int) -> int:
        if not self._is_outlands_character_card(card):
            return 0
        owner_idx = self._char_owner.get(
            getattr(card, "id", ""),
            self._active_player_index,
        )
        if not self._outlands_aura_counts:
            self._sync_outlands_aura_counts()
        def_atk_wp_hp = self._outlands_aura_counts.get(
            owner_idx, (0, 0, 0, 0)
        )
        if stat_index < 0 or stat_index >= len(def_atk_wp_hp):
            return 0
        return def_atk_wp_hp[stat_index]

    def _outlands_defense_aura_bonus_for(self, card) -> int:
        return self._outlands_aura_bonus_for(card, 0)

    def _outlands_attack_aura_bonus_for(self, card) -> int:
        return self._outlands_aura_bonus_for(card, 1)

    def _outlands_willpower_aura_bonus_for(self, card) -> int:
        return self._outlands_aura_bonus_for(card, 2)

    def _outlands_health_aura_bonus_for(self, card) -> int:
        return self._outlands_aura_bonus_for(card, 3)

    def _refresh_outlands_aura_passives(self) -> None:
        """边境外环盟友变化后，仅刷新所属玩家边境角色面板（计数未变则跳过）。"""
        if not self._field_widgets:
            return
        prev = dict(self._outlands_aura_counts)
        self._sync_outlands_aura_counts()
        if prev == self._outlands_aura_counts:
            return
        for char_id, widget in list(self._field_widgets.items()):
            card = self._field_character_card(char_id)
            if card is None or not self._is_outlands_character_card(card):
                continue
            owner_idx = self._char_owner.get(char_id, self._active_player_index)
            attachments_map = self._players[owner_idx].attachments
            self._apply_host_attachment_passives(
                card, widget, attachments_map
            )

    def _is_forlong_ally_card(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != "盟友":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.FORLONG_ALLY_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        return canonical in self.FORLONG_ALLY_NAMES

    def _is_harbor_master_ally_card(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != "盟友":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.HARBOR_MASTER_ALLY_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        return canonical in self.HARBOR_MASTER_ALLY_NAMES

    def _has_harbor_master_resource_response(self, card) -> bool:
        """响应：卡牌效果向英雄资源池增加资源后，+1 防至回合结束。"""
        if not self._is_harbor_master_ally_card(card):
            return False
        text = (getattr(card, "Text_Effect", "") or "")
        if '响应' not in text:
            return False
        if not self._card_text_refers_to_self(card):
            return False
        resource_add = (
            '资源丰富' in text
            and ("增加" in text or "添加" in text or "adds" in text.lower())
        )
        defense = (
            "防御" in text or "Defense" in text or '【防御力】' in text
        )
        return resource_add and defense

    def _harbor_masters_in_play_for_player(
        self, player_index: int
    ) -> list[tuple[str, object]]:
        result: list[tuple[str, object]] = []
        for ally in self._players[player_index].ally_cards:
            if not self._is_harbor_master_ally_card(ally):
                continue
            if not self._has_harbor_master_resource_response(ally):
                continue
            if ally.id in self._destroyed_characters:
                continue
            if not self._is_character_in_play(ally.id):
                continue
            if not self._is_character_alive(ally.id):
                continue
            result.append((ally.id, ally))
        return result

    def _is_white_tower_watchman_ally_card(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != "盟友":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.WHITE_TOWER_WATCHMAN_ALLY_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        return canonical in self.WHITE_TOWER_WATCHMAN_ALLY_NAMES

    def _is_barliman_butterbur_ally_card(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != "盟友":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.BARLIMAN_BUTTERBUR_ALLY_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        return canonical in self.BARLIMAN_BUTTERBUR_ALLY_NAMES

    def _has_barliman_butterbur_undefended_damage_ability(self, card) -> bool:
        """全哈比人英雄时，可将无人防御伤害分配给巴力曼·奶油伯。"""
        if not self._is_barliman_butterbur_ally_card(card):
            return False
        text = (getattr(card, "Text_Effect", "") or "")
        hobbit = '哈比人' in text or "hobbit" in text.lower()
        heroes = '英雄' in text or "hero" in text.lower()
        undefended = '无人防御' in text or "undefended" in text.lower()
        damage = '伤害' in text or "damage" in text.lower()
        assign = '分配' in text or "assign" in text.lower()
        return hobbit and heroes and undefended and damage and assign

    def _has_white_tower_watchman_undefended_damage_ability(self, card) -> bool:
        """每名英雄同属一种影响力派系时，可将无人防御伤害分配给白城卫队。"""
        if not self._is_white_tower_watchman_ally_card(card):
            return False
        text = (getattr(card, "Text_Effect", "") or "")
        if not self._card_text_refers_to_self(card):
            return False
        heroes = '英雄' in text or "hero" in text.lower()
        undefended = '无人防御' in text or "undefended" in text.lower()
        damage = "伤害" in text or "damage" in text.lower()
        sphere = '影响力派系' in text or "sphere of influence" in text.lower()
        return heroes and undefended and damage and sphere

    def _heroes_share_same_influence_sphere(self, player_index: int) -> bool:
        """指定玩家控制的每名英雄盟友是否至少共属同一种影响力派系。"""
        heroes = self._heroes_controlled_by_player(player_index)
        if not heroes:
            return False
        common: set[str] | None = None
        for hero in heroes:
            spheres = {
                sphere
                for sphere in self._hero_effective_spheres(hero)
                if not _is_neutral_sphere(sphere)
            }
            if not spheres:
                return False
            if common is None:
                common = set(spheres)
            else:
                common &= spheres
            if not common:
                return False
        return True

    def _white_tower_watchmen_in_play_for_player(
        self, player_index: int
    ) -> list[tuple[str, str]]:
        """场上存活白城卫队：(ally_id, display_name)。"""
        result: list[tuple[str, str]] = []
        for ally in self._players[player_index].ally_cards:
            if not self._is_white_tower_watchman_ally_card(ally):
                continue
            if not self._has_white_tower_watchman_undefended_damage_ability(ally):
                continue
            if ally.id in self._destroyed_characters:
                continue
            if not self._is_character_in_play(ally.id):
                continue
            if not self._is_character_alive(ally.id):
                continue
            widget = self._field_widgets.get(ally.id)
            if widget is None:
                continue
            health = int(widget.get_card_info().get("health", 0))
            if health <= 0:
                continue
            result.append((ally.id, ally.name))
        return result

    def _barliman_butterburs_in_play_for_player(
        self, player_index: int
    ) -> list[tuple[str, str]]:
        """场上存活巴力曼·奶油伯：(ally_id, display_name)。"""
        result: list[tuple[str, str]] = []
        for ally in self._players[player_index].ally_cards:
            if not self._is_barliman_butterbur_ally_card(ally):
                continue
            if not self._has_barliman_butterbur_undefended_damage_ability(ally):
                continue
            if ally.id in self._destroyed_characters:
                continue
            if not self._is_character_in_play(ally.id):
                continue
            if not self._is_character_alive(ally.id):
                continue
            widget = self._field_widgets.get(ally.id)
            if widget is None:
                continue
            health = int(widget.get_card_info().get("health", 0))
            if health <= 0:
                continue
            result.append((ally.id, ally.name))
        return result

    def _defender_of_the_west_allies_for_player(
        self, player_index: int
    ) -> list[tuple[str, str]]:
        """当前玩家控制的所有附有「西方守护者」的存活盟友：(ally_id, display_name)。"""
        result: list[tuple[str, str]] = []
        for ally in self._players[player_index].ally_cards:
            if ally.id in self._destroyed_characters:
                continue
            if not self._is_character_in_play(ally.id):
                continue
            if not self._is_character_alive(ally.id):
                continue
            atts = self._players[player_index].attachments.get(ally.id, [])
            has_defender = any(
                self._is_defender_of_the_west_attachment(att)
                and (getattr(att, "id", "") or "") not in self._facedown_attachment_ids
                for att in atts
            )
            if not has_defender:
                continue
            widget = self._field_widgets.get(ally.id)
            if widget is None:
                continue
            health = int(widget.get_card_info().get("health", 0))
            if health <= 0:
                continue
            result.append((ally.id, ally.name))
        return result

    def _apply_defender_of_the_west_enter_play(self, ally_id: str) -> None:
        """西方守护者进场：起始玩家获得所附属盟友的控制权。"""
        first_idx = self._starting_player_or_next_eligible()
        cur_owner = self._character_owner_index(ally_id)
        if cur_owner == first_idx:
            return
        ally_name = self._character_display_name(ally_id)
        first_tag = self._player_tag(first_idx) or f"玩家{first_idx + 1}"
        if self._transfer_ally_to_player(ally_id, first_idx):
            print(
                f"西方守护者：起始玩家 {first_tag} 获得「{ally_name}」的控制权"
            )
            self._update_player_controlled_captions()
            self._inform(
                "西方守护者",
                f"起始玩家 {first_tag} 获得「{ally_name}」的控制权。\n"
                "对你造成的无人防御的伤害可以分配到该盟友上。",
            )

    def _undefended_damage_target_options(
        self, player_index: int
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """无人防御时可选目标：(英雄列表, 可代承受伤害的盟友列表)。"""
        heroes = self._alive_heroes_for_damage(player_index)
        watchmen: list[tuple[str, str]] = []
        if self._heroes_share_same_influence_sphere(player_index):
            watchmen = self._white_tower_watchmen_in_play_for_player(
                player_index
            )
        if self._all_controlled_heroes_are_hobbits(player_index):
            butterburs = self._barliman_butterburs_in_play_for_player(
                player_index
            )
            seen_ids = {wid for wid, _ in watchmen}
            for aid, aname in butterburs:
                if aid not in seen_ids:
                    watchmen.append((aid, aname))
                    seen_ids.add(aid)
        defender_allies = self._defender_of_the_west_allies_for_player(player_index)
        seen_ids = {wid for wid, _ in watchmen}
        for aid, aname in defender_allies:
            if aid not in seen_ids:
                watchmen.append((aid, aname))
        return heroes, watchmen

    def _outlands_ally_influence_spheres_for_player(
        self, player_index: int
    ) -> set[str]:
        """场上存活边境盟友的印刷影响力派系（不含中立）。"""
        spheres: set[str] = set()
        for ally in self._players[player_index].ally_cards:
            if ally.id in self._destroyed_characters:
                continue
            if not self._is_character_alive(ally.id):
                continue
            if not self._character_has_outlands_trait(ally):
                continue
            sphere = _card_sphere(ally)
            if _is_neutral_sphere(sphere):
                continue
            spheres.add(sphere)
        return spheres

    def _forlong_four_sphere_condition_met(self, player_index: int) -> bool:
        spheres = self._outlands_ally_influence_spheres_for_player(
            player_index
        )
        return self.FORLONG_REQUIRED_INFLUENCE_SPHERES.issubset(spheres)

    def _apply_forlong_phase_begin_readies(self, step: str) -> None:
        """佛朗：四派系边境盟友齐全时，每阶段开始时重整自身。"""
        if step not in self.PHASE_ORDER:
            return
        phase_name = self.PHASE_LABELS.get(step, step)
        for player_idx in range(self.PLAYER_COUNT):
            if player_idx in self._eliminated_players:
                continue
            if not self._forlong_four_sphere_condition_met(player_idx):
                continue
            for ally in self._players[player_idx].ally_cards:
                if not self._is_forlong_ally_card(ally):
                    continue
                if ally.id in self._destroyed_characters:
                    continue
                if not self._is_character_alive(ally.id):
                    continue
                widget = self._field_widgets.get(ally.id)
                if widget is None or not widget.is_exhausted():
                    continue
                self._set_host_exhausted(ally.id, False)
                tag = self._player_tag(player_idx) or f"玩家{player_idx + 1}"
                print(
                    f"{phase_name}：{tag}「{ally.name}」"
                    "（四派系边境盟友齐全）重整"
                )

    def _note_dain_ironfoot_aura_state(self) -> None:
        """记录戴因光环开关（不重复刷新已由建卡完成的被动）。"""
        was_active = bool(getattr(self, "_dain_ironfoot_aura_was_active", False))
        now_active = self._dain_ironfoot_aura_active()
        if was_active != now_active:
            if now_active:
                print(
                    "戴因·铁足（未横置）：矮人角色获得 +1【攻击力】与 +1【意志力】"
                )
            else:
                print(
                    "戴因·铁足横置：矮人角色不再获得 +1【攻击力】与 +1【意志力】"
                )
        self._dain_ironfoot_aura_was_active = now_active

    def _note_hon_boromir_aura_state(self) -> None:
        """记录波洛米尔光环开关（不重复刷新已由建卡完成的被动）。"""
        was_active = bool(getattr(self, "_hon_boromir_aura_was_active", False))
        now_active = self._hon_boromir_gondor_ally_aura_active()
        if was_active != now_active:
            if now_active:
                print(
                    '波洛米尔（资源≥1）：刚铎盟友获得 +1【攻击力】'
                )
            else:
                print(
                    '波洛米尔资源池为空：刚铎盟友不再获得 +1【攻击力】'
                )
        self._hon_boromir_aura_was_active = now_active

    def _note_visionary_leadership_aura_state(self) -> None:
        """记录远见卓识光环开关。"""
        was_active = bool(
            getattr(self, "_visionary_leadership_aura_was_active", False)
        )
        now_active = self._visionary_leadership_aura_active()
        if was_active != now_active:
            if now_active:
                print("远见卓识（资源≥1）：刚铎角色获得 +1【意志力】")
            else:
                print("远见卓识资源池为空：刚铎角色不再获得 +1【意志力】")
        self._visionary_leadership_aura_was_active = now_active

    def _card_name_alias_bidirectional(self, card) -> tuple[str, str]:
        """返回卡名与别名规范名；若当前名是规范名，则找一个反向别名。"""
        name = (getattr(card, "name", "") or "").strip()
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical:
            return name, canonical
        for alias, dst in CARD_NAME_ALIASES.items():
            if dst == name:
                return alias, dst
        return name, ""

    def _card_matches_twin_name(self, card, twin_names: frozenset) -> bool:
        name = (getattr(card, "name", "") or "").strip()
        if name in twin_names:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in twin_names:
            return True
        for alias, canon in CARD_NAME_ALIASES.items():
            if canon == name and alias in twin_names:
                return True
        return False

    def _is_elrohir_twin_card(self, card) -> bool:
        return self._card_matches_twin_name(card, self.ELROHIR_TWIN_NAMES)

    def _is_elladan_twin_card(self, card) -> bool:
        return self._card_matches_twin_name(card, self.ELLADAN_TWIN_NAMES)

    def _any_twin_sibling_in_play(self, sibling_names: frozenset) -> bool:
        for char_id in self._field_widgets:
            if not self._is_character_alive(char_id):
                continue
            card = self._field_character_card(char_id)
            if card is not None and self._card_matches_twin_name(
                card, sibling_names
            ):
                return True
        return False

    def _elrohir_twin_defense_bonus(self, card) -> int:
        """埃尔拉丹在场时，埃洛希尔 +2【防御力】。"""
        if not self._is_elrohir_twin_card(card):
            return 0
        if not self._any_twin_sibling_in_play(self.ELLADAN_TWIN_NAMES):
            return 0
        text = (getattr(card, "Text_Effect", "") or "")
        if "防御力" not in text or '在场' not in text:
            return 0
        return 2

    def _elladan_twin_attack_bonus(self, card) -> int:
        """埃洛希尔在场时，埃尔拉丹 +2【攻击力】。"""
        if not self._is_elladan_twin_card(card):
            return 0
        if not self._any_twin_sibling_in_play(self.ELROHIR_TWIN_NAMES):
            return 0
        text = (getattr(card, "Text_Effect", "") or "")
        if '攻击力' not in text or '在场' not in text:
            return 0
        return 2

    def _refresh_twin_sibling_passives(self) -> None:
        """双生子进场/离场后刷新埃洛希尔与埃尔拉丹面板加成。"""
        if not self._field_widgets:
            return
        for char_id, widget in list(self._field_widgets.items()):
            card = self._field_character_card(char_id)
            if card is None:
                continue
            if not (
                self._is_elrohir_twin_card(card)
                or self._is_elladan_twin_card(card)
            ):
                continue
            owner_idx = self._char_owner.get(char_id, self._active_player_index)
            attachments_map = self._players[owner_idx].attachments
            self._apply_host_attachment_passives(
                card, widget, attachments_map
            )

    def _is_hero_card(self, card) -> bool:
        return (getattr(card, "type", "") or "").strip() == '英雄'

    def _attachment_restrict_clause(self, attachment_card) -> str:
        text = (getattr(attachment_card, "Text_Effect", "") or "")
        match = re.search(r'附属[到至]([^。\n]+)', text)
        return match.group(1).strip() if match else ""

    def _host_matches_attachment_restriction(self, attachment_card, host_card) -> bool:
        """附属进场限制（FAQ 1.23）：只检查卡牌文本，不比较宿主与附属派系。"""
        if self._is_dwarf_pipe_attachment(attachment_card):
            return self._is_dwarf_character_card(host_card)
        if self._is_rune_master_attachment(attachment_card):
            if self._character_effective_type(host_card) != "英雄":
                return False
            traits = self._player_race_and_traits_text(host_card)
            return (
                "游侠" in traits
                or "Ranger" in traits
                or any(
                    sphere in ("领导", "Leadership")
                    for sphere in self._hero_effective_spheres(host_card)
                )
            )
        if self._is_prince_of_dol_amroth_attachment(attachment_card):
            return self._is_prince_imrahil_character_card(host_card)
        if self._is_shadowfax_attachment(attachment_card):
            return self._is_gandalf_character_card(host_card)
        if self._is_herugrim_attachment(attachment_card):
            if self._character_effective_type(host_card) != "英雄":
                return False
            host_id = (getattr(host_card, "id", "") or "").strip()
            return self._character_has_rohan_trait(host_card, host_id or None)
        if self._is_golden_shield_attachment(attachment_card):
            if self._character_effective_type(host_card) != "英雄":
                return False
            host_id = (getattr(host_card, "id", "") or "").strip()
            return self._character_has_rohan_trait(host_card, host_id or None)
        if self._is_roheryn_attachment(attachment_card):
            if self._character_effective_type(host_card) != "英雄":
                return False
            return self._character_has_dunedain_trait(host_card)
        if self._is_livery_of_the_tower_attachment(attachment_card):
            if self._character_effective_type(host_card) != "英雄":
                return False
            host_id = (getattr(host_card, "id", "") or "").strip()
            return self._character_has_gondor_trait(host_card, host_id or None)
        if self._is_arod_attachment(attachment_card):
            if self._character_effective_type(host_card) == "英雄":
                return True
            return self._is_legolas_ally_card(host_card)
        if self._is_ring_mail_attachment(attachment_card):
            if self._character_effective_type(host_card) not in (
                '英雄', "盟友"
            ):
                return False
            return (
                self._is_dwarf_character_card(host_card)
                or self._character_has_hobbit_trait(host_card)
            )
        if self._is_ever_my_heart_rises_attachment(attachment_card):
            if self._character_effective_type(host_card) not in (
                '英雄', "盟友"
            ):
                return False
            return self._is_dwarf_character_card(host_card)
        if self._is_hardy_leadership_attachment(attachment_card):
            if self._character_effective_type(host_card) != '英雄':
                return False
            return any(
                s in ("领导", "Leadership")
                for s in self._hero_effective_spheres(host_card)
            )
        if self._is_silver_harp_attachment(attachment_card):
            if self._character_effective_type(host_card) != '英雄':
                return False
            return any(
                s in ("精神", "Spirit")
                for s in self._hero_effective_spheres(host_card)
            )
        if self._is_love_of_tales_attachment(attachment_card):
            if self._character_effective_type(host_card) != '英雄':
                return False
            return any(
                s in ('学识', "Lore")
                for s in self._hero_effective_spheres(host_card)
            )
        if self._is_healing_herbs_attachment(attachment_card):
            if self._character_effective_type(host_card) != '英雄':
                return False
            return any(
                s in ('学识', "Lore")
                for s in self._hero_effective_spheres(host_card)
            )
        if self._is_athelas_attachment(attachment_card):
            if self._character_effective_type(host_card) not in (
                '英雄', "盟友"
            ):
                return False
            return (
                self._character_has_dunedain_trait(host_card)
                or self._character_has_healer_trait(host_card)
            )
        if self._is_rivendell_bow_attachment(attachment_card):
            if self._is_aragorn_hero_card(host_card):
                return True
            trait_text = self._player_race_and_traits_text(host_card)
            return "诺多精灵" in trait_text or "西尔凡精灵" in trait_text
        if self._is_bow_of_the_galadhrim_attachment(attachment_card):
            if self._character_effective_type(host_card) not in ('英雄', "盟友"):
                return False
            trait_text = self._player_race_and_traits_text(host_card)
            if "西尔凡精灵" not in trait_text:
                return False
            host_id = (getattr(host_card, "id", "") or "").strip()
            if host_id:
                return self._character_has_ranged(host_id)
            return self._card_has_printed_ranged(host_card)
        if self._is_great_yew_bow_attachment(attachment_card):
            if not self._is_effectively_hero(host_card):
                return False
            return self._card_has_printed_ranged(host_card)
        if self._is_black_arrow_attachment(attachment_card):
            if not self._is_effectively_hero(host_card):
                return False
            return self._card_has_printed_ranged(host_card)
        if self._is_elven_mail_attachment(attachment_card):
            if self._character_effective_type(host_card) not in ('英雄', "盟友"):
                return False
            trait_text = self._player_race_and_traits_text(host_card)
            return "诺多精灵" in trait_text or "西尔凡精灵" in trait_text
        if self._is_windfola_attachment(attachment_card):
            if self._character_effective_type(host_card) != "英雄":
                return False
            return (
                self._is_eowyn_hero_card(host_card)
                or any(
                    sphere in ("学识", "Lore")
                    for sphere in self._hero_effective_spheres(host_card)
                )
            )
        if self._is_vigilant_guard_attachment(attachment_card):
            if self._character_effective_type(host_card) not in ('英雄', "盟友"):
                return False
            trait_text = self._player_race_and_traits_text(host_card)
            if "战士" not in trait_text and "Warrior" not in trait_text:
                return False
            host_id = (getattr(host_card, "id", "") or "").strip()
            return not any(
                self._is_vigilant_guard_attachment(att)
                for att in self._character_attachments(host_id)
            )
        if self._is_warden_of_arnor_attachment(attachment_card):
            if self._character_effective_type(host_card) != '英雄':
                return False
            trait_text = self._player_race_and_traits_text(host_card)
            return "斥候" in trait_text or "Scout" in trait_text
        if self._is_light_of_valinor_attachment(attachment_card):
            if self._character_effective_type(host_card) != '英雄':
                return False
            trait_text = self._player_race_and_traits_text(host_card)
            return "诺多精灵" in trait_text or "西尔凡精灵" in trait_text
        if self._is_to_the_sea_attachment(attachment_card):
            if self._character_effective_type(host_card) not in (
                '英雄', "盟友"
            ):
                return False
            trait_text = self._player_race_and_traits_text(host_card)
            return "诺多精灵" in trait_text or "Noldor" in trait_text
        if self._is_asfaloth_attachment(attachment_card):
            if self._character_effective_type(host_card) != '英雄':
                return False
            trait_text = self._player_race_and_traits_text(host_card)
            return "诺多精灵" in trait_text or "西尔凡精灵" in trait_text
        if self._is_the_road_darkens_gandalfs_staff_attachment(attachment_card):
            return self._is_gandalf_character_card(host_card)
        if self._is_the_road_darkens_wizard_pipe_attachment(attachment_card):
            return self._has_wizard_trait(host_card)
        if self._is_the_road_darkens_fellowship_of_ring_attachment(attachment_card):
            return self._is_ring_bearer_character(host_card)
        if self._is_vilya_attachment(attachment_card):
            return self._is_elrond_hero_card(host_card)
        if self._is_greeting_the_dawn_attachment(attachment_card):
            if self._character_effective_type(host_card) != '英雄':
                return False
            host_id = (getattr(host_card, "id", "") or "").strip()
            return bool(host_id) and self._character_has_vigilant(host_id)
        if self._is_narya_attachment(attachment_card):
            return (
                self._is_cirdan_shipwright_card(host_card)
                or self._is_gandalf_character_card(host_card)
            )
        if self._is_hobbit_pony_attachment(attachment_card):
            if self._character_effective_type(host_card) != '英雄':
                return False
            return self._character_has_hobbit_trait(host_card)
        if self._is_hobbit_cloak_attachment(attachment_card):
            if self._character_effective_type(host_card) != '英雄':
                return False
            return self._character_has_hobbit_trait(host_card)
        if self._is_hobbit_pipe_attachment(attachment_card):
            return self._character_has_hobbit_trait(host_card)
        if self._is_dagger_of_westernesse_attachment(attachment_card):
            return self._character_effective_type(host_card) == '英雄'
        if self._is_blood_of_numenor_attachment(attachment_card):
            if self._character_effective_type(host_card) != '英雄':
                return False
            host_id = (getattr(host_card, "id", "") or "").strip()
            return (
                self._character_has_gondor_trait(
                    host_card, host_id or None
                )
                or self._character_has_dunedain_trait(host_card)
            )
        if self._is_sword_of_numenor_attachment(attachment_card):
            if self._character_effective_type(host_card) != '英雄':
                return False
            host_id = (getattr(host_card, "id", "") or "").strip()
            return (
                self._character_has_gondor_trait(
                    host_card, host_id or None
                )
                or self._character_has_dunedain_trait(host_card)
            )
        if self._is_heir_of_valandil_attachment(attachment_card):
            if self._character_effective_type(host_card) != '英雄':
                return False
            return self._character_has_dunedain_trait(host_card)
        if self._is_heir_of_mardil_attachment(attachment_card):
            if self._character_effective_type(host_card) != '英雄':
                return False
            host_id = (getattr(host_card, "id", "") or "").strip()
            return self._character_has_noble_trait(host_card, host_id or None)
        if self._is_firefoot_attachment(attachment_card):
            if self._character_effective_type(host_card) != '英雄':
                return False
            host_id = (getattr(host_card, "id", "") or "").strip()
            has_tactics = any(
                s in ('战术', "Tactics")
                for s in self._hero_effective_spheres(host_card)
            )
            return (
                has_tactics
                or self._character_has_rohan_trait(host_card, host_id or None)
            )
        if self._is_gondorian_fire_attachment(attachment_card):
            if self._character_effective_type(host_card) != '英雄':
                return False
            host_id = (getattr(host_card, "id", "") or "").strip()
            return (
                self._character_has_gondor_trait(
                    host_card, host_id or None
                )
                or self._character_has_dunedain_trait(host_card)
            )
        if self._is_lord_of_morthond_attachment(attachment_card):
            if self._character_effective_type(host_card) != '英雄':
                return False
            host_id = (getattr(host_card, "id", "") or "").strip()
            return (
                self._character_has_gondor_trait(
                    host_card, host_id or None
                )
                or self._character_has_outlands_trait(
                    host_card, host_id or None
                )
            )
        if self._is_sword_of_morthond_attachment(attachment_card):
            if self._character_effective_type(host_card) != "盟友":
                return False
            host_id = (getattr(host_card, "id", "") or "").strip()
            return self._character_has_gondor_trait(
                host_card, host_id or None
            )
        if self._is_palantir_attachment(attachment_card):
            if self._character_effective_type(host_card) != '英雄':
                return False
            return self._character_has_noble_trait(host_card)
        if self._is_mariners_compass_attachment(attachment_card):
            if self._character_effective_type(host_card) not in (
                '英雄', "盟友"
            ):
                return False
            host_id = (getattr(host_card, "id", "") or "").strip()
            has_leadership = (
                any(
                    s in ("领导", "Leadership")
                    for s in self._hero_effective_spheres(host_card)
                )
                if self._character_effective_type(host_card) == '英雄'
                else _card_sphere(host_card) in ("领导", "Leadership")
            )
            trait_text = (
                self._player_race_and_traits_text(host_card)
                + self._card_trait_text(host_card)
            )
            return has_leadership or "斥候" in trait_text or "Scout" in trait_text
        if self._is_winged_helm_attachment(attachment_card):
            if self._character_effective_type(host_card) != '英雄':
                return False
            host_id = (getattr(host_card, "id", "") or "").strip()
            return self._character_has_vigilant(host_id)
        if self._is_weather_stained_cloak_attachment(attachment_card):
            trait_text = self._player_race_and_traits_text(host_card)
            return "游侠" in trait_text or "Ranger" in trait_text
        if self._is_ranger_spear_attachment(attachment_card):
            if self._character_effective_type(host_card) not in ('英雄', "盟友"):
                return False
            return self._character_has_ranger_trait(host_card)
        if self._is_sword_bearer_attachment(attachment_card):
            if self._character_effective_type(host_card) != "盟友":
                return False
            return _is_unique_card(host_card)
        if self._is_defender_of_the_west_attachment(attachment_card):
            if self._character_effective_type(host_card) != "盟友":
                return False
            if not _is_unique_card(host_card):
                return False
            return not self._is_objective_ally_type(host_card)
        clause = self._attachment_restrict_clause(attachment_card)
        if not clause:
            return True
        if any(k in clause for k in ('地区', '敌军', '敌人', "遭遇", "陷阱")):
            if not any(k in clause for k in ('英雄', "盟友", "角色")):
                return False
        host_type = self._character_effective_type(host_card)
        traits = self._card_trait_text(host_card)
        host_name = (getattr(host_card, "name", "") or "").strip()
        if '学识' in clause and '英雄' in clause:
            if host_type != '英雄':
                return False
            return any(
                s in ('学识', "Lore")
                for s in self._hero_effective_spheres(host_card)
            )
        if "盟友" in clause and '英雄' not in clause and "角色" not in clause:
            return host_type == "盟友"
        if self._is_born_aloft_attachment(attachment_card):
            return host_type == "盟友"
        trait_keywords = (
            "刚铎", "洛希尔", "诺多精灵", "西尔凡精灵", '哈比人', "巨鹰", '布理',
            '斥候', "登丹人", "杜内丹", "精灵", "矮人", '游侠', "医者", '战士',
            '谷地',
        )
        trait_in_clause = any(t in clause for t in trait_keywords)
        if (
            '英雄' in clause
            and "盟友" not in clause
            and "角色" not in clause
            and not trait_in_clause
        ):
            return host_type == '英雄'
        if '或' in clause:
            mentioned = [t for t in trait_keywords if t in clause]
            if len(mentioned) >= 2:
                return any(
                    t in traits or t in host_name for t in mentioned
                )
        for trait in trait_keywords:
            if trait in clause and trait not in traits and trait not in host_name:
                return False
        if (
            '英雄' in clause
            and "盟友" not in clause
            and "角色" not in clause
            and host_type != '英雄'
        ):
            return False
        host_canonical_name = CARD_NAME_ALIASES.get(host_name, "")
        for name_key in (
            "阿拉贡",
            "亚拉冈",
            "甘道夫",
            "加拉德瑞尔",
            "凯兰崔尔",
            "Galadriel",
        ):
            if (
                name_key in clause
                and name_key not in host_name
                and name_key not in host_canonical_name
            ):
                return False
        if '学识' in clause and "角色" in clause:
            if host_type not in ('英雄', "盟友"):
                return False
            if host_type == '英雄':
                if not any(
                    s in ('学识', "Lore")
                    for s in self._hero_effective_spheres(host_card)
                ):
                    return False
            elif _card_sphere(host_card) not in ('学识', "Lore"):
                return False
        if '战术' in clause and "角色" in clause:
            if host_type not in ('英雄', "盟友"):
                return False
            if host_type == '英雄':
                if not any(
                    s in ('战术', "Tactics")
                    for s in self._hero_effective_spheres(host_card)
                ):
                    return False
            elif _card_sphere(host_card) not in ('战术', "Tactics"):
                return False
        if '战术' in clause and '英雄' in clause:
            if host_type != '英雄':
                return False
            if not any(
                s in ('战术', "Tactics")
                for s in self._hero_effective_spheres(host_card)
            ):
                return False
        if "精神" in clause and '英雄' in clause:
            if host_type != '英雄':
                return False
            if not any(
                s in ("精神", "Spirit")
                for s in self._hero_effective_spheres(host_card)
            ):
                return False
        if "领导" in clause and '英雄' in clause:
            if host_type != '英雄':
                return False
            if not any(
                s in ("领导", "Leadership")
                for s in self._hero_effective_spheres(host_card)
            ):
                return False
        return True

    def _is_arod_attachment(self, card) -> bool:
        return self._card_octgn_base_id(card) == self.AROD_ATTACHMENT_OCTGN_BASE

    def _is_ent_draught_attachment(self, card) -> bool:
        return self._card_octgn_base_id(card) == self.ENT_DRAUGHT_ATTACHMENT_OCTGN_BASE

    def _player_controls_ent_character(self, player_index: int) -> bool:
        return any(
            self._is_treefolk_character_card(card)
            for char_id, _, card in self._characters_on_field()
            if self._character_owner_index(char_id) == player_index
        )

    def _valid_attachment_targets(self, attachment_card) -> list:
        """可附属的场上角色；宿主派系无需与附属牌派系一致。"""
        return [
            (char_id, label, host)
            for char_id, label, host in self._characters_on_field()
            if self._host_matches_attachment_restriction(attachment_card, host)
            and self._can_attach_to_character(
                attachment_card, char_id, host_card=host, warn=False
            )
        ]

    def _pick_character_attachment_target_for_play(
        self, attachment_card
    ) -> str | None:
        """选择角色附属目标；取消返回 None，校验失败返回 ""。"""
        targets = self._valid_attachment_targets(attachment_card)
        if not targets:
            clause = self._attachment_restrict_clause(attachment_card)
            hint = f"（限制：附属至{clause}）" if clause else ""
            self._warn(
                "附属",
                f"场上无{hint}。\n"
                '宿主派系无需与附属牌一致。',
            )
            return ""
        if self._is_galadriel_mirror_attachment(attachment_card) and len(targets) == 1:
            return targets[0][0]
        clause = self._attachment_restrict_clause(attachment_card)
        attach_prompt = None
        if clause:
            attach_prompt = (
                f"选择要附属的角色（限制：{clause}）：\n"
                + AttachTargetDialog._DEFAULT_CHARACTER_PROMPT.split(
                    "\n", 1
                )[1]
            )
        tdialog = AttachTargetDialog(
            self, targets, prompt=attach_prompt
        )
        if tdialog.exec_() != QDialog.Accepted:
            return None
        target_id = tdialog.selected_id()
        if not self._can_attach_to_character(attachment_card, target_id):
            return ""
        return target_id

    def _is_weapon_or_armor_attachment(self, card) -> bool:
        """附属是否具有武器或防具属性（贝瑞贡减量费判定）。"""
        if (getattr(card, "type", "") or "").strip() not in {"附属", "Attachment"}:
            return False
        traits = " ".join(
            str(part or "")
            for part in (
                self._card_trait_text(card),
                getattr(card, "Traits", ""),
                getattr(card, "traits", ""),
            )
        )
        return text_contains_any(traits, "武器", "防具", "Weapon", "Armor")

    def _has_beregond_weapon_armor_discount_passive(self, card) -> bool:
        """加波诺尔后贝瑞贡：武器/防具附属至其身上费用 -2。"""
        if (getattr(card, "type", "") or "").strip() != '英雄':
            return False
        text = (getattr(card, "Text_Effect", "") or "")
        return (
            "武器" in text
            and "防具" in text
            and '费用' in text
        )

    def _beregond_discount_may_apply_to_attachment(
        self, attachment_card
    ) -> bool:
        if not self._is_weapon_or_armor_attachment(attachment_card):
            return False
        return any(
            self._has_beregond_weapon_armor_discount_passive(host)  # type: ignore
            for _, __, host in self._valid_attachment_targets(  # type: ignore
                attachment_card
            )
        )

    def _beregond_attachment_play_discount(
        self, attachment_card, host_card
    ) -> int:
        if host_card is None:
            return 0
        if not self._is_weapon_or_armor_attachment(attachment_card):
            return 0
        if not self._has_beregond_weapon_armor_discount_passive(host_card):
            return 0
        return 2

    def _is_location_only_attachment(self, attachment_card) -> bool:
        clause = self._attachment_restrict_clause(attachment_card)
        if not clause or '地区' not in clause:
            return False
        return not any(k in clause for k in ('英雄', "盟友", "角色"))

    def _valid_location_attachment_targets(self, attachment_card) -> list:
        """可附属的场上地区（探查区 + 当前地区）。"""
        targets: list[tuple[str, str, object]] = []
        if self._is_elf_stone_attachment(attachment_card):
            if self.current_location_card is None:
                return []
            card = self.current_location_card
            if (
                self._is_lost_island_proxy(card)
                or self._drowned_ruins_shrine_to_morgoth_rejects_attachments(card)
            ):
                return []
            return [(card.id, f"当前地区 · {card.name}", card)]
        for card in self._staging_location_cards():
            if self._is_lost_island_proxy(card):
                continue
            if self._drowned_ruins_shrine_to_morgoth_rejects_attachments(card):
                continue
            targets.append((card.id, f"探查区 · 地区 · {card.name}", card))
        if self._is_explorers_almanac_attachment(attachment_card):
            return targets
        if self.current_location_card is not None:
            card = self.current_location_card
            if (
                not self._is_lost_island_proxy(card)
                and not self._drowned_ruins_shrine_to_morgoth_rejects_attachments(card)
            ):
                targets.append(
                    (card.id, f"当前地区 · {card.name}", card)
                )
        return targets

    def _is_long_defeat_attachment(self, card) -> bool:
        """检查卡牌是否为「长久的失败」附属。"""
        name = (getattr(card, "name", "") or "").strip()
        if name in self.LONG_DEFEAT_ATTACHMENT_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.LONG_DEFEAT_ATTACHMENT_NAMES:
            return True
        text = (getattr(card, "Text_Effect", "") or "")
        return (
            "附属" in text
            and "任务牌" in text
            and "限制" in text
            and "补" in text
            and "两张" in text
            and "治疗" in text
            and "5" in text
            and "伤害" in text
        )

    def _is_favor_of_the_valar_attachment(self, card) -> bool:
        """检查卡牌是否为「主神的眷顾 / 维拉的看重」附属。"""
        name = (getattr(card, "name", "") or "").strip()
        if name in self.FAVOR_OF_THE_VALAR_ATTACHMENT_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.FAVOR_OF_THE_VALAR_ATTACHMENT_NAMES:
            return True
        text = (getattr(card, "Text_Effect", "") or "")
        return (
            '附属' in text
            and '威胁指示板' in text
            and '强制' in text
            and '淘汰威胁等级' in text
            and '弃除' in text
            and '降低' in text
        )

    def _is_threat_dial_attachment(self, card) -> bool:
        """检查卡牌是否为附属到威胁指示板的附属。"""
        return self._is_favor_of_the_valar_attachment(card)

    _THREAT_DIAL_PLAYER_PREFIX = "__threat_dial_player_"

    def _threat_dial_player_index(self, target_id: str) -> int:
        """从威胁指示板目标 ID 中提取玩家索引。"""
        prefix = self._THREAT_DIAL_PLAYER_PREFIX
        if target_id.startswith(prefix):
            try:
                return int(target_id[len(prefix):])
            except (ValueError, IndexError):
                return 0
        return 0

    def _valid_threat_dial_attachment_targets(self, _) -> list:
        """可选玩家目标：每位玩家限制一张「主神的眷顾」。"""
        targets: list[tuple[str, str, object]] = []
        for player_idx in range(self.PLAYER_COUNT):
            if player_idx in self._eliminated_players:
                continue
            # 限制：每位玩家一张
            existing = self._player_threat_attachments.get(player_idx, [])
            already_has = any(
                self._is_favor_of_the_valar_attachment(att)
                for att in existing
            )
            if already_has:
                continue
            target_id = f"{self._THREAT_DIAL_PLAYER_PREFIX}{player_idx}"
            player_no = player_idx + 1
            targets.append((target_id, f"玩家 {player_no} 的威胁指示板", None))
        return targets

    def _is_quest_only_attachment(self, card) -> bool:
        """检查卡牌是否为仅能附属到任务牌的附属。"""
        return (
            self._is_long_defeat_attachment(card)
            or self._is_road_goes_ever_on_attachment(card)
        )

    _MAIN_QUEST_TARGET_ID = "__main_quest__"

    def _valid_quest_attachment_targets(self, attachment_card) -> list:
        """可附属的场上任务牌（主任务 + 探查区的支线任务）。"""
        targets: list[tuple[str, str, object]] = []
        is_long_defeat = self._is_long_defeat_attachment(attachment_card)
        # 主任务（始终在场）
        main_quest = self._current_main_quest_meta()
        if main_quest:
            main_name = main_quest.get("name") or "主任务"
            existing_atts = self._quest_attachments.get(
                self._MAIN_QUEST_TARGET_ID, []
            )
            # 每张任务牌限制一张
            if not is_long_defeat or not any(
                self._is_long_defeat_attachment(a) for a in existing_atts
            ):
                targets.append((
                    self._MAIN_QUEST_TARGET_ID,
                    f"主任务 · {main_name}",
                    None,
                ))
        # 探查区的玩家支线任务
        for card in self._player_side_quests_in_staging():
            cid = getattr(card, "id", "") or ""
            if not cid:
                continue
            existing_atts = self._quest_attachments.get(cid, [])
            if not is_long_defeat or not any(
                self._is_long_defeat_attachment(a) for a in existing_atts
            ):
                targets.append((cid, f"支线任务 · {card.name}", card))
        return targets

    def _try_long_defeat_quest_complete_response(self, quest_id: str) -> None:
        """长久的失败：附属任务牌被通过后，每位玩家补2张牌或治疗最多5点伤害。"""
        if quest_id not in self._quest_attachments:
            return
        long_defeat_cards = [
            att for att in self._quest_attachments.get(quest_id, [])
            if self._is_long_defeat_attachment(att)
        ]
        if not long_defeat_cards:
            return
        att_card = long_defeat_cards[0]
        if quest_id == self._MAIN_QUEST_TARGET_ID:
            quest_name = self._current_main_quest_meta().get("name", "主任务")
        else:
            quest_name = quest_id
            for sq in self._player_side_quests_in_staging():
                if getattr(sq, "id", "") == quest_id:
                    quest_name = sq.name
                    break
        title = f"响应 · {att_card.name}"
        # 先询问是否触发
        if (
            self._question(
                title,
                f"任务牌「{quest_name}」已被通过。\n"
                "是否触发「长久的失败」响应？\n"
                "每位玩家可选择：补2张卡牌，或治疗其所控制的角色上最多5点伤害。",
                default_yes=True,
            )
            != QMessageBox.Yes
        ):
            print(f"响应（{att_card.name}）：未触发")
            return
        for p_idx in range(len(self._players)):
            state = self._players[p_idx]
            tag = self._player_tag(p_idx) or f"玩家 {p_idx + 1}"
            # 玩家选择：补牌 或 治疗
            choice_title = f"{title} — {tag}"
            # 使用自定义对话框让玩家选择
            choice = self._question(
                choice_title,
                f"「{quest_name}」被通过！\n\n"
                f"{tag} 请选择：\n"
                "· 是 → 补2张卡牌\n"
                "· 否 → 治疗你所控制的角色上最多5点伤害",
                default_yes=True,
            )
            if choice == QMessageBox.Yes:
                drawn = self._draw_cards_for_player(p_idx, 2)
                if drawn:
                    names = '、'.join(c.name for c in drawn)
                    print(f"响应（{att_card.name}）：{tag} 补2张卡牌：{names}")
                    if p_idx == self._active_player_index:
                        self._refresh_hand_row(state.hand_cards)
            else:
                # 治疗：收集该玩家控制的受伤角色
                injured_chars = []
                for char_id, label, card in self._characters_on_field():
                    owner_idx = self._character_owner_index(char_id)
                    if owner_idx != p_idx:
                        continue
                    if not self._is_character_alive(char_id):
                        continue
                    dmg = self._character_damage_count(char_id)
                    if dmg > 0:
                        injured_chars.append((char_id, label, card, dmg))
                if not injured_chars:
                    self._inform(
                        choice_title,
                        f"{tag} 没有受伤的角色，改为补2张卡牌。",
                    )
                    drawn = self._draw_cards_for_player(p_idx, 2)
                    if drawn:
                        names = '、'.join(c.name for c in drawn)
                        print(
                            f"响应（{att_card.name}）："
                            f"{tag} 无伤可治，补2张卡牌：{names}"
                        )
                        if p_idx == self._active_player_index:
                            self._refresh_hand_row(state.hand_cards)
                    continue
                # 让玩家选择治疗分配
                heal_remaining = 5
                healed_any = False
                while heal_remaining > 0 and injured_chars:
                    options = "; ".join(
                        f"{label} (伤{dmg})" for _, label, _, dmg in injured_chars
                    )
                    amount, ok = QInputDialog.getInt(
                        self,
                        f"{choice_title} — 治疗（剩余 {heal_remaining} 点）",
                        f"选择要治疗的角色并输入治疗量：\n{options}\n"
                        f"剩余可治疗：{heal_remaining} 点",
                        value=min(heal_remaining, injured_chars[0][3]),
                        min=1,
                        max=heal_remaining,
                        step=1,
                    )
                    if not ok or amount <= 0:
                        break
                    if len(injured_chars) == 1:
                        target_char = injured_chars[0]
                    else:
                        options_list = [
                            CharacterPickOption(cid, label, card)
                            for cid, label, card, _ in injured_chars
                        ]
                        picked = self._pick_character_from_list(
                            options_list,
                            title=f"{choice_title} — 选择要治疗的角色",
                            prompt=f"剩余可治疗：{heal_remaining} 点\n选择角色并输入治疗量。",
                        )
                        if not picked:
                            break
                        target_char = next(
                            (c for c in injured_chars if c[0] == picked), None
                        )
                        if target_char is None:
                            break
                    cid, label, _, dmg = target_char
                    actual = min(amount, dmg)
                    self._heal_damage_from_character(cid, actual)
                    heal_remaining -= actual
                    healed_any = True
                    print(
                        f"响应（{att_card.name}）：{tag} 治疗 "
                        f"{label} {actual} 点伤害"
                    )
                    # 更新受伤角色列表
                    injured_chars = [
                        (c_id, l, c, self._character_damage_count(c_id))
                        for c_id, l, c, _ in injured_chars
                        if self._is_character_alive(c_id)
                        and self._character_damage_count(c_id) > 0
                    ]
                if not healed_any:
                    # 没治疗，改为补牌
                    drawn = self._draw_cards_for_player(p_idx, 2)
                    if drawn:
                        names = '、'.join(c.name for c in drawn)
                        print(
                            f"响应（{att_card.name}）："
                            f"{tag} 未选择治疗，补2张卡牌：{names}"
                        )
                        if p_idx == self._active_player_index:
                            self._refresh_hand_row(state.hand_cards)
        # 响应结算完毕，将长久的失败放入弃牌堆
        self._quest_attachments.pop(quest_id, None)
        # 将卡牌移到拥有者的弃牌堆
        for att in long_defeat_cards:
            owner_idx = self._char_owner.get(att.id, self._active_player_index)
            if 0 <= owner_idx < len(self._players):
                self._players[owner_idx].discard_cards.append(att)
                self._char_owner.pop(att.id, None)
        print(f"响应（{att_card.name}）：结算完毕，附属弃除。")

    def _is_wait_no_longer_event(self, card) -> bool:
        """识别“不再等待 / Wait no Longer”（17005）事件。"""
        if card is None or (getattr(card, "type", "") or "").strip() != "\u4e8b\u4ef6":
            return False
        card_code = str(
            getattr(card, "code", "")
            or getattr(card, "card_code", "")
            or getattr(card, "Code", "")
            or ""
        ).strip()
        if (
            card_code == self.WAIT_NO_LONGER_CODE
            or self._card_octgn_base_id(card).casefold()
            == self.WAIT_NO_LONGER_OCTGN_BASE
        ):
            return True
        name = (getattr(card, "name", "") or "").strip()
        return name in self.WAIT_NO_LONGER_EVENT_NAMES or (
            CARD_NAME_ALIASES.get(name, "") in self.WAIT_NO_LONGER_EVENT_NAMES
        )

    def _wait_no_longer_enemy_pick_options(
        self, cards: list
    ) -> list[CharacterPickOption]:
        options: list[CharacterPickOption] = []
        for card in cards:
            if (getattr(card, "type", "") or "").strip() != "\u654c\u4eba":
                continue
            if self._enemy_cannot_be_engaged(card):
                continue
            if self._is_immune_to_player_effects(card):
                continue
            card_id = getattr(card, "id", "") or getattr(card, "name", "")
            options.append(
                CharacterPickOption(
                    char_id=card_id,
                    label=f"{getattr(card, 'name', '')}（\u654c\u519b）",
                    image_path=getattr(card, "image_path", "") or "",
                    attack=self._card_attack(card),
                    defense=self._card_engagement(card),
                    health=self._enemy_printed_health(card),
                )
            )
        return options

    def _pick_wait_no_longer_enemy(self, cards: list):
        if not cards:
            return None
        if len(cards) == 1:
            return cards[0]
        options = self._wait_no_longer_enemy_pick_options(cards)
        if not options:
            return None
        dlg = CharacterImagePickDialog(
            self,
            "\u54cd\u5e94 · \u4e0d\u518d\u7b49\u5f85",
            "\u4ece\u906d\u9047\u724c\u7ec4\u9876\u7aef\u4e94\u5f20\u4e2d\u9009\u62e9\u4e00\u4e2a\u654c\u519b\u4e0e\u4f60\u4ea4\u950b：",
            options,
            mode="single",
            highlight_stat="health",
            mandatory=True,
        )
        if dlg.exec_() != QDialog.Accepted:
            return None
        picked_id = dlg.selected_id()
        return next(
            (
                card
                for card in cards
                if (getattr(card, "id", "") or getattr(card, "name", ""))
                == picked_id
            ),
            None,
        )

    def _apply_wait_no_longer_effect(self, player_index: int) -> str:
        """不再等待：搜寻遭遇牌顶五张中的敌军并与指定玩家交锋。"""
        self._quest_staging_reveal_reduction += 1
        self._quest_staging_reveal_minimum_zero = True
        drawer = getattr(self, "encounter_drawer", None)
        top_cards = list(getattr(drawer, "cards", [])[:5]) if drawer else []
        enemy_cards = [
            card
            for card in top_cards
            if (getattr(card, "type", "") or "").strip() == "\u654c\u4eba"
            and not self._enemy_cannot_be_engaged(card)
            and not self._is_immune_to_player_effects(card)
        ]
        chosen = self._pick_wait_no_longer_enemy(enemy_cards)
        engaged_name = "\u672a\u641c\u5230\u654c\u519b"
        if chosen is not None and drawer is not None:
            chosen_id = getattr(chosen, "id", "") or ""
            for index, card in enumerate(drawer.cards):
                card_id = getattr(card, "id", "") or ""
                if card is chosen or (chosen_id and card_id == chosen_id):
                    drawer.cards.pop(index)
                    break
            if self._perform_effect_engage(chosen, player_index):
                engaged_name = getattr(chosen, "name", "") or "\u654c\u519b"
            else:
                drawer.cards.append(chosen)
        if drawer is not None:
            drawer.shuffle_deck()
        reveal_count = self._quest_staging_reveal_count()
        note = (
            f"\u73a9\u5bb6 {player_index + 1}\uff1a\u4e0e\u300c{engaged_name}\u300d\u4ea4\u950b；"
            f"\u672c\u9636\u6bb5\u5c11\u5c55\u793a\u4e00\u5f20\u906d\u9047\u724c"
            f"（\u672c\u9636\u6bb5\u5c55\u793a {reveal_count} \u5f20，\u6700\u5c11\u4e3a\u96f6）；"
            "\u906d\u9047\u724c\u7ec4\u5df2\u6d17\u724c"
        )
        print(f"\u54cd\u5e94（\u4e0d\u518d\u7b49\u5f85）：{note}")
        self._inform("\u54cd\u5e94 · \u4e0d\u518d\u7b49\u5f85", note)
        return note

    def _offer_wait_no_longer_responses(self) -> None:
        """任务阶段开始时，按起始玩家顺序询问各玩家手中的不再等待。"""
        for offset in range(self.PLAYER_COUNT):
            player_idx = (self.first_player_index + offset) % self.PLAYER_COUNT
            if player_idx in self._eliminated_players:
                continue
            event_cards = [
                card
                for card in list(self._players[player_idx].hand_cards)
                if self._is_wait_no_longer_event(card)
            ]
            for event_card in event_cards:
                if event_card not in self._players[player_idx].hand_cards:
                    continue
                if not self._can_pay_for_hand_card(event_card, player_idx):
                    continue
                tag = self._player_tag(player_idx) or f"\u73a9\u5bb6 {player_idx + 1}"
                prev_active = self._active_player_index
                try:
                    if self.PLAYER_COUNT > 1:
                        self._set_active_player(player_idx)
                    if (
                        self._question(
                            f"\u54cd\u5e94 · {event_card.name}",
                            f"{tag}：\u662f\u5426\u6253\u51fa\u300a{event_card.name}\u300b？\n\n"
                            "\u4ece\u906d\u9047\u724c\u7ec4\u9876\u7aef\u4e94\u5f20\u4e2d\u641c\u5bfb\u4e00\u4e2a\u654c\u519b\u5e76\u4e0e\u4f60\u4ea4\u950b，"
                            "\u672c\u9636\u6bb5\u5c11\u5c55\u793a\u4e00\u5f20\u906d\u9047\u724c（\u6700\u5c11\u96f6\u5f20）。",
                            default_yes=False,
                        )
                        != QMessageBox.Yes
                    ):
                        continue
                    payment = self._pay_for_hand_card(
                        event_card,
                        title=f"\u54cd\u5e94 · {event_card.name}",
                        player_index=player_idx,
                    )
                    if payment is None:
                        continue
                    self._discard_event_from_hand(event_card, player_idx)
                    self._apply_wait_no_longer_effect(player_idx)
                finally:
                    if self.PLAYER_COUNT > 1:
                        self._set_active_player(prev_active)

    def _is_storm_comes_card(self, card) -> bool:
        """精确识别玩家支线任务「暴风将临 / The Storm Comes」。"""
        if card is None or not _is_player_side_quest_card(card):
            return False
        card_code = str(
            getattr(card, "code", "")
            or getattr(card, "card_code", "")
            or getattr(card, "Code", "")
            or ""
        ).strip()
        if (
            card_code == self.STORM_COMES_CODE
            or self._card_octgn_base_id(card) == self.STORM_COMES_OCTGN_BASE
        ):
            return True
        name = (getattr(card, "name", "") or "").strip()
        if name in self.STORM_COMES_NAMES:
            return True
        return CARD_NAME_ALIASES.get(name, "") in self.STORM_COMES_NAMES

    def _storm_comes_in_victory_display(self) -> bool:
        return any(
            self._is_storm_comes_card(card)
            for card in getattr(self, "_victory_display_cards", [])
        )

    def _storm_comes_first_ally_free_pending(
        self, card, player_index: int
    ) -> bool:
        if (getattr(card, "type", "") or "").strip() != "盟友":
            return False
        if not self._storm_comes_in_victory_display():
            return False
        used_players = getattr(
            self, "_storm_comes_first_ally_free_used_players", set()
        )
        return player_index not in used_players

    def _mark_storm_comes_first_ally_free_used(self, player_index: int) -> None:
        if self._storm_comes_in_victory_display():
            self._storm_comes_first_ally_free_used_players.add(player_index)

    def _is_road_goes_ever_on_attachment(self, card) -> bool:
        """识别「大路长啊长 / The Road Goes Ever On」任务附属。"""
        if card is None or (getattr(card, "type", "") or "").strip() != "附属":
            return False
        card_code = str(
            getattr(card, "code", "")
            or getattr(card, "card_code", "")
            or getattr(card, "Code", "")
            or ""
        ).strip()
        if (
            card_code == self.ROAD_GOES_EVER_ON_CODE
            or self._card_octgn_base_id(card)
            == self.ROAD_GOES_EVER_ON_OCTGN_BASE
        ):
            return True
        name = (getattr(card, "name", "") or "").strip()
        if name in self.ROAD_GOES_EVER_ON_ATTACHMENT_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        return canonical in self.ROAD_GOES_EVER_ON_ATTACHMENT_NAMES

    def _try_road_goes_ever_on_quest_complete_response(
        self, quest_id: str, quest_card=None
    ) -> None:
        """大路长啊长：任务通过后由起始玩家指定玩家搜寻一张分支任务。"""
        road_cards = [
            att
            for att in self._quest_attachments.get(quest_id, [])
            if self._is_road_goes_ever_on_attachment(att)
        ]
        if not road_cards:
            return

        if quest_card is not None:
            quest_name = getattr(quest_card, "name", "任务牌") or "任务牌"
        elif quest_id == self._MAIN_QUEST_TARGET_ID:
            quest_name = self._current_main_quest_meta().get("name", "主任务")
        else:
            quest_name = quest_id

        first_idx = self._starting_player_or_next_eligible()
        eligible_players = [
            idx
            for idx in range(self.PLAYER_COUNT)
            if idx not in self._eliminated_players
        ]
        for att_card in road_cards:
            title = f"响应 · {att_card.name}"
            if not eligible_players:
                print(f"响应（{att_card.name}）：没有可选择的玩家")
                continue

            if len(eligible_players) == 1:
                target_idx = eligible_players[0]
            else:
                player_options = [
                    (
                        str(idx),
                        self._player_tag(idx) or f"玩家 {idx + 1}",
                    )
                    for idx in eligible_players
                ]
                player_dlg = LargeChoiceDialog(
                    self,
                    title,
                    f"任务牌「{quest_name}」已被通过。\n"
                    f"起始玩家 {first_idx + 1}：选择一位玩家搜寻分支任务：",
                    player_options,
                    button_min_height=88,
                    font_size=22,
                )
                if player_dlg.exec_() != QDialog.Accepted:
                    print(f"响应（{att_card.name}）：未选择玩家")
                    continue
                try:
                    target_idx = int(player_dlg.selected_id())
                except (TypeError, ValueError):
                    print(f"响应（{att_card.name}）：未选择有效玩家")
                    continue
                if target_idx not in eligible_players:
                    print(f"响应（{att_card.name}）：未选择有效玩家")
                    continue

            drawer = self._player_drawer_for(target_idx)
            target_tag = self._player_tag(target_idx) or f"玩家 {target_idx + 1}"
            if drawer is None or not drawer.deck_stack:
                print(f"响应（{att_card.name}）：{target_tag} 牌库为空，跳过")
                continue
            if self._player_deck_search_blocked(target_idx):
                note = self._player_deck_search_blocked_note(
                    target_idx, source=att_card.name
                )
                print(f"响应（{att_card.name}）：{note}")
                self._inform(title, note)
                continue

            side_quests = [
                card
                for card in drawer.deck_stack
                if _is_player_side_quest_card(card)
            ]
            if not side_quests:
                print(f"响应（{att_card.name}）：{target_tag} 牌库中没有分支任务")
                self._inform(title, f"{target_tag} 的牌库中没有分支任务牌。")
                continue

            options = self._deck_peek_pick_options(side_quests)
            card_dlg = CharacterImagePickDialog(
                self,
                title,
                f"{target_tag}：从牌库中选择一张分支任务加入手牌：",
                options,
                mode="single",
                highlight_stat="defense",
            )
            if card_dlg.exec_() != QDialog.Accepted:
                print(f"响应（{att_card.name}）：{target_tag} 未选择分支任务")
                continue
            picked_id = card_dlg.selected_id()
            picked = next(
                (card for card in side_quests if card.id == picked_id), None
            )
            if picked is None:
                print(f"响应（{att_card.name}）：{target_tag} 未选择有效分支任务")
                continue
            drawer.deck_stack.remove(picked)
            random.shuffle(drawer.deck_stack)
            self._players[target_idx].hand_cards.append(picked)
            if target_idx == self._active_player_index:
                self._refresh_hand_row(self._players[target_idx].hand_cards)
            print(
                f"响应（{att_card.name}）：起始玩家指定 {target_tag}，"
                f"「{picked.name}」加入手牌，牌库已洗牌"
            )
            self._inform(
                title,
                f"起始玩家指定 {target_tag}；「{picked.name}」已加入手牌，牌库已洗牌。",
            )

        self._quest_attachments.pop(quest_id, None)
        for att in road_cards:
            owner_idx = self._char_owner.get(att.id, self._active_player_index)
            if 0 <= owner_idx < len(self._players):
                self._players[owner_idx].discard_cards.append(att)
                self._char_owner.pop(att.id, None)
        print(f"响应（{road_cards[0].name}）：结算完毕，附属弃除。")

    def _is_explorers_almanac_attachment(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != "附属":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.EXPLORERS_ALMANAC_ATTACHMENT_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.EXPLORERS_ALMANAC_ATTACHMENT_NAMES:
            return True
        text = (getattr(card, "Text_Effect", "") or "")
        return (
            "场景区" in text
            and "地区" in text
            and "任务成功" in text
            and "当前任务" in text
            and "进度" in text
        )

    def _is_forest_snare_attachment(self, card) -> bool:
        name = (getattr(card, "name", "") or "").strip()
        if name in self.FOREST_SNARE_ATTACHMENT_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        return canonical in self.FOREST_SNARE_ATTACHMENT_NAMES

    def _is_ranger_spikes_attachment(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != "附属":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.RANGER_SPIKES_ATTACHMENT_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.RANGER_SPIKES_ATTACHMENT_NAMES:
            return True
        text = (getattr(card, "Text_Effect", "") or "")
        return (
            "未附属" in text
            and "场景区" in text
            and '下一张' in text
            and ("交锋鉴定" in text or '交锋鉴定' in text)
            and ("-2" in text or "）" in text or '威胁' in text)
        )

    def _is_trap_attachment(self, card) -> bool:
        """印刷【陷阱】属性的附属（含森林罗网等陷阱类）。"""
        if (getattr(card, "type", "") or "").strip() != "附属":
            return False
        if "陷阱" in self._card_trait_text(card):
            return True
        return (
            self._is_forest_snare_attachment(card)
            or self._is_ranger_spikes_attachment(card)
            or self._is_ithilien_pit_attachment(card)
            or self._is_poisoned_stakes_attachment(card)
        )

    def _is_poisoned_stakes_attachment(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != "附属":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.POISONED_STAKES_ATTACHMENT_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.POISONED_STAKES_ATTACHMENT_NAMES:
            return True
        text = (getattr(card, "Text_Effect", "") or "")
        return (
            "未附属" in text
            and "场景区" in text
            and "回合结束" in text
            and "伤害" in text
            and ("2" in text or '中' in text)
            and "交锋鉴定" not in text
            and '交锋鉴定' not in text
        )

    def _enemy_has_trap_attachment(self, enemy_id: str) -> bool:
        for att in self._enemy_attachments.get(enemy_id, []):
            if self._is_trap_attachment(att):
                return True
        return False

    GOBLIN_TROOP_OCTGN_BASE = "ec9f4d54-a419-4b3e-a6b9-605119950d07"

    def _is_goblin_troop_card(self, card) -> bool:
        return card is not None and (
            self._card_octgn_base_id(card).strip().casefold() == self.GOBLIN_TROOP_OCTGN_BASE
            or (getattr(card, "name", "") or "").strip() in {"Goblin Troop", "地精冲锋队"}
        )

    def _is_goblin_enemy_card(self, card) -> bool:
        text = " ".join(str(getattr(card, key, "") or "") for key in ("traits", "Traits", "sphere", "name"))
        return "Goblin" in text or "地精" in text

    def _goblin_troop_engagement_bonus(self, card) -> int:
        if card is None or self._is_goblin_troop_card(card) or not self._is_goblin_enemy_card(card):
            return 0
        owner = self._engaged_enemy_owner_index(card)
        if owner is None or int(owner) < 0:
            return 0
        if any(self._is_goblin_troop_card(enemy) for enemy in self._player_engagement(owner)):
            return 1
        return 0

    def _enemy_cannot_receive_player_attachments(self, enemy_card, attachment_card=None) -> bool:
        if self._is_cold_drake_card(enemy_card):
            return True
        if self._is_king_of_the_dead_card(enemy_card):
            return True
        if self._is_snow_troll_card(enemy_card):
            return True
        if self._is_fire_drake_card(enemy_card):
            return attachment_card is None or not self._is_dragon_enemy_card(attachment_card)
        if self._is_goblin_troop_card(enemy_card):
            return True
        if self._is_stone_troll_card(enemy_card):
            return True
        if self._is_sahirs_escort_enemy(enemy_card) or self._is_corsair_skiff_card(enemy_card) or self._is_swift_raider_enemy(enemy_card) or enemy_card is self._stormcaller_area_card:
            return True
        return (
            self._is_old_stone_troll_card(enemy_card)
            or self._is_lurker_of_the_depths_card(enemy_card)
            or self._is_nameless_cave_dweller_card(enemy_card)
            or self._is_scouting_ship_enemy(enemy_card)
            or self._is_corsair_warship_card(enemy_card)
            or self._is_light_cruiser_card(enemy_card)
            or self._is_slave_ship_card(enemy_card)
        )

    def _is_staging_unattached_trap_attachment(self, card) -> bool:
        """尖兵刺桩、伊西立安陷阱、剧毒木桩等：以未附属状态置于探查区，再自动附属敌军。"""
        return (
            self._is_ranger_spikes_attachment(card)
            or self._is_ithilien_pit_attachment(card)
            or self._is_poisoned_stakes_attachment(card)
        )

    def _is_ithilien_pit_attachment(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != "附属":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.ITHILIEN_PIT_ATTACHMENT_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.ITHILIEN_PIT_ATTACHMENT_NAMES:
            return True
        text = (getattr(card, "Text_Effect", "") or "")
        return (
            "未附属" in text
            and "场景区" in text
            and '下一张' in text
            and '攻击' in text
            and "附属" in text
            and "交锋鉴定" not in text
            and '交锋鉴定' not in text
        )

    def _enemy_has_ithilien_pit_attached(self, enemy_card) -> bool:
        enemy_id = getattr(enemy_card, "id", "") or ""
        if not enemy_id:
            return False
        return any(
            self._is_ithilien_pit_attachment(att)
            for att in self._enemy_attachments.get(enemy_id, [])
        )

    def _eligible_enemy_for_ithilien_pit(self, enemy_card) -> bool:
        if (getattr(enemy_card, "type", "") or "").strip() != '敌人':
            return False
        if enemy_card.id in self._destroyed_enemies:
            return False
        if self._is_immune_to_player_effects(enemy_card):
            return False
        if self._enemy_cannot_receive_player_attachments(enemy_card):
            return False
        return True

    def _try_ithilien_pit_auto_attach(self, enemy_card) -> list[str]:
        """伊西立安陷阱：未附属时自动附属到下一张进入探查区的合理敌军。"""
        notes: list[str] = []
        if not self._eligible_enemy_for_ithilien_pit(enemy_card):
            return notes
        pending = [
            att
            for att in self._staging_unattached_attachments
            if self._is_ithilien_pit_attachment(att)
        ]
        if not pending:
            return notes
        att_card = pending[0]
        self._staging_unattached_attachments.remove(att_card)
        self._enemy_attachments.setdefault(enemy_card.id, []).append(att_card)
        self._refresh_staging_row()
        note = (
            f"  「{att_card.name}」附属至「{enemy_card.name}」。"
            "（任意角色可宣告攻击该敌军）"
        )
        notes.append(note)
        print(note.strip())
        return notes

    def _ranger_spikes_threat_modifier(self, enemy_card) -> int:
        modifier = 0
        for att in self._enemy_attachments.get(enemy_card.id, []):
            if self._is_ranger_spikes_attachment(att):
                modifier -= self.RANGER_SPIKES_THREAT_REDUCTION
        return modifier

    def _eligible_enemy_for_ranger_spikes(self, enemy_card) -> bool:
        if (getattr(enemy_card, "type", "") or "").strip() != '敌人':
            return False
        if enemy_card.id in self._destroyed_enemies:
            return False
        if self._is_immune_to_player_effects(enemy_card):
            return False
        if self._enemy_cannot_receive_player_attachments(enemy_card):
            return False
        return True

    def _try_ranger_spikes_auto_attach(self, enemy_card) -> list[str]:
        """尖兵刺桩：未附属时自动附属到下一张进入探查区的合理敌军。"""
        notes: list[str] = []
        if not self._eligible_enemy_for_ranger_spikes(enemy_card):
            return notes
        pending = [
            att
            for att in self._staging_unattached_attachments
            if self._is_ranger_spikes_attachment(att)
        ]
        if not pending:
            return notes
        att_card = pending[0]
        self._staging_unattached_attachments.remove(att_card)
        self._enemy_attachments.setdefault(enemy_card.id, []).append(att_card)
        self._ranger_spikes_skip_engage_ids.add(enemy_card.id)
        self._apply_staging_passive_threat(enemy_card)
        self._update_quest_dial_badges()
        note = (
            f"  「{att_card.name}」附属至「{enemy_card.name}」。"
            f"（-{self.RANGER_SPIKES_THREAT_REDUCTION} 威胁，"
            "玩家与其不进行交锋鉴定）"
        )
        notes.append(note)
        print(note.strip())
        return notes

    def _eligible_enemy_for_poisoned_stakes(self, enemy_card) -> bool:
        if (getattr(enemy_card, "type", "") or "").strip() != '敌人':
            return False
        if enemy_card.id in self._destroyed_enemies:
            return False
        if self._is_immune_to_player_effects(enemy_card):
            return False
        if self._enemy_cannot_receive_player_attachments(enemy_card):
            return False
        return True

    def _try_poisoned_stakes_auto_attach(self, enemy_card) -> list[str]:
        """剧毒木桩：未附属时自动附属到下一张进入探查区的合理敌军。"""
        notes: list[str] = []
        if not self._eligible_enemy_for_poisoned_stakes(enemy_card):
            return notes
        pending = [
            att
            for att in self._staging_unattached_attachments
            if self._is_poisoned_stakes_attachment(att)
        ]
        if not pending:
            return notes
        att_card = pending[0]
        self._staging_unattached_attachments.remove(att_card)
        self._enemy_attachments.setdefault(enemy_card.id, []).append(att_card)
        self._refresh_staging_row()
        note = (
            f"  「{att_card.name}」附属至「{enemy_card.name}」。"
            f"（每回合结束对其造成 {self.POISONED_STAKES_ROUND_DAMAGE} 点伤害）"
        )
        notes.append(note)
        print(note.strip())
        return notes

    def _staging_unattached_trap_play_note(self, card) -> str:
        """未附属探查区陷阱打出说明。"""
        base = (
            f"「{card.name}」以未附属状态置于探查区。"
            '将自动附属到下一张进入探查区的合理敌军'
        )
        if self._is_ithilien_pit_attachment(card):
            return base + "（任意角色可宣告攻击该敌军）。"
        if self._is_poisoned_stakes_attachment(card):
            return (
                base
                + f"（每回合结束对所附属敌军造成"
                f" {self.POISONED_STAKES_ROUND_DAMAGE} 点伤害）："
            )
        return (
            base
            + f"（-{self.RANGER_SPIKES_THREAT_REDUCTION} 威胁，"
            "玩家与其不进行交锋鉴定）。"
        )

    def _resolve_poisoned_stakes_round_end_damage(self) -> None:
        """0.1 回合结束：对所有附属剧毒木桩的敌军造成伤害。"""
        notes: list[str] = []
        seen: set[str] = set()
        for enemy in self._enemies_in_play():
            if enemy.id in seen:
                continue
            stakes_count = sum(
                1
                for att in self._enemy_attachments.get(enemy.id, [])
                if self._is_poisoned_stakes_attachment(att)
            )
            if stakes_count <= 0:
                continue
            seen.add(enemy.id)
            damage = self.POISONED_STAKES_ROUND_DAMAGE * stakes_count
            if self._is_immune_to_player_effects(enemy):
                notes.append(
                    f"「{enemy.name}」免疫玩家卡牌效果，"
                    f"剧毒木桩未造成 {damage} 点伤害"
                )
                continue
            destroyed = self._deal_damage_to_enemy(enemy, damage)
            if destroyed:
                notes.append(f"「{enemy.name}」受 {damage} 点伤害被消灭")
            else:
                notes.append(f"「{enemy.name}」受 {damage} 点伤害")
        if not notes:
            return
        detail = "\n".join(notes)
        print(f"0.1 回合结束 · 剧毒木桩：{'、'.join(notes)}")
        self._inform(
            '剧毒木桩',
            "本回合结束，结算剧毒木桩伤害：\n\n" + detail,
        )

    def _is_ancient_mathom_attachment(self, card) -> bool:
        """检测是否为「古老的马松」附属。"""
        if (getattr(card, "type", "") or "").strip() != "附属":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.ANCIENT_MATHOM_ATTACHMENT_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        return canonical in self.ANCIENT_MATHOM_ATTACHMENT_NAMES

    def _is_elf_stone_attachment(self, card) -> bool:
        """精灵宝石：附属到激活地区；地区 +1 任务点；探索离场后放置盟友。"""
        if (getattr(card, "type", "") or "").strip() != "附属":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.ELF_STONE_ATTACHMENT_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.ELF_STONE_ATTACHMENT_NAMES:
            return True
        text = (getattr(card, "Text_Effect", "") or "")
        compact = text.replace(" ", "")
        lower = compact.lower()
        return (
            ("附属到激活的地区" in compact or "attachtotheactivelocation." in lower)
            and ("任务点" in text or "quest point" in lower)
            and ("盟友" in text or "ally" in lower)
        )

    def _has_elf_stone_explored_response(self, card) -> bool:
        if not self._is_elf_stone_attachment(card):
            return False
        text = (getattr(card, "Text_Effect", "") or "")
        lower = text.lower()
        return (
            (
                '响应' in text
                and '已探索地区离场后' in text
                and '起始玩家' in text
                and '盟友' in text
            )
            or (
                'response' in lower
                and 'leaves play as an explored location' in lower
                and 'first player' in lower
                and 'ally' in lower
            )
        )

    def _has_ancient_mathom_explored_response(self, card) -> bool:
        """古老的马松：所附属地区探索完毕后，起始玩家抽 3 张牌："""
        return self._is_ancient_mathom_attachment(card)

    def _is_path_of_need_attachment(self, card) -> bool:
        """检测是否为「必选之路」附属。"""
        if (getattr(card, "type", "") or "").strip() != "附属":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.PATH_OF_NEED_ATTACHMENT_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.PATH_OF_NEED_ATTACHMENT_NAMES:
            return True
        text = (getattr(card, "Text_Effect", "") or "")
        return (
            "附属" in text
            and '地区' in text
            and ("无需横置" in text or "无须横置" in text)
            and '英雄' in text
            and ('攻击' in text or "防御" in text)
            and ('指派' in text or "执行任务" in text)
        )

    def _path_of_need_active(self) -> bool:
        """必选之路：附属地区为当前激活地区时生效。"""
        loc = self.current_location_card
        if loc is None:
            return False
        attachments = self._location_attachments.get(loc.id, [])
        return any(
            self._is_path_of_need_attachment(att) for att in attachments
        )

    def _hero_skips_exhaust_for_path_of_need(self, char_id: str) -> bool:
        if not self._path_of_need_active():
            return False
        if not self._is_hero_on_field(char_id):
            return False
        return self._character_card_by_id(char_id) is not None

    def _is_light_of_valinor_attachment(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != "附属":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.LIGHT_OF_VALINOR_ATTACHMENT_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.LIGHT_OF_VALINOR_ATTACHMENT_NAMES:
            return True
        text = (getattr(card, "Text_Effect", "") or "")
        return (
            "附属" in text
            and ("诺多精灵" in text or "西尔凡精灵" in text)
            and '英雄' in text
            and ("无需横置" in text or "无须横置" in text)
            and ('指派' in text or "执行任务" in text)
        )

    def _hero_skips_exhaust_for_light_of_valinor(self, char_id: str) -> bool:
        if not self._is_hero_on_field(char_id):
            return False
        return any(
            self._is_light_of_valinor_attachment(att)
            for att in self._character_attachments(char_id)
        )

    def _is_halbarad_hero_card(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != '英雄':
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.HALBARAD_HERO_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        return canonical in self.HALBARAD_HERO_NAMES

    def _is_amarthiul_hero_card(self, card) -> bool:
        """检查卡牌是否为阿玛希尔英雄。"""
        if (getattr(card, "type", "") or "").strip() != '英雄':
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.AMARTHIUL_HERO_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.AMARTHIUL_HERO_NAMES:
            return True
        text = (getattr(card, "Text_Effect", "") or "")
        return (
            "阿玛希尔" in text
            and "登丹人" in text
            and "战术" in text
            and "资源符号" in text
        )

    def _amarthiul_hero_for_player(self, player_index: int):
        """查找指定玩家控制的阿玛希尔英雄。"""
        if player_index < 0 or player_index >= len(self._players):
            return None
        drawer = self._player_drawer_for(player_index)
        if drawer is None:
            return None
        for hero in drawer.deck_heroes:
            if self._is_amarthiul_hero_card(hero):
                return hero
        return None

    def _amarthiul_engaged_enemy_count(self, player_index: int) -> int:
        """阿玛希尔控制玩家交锋敌军数量。"""
        return len(self._engaged_enemies_for_player(player_index))

    def _amarthiul_has_tactics_sphere_passive(self, char_id: str) -> bool:
        """阿玛希尔：交锋1+时获得战术资源符号。"""
        card = self._character_card_by_id(char_id)
        if card is None or not self._is_amarthiul_hero_card(card):
            return False
        owner_idx = self._character_owner_index(char_id)
        if owner_idx < 0:
            return False
        return self._amarthiul_engaged_enemy_count(owner_idx) >= 1

    def _amarthiul_extra_resource_passive_active(self, player_index: int) -> bool:
        """阿玛希尔：交锋2+时资源阶段额外+1资源。"""
        hero = self._amarthiul_hero_for_player(player_index)
        if hero is None:
            return False
        return self._amarthiul_engaged_enemy_count(player_index) >= 2

    def _hero_skips_exhaust_for_halbarad(self, char_id: str) -> bool:
        if not self._is_hero_on_field(char_id):
            return False
        card = self._character_card_by_id(char_id)
        if card is None or not self._is_halbarad_hero_card(card):
            return False
        owner_idx = self._character_owner_index(char_id)
        if owner_idx < 0:
            return False
        return bool(self._engaged_enemies_for_player(owner_idx))

    def _player_controls_halbarad_hero(self, player_index: int) -> bool:
        drawer = self._player_drawer_for(player_index)
        if drawer is None:
            return False
        for hero in drawer.deck_heroes:
            if not self._is_halbarad_hero_card(hero):
                continue
            if not self._is_hero_on_field(hero.id):
                continue
            if not self._is_character_alive(hero.id):
                continue
            if self._character_owner_index(hero.id) == player_index:
                return True
        return False

    def _resolve_ancient_mathom_explored_responses(self, location_card) -> list[str]:
        """古老的马松 · 响应：所附属地区探索完毕后，起始玩家抽 3 张牌。"""
        loc_id = location_card.id
        attachments = list(self._location_attachments.get(loc_id, []))
        mathoms = [
            att
            for att in attachments
            if self._has_ancient_mathom_explored_response(att)
        ]
        if not mathoms:
            return []

        loc_name = location_card.name
        lines = [
            f"响应 ·「{loc_name}」探索完毕："
            "「古老的马松」可选触发（起始玩家抽 3 张牌）"
        ]
        triggered = 0
        for offset in range(self.PLAYER_COUNT):
            player_idx = (self.first_player_index + offset) % self.PLAYER_COUNT
            if player_idx in self._eliminated_players:
                continue
            player_mathoms = [
                att
                for att in mathoms
                if self._char_owner.get(att.id, self._active_player_index)
                == player_idx
            ]
            for att in player_mathoms:
                player_no = player_idx + 1
                title = f"响应 · {att.name}"
                if (
                    self._question(
                        title,
                        f"玩家 {player_no}：「{att.name}」响应？\n\n"
                        f"附属地区「{loc_name}」已探索完毕，"
                        "起始玩家抽 3 张卡牌。",
                        default_yes=False,
                    )
                    != QMessageBox.Yes
                ):
                    print(f"响应（{att.name}）玩家 {player_no}：未触发")
                    lines.append(f"  玩家 {player_no}「{att.name}」：未触发")
                    continue
                first_idx = self.first_player_index
                first_no = first_idx + 1
                drawn = self._draw_cards_for_player(first_idx, 3)
                names = (
                    '、'.join(c.name for c in drawn)
                    if drawn
                    else self._format_player_draw_empty_reason()
                )
                note = (
                    f"  玩家 {player_no} 触发「{att.name}」→ "
                    f"起始玩家 {first_no} 抽 {len(drawn)} 张 → {names}"
                )
                lines.append(note)
                print(
                    f"响应：{att.name}）：起始玩家 {first_no} "
                    f"抽 {len(drawn)} 张 → {names}"
                )
                triggered += 1
        if triggered <= 0:
            return []
        return lines

    def _resolve_elf_stone_explored_responses(self, location_card) -> list[str]:
        """精灵宝石：所附属地区作为已探索地区离场后，起始玩家放置一名盟友进场。"""
        loc_id = location_card.id
        attachments = list(self._location_attachments.get(loc_id, []))
        elf_stones = [
            att
            for att in attachments
            if self._has_elf_stone_explored_response(att)
        ]
        if not elf_stones:
            return []

        first_idx = self._loot_control_player_index()
        first_no = first_idx + 1
        loc_name = location_card.name
        lines = [
            f"响应 ·「{loc_name}」探索完毕："
            f"「精灵宝石」可选触发（起始玩家 {first_no} 可从手牌放置 1 名盟友进场）"
        ]
        triggered = 0
        for offset in range(self.PLAYER_COUNT):
            player_idx = (self.first_player_index + offset) % self.PLAYER_COUNT
            if player_idx in self._eliminated_players:
                continue
            player_stones = [
                att
                for att in elf_stones
                if self._char_owner.get(att.id, self._active_player_index) == player_idx
            ]
            for att in player_stones:
                att_name = getattr(att, "name", "") or "精灵宝石"
                title = f"响应 · {att_name}"
                controller_no = player_idx + 1
                prev_active = self._active_player_index
                try:
                    if self.PLAYER_COUNT > 1:
                        self._set_active_player(first_idx)
                    has_valid_ally = bool(self._puttable_ally_cards_in_hand())
                finally:
                    if self.PLAYER_COUNT > 1:
                        self._set_active_player(prev_active)
                if not has_valid_ally:
                    print(
                        f"响应（{att_name}）玩家 {controller_no}："
                        f"起始玩家 {first_no} 手牌中没有可放置进场的盟友"
                    )
                    continue
                if (
                    self._question(
                        title,
                        f"玩家 {controller_no}：「{att_name}」响应？\n\n"
                        f"附属地区「{loc_name}」作为已探索地区离场后，"
                        f"起始玩家 {first_no} 将一名盟友从手牌中放置进场。",
                        default_yes=False,
                    )
                    != QMessageBox.Yes
                ):
                    print(f"响应（{att_name}）玩家 {controller_no}：未触发")
                    continue
                placed_name = ""
                placed = False
                try:
                    if self.PLAYER_COUNT > 1:
                        self._set_active_player(first_idx)
                    picked = self._pick_puttable_ally_from_hand(
                        title,
                        f"起始玩家 {first_no}：选择一名盟友从手牌放置进场：",
                    )
                    if picked is not None:
                        placed_name = getattr(picked, "name", "") or "盟友"
                        placed = self._put_ally_from_hand_into_play(picked)
                finally:
                    if self.PLAYER_COUNT > 1:
                        self._set_active_player(prev_active)
                if not placed:
                    print(f"响应（{att_name}）玩家 {controller_no}：未放置盟友进场")
                    continue
                lines.append(
                    f"  玩家 {controller_no} 触发「{att_name}」→ "
                    f"起始玩家 {first_no} 将「{placed_name}」从手牌放置进场"
                )
                print(
                    f"响应：{att_name}：起始玩家 {first_no} "
                    f"将「{placed_name}」从手牌放置进场"
                )
                triggered += 1
        if triggered <= 0:
            return []
        return lines

    def _is_ever_my_heart_rises_attachment(self, card) -> bool:
        if (getattr(card, "type", "") or "").strip() != "附属":
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.EVER_MY_HEART_RISES_ATTACHMENT_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.EVER_MY_HEART_RISES_ATTACHMENT_NAMES:
            return True
        text = (getattr(card, "Text_Effect", "") or "")
        return (
            "矮人" in text
            and '响应' in text
            and "山脉" in text
            and '地底' in text
            and '威胁' in text
        )

    def _location_is_mountain_or_underground(self, card) -> bool:
        return self._location_has_any_trait(
            card, '地底', "山脉", "Underground", "Mountain", "Mountains",
        )

    def _resolve_ever_my_heart_rises_explored_responses(
        self, location_card
    ) -> list[str]:
        """心情转好 · 响应：探索完山脉/地底地区后，重置宿主并威胁 -1。"""
        if not self._location_is_mountain_or_underground(location_card):
            return []
        loc_name = location_card.name
        candidates: list[tuple[str, object, object, int]] = []
        for char_id, _, host in self._characters_on_field():
            if not self._is_character_alive(char_id):
                continue
            if not self._is_dwarf_character_card(host):
                continue
            owner_idx = self._char_owner.get(char_id, self._active_player_index)
            for att in self._character_attachments(char_id):
                if att.id in self._facedown_attachment_ids:
                    continue
                if not self._is_ever_my_heart_rises_attachment(att):
                    continue
                candidates.append((char_id, host, att, owner_idx))
        if not candidates:
            return []

        lines = [
            f"响应 · 「{loc_name}」探索完毕（山脉/地底）："
            "「心情转好」"
        ]
        triggered = 0
        for char_id, host, att, owner_idx in candidates:
            if owner_idx in self._eliminated_players:
                continue
            player_no = owner_idx + 1
            host_name = host.name
            att_name = att.name
            title = f"响应 · {att_name}"
            prev_active = self._active_player_index
            try:
                if self.PLAYER_COUNT > 1:
                    self._set_active_player(owner_idx)
                if (
                    self._question(
                        title,
                        f"玩家 {player_no}：「{att_name}」响应？\n\n"
                        f"山脉/地底地区「{loc_name}」已探索完毕：\n"
                        f"重整「{host_name}」；你的威胁等级下降 1 点。",
                        default_yes=False,
                    )
                    != QMessageBox.Yes
                ):
                    print(f"响应（{att_name}）玩家 {player_no}：未触发")
                    lines.append(f"  玩家 {player_no}「{att_name}」：未触发")
                    continue
                if not self._is_character_alive(char_id):
                    print(
                        f"响应：{att_name}）：玩家 {player_no}："
                        '宿主已不在场'
                    )
                    lines.append(
                        f"  玩家 {player_no}「{att_name}」：宿主已不在场"
                    )
                    continue
                widget = self._field_widgets.get(char_id)
                was_exhausted = (
                    widget.is_exhausted() if widget is not None else False
                )
                if was_exhausted:
                    self._set_host_exhausted(char_id, False)
                    self._refresh_dain_ironfoot_aura_passives()
                reduced = self._lower_threat(1, player_index=owner_idx)
                threat_now = self._player_threat(owner_idx)
                ready_note = (
                    f"重整「{host_name}」。"
                    if was_exhausted
                    else f"「{host_name}」已为重整状态"
                )
                note = (
                    f"  玩家 {player_no} 触发「{att_name}」→ "
                    f"{ready_note}；威胁 -{reduced}（现为 {threat_now}）"
                )
                lines.append(note)
                print(
                    f"响应：{att_name}）：玩家 {player_no}："
                    f"{ready_note}；威胁 -{reduced}（现为 {threat_now}）"
                )
                triggered += 1
            finally:
                if self.PLAYER_COUNT > 1:
                    self._set_active_player(prev_active)
        if triggered <= 0:
            return []
        return lines

    def _is_enemy_only_attachment(self, attachment_card) -> bool:
        """附属至交锋敌军（如森林罗网）："""
        if self._is_forest_snare_attachment(attachment_card):
            return True
        clause = self._attachment_restrict_clause(attachment_card)
        if not clause:
            return False
        if '地区' in clause:
            return False
        if any(k in clause for k in ('英雄', "盟友", "角色")):
            return False
        return '敌军' in clause or '敌人' in clause

    def _valid_enemy_attachment_targets(
        self, attachment_card
    ) -> list[tuple[str, str, object]]:
        """可附属的交锋敌军（过滤免疫于玩家卡牌效果，秘密监视每名敌军限 1 张）。"""
        is_secret_vigil = self._is_secret_vigil_attachment(attachment_card)
        seen: set[str] = set()
        targets: list[tuple[str, str, object]] = []
        for player_idx in range(self.PLAYER_COUNT):
            if player_idx in self._eliminated_players:
                continue
            tag = self._player_tag(player_idx) or f"玩家{player_idx + 1}"
            for card in self._engaged_enemies_for_player(player_idx):
                if card.id in seen:
                    continue
                if self._is_immune_to_player_effects(card):
                    continue
                if self._enemy_cannot_receive_player_attachments(card, attachment_card):
                    continue
                if is_secret_vigil:
                    existing = self._enemy_attachments.get(card.id, [])
                    if any(
                        self._is_secret_vigil_attachment(att)
                        for att in existing
                    ):
                        continue
                seen.add(card.id)
                targets.append(
                    (
                        card.id,
                        f"{tag} · 敌人 · {card.name}",
                        card,
                    )
                )
        return targets

    def _attachment_target_pick_options(
        self, targets: list[tuple[str, str, object]], *, kind: str = "character"
    ) -> list[CharacterPickOption]:
        """附属目标：由 (id, 标记, card) 转为卡图选项。"""
        options: list[CharacterPickOption] = []
        for char_id, label, card in targets:
            if kind == "location":
                widget = self._staging_host_widget_for_card(card)
                threat = self._card_threat_value(card, widget)
                options.append(
                    CharacterPickOption(
                        char_id=char_id,
                        label=label,
                        image_path=getattr(card, "image_path", "") or "",
                        attack=threat,
                        defense=self._location_progress_required(card),
                        health=self._location_placed_progress(card),
                    )
                )
                continue
            if kind == "enemy":
                widget = self._encounter_widget_for_card(card)
                health = (
                    int(widget.get_card_info().get("health", 0))
                    if widget
                    else 0
                )
                if health <= 0:
                    row = load_encounter_row_by_name(
                        card.name, series=self._encounter_series()
                    )
                    if row:
                        health = _parse_threat(row.get('生命值', ""))
                player_tag = label.split(" 路 ", 1)[0] if " 路 " in label else ""
                options.append(
                    CharacterPickOption(
                        char_id=char_id,
                        label=label,
                        image_path=getattr(card, "image_path", "") or "",
                        attack=self._card_attack(card),
                        defense=self._card_engagement(card),
                        health=health,
                        player_tag=player_tag,
                    )
                )
                continue
            widget = self._field_widgets.get(char_id)
            atk = def_val = health = 0
            if widget is not None:
                info = widget.get_card_info()
                atk = int(info.get("attack", 0))
                def_val = int(info.get("defense", 0))
                health = int(info.get("health", 0))
            options.append(
                CharacterPickOption(
                    char_id=char_id,
                    label=label,
                    image_path=getattr(card, "image_path", "") or "",
                    attack=atk,
                    defense=def_val,
                    health=health,
                )
            )
        return options

    def _attachment_blocks_enemy_attack(self, attachment_card) -> bool:
        if self._is_forest_snare_attachment(attachment_card):
            return True
        text = (getattr(attachment_card, "Text_Effect", "") or "")
        return '不能攻击' in text or '无法攻击' in text

    def _discard_enemy_attachments(self, enemy_id: str, source_card=None):
        attachments = self._enemy_attachments.pop(enemy_id, [])
        self._ranger_spikes_skip_engage_ids.discard(enemy_id)
        if not attachments:
            return
        if source_card is None:
            source_card = self._enemy_card_by_id(enemy_id)
        source_name = getattr(source_card, "name", "") or enemy_id
        for att in attachments:
            if getattr(att, "_hobgoblin_guarded", False):
                owner = int(getattr(att, "_hobgoblin_owner", self._active_player_index))
                if 0 <= owner < self.PLAYER_COUNT:
                    self._players[owner].hand_cards.append(att)
                    self._char_owner.pop(getattr(att, "id", ""), None)
                    if owner == self._active_player_index:
                        self._refresh_hand_row(self.hand_cards)
                    print(
                        f"  强制 · 敌军「{source_name}」离场："
                        f"「{getattr(att, 'name', '牌组顶牌')}」返回玩家 {owner + 1} 手牌。"
                    )
                continue
            owner = self._char_owner.get(att.id, self._active_player_index)
            if self._is_carried_away_3b_quest_active() and self._is_carried_away_guarded_hero(att):
                print(
                    f"  强制 · 敌军「{source_name}」被消灭："
                    f"{self._return_carried_away_hero_from_enemy(att)}。"
                )
                continue
            if att.id in self._guarded_objective_attachment_ids:
                self._guarded_objective_attachment_ids.discard(att.id)
                self._char_owner.pop(att.id, None)
                if self._is_haldan_hero_card(att):
                    print(f"  {self._release_haldan_guarded_objective(att, source_name)}")
                elif self._is_discover_loot_target(att):
                    print(f"  {self._gain_guarded_loot_control(att, source_name)}")
                elif self._is_urdugs_horn(att):
                    owner = self._starting_player_or_next_eligible()
                    if self._resolve_urdug_horn_attachment(
                        att, owner if owner is not None else self._active_player_index
                    ):
                        print(f"  守护解除：{att.name}自动附属到场上的昂杜格。")
                    else:
                        print(f"  {self._release_guarded_objective_to_staging(att, source_name)}")
                else:
                    print(f"  {self._release_guarded_objective_to_staging(att, source_name)}")
            else:
                clear_marker_state_for_card(att)
                self._players[owner].discard_cards.append(att)
                if owner == self._active_player_index:
                    self._refresh_discard_pile()

    def _is_condition_attachment(self, card) -> bool:
        """附属是否具有「状态」/ Condition 关键词。"""
        if self._is_caught_in_web_attachment(card):
            return True
        if self._is_taking_on_water_attachment(card):
            return True
        if (getattr(card, "type", "") or "").strip() != "附属":
            return False
        traits = self._card_trait_text(card)
        if "状态" in traits:
            return True
        row = load_player_row_by_name(
            getattr(card, "name", ""),
            series=getattr(card, "series", None),
        )
        if row and "状态" in (row.get("属性") or ""):
            return True
        text = (getattr(card, "Text_Effect", "") or "")
        return bool(re.search(r"(?:^|\s)Condition\.", text, re.I | re.M))

    def _condition_attachment_pick_options(self) -> list[CharacterPickOption]:
        """场上所有状态附属（角色与地区）。"""
        options: list[CharacterPickOption] = []
        for char_id, host_label, _ in self._characters_on_field():
            owner_idx = self._character_owner_index(char_id)
            for att in self._players[owner_idx].attachments.get(char_id, []):
                if not self._is_condition_attachment(att):
                    continue
                options.append(
                    CharacterPickOption(
                        char_id=att.id,
                        label=f"{att.name} →{host_label}",
                        image_path=getattr(att, "image_path", "") or "",
                        attack=0,
                        defense=0,
                        health=0,
                    )
                )
        for loc_id, atts in self._location_attachments.items():
            loc_card = self._resolve_location_card(loc_id)
            if loc_card is None:
                loc_label = loc_id
            elif (
                self.current_location_card is not None
                and loc_card.id == self.current_location_card.id
            ):
                loc_label = f"当前地区 · {loc_card.name}"
            else:
                loc_label = f"探查区 · {loc_card.name}"
            for att in atts:
                if not self._is_condition_attachment(att):
                    continue
                options.append(
                    CharacterPickOption(
                        char_id=att.id,
                        label=f"{att.name} →{loc_label}",
                        image_path=getattr(att, "image_path", "") or "",
                        attack=0,
                        defense=0,
                        health=0,
                    )
                )
        return options

    def _is_caught_in_web_card(self, card) -> bool:
        name = (getattr(card, "name", "") or "").strip()
        if name in self.CAUGHT_IN_WEB_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.CAUGHT_IN_WEB_NAMES:
            return True
        return "Caught in a Web" in name

    def _is_caught_in_web_attachment(self, card) -> bool:
        return self._is_caught_in_web_card(card)

    def _is_taking_on_water_attachment(self, card) -> bool:
        return self._is_taking_on_water_card(card)

    def _discard_attachment_to_pile(self, att, owner_player_idx: int):
        """将场上附属移入对应弃牌堆（身份限制放置→遭遇弃牌堆）。"""
        self._facedown_attachment_ids.discard(getattr(att, "id", "") or "")
        if not isinstance(att, PlayerCard):
            self._char_owner.pop(att.id, None)
            clear_encounter_marker_state_for_card(att)
            self._to_encounter_discard_pile(att)
            self._refresh_encounter_discard_pile()
            return
        clear_marker_state_for_card(att)
        self._char_owner.pop(att.id, owner_player_idx)
        if self._remove_encounter_keyword_player_card_from_game(
            att, owner_player_idx, reason="离场"
        ):
            return
        if self._is_caught_in_web_attachment(att):
            self._to_encounter_discard_pile(att)
            self._refresh_encounter_discard_pile()
        else:
            self._players[owner_player_idx].discard_cards.append(att)
            if owner_player_idx == self._active_player_index:
                self._refresh_discard_pile()

    def _discard_attachment_from_play(self, att_id: str) -> bool:
        """将场上附属弃入其所属弃牌堆。"""
        for loc_id, atts in list(self._location_attachments.items()):
            for att in list(atts):
                if att.id != att_id:
                    continue
                if self._is_entangled_enemy(att):
                    return False
                atts.remove(att)
                if not atts:
                    self._location_attachments.pop(loc_id, None)
                self._sync_location_attachment_passives(loc_id)
                owner = self._char_owner.get(att.id, self._active_player_index)
                if self._resolve_urdug_horn_attachment(att, owner):
                    self._refresh_staging_row()
                    return True
                self._discard_attachment_to_pile(att, owner)
                self._resolve_haldan_attachment_control(loc_id)
                self._refresh_staging_row()
                self._refresh_engagement_row()
                return True
        for player_idx in range(self.PLAYER_COUNT):
            attachments = self._players[player_idx].attachments
            for host_id, atts in list(attachments.items()):
                for att in list(atts):
                    if att.id != att_id:
                        continue
                    atts.remove(att)
                    if not atts:
                        attachments.pop(host_id, None)
                    owner = self._char_owner.get(att.id, player_idx)
                    # 佩剑侍从：取消盟友的英雄提升
                    if self._is_sword_bearer_attachment(att):
                        self._promoted_ally_ids.discard(host_id)
                    if self._resolve_durins_key_attachment_control(att, owner):
                        self._refresh_host_group(host_id)
                        return True
                    self._discard_attachment_to_pile(att, owner)
                    self._resolve_haldan_attachment_control(host_id)
                    self._refresh_host_group(host_id)
                    return True
        for player_idx, atts in list(self._player_threat_attachments.items()):
            for att in list(atts):
                if att.id != att_id:
                    continue
                atts.remove(att)
                if not atts:
                    self._player_threat_attachments.pop(player_idx, None)
                owner = self._char_owner.get(att.id, player_idx)
                if self._resolve_urdug_horn_attachment(att, owner):
                    self._refresh_host_group(host_id)
                    return True
                self._discard_attachment_to_pile(att, owner)
                self._resolve_haldan_attachment_control(
                    f"{self._THREAT_DIAL_PLAYER_PREFIX}{player_idx}"
                )
                return True
        return False

    def _detach_attachment_from_play(
        self, att_id: str
    ) -> tuple[object | None, int, str | None]:
        """将场上附属移出（不进弃牌堆）。返回 (卡牌, 所属玩家索引, 宿主 id)。"""
        for loc_id, atts in list(self._location_attachments.items()):
            for att in list(atts):
                if att.id != att_id:
                    continue
                atts.remove(att)
                if not atts:
                    self._location_attachments.pop(loc_id, None)
                self._sync_location_attachment_passives(loc_id)
                owner = self._char_owner.get(att.id, self._active_player_index)
                if self._resolve_urdug_horn_attachment(att, owner):
                    return None, -1, None
                self._refresh_staging_row()
                self._refresh_engagement_row()
                self._resolve_haldan_attachment_control(loc_id)
                return att, owner, loc_id
        for player_idx in range(self.PLAYER_COUNT):
            attachments = self._players[player_idx].attachments
            for host_id, atts in list(attachments.items()):
                for att in list(atts):
                    if att.id != att_id:
                        continue
                    atts.remove(att)
                    if not atts:
                        attachments.pop(host_id, None)
                    owner = self._char_owner.get(att.id, player_idx)
                    self._refresh_host_group(host_id)
                    if self._resolve_urdug_horn_attachment(att, owner):
                        return None, -1, None
                    if self._resolve_durins_key_attachment_control(att, owner):
                        return None, -1, None
                    self._resolve_haldan_attachment_control(host_id)
                    return att, owner, host_id
        for player_idx, atts in list(self._player_threat_attachments.items()):
            for att in list(atts):
                if att.id != att_id:
                    continue
                atts.remove(att)
                if not atts:
                    self._player_threat_attachments.pop(player_idx, None)
                owner = self._char_owner.get(att.id, player_idx)
                host_id = f"{self._THREAT_DIAL_PLAYER_PREFIX}{player_idx}"
                self._resolve_haldan_attachment_control(host_id)
                return att, owner, host_id
        return None, -1, None

    def _is_ladys_favor_attachment(self, card) -> bool:
        if self._is_miruvor_attachment(card):
            return False
        name = (getattr(card, "name", "") or "").strip()
        if name in self.LADYS_FAVOR_ATTACHMENT_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.LADYS_FAVOR_ATTACHMENT_NAMES:
            return True
        text = (getattr(card, "Text_Effect", "") or "")
        normalized = text.replace("，", "+")
        if (
            "附属" not in text
            or '英雄' not in text
            or '意志力' not in text
            or "+1" not in normalized
        ):
            return False
        # 仅永久意志 +1；排除带行动/选择/时限/弃除等非常驻效果
        for keyword in (
            '行动', '选择', '直到', '至本回合', "至本阶段", "本回合", "本阶段", "弃除",
        ):
            if keyword in text:
                return False
        return True

    def _is_dark_knowledge_attachment(self, card) -> bool:
        name = (getattr(card, "name", "") or "").strip()
        if name in self.DARK_KNOWLEDGE_ATTACHMENT_NAMES:
            return True
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical in self.DARK_KNOWLEDGE_ATTACHMENT_NAMES:
            return True
        text = (getattr(card, "Text