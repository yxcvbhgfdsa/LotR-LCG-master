"""Debug：从 CSV 选择卡牌并放置到牌库顶。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
import random
from dataclasses import replace
from typing import Callable, List, Optional, TypeVar

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

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from 玩家卡抽取 import (
    CARD_NAME_ALIASES,
    DEFAULT_DECK_SERIES as PLAYER_DEFAULT_SERIES,
    Card as PlayerCard,
    load_player_cards_for_debug,
)
from 遭遇抽取 import (
    ENCOUNTER_CSV,
    DEFAULT_DECK_SERIES as ENCOUNTER_DEFAULT_SERIES,
    Card as EncounterCard,
    load_encounter_deck_from_o8d,
    load_encounter_cards_from_csv,
)

CardT = TypeVar("CardT", PlayerCard, EncounterCard)


def _player_card_search_keys(card: PlayerCard) -> List[str]:
    """筛选用名称：主名 + 卡牌别名。"""
    keys: List[str] = []
    name = (card.name or "").strip()
    if name:
        keys.append(name)
    for src, dst in CARD_NAME_ALIASES.items():
        if dst == name and src not in keys:
            keys.append(src)
        elif src == name and dst not in keys:
            keys.append(dst)
    return keys


class _DebugCardTile(QFrame):
    """可点击的卡牌缩略图。"""

    picked = pyqtSignal(object)

    def __init__(self, card, parent=None):
        super().__init__(parent)
        self._card = card
        self._selected = False
        self.setFixedSize(138, 216)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 6)
        layout.setSpacing(4)

        self._image = QLabel()
        self._image.setFixedSize(128, 170)
        self._image.setAlignment(Qt.AlignCenter)
        self._image.setStyleSheet("background-color: white; border: none;")
        layout.addWidget(self._image, 0, Qt.AlignCenter)

        self._name_label = QLabel(card.name or "?")
        self._name_label.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self._name_label.setWordWrap(True)
        self._name_label.setStyleSheet("border: none; color: #222;")
        layout.addWidget(self._name_label, 1)

        self._apply_style()
        path = getattr(card, "image_path", "") or ""
        if path:
            pix = QPixmap(path)
            if not pix.isNull():
                self._image.setPixmap(
                    pix.scaled(120, 164, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
                tooltip = card.name or "?"
                series = getattr(card, "series", "") or ""
                card_type = getattr(card, "type", "") or ""
                if series:
                    tooltip = f"{tooltip} ({series})"
                if card_type:
                    tooltip = f"{tooltip}\n{card_type}"
                self.setToolTip(tooltip)
                return
        self._image.setText((card.name or "?")[:10])
        self._image.setWordWrap(True)
        self._image.setStyleSheet("background-color: #f8f8f8; border: none; color: #333;")

    def _apply_style(self):
        border = "#0078d4" if self._selected else "#666"
        width = 3 if self._selected else 1
        self.setStyleSheet(
            f"border: {width}px solid {border}; "
            "background-color: white; border-radius: 4px;"
        )

    def set_selected(self, selected: bool):
        self._selected = selected
        self._apply_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.picked.emit(self._card)
        super().mousePressEvent(event)


class DebugCardPickDialog(QDialog):
    """从候选卡牌中单选一张（Debug 放置牌库顶）。"""

    def __init__(
        self,
        parent,
        title: str,
        cards: List[CardT],
        *,
        filter_hint: str = "输入名称筛选…",
        match_fn: Optional[Callable[[CardT, str], bool]] = None,
    ):
        super().__init__(parent)
        self._all_cards = list(cards)
        self._visible_cards = list(cards)
        self._selected: Optional[CardT] = None
        self._tiles: list[_DebugCardTile] = []
        self._match_fn = match_fn
        self._grid_cols = 0
        self.setWindowTitle(title)
        self.setMinimumSize(760, 540)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("单击卡图选择，确认后放置于牌库顶（最上方）。")
        )
        self._filter = QLineEdit()
        self._filter.setPlaceholderText(filter_hint)
        self._filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter)
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(8, 8, 8, 8)
        self._grid.setSpacing(8)
        self._grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._grid_host)
        self._scroll.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        layout.addWidget(self._scroll, 1)
        self._status = QLabel("")
        layout.addWidget(self._status)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("确认放置")
        ok_btn.clicked.connect(self._accept_selection)
        row.addWidget(cancel_btn)
        row.addWidget(ok_btn)
        layout.addLayout(row)
        self._rebuild_grid(self._all_cards)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cols = self._calc_columns()
        if cols != self._grid_cols:
            self._rebuild_grid(self._visible_cards)

    def _calc_columns(self) -> int:
        viewport = self._scroll.viewport()
        width = max(1, viewport.width())
        tile_width = 146
        return max(1, width // tile_width)

    def _card_matches(self, card: CardT, needle: str) -> bool:
        if self._match_fn is not None:
            return self._match_fn(card, needle)
        return needle in (getattr(card, "name", "") or "").casefold()

    def _apply_filter(self, text: str):
        needle = text.strip().casefold()
        if not needle:
            self._rebuild_grid(self._all_cards)
            return
        filtered = [c for c in self._all_cards if self._card_matches(c, needle)]
        self._rebuild_grid(filtered)

    def _rebuild_grid(self, cards: List[CardT]):
        self._visible_cards = list(cards)
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._tiles.clear()
        cols = self._calc_columns()
        self._grid_cols = cols
        for idx, card in enumerate(cards):
            tile = _DebugCardTile(card)
            tile.picked.connect(self._on_pick)
            tile.set_selected(self._selected is card)
            self._tiles.append(tile)
            self._grid.addWidget(tile, idx // cols, idx % cols)
        if not cards:
            self._grid.addWidget(QLabel("（无匹配卡牌）"), 0, 0, 1, cols)

    def _on_pick(self, card):
        self._selected = card
        for tile in self._tiles:
            tile.set_selected(tile._card is card)

    def _accept_selection(self):
        if self._selected is None:
            self._status.setText("请先单击选择一张卡牌。")
            return
        self.accept()

    def selected_card(self) -> Optional[CardT]:
        return self._selected


def _debug_unique_card(card: CardT) -> CardT:
    stamp = random.randint(100000, 999999)
    base_id = (card.id or card.name).split("#")[0]
    return replace(card, id=f"{base_id}#dbg{stamp}")


def _pick_card(
    parent,
    title: str,
    loader: Callable[[], List[CardT]],
    *,
    filter_hint: str = "输入名称筛选…",
    match_fn: Optional[Callable[[CardT, str], bool]] = None,
    dedupe_key: Optional[Callable[[CardT], tuple]] = None,
) -> Optional[CardT]:
    cards = loader()
    if not cards:
        return None
    seen: set = set()
    unique: List[CardT] = []
    key_fn = dedupe_key or (
        lambda c: (getattr(c, "name", "") or "", getattr(c, "type", "") or "")
    )
    for card in cards:
        key = key_fn(card)
        if key in seen:
            continue
        seen.add(key)
        unique.append(card)
    unique.sort(key=lambda c: (getattr(c, "name", "") or "", getattr(c, "series", "") or ""))
    dlg = DebugCardPickDialog(
        parent,
        title,
        unique,
        filter_hint=filter_hint,
        match_fn=match_fn,
    )
    if dlg.exec_() != QDialog.Accepted:
        return None
    picked = dlg.selected_card()
    if picked is None:
        return None
    return _debug_unique_card(picked)


def _load_encounter_cards_for_debug(
    *,
    series: Optional[str],
    deck_path: Optional[str],
) -> List[EncounterCard]:
    use_series = series or ENCOUNTER_DEFAULT_SERIES
    path_text = (deck_path or "").strip()
    if path_text:
        path = Path(path_text)
        if path.suffix.lower() == ".o8d" and path.is_file():
            cards, _missing = load_encounter_deck_from_o8d(path, series=use_series)
            if cards:
                return cards
        if path.suffix.lower() == ".csv" and path.is_file():
            cards = load_encounter_cards_from_csv(series=use_series, csv_path=path)
            if cards:
                return cards
    return load_encounter_cards_from_csv(series=use_series, csv_path=ENCOUNTER_CSV)


def pick_player_card_for_debug(
    parent,
    series: Optional[str] = None,
    *,
    deck_text: Optional[str] = None,
    deck_cards: Optional[List[PlayerCard]] = None,
) -> Optional[PlayerCard]:
    cards, source_label = load_player_cards_for_debug(
        series=series,
        deck_text=deck_text,
        deck_cards=deck_cards,
    )
    if not cards:
        return None

    def _load() -> List[PlayerCard]:
        return cards

    def _match(card: PlayerCard, needle: str) -> bool:
        return any(needle in key.casefold() for key in _player_card_search_keys(card))

    return _pick_card(
        parent,
        f"Debug · 放置玩家卡（{source_label}）",
        _load,
        filter_hint="输入名称或别名筛选（含备用名）…",
        match_fn=_match,
        dedupe_key=lambda c: (
            getattr(c, "name", "") or "",
            getattr(c, "series", "") or "",
            getattr(c, "type", "") or "",
        ),
    )


def pick_encounter_card_for_debug(
    parent,
    series: Optional[str] = None,
    *,
    deck_path: Optional[str] = None,
) -> Optional[EncounterCard]:
    use_series = series or ENCOUNTER_DEFAULT_SERIES

    def _load() -> List[EncounterCard]:
        return _load_encounter_cards_for_debug(
            series=use_series,
            deck_path=deck_path,
        )

    return _pick_card(
        parent,
        f"Debug · 放置遭遇卡（牌库顶 · {use_series}）",
        _load,
    )
