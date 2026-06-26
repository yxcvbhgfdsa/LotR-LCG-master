#!/usr/bin/env python3
"""Phase-2: auto-build replacement map from recoverable mojibake segments."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(r"C:\Users\steam\text-encoding-guard\src")))

from check_mojibake.core import MOJIBAKE_TOKENS, score_text, try_gbk_recover

PATH = ROOT / "主脚本.py"

SUSPECT_CHARS = set("".join(MOJIBAKE_TOKENS)) | set(
    "鐩琛璁鎺娓鍒娴鍦榄绛閿鍚仭牭雭庭六弭戭皁鐨鍛鍩鏍寰鍙娌鐭鐖鍩鍑鏇鎴鎴鎭鍏宸椤鍒娴鍙娌鐭"
)

TOKEN_RE = re.compile(
    r"["
    + re.escape("".join(SUSPECT_CHARS))
    + r"\u00a0-\u024f\u2000-\u2bff\u3000-\u9fff\ufe00-\uffef"
    r"]{2,}"
)


def recover(text: str) -> str | None:
    old = score_text(text)[0]
    if old <= 0:
        return None
    best = None
    best_score = old
    for fn in (
        lambda t: try_gbk_recover(t),
        lambda t: t.encode("gbk").decode("utf-8"),
        lambda t: t.encode("utf-8").decode("gb18030"),
    ):
        try:
            out = fn(text)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if not out or out == text:
            continue
        s, _ = score_text(out)
        if s < best_score:
            best = out
            best_score = s
    if best is not None and best_score < old:
        return best
    return None


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    before, _ = score_text(text)
    mapping: dict[str, str] = {}

    for match in TOKEN_RE.finditer(text):
        seg = match.group(0)
        if seg in mapping:
            continue
        fixed = recover(seg)
        if fixed and fixed != seg:
            mapping[seg] = fixed

    # Longest first to avoid partial overlaps.
    for old, new in sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True):
        text = text.replace(old, new)

    after, _ = score_text(text)
    if text != PATH.read_text(encoding="utf-8"):
        bak = PATH.with_suffix(".py.bak.mojibake2")
        if not bak.exists():
            bak.write_text(PATH.read_text(encoding="utf-8"), encoding="utf-8")
        PATH.write_text(text, encoding="utf-8", newline="")

    print(f"auto replacements: {len(mapping)}")
    print(f"score: {before} -> {after}")
    for old, new in list(mapping.items())[:20]:
        print(f"  {old!r} -> {new!r}")
    if len(mapping) > 20:
        print("  ...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
