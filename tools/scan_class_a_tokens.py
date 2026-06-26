#!/usr/bin/env python3
"""Scan and fix Class-A mojibake tokens (lossless gbk->utf-8 round-trip)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(r"C:\Users\steam\text-encoding-guard\src")))

from check_mojibake.core import MOJIBAKE_TOKENS, score_text  # noqa: E402

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

SUSPECT = set("".join(MOJIBAKE_TOKENS)) | set(
    "鐩琛璁鎺娓鍒娴鍦榄绛閿鍚仭牭雭庭六弭戭皁鐨鍛鍩鏍寰鍙娌鐭鐖鍩鍑鏇鎴鎭鍏宸椤"
)

TOKEN_RE = re.compile(
    r"[" + re.escape("".join(SUSPECT)) + r"]{2,}"
)


def is_class_a(seg: str) -> str | None:
    """Return recovered text if seg is a lossless Class-A token."""
    if not seg or not TOKEN_RE.fullmatch(seg):
        return None
    try:
        raw = seg.encode("gbk")
        recovered = raw.decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None
    if recovered == seg:
        return None
    # Round-trip: misreading recovered UTF-8 as GBK must reproduce seg.
    try:
        if recovered.encode("utf-8").decode("gbk") != seg:
            return None
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None
    old_s, _ = score_text(seg)
    new_s, _ = score_text(recovered)
    if new_s >= old_s and old_s > 0:
        return None
    return recovered


def scan_file(path: Path, apply: bool) -> tuple[dict[str, str], int]:
    text = path.read_text(encoding="utf-8")
    mapping: dict[str, str] = {}
    for m in TOKEN_RE.finditer(text):
        seg = m.group(0)
        if seg in mapping:
            continue
        fixed = is_class_a(seg)
        if fixed:
            mapping[seg] = fixed

    new_text = text
    count = 0
    if apply and mapping:
        for old, new in sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True):
            n = new_text.count(old)
            if n:
                new_text = new_text.replace(old, new)
                count += n
        if new_text != text:
            bak = path.with_suffix(path.suffix + ".bak.class_a")
            if not bak.exists():
                bak.write_text(text, encoding="utf-8")
            path.write_text(new_text, encoding="utf-8", newline="")
    return mapping, count


def main() -> int:
    apply = "--apply" in sys.argv
    total_tokens = 0
    total_hits = 0
    report: list[str] = []

    for path in TARGETS:
        if not path.is_file():
            continue
        mapping, hits = scan_file(path, apply)
        if not mapping:
            continue
        total_tokens += len(mapping)
        total_hits += hits
        report.append(f"\n=== {path.name} ({len(mapping)} tokens, {hits} replacements) ===")
        for old, new in sorted(mapping.items(), key=lambda kv: (-len(kv[0]), kv[0])):
            report.append(f"  {old!r} -> {new!r}")

    out = ROOT / "tools" / "class_a_tokens_report.txt"
    header = f"Class-A scan {'APPLIED' if apply else 'DRY-RUN'}\n"
    header += f"Total unique tokens: {total_tokens}, total replacements: {total_hits}\n"
    out.write_text(header + "\n".join(report), encoding="utf-8")
    print(header.strip())
    print(f"Report: {out}")
    for line in report[:60]:
        print(line)
    if len(report) > 60:
        print("  ...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
