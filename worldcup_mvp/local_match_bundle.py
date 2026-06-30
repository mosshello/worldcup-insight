"""本地 matches_*.json 与体彩场次对齐。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def _local_match_files() -> tuple[Path, ...]:
    return tuple(sorted(PROJECT_ROOT.glob("data/matches_*.json")))


def load_local_match_bundle(home: str, away: str) -> dict[str, Any] | None:
    """按主客队名查找本地 JSON 情报包（含 team_context / stage / odds）。"""
    for path in _local_match_files():
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        for match in payload.get("matches", []):
            if match.get("home") == home and match.get("away") == away:
                return {
                    **match,
                    "_source_file": path.name,
                }
    return None


def has_overlay_entry(home: str, away: str) -> bool:
    from .match_intelligence import load_intelligence_overlay

    overlay = load_intelligence_overlay()
    key = f"{home}|{away}"
    return key in overlay.get("matches", {})
