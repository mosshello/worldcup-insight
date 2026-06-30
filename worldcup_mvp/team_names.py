"""中英文队名与 API 队名匹配。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


DEFAULT_MAP_PATH = Path(__file__).resolve().parent.parent / "data" / "team_name_map.json"


@lru_cache(maxsize=1)
def load_team_map(path: str | None = None) -> dict[str, dict[str, str]]:
    file_path = Path(path) if path else DEFAULT_MAP_PATH
    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve_team(name: str, team_map: dict[str, dict[str, str]] | None = None) -> dict[str, str]:
    """将中文或英文队名解析为 en / abbr。"""
    mapping = team_map or load_team_map()
    if name in mapping:
        return mapping[name]

    lowered = name.casefold()
    for aliases in mapping.values():
        if aliases["en"].casefold() == lowered or aliases["abbr"].casefold() == lowered:
            return aliases

    return {"en": name, "abbr": name[:3].upper()}


def teams_match(home_a: str, away_a: str, home_b: str, away_b: str) -> bool:
    left_home = resolve_team(home_a)
    left_away = resolve_team(away_a)
    right_home = resolve_team(home_b)
    right_away = resolve_team(away_b)
    return (
        left_home["en"].casefold() == right_home["en"].casefold()
        and left_away["en"].casefold() == right_away["en"].casefold()
    )


def find_event_by_teams(events: list[dict[str, Any]], home: str, away: str) -> dict[str, Any] | None:
    for event in events:
        if teams_match(home, away, event["home_team"], event["away_team"]):
            return event
    return None
