"""Debug 模式下从 CSV 选牌并放置于牌库顶。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from collections import Counter
from typing import Dict, List, Optional, Tuple

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

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from 玩家卡抽取 import (
    CARD_NAME_ALIASES,
    Card as PlayerCard,
    PLAYER_CSV,
    SERIES_ALIASES,
    _read_player_csv_rows,
    player_row_display_name,
    resolve_player_csv_series,
)
from 遭遇抽取 import (
    Card as EncounterCard,
    ENCOUNTER_CSV,
    ENCOUNTER_DECK_TYPES,
    _read_encounter_csv_rows,
    load_encounter_cards_from_csv,
)


def _resolve_series_key(series: Optional[str]) -> str:
    return resolve_player_csv_series(series or "基础")


def _list_player_series(rows: List[dict]) -> List[str]:
    seen: List[str] = []
    for row in rows:
        name = (row.get("系列") or "").strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def _deck_copy_counts(deck_cards: Optional[List[PlayerCard]]) -> Counter:
    counts: Counter = Counter()
    for card in deck_cards or []:
        name = (getattr(card, "name", "") or "").strip()
        series = (getattr(card, "series", "") or "").strip()
        if not name:
            continue
        counts[(name, series)] += 1
        canonical = CARD_NAME_ALIASES.get(name, "")
        if canonical:
            counts[(canonical, series)] += 1
    return counts


def _player_picker_status(
    row: dict,
    deck_counts: Counter,
) -> Tuple[str, bool]:
    """返回 (状态标签, 是否可选)。"""
    card_type = (row.get("类型") or "").strip()
    if card_type == "英雄":
        return "英雄", False

    series = (row.get("系列") or "").strip()
    display = player_row_display_name(row)
    alt = (row.get("备用卡牌名称") or "").strip()
    names = [display]
    if alt and alt not in names:
        names.append(alt)
    for alias, canonical in CARD_NAME_ALIASES.items():
        if canonical in names or alias in names:
            names.extend([alias, canonical])

    total = 0
    for name in names:
        total = max(total, deck_counts.get((name, series), 0))
    if total > 0:
        return f"在牌组 ×{total}", True
    return "不在牌组", True


def _find_deck_card_instance(
    row: dict,
    deck_cards: Optional[List[PlayerCard]],
) -> Optional[PlayerCard]:
    """优先返回牌组中已有实例，便于保持 copy id。"""
    series = (row.get("系列") or "").strip()
    display = player_row_display_name(row)
    alt = (row.get("备用卡牌名称") or "").strip()
    targets = {display, alt, CARD_NAME_ALIASES.get(display, ""), CARD_NAME_ALIASES.get(alt, "")}
    targets.discard("")
    for card in deck_cards or []:
        if (getattr(card, "series", "") or "").strip() != series:
            continue
        if (getattr(card, "name", "") or "").strip() in targets:
            return card
    return None


class _DebugCardPickerDialog(QDialog):
    def __init__(
        self,
        parent,
        *,
        title: str,
        series: str,
        series_list: List[str],
        entries: List[Tuple[str, str, bool, object]],
        on_series_change,
    ):
        super().__init__(parent)
        self._series = series
        self._series_list = series_list
        self._on_series_change = on_series_change
        self._entries = entries
        self._selected: Optional[object] = None

        self.setWindowTitle(title)
        self.setMinimumSize(520, 560)

        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self._series_label = QLabel(f"扩展：{series}")
        self._series_label.setWordWrap(True)
        header.addWidget(self._series_label, 1)
        if len(series_list) > 1:
            next_btn = QPushButton("下一扩展")
            next_btn.clicked.connect(self._next_series)
            header.addWidget(next_btn)
        layout.addLayout(header)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("筛选卡牌名称…")
        self._filter_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter_edit)

        hint = QLabel("双击或选中后点「确定」放置于牌库顶（英雄不可选）。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #555;")
        layout.addWidget(hint)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self._list, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._populate_list()

    def _populate_list(self) -> None:
        self._list.clear()
        for label, _filter_key, enabled, payload in self._entries:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, payload)
            if not enabled:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self._list.addItem(item)

    def _apply_filter(self, text: str) -> None:
        needle = (text or "").strip().casefold()
        for i in range(self._list.count()):
            item = self._list.item(i)
            hay = (item.text() or "").casefold()
            item.setHidden(bool(needle) and needle not in hay)

    def _next_series(self) -> None:
        if not self._series_list:
            return
        try:
            idx = self._series_list.index(self._series)
        except ValueError:
            idx = -1
        next_series = self._series_list[(idx + 1) % len(self._series_list)]
        entries, series = self._on_series_change(next_series)
        self._series = series
        self._entries = entries
        self._series_label.setText(f"扩展：{series}")
        self._filter_edit.clear()
        self._populate_list()

    def _accept_selection(self) -> None:
        item = self._list.currentItem()
        if item is None or not (item.flags() & Qt.ItemIsEnabled):
            return
        self._selected = item.data(Qt.UserRole)
        self.accept()

    def _on_double_click(self, item: QListWidgetItem) -> None:
        if not (item.flags() & Qt.ItemIsEnabled):
            return
        self._selected = item.data(Qt.UserRole)
        self.accept()

    def selected(self):
        return self._selected


def _run_picker(
    parent,
    *,
    title: str,
    initial_series: str,
    series_list: List[str],
    build_entries,
) -> Optional[object]:
    current_series = initial_series

    def on_series_change(series: str):
        nonlocal current_series
        current_series = series
        return build_entries(series), series

    entries = build_entries(current_series)
    dlg = _DebugCardPickerDialog(
        parent,
        title=title,
        series=current_series,
        series_list=series_list,
        entries=entries,
        on_series_change=on_series_change,
    )
    if dlg.exec_() != QDialog.Accepted:
        return None
    return dlg.selected()


def pick_player_card_for_debug(
    parent=None,
    *,
    series: Optional[str] = None,
    deck_text: Optional[str] = None,
    deck_cards: Optional[List[PlayerCard]] = None,
) -> Optional[PlayerCard]:
    """弹出玩家牌选牌窗，返回要置于牌库顶的 Card。"""
    del deck_text  # 状态以当前 drawer.cards 为准
    rows = _read_player_csv_rows(PLAYER_CSV)
    if not rows:
        return None

    series_list = _list_player_series(rows)
    initial_series = _resolve_series_key(series)
    if initial_series not in series_list and series_list:
        initial_series = series_list[0]

    deck_counts = _deck_copy_counts(deck_cards)

    def build_entries(active_series: str) -> List[Tuple[str, str, bool, PlayerCard]]:
        resolved = _resolve_series_key(active_series)
        alias = SERIES_ALIASES.get(resolved, "")
        series_keys = [resolved]
        if alias and alias not in series_keys:
            series_keys.append(alias)

        items: List[Tuple[str, str, bool, PlayerCard]] = []
        for row in rows:
            row_series = (row.get("系列") or "").strip()
            if row_series not in series_keys:
                continue
            status, selectable = _player_picker_status(row, deck_counts)
            card_type = (row.get("类型") or "").strip()
            name = player_row_display_name(row)
            label = f"[{status}] {name}（{card_type or '—'}）"
            if selectable:
                payload = _find_deck_card_instance(row, deck_cards)
                if payload is None:
                    payload = PlayerCard.from_csv_row(row)
            else:
                payload = None
            items.append((label, name, selectable, payload))

        items.sort(key=lambda x: x[1])
        return items

    picked = _run_picker(
        parent,
        title="Debug · 放置玩家卡（牌库顶）",
        initial_series=initial_series,
        series_list=series_list,
        build_entries=build_entries,
    )
    return picked


def pick_encounter_card_for_debug(
    parent=None,
    *,
    series: Optional[str] = None,
) -> Optional[EncounterCard]:
    """弹出遭遇牌选牌窗，返回要置于牌库顶的 Card。"""
    if not ENCOUNTER_CSV.is_file():
        return None

    rows = _read_encounter_csv_rows(ENCOUNTER_CSV)
    series_list = _list_player_series(rows)
    initial_series = (series or "").strip() or series_list[0] if series_list else ""
    if initial_series and initial_series not in series_list and series_list:
        initial_series = series_list[0]

    def build_entries(active_series: str) -> List[Tuple[str, str, bool, EncounterCard]]:
        cards = load_encounter_cards_from_csv(series=active_series, exclude_types=("探险",))
        items: List[Tuple[str, str, bool, EncounterCard]] = []
        for card in cards:
            card_type = (getattr(card, "type", "") or "").strip()
            if card_type and card_type not in ENCOUNTER_DECK_TYPES:
                continue
            name = (getattr(card, "name", "") or "").strip()
            label = f"{name}（{card_type or '—'}）"
            items.append((label, name, True, card))
        items.sort(key=lambda x: x[1])
        return items

    if not series_list:
        series_list = [initial_series] if initial_series else []

    picked = _run_picker(
        parent,
        title="Debug · 放置遭遇卡（牌库顶）",
        initial_series=initial_series,
        series_list=series_list,
        build_entries=build_entries,
    )
    return picked
