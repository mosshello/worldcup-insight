#!/usr/bin/env python3
"""从 matches_*.json 导入伤停/人员情报到 intelligence_overlay.json。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldcup_mvp.match_intelligence import OVERLAY_PATH, load_intelligence_overlay


def _merge_side(target: dict, source: dict | None) -> None:
    if not source:
        return
    for key in ("absences", "scorers", "predicted_lineup", "tactics"):
        if source.get(key):
            target[key] = source[key]


def import_matches_file(path: Path, overlay: dict[str, Any]) -> int:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    count = 0
    for match in payload.get("matches", []):
        home = match.get("home")
        away = match.get("away")
        if not home or not away:
            continue
        key = f"{home}|{away}"
        entry = overlay.setdefault("matches", {}).setdefault(key, {})
        entry.setdefault("home", {})
        entry.setdefault("away", {})
        ctx = match.get("team_context") or {}
        _merge_side(entry["home"], ctx.get("home"))
        _merge_side(entry["away"], ctx.get("away"))
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="导入 matches JSON 到 intelligence overlay")
    parser.add_argument("files", nargs="+", help="matches_*.json 路径")
    parser.add_argument("--output", default=str(OVERLAY_PATH), help="输出 overlay 路径")
    args = parser.parse_args()

    overlay = load_intelligence_overlay(Path(args.output))
    total = 0
    for file_arg in args.files:
        path = Path(file_arg)
        if not path.exists():
            print(f"跳过不存在文件：{path}", file=sys.stderr)
            continue
        total += import_matches_file(path, overlay)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        json.dump(overlay, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(f"已合并 {total} 场到 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
