from __future__ import annotations

import csv
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5.QtCore import QEvent, QPoint, QRect, QSettings, Qt
from PyQt5.QtGui import QColor, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QWidget,
)
from PyQt5.QtTest import QTest

from card_drag_zoom import CardDragZoomController
import card_detail_ui as card_detail_module
from card_detail_ui import (
    CARD_DETAIL_DEFAULT_FONT_SIZE,
    CARD_DETAIL_MAX_FONT_SIZE,
    CARD_DETAIL_MIN_FONT_SIZE,
    CardDetailPanel,
    CardDetailPayload,
    CardDetailSettingsDialog,
    CardPreviewWidget,
    card_detail_font_size,
    card_detail_icon_size,
    card_detail_from_row,
    clear_card_detail_cache,
    reset_card_detail_font_size,
    resolve_card_detail,
    set_card_detail_font_size,
)
from CardWidget import CardImageZoomDialog as EncounterCardImageZoomDialog
from CardViewer import ImageDialog as CardViewerImageDialog
from 玩家CardWidget import (
    CardImageZoomDialog as PlayerCardImageZoomDialog,
    CardWidget as PlayerCardWidget,
)
from 玩家卡抽取 import (
    Card as PlayerCard,
    ImageZoomDialog as PlayerDrawerImageZoomDialog,
    PLAYER_CSV,
    SameNameCardDialog,
    resolve_player_image,
)
from 遭遇抽取 import ImageZoomDialog as EncounterDrawerImageZoomDialog
from 场景 import ImageZoomDialog as SceneImageZoomDialog
from 主脚本 import (
    CharacterImagePickDialog,
    CharacterPickOption,
    DiscardPilePanel,
    MainWindow,
    _CardHoverPreviewController,
    _HeroResourcePayCard,
)


def _detail_row(number: object = "") -> dict[str, object]:
    return {
        "系列": "测试系列",
        "编号": number,
        "派系": "学识",
        "卡牌名称": "测试卡",
        "备用卡牌名称": "Test Card",
        "类型": "盟友",
        "独有": "*",
        "卡牌费用": "2",
        "初始威胁": "9",
        "意志力": "1",
        "攻击力": "2",
        "防御力": "3",
        "生命值": "4",
        "任务点": "5",
        "属性": "游侠.斟候.",
        "种族": "游侠",
        "警戒": "√",
        "远攻": "1",
        "限制": "*",
        "隐匿": "2",
        "厄运": "1",
        "遭遇": "*",
        "守护": "地区",
        "协同": "√",
        "关键字": "胜利 1.警戒.",
        "规则文字": "响应：执行测试效果。",
    }


def _real_player_row(series: str, name: str) -> dict[str, str]:
    with open(PLAYER_CSV, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            row_names = {
                (row.get("卡牌名称") or "").strip(),
                (row.get("备用卡牌名称") or "").strip(),
            }
            if (row.get("系列") or "").strip() == series and name in row_names:
                return row
    raise AssertionError(f"未找到真实玩家卡数据：{series} · {name}")


def _real_player_source(series: str, name: str) -> SimpleNamespace:
    row = _real_player_row(series, name)
    image_path = resolve_player_image((row.get("图片链接") or "").strip())
    if not image_path or not Path(image_path).is_file():
        raise AssertionError(f"未找到真实玩家卡图：{series} · {name}")
    return SimpleNamespace(
        id=Path(image_path).stem,
        name=name,
        series=series,
        image_path=image_path,
        sphere=(row.get("派系") or "").strip(),
    )


class CardDetailAppearanceSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["test-card-detail-settings"])

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._settings_path = Path(self._temp_dir.name) / "detail-settings.ini"
        self._appearance = card_detail_module._CARD_DETAIL_APPEARANCE
        self._original_settings = self._appearance._settings
        self._original_font_size = self._appearance._font_size
        self._test_settings = QSettings(
            str(self._settings_path),
            QSettings.IniFormat,
        )
        self._test_settings.clear()
        self._appearance._settings = self._test_settings
        self._appearance._font_size = CARD_DETAIL_DEFAULT_FONT_SIZE
        self._appearance.font_size_changed.emit(CARD_DETAIL_DEFAULT_FONT_SIZE)
        self.widgets: list[QWidget] = []

    def tearDown(self) -> None:
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        self.app.processEvents()
        self._appearance._settings = self._original_settings
        self._appearance._font_size = self._original_font_size
        self._appearance.font_size_changed.emit(self._original_font_size)
        self._temp_dir.cleanup()

    @staticmethod
    def _inline_icon_sizes(label: QLabel) -> list[tuple[int, int]]:
        return [
            (int(width), int(height))
            for width, height in re.findall(
                r'<img[^>]*width="(\d+)"[^>]*height="(\d+)"',
                label.text(),
            )
        ]

    def _show(self, widget: QWidget) -> QWidget:
        self.widgets.append(widget)
        widget.show()
        self.app.processEvents()
        return widget

    def test_slider_updates_existing_and_future_panels_one_pixel_at_a_time(self) -> None:
        row = _detail_row()
        row["规则文字"] = "获得+1【意志力】并花费**学识**资源。"
        payload = card_detail_from_row(row, kind="player")
        assert payload is not None
        panel = self._show(CardDetailPanel(payload))
        dialog = self._show(CardDetailSettingsDialog())
        assert isinstance(panel, CardDetailPanel)
        assert isinstance(dialog, CardDetailSettingsDialog)

        self.assertEqual(dialog.slider.orientation(), Qt.Horizontal)
        self.assertEqual(dialog.slider.minimum(), CARD_DETAIL_MIN_FONT_SIZE)
        self.assertEqual(dialog.slider.maximum(), CARD_DETAIL_MAX_FONT_SIZE)
        self.assertEqual(dialog.slider.singleStep(), 1)
        self.assertEqual(dialog.slider.pageStep(), 1)

        dialog.slider.setFocus()
        QTest.keyClick(dialog.slider, Qt.Key_Right)
        self.app.processEvents()
        adjusted = CARD_DETAIL_DEFAULT_FONT_SIZE + 1
        adjusted_icon = card_detail_icon_size(adjusted)
        self.assertEqual(card_detail_font_size(), adjusted)
        self.assertEqual(panel.property("detailFontSize"), adjusted)
        self.assertEqual(panel.property("detailIconSize"), adjusted_icon)
        self.assertEqual(panel.title_label.font().pointSize(), adjusted + 2)
        assert panel.secondary_label is not None
        self.assertEqual(panel.secondary_label.font().pixelSize(), adjusted - 1)
        for label in (
            panel.meta_label,
            panel.stats_label,
            panel.traits_label,
            panel.keyword_label,
            panel.rules_label,
        ):
            assert label is not None
            self.assertEqual(label.font().pixelSize(), adjusted)

        for label in (panel.meta_label, panel.stats_label, panel.rules_label):
            assert label is not None
            sizes = self._inline_icon_sizes(label)
            self.assertTrue(sizes)
            self.assertTrue(all(size == (adjusted_icon, adjusted_icon) for size in sizes))

        future_panel = self._show(CardDetailPanel(payload))
        assert isinstance(future_panel, CardDetailPanel)
        self.assertEqual(future_panel.property("detailFontSize"), adjusted)
        self.assertEqual(future_panel.property("detailIconSize"), adjusted_icon)

        QTest.mouseClick(dialog.default_button, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(card_detail_font_size(), CARD_DETAIL_DEFAULT_FONT_SIZE)
        self.assertEqual(dialog.slider.value(), CARD_DETAIL_DEFAULT_FONT_SIZE)
        self.assertEqual(
            panel.property("detailIconSize"),
            card_detail_icon_size(CARD_DETAIL_DEFAULT_FONT_SIZE),
        )
        self.assertFalse(
            self._test_settings.contains(card_detail_module._CARD_DETAIL_FONT_SIZE_KEY)
        )

    def test_saved_size_is_loaded_by_a_new_appearance_manager(self) -> None:
        adjusted = CARD_DETAIL_DEFAULT_FONT_SIZE + 3
        self.assertEqual(set_card_detail_font_size(adjusted), adjusted)
        self._test_settings.sync()

        reloaded_settings = QSettings(str(self._settings_path), QSettings.IniFormat)
        reloaded = card_detail_module._CardDetailAppearance(reloaded_settings)
        self.assertEqual(reloaded.font_size, adjusted)

        self.assertEqual(reset_card_detail_font_size(), CARD_DETAIL_DEFAULT_FONT_SIZE)
        reloaded_after_default = card_detail_module._CardDetailAppearance(
            QSettings(str(self._settings_path), QSettings.IniFormat)
        )
        self.assertEqual(
            reloaded_after_default.font_size,
            CARD_DETAIL_DEFAULT_FONT_SIZE,
        )

    def test_main_window_open_method_reuses_non_modal_settings_dialog(self) -> None:
        host = self._show(QMainWindow())
        host._card_detail_settings_dialog = None

        MainWindow._show_card_detail_settings(host)
        first = host._card_detail_settings_dialog
        self.assertIsInstance(first, CardDetailSettingsDialog)
        self.assertTrue(first.isVisible())

        MainWindow._show_card_detail_settings(host)
        self.assertIs(host._card_detail_settings_dialog, first)

    def test_font_setting_does_not_resize_a_pure_image_preview(self) -> None:
        pixmap = QPixmap(200, 300)
        pixmap.fill(QColor("#536779"))
        preview = self._show(
            CardPreviewWidget(
                pixmap,
                None,
                max_width=400,
                max_height=400,
            )
        )
        before = preview.size()
        set_card_detail_font_size(CARD_DETAIL_DEFAULT_FONT_SIZE + 1)
        self.app.processEvents()
        self.assertEqual(preview.size(), before)


class CardDetailResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_card_detail_cache()

    def test_empty_and_whitespace_numbers_show_complete_player_details(self) -> None:
        for number in ("", "   ", "\t\r\n"):
            with self.subTest(number=repr(number)):
                payload = card_detail_from_row(_detail_row(number), kind="player")
                self.assertIsNotNone(payload)
                assert payload is not None
                self.assertEqual(payload.kind, "player")
                self.assertEqual(payload.name, "测试卡")
                self.assertEqual(payload.secondary_name, "Test Card")
                self.assertEqual(payload.card_type, "盟友")
                self.assertEqual(payload.sphere, "学识")
                self.assertEqual(payload.series, "测试系列")
                self.assertTrue(payload.unique)
                self.assertEqual(
                    payload.stats,
                    (
                        ("费用", "2"),
                        ("初始威胁", "9"),
                        ("意志", "1"),
                        ("攻击", "2"),
                        ("防御", "3"),
                        ("生命", "4"),
                        ("任务点", "5"),
                    ),
                )
                self.assertEqual(payload.traits, ("游侠", "斟候"))
                self.assertEqual(
                    payload.keywords,
                    (
                        "警戒",
                        "远攻",
                        "限制",
                        "隐匿 2",
                        "厄运 1",
                        "遭遇",
                        "守护 地区",
                        "协同",
                        "胜利 1",
                    ),
                )
                self.assertEqual(payload.rules_text, "响应：执行测试效果。")

    def test_numbered_missing_number_and_unmatched_cards_do_not_show_details(self) -> None:
        self.assertIsNone(card_detail_from_row(_detail_row("17"), kind="player"))
        self.assertIsNone(card_detail_from_row(_detail_row(0), kind="player"))
        self.assertIsNone(card_detail_from_row(_detail_row(None), kind="player"))

        row_without_number = _detail_row()
        del row_without_number["编号"]
        self.assertIsNone(card_detail_from_row(row_without_number, kind="player"))

        self.assertIsNone(
            resolve_card_detail(
                name="不存在的测试卡",
                series="不存在的系列",
                kind="player",
                csv_path=PLAYER_CSV,
            )
        )

        zero_stat_row = _detail_row()
        zero_stat_row["卡牌费用"] = 0
        zero_stat_payload = card_detail_from_row(zero_stat_row, kind="player")
        self.assertIsNotNone(zero_stat_payload)
        assert zero_stat_payload is not None
        self.assertIn(("费用", "0"), zero_stat_payload.stats)

    def test_real_blank_number_card_fang_resolves(self) -> None:
        payload = resolve_card_detail(
            SimpleNamespace(name="尖牙", series="洛希尔人的誓言"),
            kind="player",
            csv_path=PLAYER_CSV,
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload.name, "尖牙")
        self.assertEqual(payload.card_type, "盟友")
        self.assertEqual(payload.sphere, "战术")
        self.assertIn("生物", payload.traits)
        self.assertIn("每套牌组限制1张", payload.rules_text)

    def test_real_numbered_core_aragorn_does_not_resolve(self) -> None:
        payload = resolve_card_detail(
            SimpleNamespace(name="阿拉贡", series="基础"),
            kind="player",
            csv_path=PLAYER_CSV,
        )
        self.assertIsNone(payload)

    def test_duplicate_rows_with_the_same_exact_image_identity_still_resolve(self) -> None:
        payload = resolve_card_detail(
            name="德拉加兹屠戮者",
            series="法纳尔的遗产",
            image_path="eb4750c8-2c50-4dc9-a06d-b6e8345ec221.png",
            kind="player",
            csv_path=PLAYER_CSV,
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload.name, "德拉加兹屠戮者")

    def test_series_and_exact_image_matching_do_not_mix_same_name_versions(self) -> None:
        fields = [
            "系列",
            "编号",
            "卡牌名称",
            "备用卡牌名称",
            "图片链接",
            "类型",
            "派系",
            "规则文字",
        ]
        rows = [
            {
                "系列": "系列A",
                "编号": "",
                "卡牌名称": "同名卡",
                "备用卡牌名称": "备用名",
                "图片链接": "blank-version.jpg",
                "类型": "事件",
                "派系": "学识",
                "规则文字": "空编号版本",
            },
            {
                "系列": "系列B",
                "编号": "7",
                "卡牌名称": "同名卡",
                "备用卡牌名称": "",
                "图片链接": "numbered-version.jpg",
                "类型": "事件",
                "派系": "战术",
                "规则文字": "有编号版本",
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "same-name.csv"
            with open(csv_path, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)

            blank = resolve_card_detail(
                name="备用名",
                series="系列A",
                kind="player",
                csv_path=csv_path,
            )
            self.assertIsNotNone(blank)
            self.assertEqual(blank.rules_text, "空编号版本")
            self.assertIsNone(
                resolve_card_detail(
                    name="同名卡",
                    series="系列B",
                    kind="player",
                    csv_path=csv_path,
                )
            )
            self.assertIsNone(
                resolve_card_detail(
                    name="同名卡",
                    kind="player",
                    csv_path=csv_path,
                )
            )
            self.assertIsNone(
                resolve_card_detail(
                    image_path="numbered-version.jpg",
                    name="同名卡",
                    series="系列A",
                    kind="player",
                    csv_path=csv_path,
                )
            )
            self.assertIsNone(
                resolve_card_detail(
                    card_id="blank-version.B",
                    kind="player",
                    csv_path=csv_path,
                )
            )

    def test_generic_encounter_row_is_supported_but_still_requires_blank_number(self) -> None:
        encounter_row = {
            "系列": "测试剧本",
            "遭遇组": "测试遭遇组",
            "编号": " ",
            "卡牌名称": "测试敌军",
            "英文名称": "Test Enemy",
            "类型": "敌军",
            "交战值": "30",
            "威胁值": "2",
            "攻击力": "3",
            "防御值": "1",
            "生命值": "4",
            "特性": "奥克|山地",
            "关键字": "涌现",
            "规则文字": "强制：执行遭遇效果。",
        }
        payload = card_detail_from_row(encounter_row)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload.kind, "encounter")
        self.assertEqual(payload.encounter_set, "测试遭遇组")
        self.assertEqual(payload.traits, ("奥克", "山地"))
        self.assertEqual(payload.keywords, ("涌现",))
        self.assertIn(("交战", "30"), payload.stats)

        numbered = dict(encounter_row, **{"编号": "99"})
        self.assertIsNone(card_detail_from_row(numbered, kind="encounter"))


class CardPreviewWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["test-card-detail-ui"])

    def setUp(self) -> None:
        payload = card_detail_from_row(_detail_row(), kind="player")
        assert payload is not None
        self.payload = payload
        self.pixmap = QPixmap(200, 300)
        self.pixmap.fill(QColor("#6d7f91"))
        self.widgets: list[CardPreviewWidget] = []

    def tearDown(self) -> None:
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def _show(self, widget: CardPreviewWidget) -> CardPreviewWidget:
        self.widgets.append(widget)
        widget.show()
        widget.layout().activate()
        self.app.processEvents()
        return widget

    def _show_panel(self, payload: CardDetailPayload) -> CardDetailPanel:
        panel = CardDetailPanel(payload)
        self.widgets.append(panel)
        panel.show()
        panel.layout().activate()
        self.app.processEvents()
        return panel

    def test_stats_and_meta_use_requested_inline_icons(self) -> None:
        panel = self._show_panel(self.payload)
        assert panel.stats_label is not None

        self.assertEqual(panel.stats_label.textFormat(), Qt.RichText)
        stats_html = panel.stats_label.text()
        for filename in (
            "resource.png",
            "Threat.jpg",
            "Willpower.jpg",
            "attack.png",
            "Defense.png",
        ):
            with self.subTest(filename=filename):
                self.assertIn(filename, stats_html)
        self.assertEqual(
            set(panel.stats_label.property("inlineIconNames")),
            {"费用", "初始威胁", "意志", "攻击", "防御"},
        )
        self.assertIn("费用 2", panel.stats_label.accessibleName())
        self.assertIn("意志 1", panel.stats_label.accessibleName())
        self.assertIn("攻击 2", panel.stats_label.accessibleName())
        self.assertIn("防御 3", panel.stats_label.accessibleName())

        self.assertEqual(panel.meta_label.textFormat(), Qt.RichText)
        self.assertIn("lore.png", panel.meta_label.text())
        self.assertEqual(panel.meta_label.property("inlineIconNames"), ("学识",))
        self.assertIn("学识", panel.meta_label.accessibleName())

        for relative_path in (
            "cards/images/tokens/resource.png",
            "cards/images/Threat.jpg",
            "cards/images/Willpower.jpg",
            "cards/images/attack.png",
            "cards/images/Defense.png",
            "cards/images/icons/lore.png",
        ):
            with self.subTest(relative_path=relative_path):
                self.assertFalse(QPixmap(str(PROJECT_ROOT / relative_path)).isNull())

    def test_rules_replace_marked_stats_and_spheres_without_changing_other_text(self) -> None:
        row = _detail_row()
        row["规则文字"] = (
            "获得+1【意志力】、+1【攻击力】、+1【防御力】，并花费【资源】。"
            "使用【领导】【学识】【战术】【精神】。\n"
            "【中立】与【尖牙】和精神抖擞，<不得&更改>。"
        )
        payload = card_detail_from_row(row, kind="player")
        assert payload is not None
        panel = self._show_panel(payload)
        assert panel.rules_label is not None

        self.assertEqual(panel.rules_label.textFormat(), Qt.RichText)
        rules_html = panel.rules_label.text()
        for filename in (
            "Willpower.jpg",
            "attack.png",
            "Defense.png",
            "resource.png",
            "leadership.png",
            "lore.png",
            "tactics.png",
            "spirit.png",
        ):
            with self.subTest(filename=filename):
                self.assertIn(filename, rules_html)
        self.assertIn("【中立】", rules_html)
        self.assertIn("【尖牙】", rules_html)
        self.assertIn("精神抖擞", rules_html)
        self.assertIn("&lt;不得&amp;更改&gt;", rules_html)
        self.assertIn("<br/>", rules_html)
        self.assertEqual(panel.rules_label.accessibleName(), row["规则文字"])

    def test_unknown_sphere_keeps_readable_text(self) -> None:
        row = _detail_row()
        row["派系"] = "中立"
        payload = card_detail_from_row(row, kind="player")
        assert payload is not None
        panel = self._show_panel(payload)

        self.assertIn("中立", panel.meta_label.text())
        self.assertEqual(panel.meta_label.property("inlineIconNames"), ())

    def test_horizontal_details_are_to_the_right(self) -> None:
        widget = self._show(
            CardPreviewWidget(
                self.pixmap,
                self.payload,
                orientation="horizontal",
                max_width=700,
                max_height=500,
            )
        )
        self.assertEqual(widget.layout().direction(), QBoxLayout.LeftToRight)
        self.assertIsNotNone(widget.detail_panel)
        assert widget.detail_panel is not None
        self.assertGreater(
            widget.detail_panel.geometry().left(),
            widget.image_label.geometry().right(),
        )

    def test_vertical_details_are_below(self) -> None:
        widget = self._show(
            CardPreviewWidget(
                self.pixmap,
                self.payload,
                orientation="vertical",
                max_width=420,
                max_height=650,
            )
        )
        self.assertEqual(widget.layout().direction(), QBoxLayout.TopToBottom)
        self.assertIsNotNone(widget.detail_panel)
        assert widget.detail_panel is not None
        self.assertGreater(
            widget.detail_panel.geometry().top(),
            widget.image_label.geometry().bottom(),
        )

    def test_no_details_preserves_pure_image_size(self) -> None:
        max_width, max_height = 400, 400
        expected = self.pixmap.scaled(
            max_width,
            max_height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        ).size()
        widget = self._show(
            CardPreviewWidget(
                self.pixmap,
                None,
                orientation="horizontal",
                max_width=max_width,
                max_height=max_height,
            )
        )
        self.assertIsNone(widget.detail_panel)
        self.assertEqual(widget.image_label.size(), expected)
        self.assertEqual(widget.size(), expected)
        self.assertEqual(widget.layout().spacing(), 0)

    def test_switching_details_detaches_old_panel_and_clears_content(self) -> None:
        widget = self._show(
            CardPreviewWidget(
                self.pixmap,
                self.payload,
                orientation="horizontal",
                max_width=700,
                max_height=500,
            )
        )
        old_panel = widget.detail_panel
        self.assertIsNotNone(old_panel)

        widget.set_content(
            self.pixmap,
            None,
            max_width=400,
            max_height=400,
        )
        widget.layout().activate()
        self.app.processEvents()
        self.assertIsNone(widget.detail_panel)
        self.assertIsNone(widget.details)
        self.assertEqual(widget.layout().count(), 1)
        assert old_panel is not None
        self.assertIsNone(old_panel.parent())

        replacement = CardDetailPayload(kind="player", name="替换后详情")
        widget.set_content(
            self.pixmap,
            replacement,
            max_width=420,
            max_height=650,
            orientation="vertical",
        )
        widget.layout().activate()
        self.app.processEvents()
        self.assertIsNotNone(widget.detail_panel)
        assert widget.detail_panel is not None
        self.assertEqual(widget.detail_panel.title_label.text(), "替换后详情")
        self.assertEqual(widget.layout().direction(), QBoxLayout.TopToBottom)

    def test_long_text_stays_within_small_bounds_and_uses_qt_scroll_area(self) -> None:
        long_payload = CardDetailPayload(
            kind="player",
            name="长文本测试",
            rules_text=("响应：这是一段用于验证滚动区域的规则文字。" * 80),
        )
        max_width, max_height = 260, 300
        widget = self._show(
            CardPreviewWidget(
                self.pixmap,
                long_payload,
                orientation="vertical",
                max_width=max_width,
                max_height=max_height,
            )
        )
        self.assertLessEqual(widget.width(), max_width)
        self.assertLessEqual(widget.height(), max_height)
        self.assertIsNotNone(widget.detail_panel)
        assert widget.detail_panel is not None
        self.assertGreater(
            widget.detail_panel.scroll_area.verticalScrollBar().maximum(),
            0,
        )


class ZoomDialogEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["test-zoom-dialog-entries"])

    def setUp(self) -> None:
        payload = card_detail_from_row(_detail_row(), kind="player")
        assert payload is not None
        self.payload = payload
        self.pixmap = QPixmap(200, 300)
        self.pixmap.fill(QColor("#8795a3"))
        self.widgets = []

    def tearDown(self) -> None:
        for widget in reversed(self.widgets):
            try:
                widget.close()
            except RuntimeError:
                pass
        self.app.processEvents()

    def _track_and_show(self, widget):
        self.widgets.append(widget)
        widget.show()
        if widget.layout() is not None:
            widget.layout().activate()
        self.app.processEvents()
        return widget

    def _assert_horizontal_details(self, dialog) -> None:
        self.assertIsNotNone(dialog.preview_widget)
        preview = dialog.preview_widget
        self.assertEqual(preview.layout().direction(), QBoxLayout.LeftToRight)
        self.assertIs(preview.details, self.payload)
        self.assertIsNotNone(preview.detail_panel)
        assert preview.detail_panel is not None
        self.assertGreater(
            preview.detail_panel.geometry().left(),
            preview.image_label.geometry().right(),
        )

    def _small_host(self, *, width: int = 360, height: int = 260) -> QMainWindow:
        host = QMainWindow()
        host.setGeometry(137, 83, width, height)
        return self._track_and_show(host)

    def _assert_inside_host(self, host: QMainWindow, dialog) -> None:
        self._track_and_show(dialog)
        self.app.processEvents()
        self.assertTrue(
            host.frameGeometry().contains(dialog.frameGeometry()),
            msg=(
                f"{dialog.__class__.__module__}.{dialog.__class__.__name__} "
                f"frame={dialog.frameGeometry().getRect()} escaped "
                f"host={host.frameGeometry().getRect()}"
            ),
        )

    def test_all_zoom_dialogs_stay_inside_a_small_offset_main_window(self) -> None:
        host = self._small_host()
        dialog_types = (
            PlayerCardImageZoomDialog,
            EncounterCardImageZoomDialog,
            PlayerDrawerImageZoomDialog,
            EncounterDrawerImageZoomDialog,
        )
        for dialog_type in dialog_types:
            with self.subTest(dialog=dialog_type.__module__):
                self._assert_inside_host(
                    host,
                    dialog_type(self.pixmap, parent=host, details=self.payload),
                )

        self._assert_inside_host(
            host,
            SceneImageZoomDialog(self.pixmap, parent=host),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "viewer-bounds.png"
            self.assertTrue(self.pixmap.save(str(image_path), "PNG"))
            self._assert_inside_host(
                host,
                CardViewerImageDialog(
                    str(image_path),
                    "CardViewer bounds",
                    parent=host,
                    details=self.payload,
                ),
            )

    def test_pure_image_zoom_stays_inside_an_extremely_small_main_window(self) -> None:
        host = self._small_host(width=180, height=120)
        for dialog_type in (
            PlayerCardImageZoomDialog,
            EncounterCardImageZoomDialog,
            PlayerDrawerImageZoomDialog,
            EncounterDrawerImageZoomDialog,
            SceneImageZoomDialog,
        ):
            with self.subTest(dialog=dialog_type.__module__):
                self._assert_inside_host(
                    host,
                    dialog_type(self.pixmap, parent=host),
                )

    def test_active_main_window_fallback_uses_the_hosts_screen(self) -> None:
        first_screen_geometry = QRect(0, 0, 800, 600)
        second_screen_geometry = QRect(800, 0, 800, 600)

        class FakeScreen:
            def __init__(self, geometry: QRect) -> None:
                self._geometry = QRect(geometry)

            def availableGeometry(self) -> QRect:
                return QRect(self._geometry)

        first_screen = FakeScreen(first_screen_geometry)
        second_screen = FakeScreen(second_screen_geometry)
        host = QMainWindow()
        host.setGeometry(900, 50, 600, 500)
        orphan = QWidget()
        self.widgets.extend((host, orphan))

        class FakeApplication:
            @staticmethod
            def activeWindow():
                return host

            @staticmethod
            def screenAt(point):
                return second_screen if point.x() >= 800 else first_screen

            @staticmethod
            def primaryScreen():
                return first_screen

            @staticmethod
            def screens():
                return [first_screen, second_screen]

        with patch.object(card_detail_module, "QApplication", FakeApplication):
            available = card_detail_module.card_zoom_available_geometry(orphan)

        self.assertTrue(host.frameGeometry().contains(available))
        self.assertTrue(second_screen_geometry.contains(available))

    def test_player_drawer_zoom_accepts_horizontal_details_and_left_click_closes(self) -> None:
        dialog = self._track_and_show(
            PlayerDrawerImageZoomDialog(self.pixmap, details=self.payload)
        )
        self._assert_horizontal_details(dialog)
        self.assertTrue(dialog.isVisible())

        QTest.mouseClick(dialog, Qt.LeftButton, pos=dialog.rect().center())
        self.app.processEvents()
        self.assertFalse(dialog.isVisible())

    def test_encounter_drawer_zoom_accepts_horizontal_details_and_escape_closes(self) -> None:
        dialog = self._track_and_show(
            EncounterDrawerImageZoomDialog(self.pixmap, details=self.payload)
        )
        self._assert_horizontal_details(dialog)
        self.assertTrue(dialog.isVisible())

        QTest.keyClick(dialog, Qt.Key_Escape)
        self.app.processEvents()
        self.assertFalse(dialog.isVisible())

    def test_detail_scrollbars_remain_interactive_in_all_click_to_close_dialogs(self) -> None:
        long_payload = CardDetailPayload(
            kind="player",
            name="长规则卡",
            rules_text=("这是需要滚动查看的长规则文字。" * 160),
        )
        dialog_types = (
            PlayerCardImageZoomDialog,
            EncounterCardImageZoomDialog,
            PlayerDrawerImageZoomDialog,
            EncounterDrawerImageZoomDialog,
        )
        for dialog_type in dialog_types:
            with self.subTest(dialog=dialog_type.__module__):
                dialog = self._track_and_show(
                    dialog_type(self.pixmap, details=long_payload)
                )
                panel = dialog.preview_widget.detail_panel
                self.assertIsNotNone(panel)
                assert panel is not None
                bar = panel.scroll_area.verticalScrollBar()
                self.assertGreater(bar.maximum(), 0)
                QTest.mouseClick(bar, Qt.LeftButton, pos=bar.rect().center())
                self.app.processEvents()
                self.assertTrue(dialog.isVisible())

    def test_cardviewer_image_dialog_accepts_horizontal_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "viewer-entry.png"
            self.assertTrue(self.pixmap.save(str(image_path), "PNG"))
            dialog = self._track_and_show(
                CardViewerImageDialog(
                    str(image_path),
                    "卡牌详情入口测试",
                    details=self.payload,
                )
            )
            self._assert_horizontal_details(dialog)

    def test_same_name_card_dialog_passes_blank_row_details_to_zoom(self) -> None:
        row = _real_player_row("洛希尔人的誓言", "尖牙")
        dialog = self._track_and_show(SameNameCardDialog([row]))

        dialog.show_selected_card_zoom()
        self.app.processEvents()
        self.assertIsNotNone(dialog.zoom_dialog)
        assert dialog.zoom_dialog is not None
        self.widgets.append(dialog.zoom_dialog)
        self.assertIsNotNone(dialog.zoom_dialog.detail_panel)
        assert dialog.zoom_dialog.detail_panel is not None
        self.assertEqual(dialog.zoom_dialog.detail_panel.payload.name, "尖牙")
        self.assertEqual(
            dialog.zoom_dialog.preview_widget.layout().direction(),
            QBoxLayout.LeftToRight,
        )

    def test_character_image_pick_dialog_passes_card_details_to_zoom(self) -> None:
        card = _real_player_source("洛希尔人的誓言", "尖牙")
        option = CharacterPickOption(
            char_id="fang",
            label=card.name,
            image_path=card.image_path,
            attack=1,
            defense=1,
            health=1,
        )
        dialog = self._track_and_show(
            CharacterImagePickDialog(
                None,
                "卡图选择测试",
                "请选择",
                [option],
            )
        )
        tile = dialog._tiles["fang"]
        self.assertIsNotNone(tile._image._card_detail_payload)

        tile._image.show_zoomed_image()
        self.app.processEvents()
        self.assertIsNotNone(tile._image.zoom_dialog)
        assert tile._image.zoom_dialog is not None
        self.widgets.append(tile._image.zoom_dialog)
        self.assertIsNotNone(tile._image.zoom_dialog.detail_panel)
        assert tile._image.zoom_dialog.detail_panel is not None
        self.assertEqual(tile._image.zoom_dialog.detail_panel.payload.name, "尖牙")

    def test_discard_top_preview_binds_original_card_before_zooming(self) -> None:
        card = PlayerCard.from_csv_row(
            _real_player_row("洛希尔人的誓言", "尖牙")
        )
        panel = self._track_and_show(
            DiscardPilePanel(title="弃牌堆", card_kind="player")
        )
        panel.set_top_card(card)
        self.app.processEvents()
        widget = panel._top_slot.itemAt(0).widget()
        self.assertIsInstance(widget, PlayerCardWidget)
        self.assertIs(widget.current_card, card)

        widget.show_zoomed_card()
        self.app.processEvents()
        self.assertIsNotNone(widget.zoom_dialog)
        assert widget.zoom_dialog is not None
        self.widgets.append(widget.zoom_dialog)
        self.assertIsNotNone(widget.zoom_dialog.detail_panel)
        assert widget.zoom_dialog.detail_panel is not None
        self.assertEqual(widget.zoom_dialog.detail_panel.payload.name, "尖牙")


class PlayerCardZoomEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["test-player-card-zoom"])

    def setUp(self) -> None:
        clear_card_detail_cache()
        self.widgets: list[PlayerCardWidget] = []

    def tearDown(self) -> None:
        for widget in self.widgets:
            if widget.zoom_dialog is not None:
                widget.zoom_dialog.close()
                widget.zoom_dialog.deleteLater()
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def _card_widget(self, name: str, series: str) -> PlayerCardWidget:
        widget = PlayerCardWidget(
            card_name=name,
            series=series,
            max_height=120,
            restore_markers=False,
        )
        self.widgets.append(widget)
        self.assertIsNotNone(widget.current_card)
        self.assertIsNotNone(widget.current_pixmap)
        return widget

    def test_blank_number_zoom_has_details(self) -> None:
        widget = self._card_widget("尖牙", "洛希尔人的誓言")
        widget.show_zoomed_card()
        self.app.processEvents()
        self.assertIsNotNone(widget.zoom_dialog)
        assert widget.zoom_dialog is not None
        self.assertIsNotNone(widget.zoom_dialog.detail_panel)
        self.assertEqual(widget.zoom_dialog.detail_panel.payload.name, "尖牙")

    def test_numbered_zoom_remains_image_only(self) -> None:
        widget = self._card_widget("阿拉贡", "基础")
        widget.show_zoomed_card()
        self.app.processEvents()
        self.assertIsNotNone(widget.zoom_dialog)
        assert widget.zoom_dialog is not None
        self.assertIsNone(widget.zoom_dialog.detail_panel)

    def test_face_down_blank_number_zoom_remains_image_only(self) -> None:
        widget = self._card_widget("尖牙", "洛希尔人的誓言")
        widget.set_face_down(True)
        widget.show_zoomed_card()
        self.app.processEvents()
        self.assertIsNotNone(widget.zoom_dialog)
        assert widget.zoom_dialog is not None
        self.assertIsNone(widget.zoom_dialog.detail_panel)


class HoverAndDragPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["test-hover-card-detail"])

    def setUp(self) -> None:
        self.pixmap = QPixmap(200, 300)
        self.pixmap.fill(QColor("#778899"))
        self.window = QMainWindow()
        self.window.setGeometry(260, 50, 280, 400)
        self.window.show()
        self.app.processEvents()
        self.controller = _CardHoverPreviewController(self.window)

    def tearDown(self) -> None:
        self.controller.hide_preview()
        self.controller._preview.close()
        self.controller._preview.deleteLater()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def _source(self) -> QLabel:
        source = QLabel(self.window)
        source._hover_card_pixmap = self.pixmap
        return source

    def test_hover_is_vertical_and_switching_to_numbered_card_clears_details(self) -> None:
        blank_source = self._source()
        blank_source._card_detail_payload = resolve_card_detail(
            name="尖牙",
            series="洛希尔人的誓言",
            kind="player",
        )
        self.controller._show_source(blank_source)
        self.app.processEvents()

        preview = self.controller._preview_content
        self.assertEqual(preview.layout().direction(), QBoxLayout.TopToBottom)
        self.assertIsNotNone(preview.detail_panel)
        assert preview.detail_panel is not None
        self.assertGreater(
            preview.detail_panel.geometry().top(),
            preview.image_label.geometry().bottom(),
        )

        blank_source._card_detail_payload = None
        blank_source._card_detail_name = "阿拉贡"
        blank_source._card_detail_series = "基础"
        blank_source._card_detail_kind = "player"
        self.controller.eventFilter(blank_source, QEvent(QEvent.Enter))
        self.app.processEvents()
        self.assertIsNone(preview.detail_panel)
        self.assertIsNone(self.controller._source_details)

    def test_face_down_source_hides_and_clears_hover_details(self) -> None:
        source = self._source()
        source._card_detail_payload = resolve_card_detail(
            name="尖牙",
            series="洛希尔人的誓言",
            kind="player",
        )
        self.controller._show_source(source)
        self.assertIsNotNone(self.controller._source_details)

        source._hover_card_face_up = False
        self.controller.eventFilter(source, QEvent(QEvent.Paint))
        self.app.processEvents()
        self.assertFalse(self.controller._preview.isVisible())
        self.assertIsNone(self.controller._source_details)
        self.assertIsNone(self.controller._preview_content.detail_panel)

    def test_visible_player_widget_face_down_update_clears_hover_automatically(self) -> None:
        card_widget = PlayerCardWidget(
            card_name="尖牙",
            series="洛希尔人的誓言",
            max_height=120,
            restore_markers=False,
            parent=self.window,
        )
        card_widget.show()
        self.app.processEvents()
        self.controller._show_source(card_widget)
        self.assertIsNotNone(self.controller._source_details)

        card_widget.set_face_down(True)
        self.app.processEvents()
        self.app.processEvents()
        self.assertFalse(self.controller._preview.isVisible())
        self.assertIsNone(self.controller._source_details)
        self.assertIsNone(self.controller._preview_content.detail_panel)

    def test_hero_payment_card_hover_uses_its_player_card_details(self) -> None:
        hero = _real_player_source("洛希尔人的誓言", "尖牙")
        pay_card = _HeroResourcePayCard(hero, available=1, parent=self.window)
        pay_card.show()
        self.app.processEvents()

        source = self.controller._source_from_widget(pay_card._image)
        self.assertIs(source, pay_card._image)
        assert source is not None
        self.controller._show_source(source)
        self.app.processEvents()
        self.assertIsNotNone(self.controller._source_details)
        assert self.controller._source_details is not None
        self.assertEqual(self.controller._source_details.name, "尖牙")
        self.assertIsNotNone(self.controller._preview_content.detail_panel)

    def test_long_press_drag_live_preview_remains_a_plain_image_label(self) -> None:
        host = QLabel(self.window)
        host.resize(100, 150)
        host.current_pixmap = self.pixmap
        drag = CardDragZoomController(host, lambda: None)
        drag.install()
        drag._update_preview(QPoint(200, 200), 1.5)
        self.app.processEvents()

        self.assertIsInstance(drag._preview, QLabel)
        assert drag._preview is not None
        self.assertIsNone(drag._preview.layout())
        self.assertIsNotNone(drag._preview.pixmap())
        drag._hide_preview()

    def test_hover_reflows_to_a_shorter_side_screen_without_overflow(self) -> None:
        self.controller._source_details = CardDetailPayload(
            kind="player",
            name="异高屏测试",
            rules_text=("长规则文字。" * 100),
        )
        short_available = QRect(0, 0, 400, 180)

        def side_regions(frame, side):
            if side == "right":
                return [(300, 0, short_available)]
            return []

        self.controller._side_regions = side_regions
        chosen = self.controller._choose_region(
            QRect(0, 0, 280, 500),
            self.pixmap,
        )
        self.assertIsNotNone(chosen)
        assert chosen is not None
        _, available, content_size = chosen
        self.assertLessEqual(
            content_size.height() + self.controller.PREVIEW_FRAME_EXTRA,
            available.height() - 2 * self.controller.MARGIN,
        )
        self.assertLessEqual(
            content_size.width() + self.controller.PREVIEW_FRAME_EXTRA,
            300,
        )

    def test_hover_window_shrinks_to_current_vertical_content_width(self) -> None:
        wide_pixmap = QPixmap(600, 200)
        wide_pixmap.fill(QColor("#445566"))
        wide_source = QLabel(self.window)
        wide_source._hover_card_pixmap = wide_pixmap
        self.controller._show_source(wide_source)
        self.app.processEvents()
        # 模拟 Qt 布局在上一张较宽卡牌后遗留的最小宽度缓存。
        self.controller._preview.setMinimumWidth(480)

        narrow_source = self._source()
        narrow_source._card_detail_payload = resolve_card_detail(
            name="尖牙",
            series="洛希尔人的誓言",
            kind="player",
        )
        self.controller._show_source(narrow_source)
        self.app.processEvents()

        content = self.controller._preview_content
        self.assertLess(self.controller._preview.width(), 480)
        self.assertEqual(
            self.controller._preview.width(),
            content.width() + self.controller.PREVIEW_FRAME_EXTRA,
        )
        self.assertEqual(
            self.controller._preview.height(),
            content.height() + self.controller.PREVIEW_FRAME_EXTRA,
        )

    def test_wheel_over_source_scrolls_long_hover_details_without_consuming_event(self) -> None:
        source = self._source()
        source._card_detail_payload = CardDetailPayload(
            kind="player",
            name="长规则卡",
            rules_text=("需要在悬浮详情中滚动查看的规则。" * 160),
        )
        self.controller._show_source(source)
        self.app.processEvents()
        panel = self.controller._preview_content.detail_panel
        self.assertIsNotNone(panel)
        assert panel is not None
        bar = panel.scroll_area.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        self.assertEqual(bar.value(), 0)

        wheel_event = SimpleNamespace(
            type=lambda: QEvent.Wheel,
            angleDelta=lambda: QPoint(0, -120),
        )
        consumed = self.controller.eventFilter(source, wheel_event)
        self.assertFalse(consumed)
        self.assertGreater(bar.value(), 0)


class MainWindowPreviewWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["test-main-preview-wiring"])

    class _CaptureZoomDialog:
        instances = []

        def __init__(self, pixmap, parent=None, *, details=None):
            self.pixmap = pixmap
            self.parent = parent
            self.details = details
            self.title = ""
            type(self).instances.append(self)

        def setWindowTitle(self, title: str) -> None:
            self.title = title

        def exec_(self) -> int:
            return 0

    def setUp(self) -> None:
        self._CaptureZoomDialog.instances.clear()
        self.host = QMainWindow()

    def tearDown(self) -> None:
        self.host.close()
        self.host.deleteLater()
        self.app.processEvents()

    def test_gandalf_deck_top_passes_blank_card_details(self) -> None:
        top_card = _real_player_source("洛希尔人的誓言", "尖牙")
        self.host._gandalf_revealed_deck_top_card = lambda: top_card
        self.host._refresh_gandalf_deck_top_panel = lambda: None
        self.host._inform = lambda *args, **kwargs: None

        with patch("主脚本.CardImageZoomDialog", self._CaptureZoomDialog):
            MainWindow._show_gandalf_deck_top_dialog(self.host)

        self.assertEqual(len(self._CaptureZoomDialog.instances), 1)
        captured = self._CaptureZoomDialog.instances[0]
        self.assertIsNotNone(captured.details)
        self.assertEqual(captured.details.name, "尖牙")

    def test_deck_bottom_action_passes_blank_card_details(self) -> None:
        bottom_card = _real_player_source("洛希尔人的誓言", "尖牙")
        action_card = SimpleNamespace(name="卡冯")
        drawer = SimpleNamespace(
            deck_stack=[bottom_card],
            _ensure_deck_stack=lambda: None,
        )
        self.host._character_card_by_id = lambda char_id: action_card
        self.host._has_calphon_deck_bottom_swap_action = lambda card: True
        self.host._is_character_in_play = lambda char_id: True
        self.host._field_widgets = {
            "calphon": SimpleNamespace(is_exhausted=lambda: False)
        }
        self.host._character_owner_index = lambda char_id: 0
        self.host._player_drawer_for = lambda player_index: drawer
        self.host._player_tag = lambda player_index: "玩家1"
        self.host._inform = lambda *args, **kwargs: None
        self.host._modal_question = lambda *args, **kwargs: QMessageBox.No

        with (
            patch("主脚本.CardImageZoomDialog", self._CaptureZoomDialog),
            patch("builtins.print"),
        ):
            handled = MainWindow._try_calphon_deck_bottom_swap_action(
                self.host,
                "calphon",
            )

        self.assertTrue(handled)
        self.assertEqual(len(self._CaptureZoomDialog.instances), 1)
        captured = self._CaptureZoomDialog.instances[0]
        self.assertIsNotNone(captured.details)
        self.assertEqual(captured.details.name, "尖牙")

if __name__ == "__main__":
    unittest.main()
