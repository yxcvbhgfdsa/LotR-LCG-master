"""空编号卡牌的详情解析与 PyQt5 预览组件。"""

from __future__ import annotations

import csv
import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from PyQt5.QtCore import QObject, QRect, QSettings, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QCursor, QFont, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QBoxLayout,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStyle,
    QVBoxLayout,
    QWidget,
)


_PROJECT_ROOT = Path(__file__).resolve().parent
_DEFAULT_CSV_PATHS = {
    "player": _PROJECT_ROOT / "魔戒玩家牌.csv",
    "encounter": _PROJECT_ROOT / "魔戒遭遇.csv",
}

_CARD_IMAGE_DIR = _PROJECT_ROOT / "cards" / "images"
_CARD_ICON_DIR = _CARD_IMAGE_DIR / "icons"
_STAT_ICON_PATHS = {
    "费用": _CARD_IMAGE_DIR / "tokens" / "resource.png",
    "资源": _CARD_IMAGE_DIR / "tokens" / "resource.png",
    "初始威胁": _CARD_IMAGE_DIR / "Threat.jpg",
    "威胁": _CARD_IMAGE_DIR / "Threat.jpg",
    "意志": _CARD_IMAGE_DIR / "Willpower.jpg",
    "攻击": _CARD_IMAGE_DIR / "attack.png",
    "防御": _CARD_IMAGE_DIR / "Defense.png",
}
_SPHERE_ICON_PATHS = {
    "领导": _CARD_ICON_DIR / "leadership.png",
    "战术": _CARD_ICON_DIR / "tactics.png",
    "精神": _CARD_ICON_DIR / "spirit.png",
    "学识": _CARD_ICON_DIR / "lore.png",
}
_RULE_ICON_PATHS = {
    "资源": _STAT_ICON_PATHS["资源"],
    "意志": _STAT_ICON_PATHS["意志"],
    "意志力": _STAT_ICON_PATHS["意志"],
    "攻击": _STAT_ICON_PATHS["攻击"],
    "攻击力": _STAT_ICON_PATHS["攻击"],
    "防御": _STAT_ICON_PATHS["防御"],
    "防御力": _STAT_ICON_PATHS["防御"],
    "威胁": _STAT_ICON_PATHS["威胁"],
    "威胁值": _STAT_ICON_PATHS["威胁"],
    **_SPHERE_ICON_PATHS,
}

_TRUE_MARKERS = frozenset({"*", "√", "是", "y", "yes", "1", "true"})
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"})
_COPY_SUFFIX_RE = re.compile(r"#\d+$")
_TOKEN_SPLIT_RE = re.compile(r"[|。.;；、，,\n]+")

CARD_DETAIL_DEFAULT_FONT_SIZE = 11
CARD_DETAIL_MIN_FONT_SIZE = 8
CARD_DETAIL_MAX_FONT_SIZE = 24
CARD_DETAIL_DEFAULT_ICON_SIZE = 18
_CARD_DETAIL_SETTINGS_ORGANIZATION = "LotR-LCG"
_CARD_DETAIL_SETTINGS_APPLICATION = "LotR-LCG"
_CARD_DETAIL_FONT_SIZE_KEY = "cardDetail/fontSize"


def card_detail_icon_size(font_size: Optional[int] = None) -> int:
    """正文每增减 1px，内联图标也跟随增减 1px。"""

    size = card_detail_font_size() if font_size is None else int(font_size)
    return max(12, CARD_DETAIL_DEFAULT_ICON_SIZE + size - CARD_DETAIL_DEFAULT_FONT_SIZE)


class _CardDetailAppearance(QObject):
    font_size_changed = pyqtSignal(int)

    def __init__(self, settings: Optional[QSettings] = None) -> None:
        super().__init__()
        self._settings = settings if settings is not None else QSettings(
            _CARD_DETAIL_SETTINGS_ORGANIZATION,
            _CARD_DETAIL_SETTINGS_APPLICATION,
        )
        try:
            stored_size = int(
                self._settings.value(
                    _CARD_DETAIL_FONT_SIZE_KEY,
                    CARD_DETAIL_DEFAULT_FONT_SIZE,
                )
            )
        except (TypeError, ValueError):
            stored_size = CARD_DETAIL_DEFAULT_FONT_SIZE
        self._font_size = max(
            CARD_DETAIL_MIN_FONT_SIZE,
            min(CARD_DETAIL_MAX_FONT_SIZE, stored_size),
        )

    @property
    def font_size(self) -> int:
        return self._font_size

    def set_font_size(self, value: int) -> int:
        value = max(CARD_DETAIL_MIN_FONT_SIZE, min(CARD_DETAIL_MAX_FONT_SIZE, int(value)))
        self._settings.setValue(_CARD_DETAIL_FONT_SIZE_KEY, value)
        self._settings.sync()
        if value != self._font_size:
            self._font_size = value
            self.font_size_changed.emit(value)
        return self._font_size

    def reset_font_size(self) -> int:
        self._settings.remove(_CARD_DETAIL_FONT_SIZE_KEY)
        self._settings.sync()
        if self._font_size != CARD_DETAIL_DEFAULT_FONT_SIZE:
            self._font_size = CARD_DETAIL_DEFAULT_FONT_SIZE
            self.font_size_changed.emit(self._font_size)
        return self._font_size


_CARD_DETAIL_APPEARANCE = _CardDetailAppearance()


def card_detail_font_size() -> int:
    return _CARD_DETAIL_APPEARANCE.font_size


def set_card_detail_font_size(value: int) -> int:
    return _CARD_DETAIL_APPEARANCE.set_font_size(value)


def reset_card_detail_font_size() -> int:
    return _CARD_DETAIL_APPEARANCE.reset_font_size()


def card_zoom_available_geometry(
    parent: Optional[QWidget],
    *,
    inset: int = 10,
) -> QRect:
    """返回当前主窗口内可安全容纳卡牌放大窗口的全局矩形。"""

    root = parent
    main_window: Optional[QMainWindow] = None
    main_window_from_parent = False
    current = parent
    while current is not None:
        root = current
        if isinstance(current, QMainWindow):
            main_window = current
            main_window_from_parent = True
        current = current.parentWidget()

    if main_window is None:
        active = QApplication.activeWindow()
        current = active
        while current is not None:
            if isinstance(current, QMainWindow):
                main_window = current
            current = current.parentWidget()

    host = main_window
    if host is None and root is not None and root.isWindow() and root.isVisible():
        host = root

    host_geometry = host.frameGeometry() if host is not None else QRect()
    host_from_parent = main_window_from_parent or (host is not None and host is root)
    if host_from_parent and parent is not None and parent.rect().isValid():
        probe = parent.mapToGlobal(parent.rect().center())
    else:
        probe = host_geometry.center() if host_geometry.isValid() else QCursor.pos()
    screen_obj = QApplication.screenAt(probe) or QApplication.primaryScreen()

    if host_geometry.isValid() and (
        screen_obj is None
        or screen_obj.availableGeometry().intersected(host_geometry).isEmpty()
    ):
        best_screen = None
        best_area = 0
        for candidate in QApplication.screens():
            overlap = candidate.availableGeometry().intersected(host_geometry)
            area = max(0, overlap.width()) * max(0, overlap.height())
            if area > best_area:
                best_screen = candidate
                best_area = area
        screen_obj = best_screen

    if screen_obj is not None:
        available = screen_obj.availableGeometry()
    elif host_geometry.isValid():
        available = QRect(host_geometry)
    else:
        available = QRect(0, 0, 800, 600)

    if host_geometry.isValid():
        within_root = available.intersected(host_geometry)
        if within_root.isValid() and not within_root.isEmpty():
            available = within_root
        else:
            available = QRect(host_geometry)

    inset = max(0, int(inset))
    horizontal_inset = min(inset, max(0, (available.width() - 1) // 2))
    vertical_inset = min(inset, max(0, (available.height() - 1) // 2))
    return available.adjusted(
        horizontal_inset,
        vertical_inset,
        -horizontal_inset,
        -vertical_inset,
    )


def _card_zoom_frame_extents(window: QWidget) -> tuple[int, int]:
    window.ensurePolished()
    window.winId()
    handle = window.windowHandle()
    margins = handle.frameMargins() if handle is not None else None
    actual_width = margins.left() + margins.right() if margins is not None else 0
    actual_height = margins.top() + margins.bottom() if margins is not None else 0
    if window.windowFlags() & Qt.FramelessWindowHint:
        return actual_width, actual_height
    style = window.style() or QApplication.style()
    frame_width = max(0, style.pixelMetric(QStyle.PM_DefaultFrameWidth))
    title_height = max(0, style.pixelMetric(QStyle.PM_TitleBarHeight))
    return (
        max(actual_width, frame_width * 2),
        max(actual_height, title_height + frame_width * 2),
    )


def card_zoom_content_limits(
    window: QWidget,
    parent: Optional[QWidget],
    *,
    layout_width: int,
    layout_height: int,
) -> tuple[QRect, QSize]:
    """计算扣除布局边距和系统窗口外框后的卡图/详情内容上限。"""

    available = card_zoom_available_geometry(parent)
    frame_width, frame_height = _card_zoom_frame_extents(window)
    return available, QSize(
        max(1, available.width() - max(0, int(layout_width)) - frame_width),
        max(1, available.height() - max(0, int(layout_height)) - frame_height),
    )


def place_card_zoom_window(window: QWidget, available: QRect) -> None:
    """按实际 frameGeometry 居中，并将窗口最终夹在主窗口范围内。"""

    available = QRect(available)

    def place_now() -> None:
        window.ensurePolished()
        window.winId()
        handle = window.windowHandle()
        margins = handle.frameMargins() if handle is not None else None
        left_margin = margins.left() if margins is not None else 0
        top_margin = margins.top() if margins is not None else 0
        right_margin = margins.right() if margins is not None else 0
        bottom_margin = margins.bottom() if margins is not None else 0
        frame_width = window.width() + left_margin + right_margin
        frame_height = window.height() + top_margin + bottom_margin
        frame_left = available.left() + (available.width() - frame_width) // 2
        frame_top = available.top() + (available.height() - frame_height) // 2
        # QWidget.move() 对顶层窗口使用包含外框的坐标。
        window.move(frame_left, frame_top)

        frame = window.frameGeometry()
        if frame.width() <= available.width():
            clamped_left = min(
                max(frame.left(), available.left()),
                available.right() - frame.width() + 1,
            )
        else:
            clamped_left = available.left()
        if frame.height() <= available.height():
            clamped_top = min(
                max(frame.top(), available.top()),
                available.bottom() - frame.height() + 1,
            )
        else:
            clamped_top = available.top()
        if clamped_left != frame.left() or clamped_top != frame.top():
            window.move(
                window.x() + clamped_left - frame.left(),
                window.y() + clamped_top - frame.top(),
            )

    place_now()

    # 窗口显示后平台可能会更新标题栏或 DPI 外框，再夹紧一次。
    def place_after_show() -> None:
        try:
            if window.isVisible():
                place_now()
        except RuntimeError:
            pass

    QTimer.singleShot(0, place_after_show)


def _icon_is_available(path: Path) -> bool:
    return path.is_file() and not QPixmap(str(path)).isNull()


def _icon_html(
    path: Path,
    name: str,
    *,
    size: int = CARD_DETAIL_DEFAULT_ICON_SIZE,
) -> str:
    """返回可由 Qt 富文本加载的小图标；素材缺失时保留可读文字。"""

    if not _icon_is_available(path):
        return html.escape(name)
    uri = QUrl.fromLocalFile(str(path)).toString(QUrl.FullyEncoded)
    return (
        f'<img src="{html.escape(uri, quote=True)}" width="{size}" height="{size}" '
        f'alt="{html.escape(name, quote=True)}" style="vertical-align: middle;"/>'
    )


def _rules_rich_text(
    text: str,
    *,
    icon_size: int = CARD_DETAIL_DEFAULT_ICON_SIZE,
) -> tuple[str, tuple[str, ...]]:
    """转义规则原文，并把明确标注的属性/派系词替换为内联图标。"""

    normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
    rich = html.escape(normalized_text)
    used: list[str] = []
    # 先处理较长名称，以免“意志”抢先匹配“意志力”等标记。
    for name in sorted(_RULE_ICON_PATHS, key=len, reverse=True):
        icon_path = _RULE_ICON_PATHS[name]
        if not _icon_is_available(icon_path):
            continue
        icon = _icon_html(icon_path, name, size=icon_size)
        for marked in (f"【{name}】", f"**{name}**"):
            escaped_marker = html.escape(marked)
            if escaped_marker in rich:
                rich = rich.replace(escaped_marker, icon)
                used.append(name)
    return rich.replace("\n", "<br/>"), tuple(dict.fromkeys(used))


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalized(value: Any) -> str:
    return _text(value).casefold()


def _deduplicated_parts(*values: Any) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for part in _TOKEN_SPLIT_RE.split(_text(value)):
            part = part.strip()
            key = part.casefold()
            if part and key not in seen:
                seen.add(key)
                result.append(part)
    return tuple(result)


def _keyword_value(label: str, raw: Any, *, numeric: bool = False) -> str:
    value = _text(raw)
    if not value:
        return ""
    if numeric and value.replace(".", "", 1).isdigit():
        return f"{label} {value}"
    if value.casefold() in _TRUE_MARKERS:
        return label
    if value.casefold().startswith(label.casefold()):
        return value
    return f"{label} {value}"


@dataclass(frozen=True)
class CardDetailPayload:
    """已经规范化、可直接交给详情面板显示的数据。"""

    kind: str
    name: str
    secondary_name: str = ""
    card_type: str = ""
    sphere: str = ""
    series: str = ""
    encounter_set: str = ""
    unique: bool = False
    stats: tuple[tuple[str, str], ...] = ()
    traits: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    rules_text: str = ""


def _stats_from_row(row: Mapping[str, Any], kind: str) -> tuple[tuple[str, str], ...]:
    if kind == "player":
        fields = (
            ("费用", "卡牌费用"),
            ("初始威胁", "初始威胁"),
            ("意志", "意志力"),
            ("攻击", "攻击力"),
            ("防御", "防御力"),
            ("生命", "生命值"),
            ("任务点", "任务点"),
        )
    else:
        fields = (
            ("探险面", "探险编号"),
            ("探险进度", "探险进度"),
            ("交战", "交战值"),
            ("威胁", "威胁值"),
            ("攻击", "攻击力"),
            ("防御", "防御值"),
            ("探险点", "探险点数"),
            ("生命", "生命值"),
            ("意志", "意志值"),
            ("胜利", "胜利"),
            ("费用", "费用"),
        )
    return tuple((label, _text(row.get(column))) for label, column in fields if _text(row.get(column)))


def card_detail_from_row(
    row: Optional[Mapping[str, Any]],
    *,
    kind: Optional[str] = None,
) -> Optional[CardDetailPayload]:
    """空编号行返回详情；有编号、缺编号字段或无行时返回 ``None``。"""

    if not row or "编号" not in row:
        return None
    raw_number = row.get("编号")
    if not isinstance(raw_number, str) or raw_number.strip():
        return None
    normalized_kind = _normalized(kind)
    if normalized_kind not in ("player", "encounter"):
        fields = set(row)
        if {"遭遇组", "探险编号", "交战值", "威胁值"} & fields:
            normalized_kind = "encounter"
        elif {"派系", "卡牌费用", "备用卡牌名称", "独有"} & fields:
            normalized_kind = "player"
        else:
            normalized_kind = "player"

    primary_name = _text(row.get("卡牌名称"))
    alternate_name = _text(row.get("备用卡牌名称"))
    name = primary_name or alternate_name or _text(row.get("英文名称")) or "未知卡牌"
    secondary_name = ""
    if normalized_kind == "player":
        if primary_name and alternate_name and primary_name != alternate_name:
            secondary_name = alternate_name
    else:
        english_name = _text(row.get("英文名称"))
        if english_name and english_name != name:
            secondary_name = english_name

    unique_raw = _text(row.get("独有"))
    unique = bool(unique_raw) and unique_raw.casefold() not in {"否", "n", "no", "0", "false"}

    if normalized_kind == "player":
        traits = _deduplicated_parts(row.get("属性"), row.get("种族"))
        keywords = tuple(
            keyword
            for keyword in (
                _keyword_value("警戒", row.get("警戒")),
                _keyword_value("远攻", row.get("远攻")),
                _keyword_value("限制", row.get("限制")),
                _keyword_value("隐匿", row.get("隐匿"), numeric=True),
                _keyword_value("厄运", row.get("厄运"), numeric=True),
                _keyword_value("遭遇", row.get("遭遇"), numeric=True),
                _keyword_value("守护", row.get("守护")),
                _keyword_value("协同", row.get("协同")),
            )
            if keyword
        )
        extra_keywords = _deduplicated_parts(row.get("关键字"))
        keywords = _deduplicated_parts(*keywords, *extra_keywords)
        rules_text = _text(row.get("规则文字"))
    else:
        traits = _deduplicated_parts(row.get("特性"))
        keywords = _deduplicated_parts(row.get("关键字"))
        rule_parts = tuple(
            part
            for part in (
                _text(row.get("规则文字")),
                _text(row.get("规则效果")),
                _text(row.get("文本")),
                _text(row.get("魔影效果")),
            )
            if part
        )
        rules_text = "\n\n".join(dict.fromkeys(rule_parts))

    return CardDetailPayload(
        kind=normalized_kind,
        name=name,
        secondary_name=secondary_name,
        card_type=_text(row.get("类型")),
        sphere=_text(row.get("派系")),
        series=_text(row.get("系列")),
        encounter_set=_text(row.get("遭遇组")),
        unique=unique,
        stats=_stats_from_row(row, normalized_kind),
        traits=traits,
        keywords=keywords,
        rules_text=rules_text,
    )


@dataclass
class _CsvIndex:
    signature: tuple[int, int]
    kind: str
    by_image: dict[str, list[dict[str, str]]]
    by_series_name: dict[tuple[str, str], list[dict[str, str]]]


_INDEX_CACHE: dict[tuple[Path, str], _CsvIndex] = {}


def clear_card_detail_cache() -> None:
    _INDEX_CACHE.clear()


def _image_tokens(value: Any) -> tuple[str, ...]:
    raw = _COPY_SUFFIX_RE.sub("", _text(value)).replace("\\", "/")
    if not raw:
        return ()
    name = raw.rsplit("/", 1)[-1].casefold()
    tokens = [name]
    suffix = Path(name).suffix.casefold()
    if suffix in _IMAGE_SUFFIXES:
        tokens.append(name[: -len(suffix)])
    return tuple(dict.fromkeys(token for token in tokens if token))


def _index_csv(path: Path, requested_kind: Optional[str] = None) -> Optional[_CsvIndex]:
    try:
        stat = path.stat()
    except OSError:
        return None
    signature = (stat.st_mtime_ns, stat.st_size)
    cache_kind = _normalized(requested_kind)
    if cache_kind not in ("player", "encounter"):
        cache_kind = ""
    cache_key = (path, cache_kind)
    cached = _INDEX_CACHE.get(cache_key)
    if cached is not None and cached.signature == signature:
        return cached
    try:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, csv.Error):
        return None
    if rows:
        fields = set(rows[0])
    else:
        fields = set()
    kind = _normalized(requested_kind)
    if kind not in ("player", "encounter"):
        if {"遭遇组", "探险编号", "交战值", "威胁值"} & fields:
            kind = "encounter"
        elif {"派系", "卡牌费用", "备用卡牌名称", "独有"} & fields:
            kind = "player"
        else:
            kind = "player"
    by_image: dict[str, list[dict[str, str]]] = {}
    by_series_name: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        for token in _image_tokens(row.get("图片链接")):
            by_image.setdefault(token, []).append(row)
        series = _normalized(row.get("系列"))
        for name_key in ("卡牌名称", "备用卡牌名称", "英文名称"):
            name = _normalized(row.get(name_key))
            if name:
                by_series_name.setdefault((series, name), []).append(row)
    index = _CsvIndex(signature, kind, by_image, by_series_name)
    _INDEX_CACHE[cache_key] = index
    return index


def _source_value(source: Any, *names: str) -> Any:
    if isinstance(source, Mapping):
        for name in names:
            if name in source and source.get(name) not in (None, ""):
                return source.get(name)
        return None
    for name in names:
        value = getattr(source, name, None)
        if value not in (None, ""):
            return value
    return None


def _choose_row(
    rows: list[dict[str, str]],
    *,
    series: str,
    name: str,
    allow_duplicate_identity: bool = False,
) -> Optional[dict[str, str]]:
    if not rows:
        return None
    series_key = _normalized(series)
    name_key = _normalized(name)
    if series_key or name_key:
        filtered = []
        for row in rows:
            row_series = _normalized(row.get("系列"))
            row_names = {
                _normalized(row.get("卡牌名称")),
                _normalized(row.get("备用卡牌名称")),
                _normalized(row.get("英文名称")),
            }
            if series_key and row_series != series_key:
                continue
            if name_key and name_key not in row_names:
                continue
            filtered.append(row)
        if filtered:
            rows = filtered
    first = rows[0]
    if all(dict(candidate) == dict(first) for candidate in rows[1:]):
        return first
    if allow_duplicate_identity:
        identity_fields = (
            "系列",
            "遭遇组",
            "编号",
            "卡牌名称",
            "备用卡牌名称",
            "英文名称",
            "图片链接",
            "类型",
        )
        first_identity = tuple(_normalized(first.get(field)) for field in identity_fields)
        if all(
            tuple(_normalized(candidate.get(field)) for field in identity_fields)
            == first_identity
            for candidate in rows[1:]
        ):
            return first
    return first if len(rows) == 1 else None


def resolve_card_detail(
    source: Any = None,
    *,
    row: Optional[Mapping[str, Any]] = None,
    image_path: Any = "",
    card_id: Any = "",
    series: Any = "",
    name: Any = "",
    kind: Optional[str] = None,
    csv_path: Optional[Path] = None,
) -> Optional[CardDetailPayload]:
    """按源行、图片标识、系列+名称依次解析空编号卡详情。"""

    if row is not None:
        return card_detail_from_row(row, kind=kind)

    source_kind = _normalized(kind or _source_value(source, "deck_type", "kind"))
    if source_kind not in ("player", "encounter"):
        module_name = getattr(type(source), "__module__", "") if source is not None else ""
        source_kind = "encounter" if "遭遇" in module_name else "player" if "玩家" in module_name else ""
    source_image = image_path or _source_value(source, "image_path", "图片链接")
    source_id = card_id or _source_value(source, "id", "card_id")
    source_series = _text(series or _source_value(source, "series", "系列"))
    source_name = _text(name or _source_value(source, "name", "卡牌名称", "备用卡牌名称"))

    if csv_path is not None:
        candidates = [(Path(csv_path), source_kind or None)]
    elif source_kind in _DEFAULT_CSV_PATHS:
        candidates = [(_DEFAULT_CSV_PATHS[source_kind], source_kind)]
    else:
        candidates = [
            (_DEFAULT_CSV_PATHS["player"], "player"),
            (_DEFAULT_CSV_PATHS["encounter"], "encounter"),
        ]

    for path, candidate_kind in candidates:
        index = _index_csv(path, candidate_kind)
        if index is None:
            continue
        matched: Optional[dict[str, str]] = None
        for identifier in (source_image, source_id):
            for token in _image_tokens(identifier):
                matched = _choose_row(
                    index.by_image.get(token, []),
                    series=source_series,
                    name=source_name,
                    allow_duplicate_identity=True,
                )
                if matched is not None:
                    break
            if matched is not None:
                break
        if matched is None and source_name:
            matched = _choose_row(
                index.by_series_name.get(
                    (_normalized(source_series), _normalized(source_name)), []
                ),
                series=source_series,
                name=source_name,
            )
        if matched is None and source_name and not source_series:
            cross_series: list[dict[str, str]] = []
            name_key = _normalized(source_name)
            for (row_series, row_name), rows in index.by_series_name.items():
                if row_name == name_key:
                    cross_series.extend(rows)
            matched = _choose_row(cross_series, series="", name=source_name)
        if matched is not None:
            return card_detail_from_row(matched, kind=index.kind)
    return None


class CardDetailSettingsDialog(QDialog):
    """全局详情字号设置；滑块变化会立即更新所有现存详情面板。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("cardDetailSettingsDialog")
        self.setWindowTitle("详情界面设置")
        self.setWindowModality(Qt.NonModal)
        self.setMinimumWidth(390)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        heading = QLabel("详情文字与图标大小", self)
        heading_font = heading.font()
        heading_font.setPointSize(12)
        heading_font.setBold(True)
        heading.setFont(heading_font)
        layout.addWidget(heading)

        hint = QLabel("拖动滑块可按 1 像素微调；修改会立即生效并在重启后保留。", self)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.value_label = QLabel(self)
        self.value_label.setObjectName("cardDetailFontSizeValue")
        self.value_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.value_label)

        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setObjectName("cardDetailFontSizeSlider")
        self.slider.setRange(CARD_DETAIL_MIN_FONT_SIZE, CARD_DETAIL_MAX_FONT_SIZE)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(1)
        self.slider.setTickInterval(1)
        self.slider.setTickPosition(QSlider.TicksBelow)
        self.slider.setTracking(True)
        self.slider.setValue(card_detail_font_size())
        self.slider.valueChanged.connect(set_card_detail_font_size)
        layout.addWidget(self.slider)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.default_button = QPushButton("默认", self)
        self.default_button.setObjectName("cardDetailFontSizeDefaultButton")
        self.default_button.setToolTip(
            f"恢复默认正文大小 {CARD_DETAIL_DEFAULT_FONT_SIZE}px"
        )
        self.close_button = QPushButton("关闭", self)
        self.close_button.setObjectName("cardDetailSettingsCloseButton")
        self.default_button.clicked.connect(reset_card_detail_font_size)
        self.close_button.clicked.connect(self.close)
        button_row.addWidget(self.default_button)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

        _CARD_DETAIL_APPEARANCE.font_size_changed.connect(self._sync_font_size)
        self._sync_font_size(card_detail_font_size())

    def _sync_font_size(self, font_size: int) -> None:
        if self.slider.value() != font_size:
            blocked = self.slider.blockSignals(True)
            self.slider.setValue(font_size)
            self.slider.blockSignals(blocked)
        self.value_label.setText(
            f"正文 {font_size}px　·　图标 {card_detail_icon_size(font_size)}px"
        )

    def showEvent(self, event) -> None:
        self._sync_font_size(card_detail_font_size())
        super().showEvent(event)


class CardDetailPanel(QFrame):
    """浅色、自动换行且可滚动的卡牌详情面板。"""

    MIN_WIDTH = 230
    PREFERRED_WIDTH = 340

    def __init__(self, payload: CardDetailPayload, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.payload = payload
        self._body_labels: list[tuple[QLabel, bool]] = []
        self.secondary_label: Optional[QLabel] = None
        self.traits_label: Optional[QLabel] = None
        self.keyword_label: Optional[QLabel] = None
        self.setObjectName("cardDetailPanel")
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.setStyleSheet(
            "QFrame#cardDetailPanel {"
            " background-color: #c6c7c9; border: 1px solid #555;"
            " border-radius: 7px; color: #151515;"
            "}"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame(self)
        header.setObjectName("cardDetailHeader")
        header.setStyleSheet(
            "QFrame#cardDetailHeader {"
            " background-color: #555b63; border: none;"
            " border-top-left-radius: 7px; border-top-right-radius: 7px;"
            "}"
        )
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_layout.setSpacing(2)
        self.title_label = QLabel(
            ("◆ " if payload.unique else "") + payload.name,
            header,
        )
        title_font = QFont()
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("color: white; background: transparent;")
        header_layout.addWidget(self.title_label)
        if payload.secondary_name:
            self.secondary_label = QLabel(payload.secondary_name, header)
            self.secondary_label.setObjectName("cardDetailSecondaryName")
            self.secondary_label.setWordWrap(True)
            self.secondary_label.setStyleSheet("color: #e5e7ea;")
            header_layout.addWidget(self.secondary_label)
        outer.addWidget(header)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("cardDetailScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet(
            "QScrollArea#cardDetailScroll { background: transparent; border: none; }"
            "QScrollArea#cardDetailScroll > QWidget > QWidget { background: transparent; }"
        )
        self.body_widget = QWidget()
        self.body_widget.setStyleSheet("background: transparent;")
        self.body_layout = QVBoxLayout(self.body_widget)
        self.body_layout.setContentsMargins(12, 9, 12, 12)
        self.body_layout.setSpacing(7)

        meta_parts = [part for part in (payload.card_type, payload.sphere) if part]
        if payload.unique:
            meta_parts.append("独有")
        for set_name in (payload.series, payload.encounter_set):
            if set_name and set_name not in meta_parts:
                meta_parts.append(set_name)
        self._meta_parts = tuple(meta_parts)
        self.meta_label = self._body_label(
            "",
            bold=True,
            rich=True,
        )
        self.meta_label.setObjectName("cardDetailMeta")
        self.meta_label.setAccessibleName(" · ".join(meta_parts))
        if meta_parts:
            self.body_layout.addWidget(self.meta_label)

        if payload.stats:
            stats_text = "　".join(f"{label} {value}" for label, value in payload.stats)
            self.stats_label = self._body_label(
                "",
                rich=True,
            )
            self.stats_label.setObjectName("cardDetailStats")
            self.stats_label.setAccessibleName(stats_text)
            self.body_layout.addWidget(self.stats_label)
        else:
            self.stats_label = None

        if payload.traits:
            self.traits_label = self._body_label("特性：" + " · ".join(payload.traits))
            self.traits_label.setObjectName("cardDetailTraits")
            self.traits_label.setStyleSheet("font-style: italic; color: #333;")
            self.body_layout.addWidget(self.traits_label)

        if payload.keywords:
            self.keyword_label = self._body_label(
                "关键词：" + " · ".join(payload.keywords),
                bold=True,
            )
            self.keyword_label.setObjectName("cardDetailKeywords")
            self.body_layout.addWidget(self.keyword_label)

        if payload.rules_text:
            divider = QFrame()
            divider.setFrameShape(QFrame.HLine)
            divider.setStyleSheet("color: #777;")
            self.body_layout.addWidget(divider)
            self.rules_label = self._body_label("", rich=True)
            self.rules_label.setObjectName("cardDetailRules")
            self.rules_label.setAccessibleName(payload.rules_text)
            self.rules_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            self.body_layout.addWidget(self.rules_label)
        else:
            self.rules_label = None
        self.body_layout.addStretch(1)
        self.scroll_area.setWidget(self.body_widget)
        outer.addWidget(self.scroll_area, 1)

        _CARD_DETAIL_APPEARANCE.font_size_changed.connect(self._apply_font_size)
        self._apply_font_size(card_detail_font_size())

    def _body_label(
        self,
        text: str,
        *,
        bold: bool = False,
        rich: bool = False,
    ) -> QLabel:
        label = QLabel(text)
        label.setTextFormat(Qt.RichText if rich else Qt.PlainText)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        label.setTextInteractionFlags(Qt.NoTextInteraction)
        label.setStyleSheet("color: #151515;")
        font = label.font()
        font.setPixelSize(card_detail_font_size())
        font.setBold(bold)
        label.setFont(font)
        label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self._body_labels.append((label, bold))
        return label

    def _render_icon_text(self, icon_size: int) -> None:
        meta_fragments: list[str] = []
        meta_icon_names: list[str] = []
        for part in self._meta_parts:
            sphere_path = (
                _SPHERE_ICON_PATHS.get(part) if part == self.payload.sphere else None
            )
            if sphere_path is not None and _icon_is_available(sphere_path):
                meta_fragments.append(_icon_html(sphere_path, part, size=icon_size))
                meta_icon_names.append(part)
            else:
                meta_fragments.append(html.escape(part))
        self.meta_label.setText(" &nbsp;·&nbsp; ".join(meta_fragments))
        self.meta_label.setProperty("inlineIconNames", tuple(meta_icon_names))

        if self.stats_label is not None:
            stats_fragments: list[str] = []
            stats_icon_names: list[str] = []
            for label, value in self.payload.stats:
                icon_path = _STAT_ICON_PATHS.get(label)
                if icon_path is not None and _icon_is_available(icon_path):
                    stats_fragments.append(
                        '<span style="white-space: nowrap;">'
                        + _icon_html(icon_path, label, size=icon_size)
                        + "&nbsp;"
                        + f"<b>{html.escape(value)}</b>"
                        + "</span>"
                    )
                    stats_icon_names.append(label)
                else:
                    stats_fragments.append(
                        '<span style="white-space: nowrap;">'
                        + html.escape(f"{label} {value}")
                        + "</span>"
                    )
            self.stats_label.setText(" &nbsp; ".join(stats_fragments))
            self.stats_label.setProperty("inlineIconNames", tuple(stats_icon_names))

        if self.rules_label is not None:
            rules_html, rule_icon_names = _rules_rich_text(
                self.payload.rules_text,
                icon_size=icon_size,
            )
            self.rules_label.setText(rules_html)
            self.rules_label.setProperty("inlineIconNames", rule_icon_names)

    def _apply_font_size(self, font_size: int) -> None:
        font_size = max(
            CARD_DETAIL_MIN_FONT_SIZE,
            min(CARD_DETAIL_MAX_FONT_SIZE, int(font_size)),
        )
        icon_size = card_detail_icon_size(font_size)
        self.setProperty("detailFontSize", font_size)
        self.setProperty("detailIconSize", icon_size)

        title_font = self.title_label.font()
        title_font.setPointSize(font_size + 2)
        title_font.setBold(True)
        self.title_label.setFont(title_font)

        if self.secondary_label is not None:
            secondary_font = self.secondary_label.font()
            secondary_font.setPixelSize(max(7, font_size - 1))
            self.secondary_label.setFont(secondary_font)

        for label, bold in self._body_labels:
            body_font = label.font()
            body_font.setPixelSize(font_size)
            body_font.setBold(bold)
            label.setFont(body_font)

        self._render_icon_text(icon_size)
        self.body_layout.invalidate()
        self.body_widget.updateGeometry()
        self.updateGeometry()

    def sizeHint(self) -> QSize:
        return QSize(self.PREFERRED_WIDTH, 330)


class CardPreviewWidget(QFrame):
    """卡图与可选详情的共享横向/纵向组合控件。"""

    SPACING = 12

    def __init__(
        self,
        pixmap: QPixmap,
        details: Optional[CardDetailPayload] = None,
        *,
        orientation: str = "horizontal",
        max_width: int = 800,
        max_height: int = 900,
        image_style: str = "border: 2px solid #888;",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("background: transparent; border: none;")
        self.image_label = QLabel(self)
        self.image_label.setObjectName("cardPreviewImage")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet(image_style)
        self.image_label.setContextMenuPolicy(Qt.NoContextMenu)
        self.detail_panel: Optional[CardDetailPanel] = None
        self._orientation = "vertical" if orientation == "vertical" else "horizontal"
        self._layout = QBoxLayout(
            QBoxLayout.TopToBottom
            if self._orientation == "vertical"
            else QBoxLayout.LeftToRight,
            self,
        )
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._pixmap = QPixmap(pixmap)
        self._details = details
        self.reflow(max_width=max_width, max_height=max_height)

    @property
    def details(self) -> Optional[CardDetailPayload]:
        return self._details

    def set_content(
        self,
        pixmap: QPixmap,
        details: Optional[CardDetailPayload],
        *,
        max_width: int,
        max_height: int,
        orientation: Optional[str] = None,
    ) -> QSize:
        self._pixmap = QPixmap(pixmap)
        self._details = details
        if orientation is not None:
            self._orientation = "vertical" if orientation == "vertical" else "horizontal"
        return self.reflow(max_width=max_width, max_height=max_height)

    def _replace_layout(self) -> None:
        while self._layout.count():
            self._layout.takeAt(0)
        if self.detail_panel is not None:
            self.detail_panel.setParent(None)
            self.detail_panel.deleteLater()
            self.detail_panel = None
        self._layout.setDirection(
            QBoxLayout.TopToBottom
            if self._orientation == "vertical"
            else QBoxLayout.LeftToRight
        )
        self._layout.setSpacing(self.SPACING if self._details is not None else 0)

    def reflow(self, *, max_width: int, max_height: int) -> QSize:
        max_width = max(1, int(max_width))
        max_height = max(1, int(max_height))
        self._replace_layout()
        if self._pixmap.isNull():
            scaled = QPixmap()
            image_size = QSize(1, 1)
        elif self._details is None:
            scaled = self._pixmap.scaled(
                max_width, max_height, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            image_size = scaled.size()
        elif self._orientation == "horizontal":
            panel_width = min(
                CardDetailPanel.PREFERRED_WIDTH,
                max(180, int(max_width * 0.40)),
            )
            image_max_width = max(1, max_width - panel_width - self.SPACING)
            scaled = self._pixmap.scaled(
                image_max_width,
                max_height,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            image_size = scaled.size()
        else:
            preferred_detail_height = min(300, max(100, int(max_height * 0.34)))
            preferred_detail_height = min(
                preferred_detail_height,
                max(1, max_height - self.SPACING - 1),
            )
            image_max_height = max(1, max_height - preferred_detail_height - self.SPACING)
            scaled = self._pixmap.scaled(
                max_width,
                image_max_height,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            image_size = scaled.size()

        self.image_label.setPixmap(scaled)
        self.image_label.setFixedSize(image_size)
        if self._details is None:
            self._layout.addWidget(self.image_label)
            total = QSize(image_size)
        elif self._orientation == "horizontal":
            panel_width = min(
                CardDetailPanel.PREFERRED_WIDTH,
                max(1, max_width - image_size.width() - self.SPACING),
            )
            panel_height = max(1, min(max_height, image_size.height()))
            self.detail_panel = CardDetailPanel(self._details, self)
            self.detail_panel.setFixedSize(panel_width, panel_height)
            self._layout.addWidget(self.image_label, 0, Qt.AlignTop)
            self._layout.addWidget(self.detail_panel, 0, Qt.AlignTop)
            total = QSize(image_size.width() + self.SPACING + panel_width, panel_height)
        else:
            panel_width = min(max_width, max(1, image_size.width(), min(230, max_width)))
            panel_height = max(
                1,
                min(300, max_height - image_size.height() - self.SPACING),
            )
            self.detail_panel = CardDetailPanel(self._details, self)
            self.detail_panel.setFixedSize(panel_width, panel_height)
            self._layout.addWidget(self.image_label, 0, Qt.AlignHCenter)
            self._layout.addWidget(self.detail_panel, 0, Qt.AlignHCenter)
            total = QSize(panel_width, image_size.height() + self.SPACING + panel_height)
        self.setFixedSize(total)
        return total
