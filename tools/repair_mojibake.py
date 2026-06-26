#!/usr/bin/env python3
"""Repair mixed mojibake in LotR-LCG Python sources."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(r"C:\Users\steam\text-encoding-guard\src")))

from check_mojibake.core import (  # noqa: E402
    MOJIBAKE_TOKENS,
    _line_has_mojibake,
    score_text,
    try_gbk_recover,
)

TARGETS = [
    ROOT / "主脚本.py",
    ROOT / "玩家卡抽取.py",
    ROOT / "玩家CardWidget.py",
    ROOT / "遭遇抽取.py",
    ROOT / "场景.py",
    ROOT / "CardWidget.py",
    ROOT / "CardViewer.py",
    ROOT / "旧版术语.py",
    ROOT / "ringsdb_mcp" / "mapping.py",
]

# Chars that rarely appear in intentional UI text but common in GBK-mojibake.
SUSPECT_RE = re.compile(
    "[" + re.escape("".join(MOJIBAKE_TOKENS)) + r"鐩琛璁鎺娓鍒娴鍦榄绛閿鍚仭牭雭庭六弭戭夭經]"
    r"+"
)

MANUAL_REPLACEMENTS: list[tuple[str, str]] = [
    ('card_type == "鐩熷弸"', 'card_type == "盟友"'),
    ("EOWYN_HERO_NAMES = frozenset({'伊奥渭'", "EOWYN_HERO_NAMES = frozenset({'伊奥温'"),
    ('"寮?" in text', '"弃除" in text'),
    ('QPushButton("鍒囨崲")', 'QPushButton("切换")'),
    ("进六[", "进入["),
    ("目标进庭", "目标进度"),
    ("枚进庭", "枚进度"),
    ("探险卡进庭", "探险卡进度"),
    ("响庭", "响应"),
    ("翻弭", "翻开"),
    ("已翻弭", "已翻开"),
    ("交戭", "交战"),
    ("场夭[", "原手牌 ["),
    ("新手牭[", "新手牌 ["),
    ("起始手牭", "起始手牌"),
    ("手牭", "手牌"),
    ("英雭", "英雄"),
    ("玩宭", "玩家"),
    ("任务链已仭", "任务链已同步"),
    ("鍚屾:", "同步："),
    ("銆嶏級", "」"),
    ("鍒濆威胁", "初始威胁"),
    ("娓告垙", "游戏"),
    ("探陭", "探险"),
    ("鎺㈤櫓", "探险"),
    ("璁″垝", "计划"),
    ("鎴樻枟", "战斗"),
    ("鎭㈠", "恢复"),
    ("娴佺▼", "流程"),
    ("琛屽姩", "行动"),
    ("鍦板尯", "地区"),
    ("榄斿奖", "魔影"),
    ("鐩熷弸", "盟友"),
    ("附属鐩", "附属目"),
    ("探查鍖?", "探查区"),
    ("弃牌堭", "弃牌堆"),
    ("威胭", "威胁"),
    ("结杭", "结束"),
    ("魉择", "选择"),
    ("支仭", "支付"),
    ("鑺辫垂资源", "花费资源"),
    (" 路 ", " · "),
    ("锛?", "）"),
    ("锛歿", "："),
    ("锛夛細", "）："),
    ("銆", "「"),
    ("Ｍ", "）"),
    ("鈫", "→"),
    ("鈥斺?", "——"),
    ("↭", "→"),
]


def try_recover_substring(text: str) -> str | None:
    if not text or not SUSPECT_RE.search(text):
        return None
    old_score = score_text(text)[0]
    candidates: list[str] = []
    for fn in (
        lambda t: try_gbk_recover(t),
        lambda t: t.encode("gbk").decode("utf-8"),
        lambda t: t.encode("utf-8").decode("gb18030"),
    ):
        try:
            out = fn(text)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if out and out != text:
            candidates.append(out)
    best: str | None = None
    best_score = old_score
    for cand in candidates:
        s, _ = score_text(cand)
        if s < best_score:
            best = cand
            best_score = s
    return best


def recover_line(line: str) -> str:
    if not _line_has_mojibake(line):
        return line

    whole = try_recover_substring(line)
    if whole is not None:
        return whole

    def repl(match: re.Match[str]) -> str:
        seg = match.group(0)
        fixed = try_recover_substring(seg)
        return fixed if fixed is not None else seg

    return SUSPECT_RE.sub(repl, line)


def apply_manual(text: str) -> str:
    for old, new in sorted(MANUAL_REPLACEMENTS, key=lambda p: len(p[0]), reverse=True):
        text = text.replace(old, new)
    return text


def repair_file(path: Path) -> tuple[int, int]:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    before, _ = score_text(text)

    for _ in range(6):
        lines = text.splitlines()
        changed = 0
        for i, ln in enumerate(lines):
            new_ln = recover_line(ln)
            if new_ln != ln:
                lines[i] = new_ln
                changed += 1
        text = "\n".join(lines)
        if raw.endswith(b"\n") and text and not text.endswith("\n"):
            text += "\n"
        if changed == 0:
            break

    text = apply_manual(text)
    after, _ = score_text(text)

    if text != raw.decode("utf-8"):
        bak = path.with_suffix(path.suffix + ".bak.mojibake")
        if not bak.exists():
            bak.write_bytes(raw)
        path.write_text(text, encoding="utf-8", newline="")
    return before, after


def main() -> int:
    print("Repairing mojibake...")
    for path in TARGETS:
        if not path.is_file():
            continue
        before, after = repair_file(path)
        status = "ok" if after == 0 else "partial"
        print(f"  [{status}] {path.name}: {before} -> {after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
