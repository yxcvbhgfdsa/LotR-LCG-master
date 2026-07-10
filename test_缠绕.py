"""“缠绕”关键词的无 GUI 规则测试。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from 主脚本 import (
    MainWindow,
    _has_entangle_keyword,
    _parse_entangle_condition,
)


class EntangleHarness(SimpleNamespace):
    ENTANGLE_THREAT_BONUS = MainWindow.ENTANGLE_THREAT_BONUS
    ENTANGLE_CONDITION_ALIASES = MainWindow.ENTANGLE_CONDITION_ALIASES

    _is_entangled_enemy = MainWindow._is_entangled_enemy
    _entangle_condition_key = MainWindow._entangle_condition_key
    _entangle_condition_for_card = MainWindow._entangle_condition_for_card
    _location_threat_modifier = MainWindow._location_threat_modifier
    _is_immune_to_player_effects = MainWindow._is_immune_to_player_effects

    def _refresh_staging_row(self, *args, **kwargs):
        self.refresh_count = getattr(self, "refresh_count", 0) + 1

    def _sync_location_attachment_passives(self, *_args):
        self.sync_count = getattr(self, "sync_count", 0) + 1

    def _on_enemy_added_to_staging(self, enemy):
        self.released_entered.append(enemy)
        return []

    def _pick_entangle_location(self, locations, _condition):
        return self.selected_location if locations else None

    def _entangle_location_candidates(self, _condition):
        return list(self.candidate_locations)

    def _is_strength_of_the_earth_attachment(self, _card):
        return False

    def _is_sunken_treasury_location(self, _card):
        return False

    def _belegost_loot_attached_to_heroes_count(self):
        return 0

    def _is_burning_piers_location(self, _card):
        return False

    def _location_damage_count(self, _card):
        return 0

    def _voyage_calphons_divination_ocean_threat_bonus_for(self, _card):
        return 0


class LocationSelectionHarness(EntangleHarness):
    _entangle_location_candidates = MainWindow._entangle_location_candidates

    def _staging_location_cards(self):
        return list(self.staging_cards)

    def _is_immune_to_player_effects(self, _card):
        return False

    def _staging_host_widget_for_card(self, _card):
        return None

    def _card_threat_value(self, location, _widget=None):
        return self.threat_values[location.id]

    def _location_progress_required(self, location):
        return self.progress_values[location.id]


def card(card_id="enemy-1", **kwargs):
    defaults = {
        "id": card_id,
        "name": "缠绕触手",
        "type": "敌人",
        "Keywords": "缠绕（最高威胁的地区）",
        "Text_Effect": "",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class EntangleTests(unittest.TestCase):
    def test_keyword_parser_prefers_keywords_and_supports_parentheses(self):
        enemy = card(Keywords="缠绕(最高威胁的地区)", Text_Effect="缠绕（错误条件）")
        self.assertEqual(_parse_entangle_condition(enemy), "最高威胁的地区")
        self.assertTrue(_has_entangle_keyword(enemy))

    def test_keyword_parser_falls_back_to_rules_text(self):
        enemy = card(Keywords="", Text_Effect="缠绕（最高探险点数的地区）")
        self.assertEqual(_parse_entangle_condition(enemy), "最高探险点数的地区")

    def test_resolve_attaches_enemy_face_down_and_removes_it_from_staging(self):
        location = SimpleNamespace(id="location-1", name="最高威胁地区")
        enemy = card()
        harness = EntangleHarness(
            _entangled_enemy_ids=set(),
            _facedown_attachment_ids=set(),
            _location_attachments={},
            _destroyed_enemies=set(),
            staging_cards=[enemy],
            candidate_locations=[location],
            selected_location=location,
            released_entered=[],
        )
        notes = MainWindow._resolve_entangle_keyword(harness, enemy)
        self.assertTrue(notes)
        self.assertNotIn(enemy, harness.staging_cards)
        self.assertIn(enemy, harness._location_attachments[location.id])
        self.assertIn(enemy.id, harness._entangled_enemy_ids)
        self.assertIn(enemy.id, harness._facedown_attachment_ids)

    def test_no_matching_location_keeps_enemy_unattached(self):
        enemy = card()
        harness = EntangleHarness(
            _entangled_enemy_ids=set(),
            _facedown_attachment_ids=set(),
            _location_attachments={},
            _destroyed_enemies=set(),
            staging_cards=[enemy],
            candidate_locations=[],
            selected_location=None,
            released_entered=[],
        )
        MainWindow._resolve_entangle_keyword(harness, enemy)
        self.assertIn(enemy, harness.staging_cards)
        self.assertNotIn(enemy.id, harness._entangled_enemy_ids)

    def test_location_candidates_select_highest_threat_and_highest_progress(self):
        low = SimpleNamespace(id="location-low", name="低")
        high = SimpleNamespace(id="location-high", name="高")
        tied = SimpleNamespace(id="location-tied", name="并列")
        harness = LocationSelectionHarness(
            staging_cards=[low, high, tied],
            threat_values={low.id: 2, high.id: 5, tied.id: 5},
            progress_values={low.id: 1, high.id: 4, tied.id: 2},
        )
        threat_targets = MainWindow._entangle_location_candidates(
            harness, "最高威胁的地区"
        )
        progress_targets = MainWindow._entangle_location_candidates(
            harness, "最高探险点数的地区"
        )
        self.assertEqual({card.id for card in threat_targets}, {high.id, tied.id})
        self.assertEqual([card.id for card in progress_targets], [high.id])

    def test_entangled_enemy_adds_two_threat_and_is_immune(self):
        location = SimpleNamespace(id="location-1")
        enemy = card()
        harness = EntangleHarness(
            _entangled_enemy_ids={enemy.id},
            _location_attachments={location.id: [enemy]},
        )
        self.assertEqual(MainWindow._location_threat_modifier(harness, location), 2)
        self.assertTrue(MainWindow._is_immune_to_player_effects(harness, enemy))

    def test_release_flips_enemy_and_calls_normal_staging_enter_hook(self):
        location = SimpleNamespace(id="location-1", name="海湾")
        enemy = card()
        harness = EntangleHarness(
            _entangled_enemy_ids={enemy.id},
            _facedown_attachment_ids={enemy.id},
            _location_attachments={location.id: [enemy]},
            _destroyed_enemies=set(),
            staging_cards=[],
            released_entered=[],
        )
        notes = MainWindow._release_entangled_enemies_from_location(
            harness, location, reason="测试"
        )
        self.assertTrue(notes)
        self.assertIn(enemy, harness.staging_cards)
        self.assertNotIn(enemy.id, harness._entangled_enemy_ids)
        self.assertNotIn(enemy.id, harness._facedown_attachment_ids)
        self.assertEqual(harness.released_entered, [enemy])


if __name__ == "__main__":
    unittest.main()
