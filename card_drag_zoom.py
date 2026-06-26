"""长按后上下拖动：实时浮动放大预览，松手达阈值时打开放大对话框。"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt5.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QLabel, QWidget

class CardDragZoomController(QObject):
    """卡牌长按 + 纵向拖动的放大手势（与横向滑动互不干扰）。"""

    LONG_PRESS_MS = 300
    DRAG_THRESHOLD = 12
    DIALOG_SCALE = 1.55
    MAX_SCALE = 2.2
    SCALE_PER_PX = 1.0 / 120.0

    def __init__(
        self,
        widget: QWidget,
        zoom_callback: Callable[[], None],
        pixmap_getter: Callable[[QWidget], Optional[QPixmap]] | None = None,
    ):
        super().__init__(widget)
        self._widget = widget
        self._zoom_callback = zoom_callback
        self._pixmap_getter = pixmap_getter or self._default_pixmap_getter
        self._targets: set[QWidget] = set()
        self._press_timer = QTimer(self)
        self._press_timer.setSingleShot(True)
        self._press_timer.timeout.connect(self._on_long_press_ready)
        self._press_global: QPoint | None = None
        self._long_press_ready = False
        self._drag_active = False
        self._vertical_dominant = False
        self._current_scale = 1.0
        self._suppress_click = False
        self._preview: QLabel | None = None

    @staticmethod
    def _default_pixmap_getter(widget: QWidget) -> Optional[QPixmap]:
        pixmap = getattr(widget, "current_pixmap", None)
        if pixmap is not None and not pixmap.isNull():
            return pixmap
        original = getattr(widget, "original_pixmap", None)
        if original is not None and not original.isNull():
            return original
        return None

    def install(self, *extra_targets: QWidget) -> None:
        """在宿主及卡图子控件上监听鼠标事件。"""
        self._targets = {self._widget, *extra_targets}
        for target in self._targets:
            target.installEventFilter(self)

    def is_drag_active(self) -> bool:
        return self._drag_active

    def suppress_click(self) -> bool:
        return self._suppress_click

    def clear_suppress_click(self) -> None:
        self._suppress_click = False

    def _on_long_press_ready(self) -> None:
        self._long_press_ready = True

    def _pixmap(self) -> Optional[QPixmap]:
        return self._pixmap_getter(self._widget)

    def _scale_from_dy(self, dy: int) -> float:
        return min(self.MAX_SCALE, 1.0 + abs(dy) * self.SCALE_PER_PX)

    def _ensure_preview(self) -> QLabel | None:
        pixmap = self._pixmap()
        if pixmap is None or pixmap.isNull():
            return None
        if self._preview is None:
            self._preview = QLabel(None, Qt.ToolTip | Qt.FramelessWindowHint)
            self._preview.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self._preview.setStyleSheet(
                "background: rgba(0, 0, 0, 180); border: 2px solid #888;"
                " border-radius: 6px;"
            )
        return self._preview

    def _update_preview(self, global_pos: QPoint, scale: float) -> None:
        preview = self._ensure_preview()
        pixmap = self._pixmap()
        if preview is None or pixmap is None:
            return
        base_w = max(1, self._widget.width())
        base_h = max(1, self._widget.height())
        target_w = max(base_w, int(base_w * scale))
        target_h = max(base_h, int(base_h * scale))
        scaled = pixmap.scaled(
            target_w,
            target_h,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        preview.setPixmap(scaled)
        preview.resize(scaled.size())
        preview.move(
            global_pos.x() - preview.width() // 2,
            global_pos.y() - preview.height() // 2,
        )
        if not preview.isVisible():
            preview.show()

    def _hide_preview(self) -> None:
        if self._preview is not None:
            self._preview.hide()

    def _release_mouse_grab(self) -> None:
        try:
            if QWidget.mouseGrabber() is self._widget:
                self._widget.releaseMouse()
        except RuntimeError:
            pass

    def _reset(self) -> None:
        self._press_timer.stop()
        self._press_global = None
        self._long_press_ready = False
        self._drag_active = False
        self._vertical_dominant = False
        self._current_scale = 1.0
        self._hide_preview()
        self._release_mouse_grab()

    def _watches(self, watched: QObject) -> bool:
        return watched in self._targets

    def _zoom_open(self) -> bool:
        zoom = getattr(self._widget, "zoom_dialog", None)
        return zoom is not None and zoom.isVisible()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if not self._watches(watched):
            return False
        et = event.type()
        if et == QEvent.MouseButtonPress and event.button() == Qt.RightButton:
            if (
                self._drag_active
                or self._zoom_open()
                or (self._preview is not None and self._preview.isVisible())
            ):
                event.accept()
                return True
        if et == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            if self._pixmap() is None:
                return False
            self._reset()
            self._press_global = event.globalPos()
            self._press_timer.start(self.LONG_PRESS_MS)
            return False
        if et == QEvent.MouseMove and self._press_global is not None:
            if not self._long_press_ready:
                return False
            delta = event.globalPos() - self._press_global
            dx, dy = delta.x(), delta.y()
            if abs(dx) < self.DRAG_THRESHOLD and abs(dy) < self.DRAG_THRESHOLD:
                return False
            if abs(dy) <= abs(dx):
                return False
            if not self._drag_active:
                self._widget.grabMouse()
            self._vertical_dominant = True
            self._drag_active = True
            self._current_scale = self._scale_from_dy(dy)
            self._update_preview(event.globalPos(), self._current_scale)
            event.accept()
            return True
        if et == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            opened_dialog = False
            if self._press_global is not None:
                if self._drag_active:
                    self._suppress_click = True
                    if self._current_scale >= self.DIALOG_SCALE:
                        self._zoom_callback()
                        opened_dialog = True
                was_vertical = self._vertical_dominant
                self._reset()
                if was_vertical or opened_dialog:
                    event.accept()
                    return True
            return False
        return False
