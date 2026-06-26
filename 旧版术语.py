"""重置版 / 旧版规则术语对照（来源：旧版译名对照表.csv）。

模拟器 UI 与流程条采用重置版用语；魔戒玩家牌.csv 规则文字多为旧版印刷。
本模块在解析卡牌 Text_Effect 时同时识别两种写法。
"""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, FrozenSet, Set

_PROJECT_ROOT = Path(__file__).resolve().parent
TERMINOLOGY_CSV = _PROJECT_ROOT / "旧版译名对照表.csv"


def _split_terms(value: str) -> list[str]:
    parts: list[str] = []
    for piece in re.split(r"/", value or ""):
        term = piece.strip()
        if term:
            parts.append(term)
    return parts


@lru_cache(maxsize=1)
def load_terminology() -> tuple[
    Dict[str, FrozenSet[str]],
    Dict[str, str],
    Dict[str, str],
]:
    """
    返回:
      variants_by_revised — 重置版词条 → 全部等价写法
      alias_to_revised — 旧版 / 英文 / 重置版子串 → 重置版 canonical
      english_to_revised — 英文 → 重置版
    """
    variants_by_revised: Dict[str, Set[str]] = {}
    alias_to_revised: Dict[str, str] = {}
    english_to_revised: Dict[str, str] = {}

    if not TERMINOLOGY_CSV.is_file():
        return {}, {}, {}

    with open(TERMINOLOGY_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            english = (row.get("英文") or "").strip()
            revised = (row.get("重置版") or "").strip()
            old_raw = (row.get("旧版") or "").strip()
            if not revised:
                continue
            bucket = variants_by_revised.setdefault(revised, set())
            bucket.add(revised)
            for part in _split_terms(revised):
                bucket.add(part)
                alias_to_revised.setdefault(part, revised)
            for part in _split_terms(old_raw):
                bucket.add(part)
                alias_to_revised[part] = revised
            if english:
                bucket.add(english)
                english_to_revised[english] = revised
                alias_to_revised.setdefault(english, revised)

    frozen = {key: frozenset(values) for key, values in variants_by_revised.items()}
    return frozen, alias_to_revised, english_to_revised


def term_variants(canonical_revised: str) -> FrozenSet[str]:
    """给定重置版 canonical 词条，返回重置版 + 旧版 + 英文等全部写法。"""
    variants_by_revised, alias_to_revised, _ = load_terminology()
    if canonical_revised in variants_by_revised:
        return variants_by_revised[canonical_revised]
    resolved = alias_to_revised.get(canonical_revised, canonical_revised)
    if resolved in variants_by_revised:
        return variants_by_revised[resolved]
    return frozenset({canonical_revised})


def resolve_to_revised(term: str) -> str:
    """任意写法 → 重置版 canonical（未知则原样返回）。"""
    _, alias_to_revised, english_to_revised = load_terminology()
    key = (term or "").strip()
    if not key:
        return key
    return alias_to_revised.get(key, english_to_revised.get(key, key))


def text_contains(text: str, canonical_revised: str) -> bool:
    """规则文字是否含该词条（兼容重置版与旧版写法）。"""
    body = text or ""
    if not body:
        return False
    return any(variant in body for variant in term_variants(canonical_revised))


def text_contains_all(text: str, *canonical_revised_terms: str) -> bool:
    return all(text_contains(text, term) for term in canonical_revised_terms)


def text_contains_any(text: str, *canonical_revised_terms: str) -> bool:
    return any(text_contains(text, term) for term in canonical_revised_terms)


def card_text_contains(card, canonical_revised: str) -> bool:
    text = (getattr(card, "Text_Effect", "") or "")
    return text_contains(text, canonical_revised)


def card_text_contains_all(card, *canonical_revised_terms: str) -> bool:
    text = (getattr(card, "Text_Effect", "") or "")
    return text_contains_all(text, *canonical_revised_terms)


def card_text_contains_any(card, *canonical_revised_terms: str) -> bool:
    text = (getattr(card, "Text_Effect", "") or "")
    return text_contains_any(text, *canonical_revised_terms)


def normalize_text_to_revised(text: str) -> str:
    """将规则文字中的旧版术语统一为重置版（展示/日志用）。"""
    _, alias_to_revised, _ = load_terminology()
    if not text:
        return text
    pairs = sorted(alias_to_revised.items(), key=lambda kv: len(kv[0]), reverse=True)
    result = text
    for old, revised in pairs:
        if old != revised and old in result:
            result = result.replace(old, revised)
    return result
