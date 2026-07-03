"""从近期正式比赛推断今日预计首发。"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode


ESPN_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world"
MAX_RESPONSE_BYTES = 6_000_000


class RecentLineupError(RuntimeError):
    """近期首发数据不可用。"""


def _norm(value: Any) -> str:
    return "".join(char for char in str(value or "").casefold() if char.isalnum())


@lru_cache(maxsize=64)
def _get_json(path: str, query_items: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    curl = shutil.which("curl")
    if not curl:
        raise RecentLineupError("系统缺少 curl，无法读取近期首发")
    url = f"{ESPN_BASE_URL}/{path.lstrip('/')}"
    if query_items:
        url = f"{url}?{urlencode(query_items)}"
    try:
        completed = subprocess.run(
            [
                curl,
                "--silent",
                "--show-error",
                "--location",
                "--fail",
                "--max-time",
                "12",
                "--user-agent",
                "worldcup-console-mvp/2.0",
                url,
            ],
            check=False,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecentLineupError("近期首发请求失败或超时") from exc
    if completed.returncode != 0:
        raise RecentLineupError("近期首发数据源返回错误")
    if len(completed.stdout) > MAX_RESPONSE_BYTES:
        raise RecentLineupError("近期首发响应超过大小限制")
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecentLineupError("近期首发响应不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise RecentLineupError("近期首发响应格式无效")
    return payload


def _event_teams(event: dict[str, Any]) -> list[dict[str, Any]]:
    competitions = event.get("competitions") or []
    if not competitions or not isinstance(competitions[0], dict):
        return []
    return [
        item
        for item in competitions[0].get("competitors") or []
        if isinstance(item, dict) and isinstance(item.get("team"), dict)
    ]


def _team_matches(competitor: dict[str, Any], names: set[str]) -> bool:
    team = competitor.get("team") or {}
    candidates = {
        _norm(team.get("displayName")),
        _norm(team.get("name")),
        _norm(team.get("abbreviation")),
        _norm(team.get("shortDisplayName")),
    }
    return bool(candidates & names)


def _latest_event(
    events: list[dict[str, Any]],
    names: set[str],
    kickoff: datetime,
) -> dict[str, Any] | None:
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for event in events:
        status = event.get("status") or {}
        status_type = status.get("type") or {}
        if status_type.get("completed") is not True:
            continue
        try:
            event_time = datetime.fromisoformat(str(event.get("date")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if event_time >= kickoff.astimezone(timezone.utc):
            continue
        if any(_team_matches(item, names) for item in _event_teams(event)):
            candidates.append((event_time, event))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _parse_team_summary(summary: dict[str, Any], names: set[str]) -> dict[str, Any] | None:
    selected: dict[str, Any] | None = None
    for item in summary.get("rosters") or []:
        team = item.get("team") or {}
        candidates = {
            _norm(team.get("displayName")),
            _norm(team.get("abbreviation")),
        }
        if candidates & names:
            selected = item
            break
    if not selected:
        return None

    starters: list[dict[str, Any]] = []
    for player in selected.get("roster") or []:
        if player.get("starter") is not True:
            continue
        athlete = player.get("athlete") or {}
        name = athlete.get("displayName") or athlete.get("fullName")
        if not name:
            continue
        starters.append(
            {
                "player": str(name),
                "position": (player.get("position") or {}).get("abbreviation"),
                "formation_place": player.get("formationPlace"),
                "provider_player_id": str(athlete.get("id") or "") or None,
            }
        )
    starters.sort(key=lambda item: int(item["formation_place"]) if str(item.get("formation_place") or "").isdigit() else 99)
    if len(starters) != 11:
        return None
    return {
        "predicted_lineup": [item["player"] for item in starters],
        "lineup_detail": starters,
        "recent_availability": {
            "status": "recently_started",
            "available_baseline": 11,
            "official_injury_confirmation": False,
            "note": "沿用最近一场正式比赛确认首发；不代表官方确认无伤停。",
        },
    }


def infer_recent_lineups(
    *,
    home: str,
    away: str,
    kickoff_beijing: str,
    home_aliases: tuple[str, ...] = (),
    away_aliases: tuple[str, ...] = (),
) -> dict[str, Any]:
    """以两队最近一场正式比赛的确认首发作为本场基线。"""
    kickoff = datetime.fromisoformat(kickoff_beijing)
    if kickoff.tzinfo is None:
        raise RecentLineupError("开球时间必须包含时区")
    start = datetime(2026, 6, 11, tzinfo=timezone.utc).strftime("%Y%m%d")
    end = kickoff.astimezone(timezone.utc).strftime("%Y%m%d")
    scoreboard = _get_json("scoreboard", (("dates", f"{start}-{end}"), ("limit", "200")))
    events = [item for item in scoreboard.get("events") or [] if isinstance(item, dict)]

    side_names = {
        "home": {_norm(home), *(_norm(item) for item in home_aliases)},
        "away": {_norm(away), *(_norm(item) for item in away_aliases)},
    }
    result: dict[str, Any] = {
        "available": False,
        "source": "espn-public-match-summary",
        "method": "latest-confirmed-starting-xi",
        "home": None,
        "away": None,
    }
    for side in ("home", "away"):
        event = _latest_event(events, side_names[side], kickoff)
        if not event:
            continue
        event_id = str(event.get("id") or "")
        if not event_id:
            continue
        summary = _get_json("summary", (("event", event_id),))
        parsed = _parse_team_summary(summary, side_names[side])
        if not parsed:
            continue
        result[side] = {
            **parsed,
            "source_match_id": event_id,
            "source_match": event.get("name"),
            "source_kickoff": event.get("date"),
        }
    result["available"] = bool(result["home"] and result["away"])
    return result
