import csv
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Optional, List, Dict

from PyQt5.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QMenu, QPushButton, QVBoxLayout, QWidget,
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QPixmap

from card_drag_zoom import CardDragZoomController

_PROJECT_ROOT = Path(__file__).resolve().parent
ENCOUNTER_CSV = _PROJECT_ROOT / "魔戒遭遇.csv"
SCENE_IMAGE_DIR = _PROJECT_ROOT / "cards" / "场景"
# 准备卡牌列中的指令标记（以 @ 开头，非具体卡名）
SETUP_REVEAL_PER_PLAYER = "@每位玩家翻遭遇牌库顶"


def resolve_scene_image(image_id: str) -> Optional[Path]:
    """根据 CSV 图片链接解析 cards/场景/ 下的图片文件。"""
    image_id = (image_id or "").strip()
    if not image_id:
        return None
    for ext in (".jpg", ".jpeg", ".png", ".JPG"):
        path = SCENE_IMAGE_DIR / f"{image_id}{ext}"
        if path.is_file():
            return path
    return None


def load_quest_scenes_from_csv(
    series: str = "穿越黑森林",
    numbers=None,
    card_type: str = "探险",
    csv_path: Optional[Path] = None,
):
    """
    从魔戒遭遇.csv 读取指定系列、类型、编号的探险任务。
    返回按编号排序的列表，每项含 number, name, image_id, path, target。
    """
    if numbers is None:
        numbers = range(1, 8)
    number_set = {int(n) for n in numbers}
    csv_path = csv_path or ENCOUNTER_CSV
    if not csv_path.is_file():
        return []

    rows = []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("系列") or "").strip() != series:
                continue
            if (row.get("类型") or "").strip() != card_type:
                continue
            try:
                num = int((row.get("编号") or "").strip())
            except ValueError:
                continue
            if num not in number_set:
                continue

            image_id = (row.get("图片链接") or "").strip()
            img_path = resolve_scene_image(image_id)
            progress_raw = (row.get("探险进度") or "").strip()
            target = int(progress_raw) if progress_raw.isdigit() else 0
            face = (row.get("探险编号") or "").strip()
            setup_raw = (row.get("准备卡牌") or "").strip()
            setup_parts = [
                part.strip()
                for part in setup_raw.split("|")
                if part.strip()
            ]
            setup_reveal_per_player = SETUP_REVEAL_PER_PLAYER in setup_parts
            setup_cards = tuple(
                part for part in setup_parts
                if not part.startswith("@")
            )
            rows.append({
                "number": num,
                "name": (row.get("卡牌名称") or "").strip(),
                "face": face,
                "image_id": image_id,
                "path": str(img_path) if img_path else None,
                "target": target,
                "setup_cards": setup_cards,
                "setup_reveal_per_player": setup_reveal_per_player,
            })

    rows.sort(key=lambda r: r["number"])
    return rows


O8D_QUEST_SECTION = "Quest"


def _build_quest_index_by_image_id(csv_path: Optional[Path] = None) -> Dict[str, Dict[str, str]]:
    """按图片链接索引所有探险任务行。"""
    csv_path = csv_path or ENCOUNTER_CSV
    index: Dict[str, Dict[str, str]] = {}
    if not csv_path.is_file():
        return index
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("类型") or "").strip() != "探险":
                continue
            image_id = (row.get("图片链接") or "").strip()
            if image_id:
                index[image_id] = row
    return index


def load_quest_scenes_from_o8d(
    o8d_path: str | Path,
    csv_path: Optional[Path] = None,
) -> List[Dict]:
    """
    从 o8d 的 Quest 节按顺序加载探险任务（对应 场景.py 任务链）。
    进度目标取自 CSV「探险进度」。
    """
    o8d_path = Path(o8d_path)
    csv_path = csv_path or ENCOUNTER_CSV
    try:
        root = ET.parse(o8d_path).getroot()
    except ET.ParseError:
        return []

    quest_section = None
    for section in root.findall("section"):
        if (section.get("name") or "").strip() == O8D_QUEST_SECTION:
            quest_section = section
            break
    if quest_section is None:
        return []

    index = _build_quest_index_by_image_id(csv_path)
    quests: List[Dict] = []
    missing: List[str] = []

    for order, card_el in enumerate(quest_section.findall("card"), 1):
        image_id = (card_el.get("id") or "").strip()
        if not image_id:
            continue
        row = index.get(image_id)
        label = (card_el.text or "").strip() or image_id
        if not row:
            missing.append(label)
            continue

        img_path = resolve_scene_image(image_id)
        progress_raw = (row.get("探险进度") or "").strip()
        target = int(progress_raw) if progress_raw.isdigit() else 0
        try:
            number = int((row.get("编号") or "").strip())
        except ValueError:
            number = order

        face = (row.get("探险编号") or "").strip()
        setup_raw = (row.get("准备卡牌") or "").strip()
        setup_parts = [
            part.strip()
            for part in setup_raw.split("|")
            if part.strip()
        ]
        setup_reveal_per_player = SETUP_REVEAL_PER_PLAYER in setup_parts
        setup_cards = tuple(
            part for part in setup_parts
            if not part.startswith("@")
        )
        quests.append({
            "number": number,
            "name": (row.get("卡牌名称") or "").strip() or label,
            "face": face,
            "image_id": image_id,
            "path": str(img_path) if img_path else None,
            "target": target,
            "setup_cards": setup_cards,
            "setup_reveal_per_player": setup_reveal_per_player,
        })

    if missing:
        print(f"o8d Quest 节未在 CSV 匹配的探险卡: {', '.join(missing)}")
    return quests


class ImageZoomDialog(QDialog):
    def __init__(self, pixmap, parent=None, title: str = "任务图片放大显示 - 单击关闭"):
        super().__init__(parent)
        self.original_pixmap = pixmap
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setStyleSheet("background: white")
        self.setContextMenuPolicy(Qt.NoContextMenu)

        scaled_pixmap = pixmap.scaled(600, 800, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setFixedSize(scaled_pixmap.width() + 20, scaled_pixmap.height() + 20)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setPixmap(scaled_pixmap)
        self.image_label.setContextMenuPolicy(Qt.NoContextMenu)
        layout.addWidget(self.image_label)

    def contextMenuEvent(self, event):
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        super().keyPressEvent(event)


# 场景卡图源尺寸比例（宽 × 高）
SCENE_CARD_W = 1750
SCENE_CARD_H = 1250


class ClickableTaskLabel(QLabel):
    double_clicked = pyqtSignal()
    right_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.original_pixmap = None
        self.zoom_dialog = None
        self._right_menu_timer = QTimer(self)
        self._right_menu_timer.setSingleShot(True)
        self._right_menu_timer.timeout.connect(self.right_clicked.emit)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("border: none; background: transparent;")
        self._drag_zoom = CardDragZoomController(self, self.show_zoomed_image)
        self._drag_zoom.install()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if getattr(self, "_drag_zoom", None) and self._drag_zoom.suppress_click():
                self._drag_zoom.clear_suppress_click()
                event.accept()
                super().mouseReleaseEvent(event)
                return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.RightButton:
            self._right_menu_timer.stop()
            if self.original_pixmap and not self.original_pixmap.isNull():
                self.show_zoomed_image()
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            if not (self.original_pixmap and not self.original_pixmap.isNull()):
                self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            if self.zoom_dialog is not None and self.zoom_dialog.isVisible():
                event.accept()
                return
            self._right_menu_timer.start(QApplication.doubleClickInterval())
        super().mousePressEvent(event)

    def show_zoomed_image(self):
        self._right_menu_timer.stop()
        if self.zoom_dialog:
            self.zoom_dialog.close()
        self.zoom_dialog = ImageZoomDialog(self.original_pixmap, self)
        self.zoom_dialog.show()

    def set_image(self, pixmap):
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


class 任务(QWidget):
    task_advanced = pyqtSignal()
    progress_changed = pyqtSignal(int)
    quest_stage_completed = pyqtSignal(str)

    def __init__(self, config_path="scene_config.json", parent=None):
        super().__init__(parent)
        self.config_path = config_path
        self.task_index = 0
        self.task_paths = []
        self.progress_count = 0
        self.progress_pixmap = None
        self.target_progress = 0
        self.task_requirements = {}
        self.quest_meta = []
        self.debug_mode = False
        self._quest_progress_cap_fn: Callable[[], Optional[int]] | None = None
        self._quest_completion_gate: Callable[[], bool] | None = None
        self.init_ui()
        self.load_config()
        self.load_progress_token()

    def init_ui(self):
        card_w = 140
        card_h = round(card_w * SCENE_CARD_H / SCENE_CARD_W)
        bar_h = 26
        self.setFixedSize(card_w, card_h + bar_h)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.task_container = QFrame(self)
        self.task_container.setFixedSize(card_w, card_h)
        self.task_container.setStyleSheet("background: transparent; border: none;")

        self.task_label = ClickableTaskLabel(self.task_container)
        self.task_label.setGeometry(0, 0, card_w, card_h)
        self.task_label.double_clicked.connect(self.add_progress_token)
        self.task_label.right_clicked.connect(self.show_context_menu_at_label)

        self.progress_bar = QFrame(self)
        self.progress_bar.setFixedSize(card_w, bar_h)
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
        self.progress_value_label.setStyleSheet("color: #FFD700; background: transparent; border: none;")
        self.progress_value_label.setAlignment(Qt.AlignVCenter | Qt.AlignRight)

        self.progress_icon_label = QLabel()
        self.progress_icon_label.setFixedSize(18, 18)
        self.progress_icon_label.setScaledContents(True)
        self.progress_icon_label.setStyleSheet("background: transparent; border: none;")

        bar_layout.addStretch()
        bar_layout.addWidget(self.progress_value_label)
        bar_layout.addWidget(self.progress_icon_label)
        bar_layout.addStretch()

        self.main_layout.addWidget(self.task_container)
        self.main_layout.addWidget(self.progress_bar)

    def set_debug_mode(self, enabled: bool):
        self.debug_mode = bool(enabled)

    def set_quest_progress_cap_fn(
        self, fn: Callable[[], Optional[int]] | None
    ):
        """可选：返回当前探险面允许的最大进度（低于目标时表示暂不可完成）。"""
        self._quest_progress_cap_fn = fn

    def set_quest_completion_gate(self, gate: Callable[[], bool] | None):
        """可选：返回 False 时进度可满但不推进探险面。"""
        self._quest_completion_gate = gate

    def _max_progress_allowed(self) -> int:
        target = self.progress_target()
        if self._quest_progress_cap_fn is not None:
            cap = self._quest_progress_cap_fn()
            if cap is not None:
                return max(0, int(cap))
        if target > 0:
            return target
        return self._progress_limit()

    def _debug_enabled(self) -> bool:
        win = self.window()
        if win is not None and hasattr(win, "debug_mode"):
            return bool(win.debug_mode)
        return self.debug_mode

    def show_context_menu_at_label(self):
        if not self._debug_enabled():
            return
        center_point = self.task_label.rect().center()
        self.show_context_menu(center_point)

    def show_context_menu(self, position):
        menu = QMenu(self)
        next_action = menu.addAction("下一任务")
        reset_action = menu.addAction("重置任务")
        menu.addSeparator()
        add_action = menu.addAction("增加进度")
        remove_action = menu.addAction("减少进度")
        clear_action = menu.addAction("清空进度")
        menu.addSeparator()
        zoom_action = menu.addAction("放大图片")

        next_action.triggered.connect(self.advance_task)
        reset_action.triggered.connect(self.reset_task)
        add_action.triggered.connect(self.add_progress_token)
        remove_action.triggered.connect(self.remove_progress_token)
        clear_action.triggered.connect(self.clear_progress_tokens)
        zoom_action.triggered.connect(self.task_label.show_zoomed_image)

        remove_action.setEnabled(self.progress_count > 0)
        clear_action.setEnabled(self.progress_count > 0)
        zoom_action.setEnabled(self.task_label.original_pixmap and not self.task_label.original_pixmap.isNull())

        global_pos = self.task_label.mapToGlobal(position)
        menu.exec_(global_pos)

    def load_config(self):
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.task_paths = [p for p in config.get("scene_paths", []) if os.path.exists(p)]
            self.target_progress = config.get("target_progress", 0)
            self.task_requirements = config.get("scene_requirements", {})
        self.load_current_task()

    def load_from_encounter_csv(
        self,
        series: str = "穿越黑森林",
        numbers=None,
        card_type: str = "探险",
    ):
        """从魔戒遭遇.csv 加载探险任务图片与进度目标。"""
        if numbers is None:
            numbers = range(1, 8)
        quests = load_quest_scenes_from_csv(series, numbers, card_type)
        self.quest_meta = quests
        self.task_paths = [q["path"] for q in quests if q["path"]]
        self.task_requirements = {
            q["path"]: q["target"]
            for q in quests
            if q["path"] and q["target"]
        }
        self.task_index = 0
        self.load_current_task()

    def load_from_o8d(self, o8d_path: str | Path):
        """从 o8d Quest 节加载探险任务链。"""
        quests = load_quest_scenes_from_o8d(o8d_path)
        self.quest_meta = quests
        self.task_paths = [q["path"] for q in quests if q["path"]]
        self.task_requirements = {
            q["path"]: q["target"]
            for q in quests
            if q["path"] and q["target"]
        }
        self.task_index = 0
        self.load_current_task()
        if quests:
            names = " → ".join(q["name"] for q in quests)
            print(f"已从 o8d 加载 {len(quests)} 个探险任务: {names}")

    def load_progress_token(self):
        path = _PROJECT_ROOT / "cards" / "images" / "tokens" / "progress.png"
        if path.is_file():
            self.progress_pixmap = QPixmap(str(path))
            icon = self.progress_pixmap.scaled(
                18, 18, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.progress_icon_label.setPixmap(icon)
        self._update_progress_display()

    def _task_count(self):
        if self.quest_meta:
            return len(self.quest_meta)
        return len(self.task_paths)

    def _current_task_path(self):
        if self.quest_meta and 0 <= self.task_index < len(self.quest_meta):
            return self.quest_meta[self.task_index].get("path")
        if self.task_paths and 0 <= self.task_index < len(self.task_paths):
            return self.task_paths[self.task_index]
        return None

    def find_quest_index_by_image_id(self, image_id: str) -> int:
        target = (image_id or "").strip()
        if not target:
            return -1
        for i, quest in enumerate(self.quest_meta):
            if (quest.get("image_id") or "").strip() == target:
                return i
        return -1

    def find_quest_face_index_by_face(self, face: str) -> int:
        """按探险编号（如 1a、2b）在任务链中定位探险面。"""
        target = (face or "").strip().lower()
        if not target:
            return -1
        for i, quest in enumerate(self.quest_meta):
            if (quest.get("face") or "").strip().lower() == target:
                return i
        return -1

    def find_quest_face_index(
        self,
        image_id: str = "",
        number: Optional[int] = None,
        name: str = "",
        face: str = "",
    ) -> int:
        """按探险编号、image_id 或 编号+名称 在任务链中定位探险面。"""
        idx = self.find_quest_face_index_by_face(face)
        if idx >= 0:
            return idx
        idx = self.find_quest_index_by_image_id(image_id)
        if idx >= 0:
            return idx
        target_name = (name or "").strip()
        if number is not None and target_name:
            for i, quest in enumerate(self.quest_meta):
                if quest.get("number") == number and quest.get("name") == target_name:
                    return i
        return -1

    def focus_quest_face(
        self,
        image_id: str = "",
        number: Optional[int] = None,
        name: str = "",
        face: str = "",
    ) -> bool:
        """切换到指定探险面并清空进度。"""
        idx = self.find_quest_face_index(
            image_id=image_id, number=number, name=name, face=face
        )
        if idx < 0:
            return False
        self.task_index = idx
        self.clear_progress_tokens()
        self.load_current_task()
        return True

    def focus_quest_task_index(self, index: int) -> bool:
        """切换到任务链指定索引并清空进度。"""
        if index < 0 or index >= self._task_count():
            return False
        self.task_index = index
        self.clear_progress_tokens()
        self.load_current_task()
        self.task_advanced.emit()
        return True

    def get_quest_pixmap_by_image_id(self, image_id: str) -> Optional[QPixmap]:
        idx = self.find_quest_index_by_image_id(image_id)
        if idx < 0:
            return None
        path = self.quest_meta[idx].get("path")
        if not path or not os.path.exists(path):
            return None
        pixmap = QPixmap(path)
        return pixmap if not pixmap.isNull() else None

    def show_quest_setup_zoom(
        self,
        image_id: str = "",
        parent=None,
        title: str = "探险 1a 准备 - 单击关闭",
        number: Optional[int] = None,
        name: str = "",
        face: str = "",
    ) -> bool:
        """切换到指定任务面并模态放大，关闭对话框后返回。"""
        if not self.focus_quest_face(
            image_id=image_id, number=number, name=name, face=face
        ):
            return False
        resolved_id = (image_id or "").strip()
        if not resolved_id and face:
            idx = self.find_quest_face_index_by_face(face)
            if 0 <= idx < len(self.quest_meta):
                resolved_id = (self.quest_meta[idx].get("image_id") or "").strip()
        pixmap = self.get_quest_pixmap_by_image_id(resolved_id)
        if pixmap is None:
            path = self._current_task_path()
            if path and os.path.exists(path):
                candidate = QPixmap(path)
                if not candidate.isNull():
                    pixmap = candidate
        if pixmap is None:
            return False
        dialog = ImageZoomDialog(pixmap, parent or self.window(), title=title)
        dialog.exec_()
        return True

    def load_current_task(self):
        path = self._current_task_path()
        if path:
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                self.task_label.set_image(pixmap)
                return
        self.task_label.clear()
        self.task_label.setText("缺少图片")
        self.task_label.original_pixmap = None

    def _progress_limit(self):
        path = self._current_task_path()
        if path and self.task_requirements.get(path):
            return self.task_requirements[path]
        return 99

    def _update_progress_display(self):
        self.progress_value_label.setText(str(self.progress_count))

    def add_progress_token(self):
        limit = self._max_progress_allowed()
        if self.progress_count >= limit:
            return
        self.progress_count += 1
        self._update_progress_display()
        self.progress_changed.emit(self.progress_count)
        self._try_advance_quest_stage_if_complete()

    def remove_progress_token(self):
        if self.progress_count > 0:
            self.progress_count -= 1
            self._update_progress_display()
            self.progress_changed.emit(self.progress_count)

    def clear_progress_tokens(self):
        self.progress_count = 0
        self._update_progress_display()
        self.progress_changed.emit(self.progress_count)

    def progress_target(self) -> int:
        path = self._current_task_path()
        if path and self.task_requirements.get(path):
            return int(self.task_requirements[path])
        if self.quest_meta and 0 <= self.task_index < len(self.quest_meta):
            target = self.quest_meta[self.task_index].get("target", 0)
            if target:
                return int(target)
        return 0

    def apply_progress(self, amount: int) -> tuple[int, Optional[str]]:
        """
        放置进度标记；达到探险面目标时立即前往下一探险面。
        返回 (溢出弃除数, 下一面名称或 None)。
        """
        if amount <= 0:
            return 0, None
        target = self.progress_target()
        if target <= 0:
            self.progress_count += amount
            self._update_progress_display()
            self.progress_changed.emit(self.progress_count)
            return 0, None
        max_allowed = self._max_progress_allowed()
        space = max(0, max_allowed - self.progress_count)
        added = min(amount, space)
        self.progress_count += added
        overflow = amount - added
        self._update_progress_display()
        self.progress_changed.emit(self.progress_count)
        _, next_name = self._try_advance_quest_stage_if_complete()
        if next_name is not None:
            return 0, next_name
        return overflow, None

    def remove_progress(self, amount: int) -> int:
        """从当前探险卡移除进度（卡牌效果等）。不影响当前地区。"""
        if amount <= 0:
            return 0
        removed = min(amount, self.progress_count)
        if removed <= 0:
            return 0
        self.progress_count -= removed
        self._update_progress_display()
        self.progress_changed.emit(self.progress_count)
        return removed

    def is_quest_complete(self) -> bool:
        target = self.progress_target()
        return target > 0 and self.progress_count >= target

    def _try_advance_quest_stage_if_complete(self) -> tuple[int, Optional[str]]:
        """
        进度达到当前探险面目标时，立即弃除多余进度并前往下一探险面。
        返回 (弃除数, 下一面名称)；未达成目标则 (0, None)。
        """
        if not self.is_quest_complete():
            return 0, None
        gate = self._quest_completion_gate
        if gate is not None and not gate():
            return 0, None
        discarded, next_name = self.complete_and_advance_quest()
        self.quest_stage_completed.emit(next_name)
        return discarded, next_name

    def complete_and_advance_quest(self) -> tuple[int, str]:
        """
        探险面完成：弃除超出目标的进度，并前往下一探险面。
        返回 (弃除进度数, 下一面名称；无下一面则为空字符串)。
        """
        target = self.progress_target()
        if target <= 0 or self.progress_count < target:
            return 0, ""

        discarded = max(0, self.progress_count - target)
        if discarded:
            self.progress_count = target
            self._update_progress_display()
            self.progress_changed.emit(self.progress_count)

        next_name = ""
        if self.task_index < self._task_count() - 1:
            self.advance_task()
            if self.quest_meta and self.task_index < len(self.quest_meta):
                next_name = self.quest_meta[self.task_index].get("name", "?") or "?"
            print(f"探险面完成，前往下一探险面：{next_name or '?'}")
        else:
            print("探险面完成（已是任务链最后一张）")

        return discarded, next_name

    def advance_task(self):
        if self.task_index < self._task_count() - 1:
            self.task_index += 1
            self.load_current_task()
            self.clear_progress_tokens()
            self.task_advanced.emit()

    def reset_task(self):
        self.task_index = 0
        self.load_current_task()
        self.clear_progress_tokens()


class SceneTestWindow(QMainWindow):
    """任务模块测试窗口。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("场景模块测试")
        self.setFixedSize(420, 320)
        self.debug_mode = True

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        hint = QLabel(
            "左键双击图片：进度 +1\n"
            "右键双击图片：放大\n"
            "Debug 开启后右键图片：菜单（下一任务 / 进度 / 放大）\n"
            "卡图下方：进度数值 + 进度标记图"
        )
        hint.setStyleSheet("color: #444;")
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.addStretch()
        self.task_widget = 任务()
        self.task_widget.load_from_encounter_csv("穿越黑森林", range(1, 8))
        row.addWidget(self.task_widget)
        row.addStretch()
        layout.addLayout(row)

        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 14px; color: #004488;")
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        for text, slot in (
            ("+ 进度", self.task_widget.add_progress_token),
            ("- 进度", self.task_widget.remove_progress_token),
            ("清空进度", self.task_widget.clear_progress_tokens),
            ("下一任务", self.task_widget.advance_task),
            ("重置任务", self.task_widget.reset_task),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)
        layout.addLayout(btn_row)

        self.task_widget.progress_changed.connect(self._update_status)
        self.task_widget.task_advanced.connect(self._update_status)
        self._update_status()

    def _update_status(self):
        meta = getattr(self.task_widget, "quest_meta", [])
        total = len(meta)
        idx = self.task_widget.task_index
        tokens = self.task_widget.progress_count

        if meta and idx < len(meta):
            cur = meta[idx]
            name = f"{cur['number']}.{cur['name']}"
            target = cur.get("target") or self.task_widget.task_requirements.get(cur.get("path"), 0)
            img_ok = "已加载" if cur.get("path") else "缺图"
            target_txt = f"目标进度 {target}" if target else "无进度目标"
            detail = f"{name}（{img_ok}，{target_txt}）"
        elif self.task_widget.task_paths:
            detail = f"{idx + 1}/{len(self.task_widget.task_paths)}"
        else:
            missing = [q for q in meta if not q["path"]]
            names = "、".join(f"{q['image_id']}.jpg" for q in missing[:3])
            extra = f" 等{len(missing)}张" if len(missing) > 3 else ""
            detail = f"请将图片放入 cards/场景/：{names}{extra}"

        self.status_label.setText(
            f"穿越黑森林 探险 1-7　|　{detail}　|　标记：{tokens}/{total or '?'}"
        )