"""中国体育彩票竞彩足球官方 Web API 客户端。"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .team_names import resolve_team
from .http_client import DataSourceError, HttpJsonClient

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
SPORTTERY_BASE_URL = "https://webapi.sporttery.cn"

MATCH_CALC_URL = (
    "https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry"
)
MATCH_LIST_URL = (
    "https://webapi.sporttery.cn/gateway/uniform/football/getMatchListV1.qry"
)
MATCH_RESULT_URL = (
    "https://webapi.sporttery.cn/gateway/uniform/football/getMatchResultV1.qry"
)
FIXED_BONUS_URL = (
    "https://webapi.sporttery.cn/gateway/uniform/football/getFixedBonusV1.qry"
)

HAD_RESULT_KEYS = {"H": "home", "D": "draw", "A": "away"}
HAD_RESULT_LABELS = {"H": "主胜", "D": "平", "A": "客胜"}
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.sporttery.cn/jc/zqszsc/",
    "Origin": "https://www.sporttery.cn",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = (0.8, 2.0, 4.0)
RETRYABLE_HTTP_CODES = {403, 429, 500, 502, 503, 504}
MATCH_LIVE_HOURS = 3.0
MATCH_TRACKING_HOURS = 72.0

_sporttery_http: HttpJsonClient | None = None


def _get_sporttery_http() -> HttpJsonClient:
    global _sporttery_http
    if _sporttery_http is None:
        _sporttery_http = HttpJsonClient(
            SPORTTERY_BASE_URL,
            provider_name="中国体彩网",
            timeout=15.0,
            max_retries=0,
        )
    return _sporttery_http


class SportteryApiError(RuntimeError):
    """体彩 API 请求失败。"""


def _should_retry_http(code: int) -> bool:
    return code in RETRYABLE_HTTP_CODES


def _request_once(url: str, params: dict[str, str] | None = None, *, timeout: float = 15.0) -> dict[str, Any]:
    path = url.removeprefix(f"{SPORTTERY_BASE_URL}/")
    client = _get_sporttery_http()
    client.timeout = timeout
    try:
        payload = client.get_json(path, query=params or {}, headers=DEFAULT_HEADERS)
    except DataSourceError as exc:
        error = SportteryApiError(str(exc))
        if "HTTP 403" in str(exc):
            error.http_code = 403  # type: ignore[attr-defined]
        raise error from exc

    if not payload.get("success"):
        raise SportteryApiError(payload.get("errorMessage") or "体彩 API 返回失败")
    return payload


def _request(url: str, params: dict[str, str] | None = None, *, timeout: float = 15.0) -> dict[str, Any]:
    last_error: SportteryApiError | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return _request_once(url, params, timeout=timeout)
        except SportteryApiError as exc:
            last_error = exc
            http_code = getattr(exc, "http_code", None)
            if http_code is not None and _should_retry_http(http_code) and attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_SECONDS[attempt])
                continue
            if "timed out" in str(exc).lower() and attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_SECONDS[attempt])
                continue
            raise
    if last_error is not None:
        raise last_error
    raise SportteryApiError("体彩 API 请求失败")


def _parse_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_trend_flag(flag: Any) -> str:
    """体彩变动标志：-1 下降，0 持平，1 上升。"""
    text = str(flag).strip()
    if text == "-1":
        return "down"
    if text == "1":
        return "up"
    return "flat"


TREND_LABELS = {"down": "下调", "up": "上调", "flat": "持平"}


def _normalize_pool(pool: dict[str, Any] | None) -> dict[str, Any] | None:
    if not pool:
        return None
    home = _parse_float(pool.get("h"))
    draw = _parse_float(pool.get("d"))
    away = _parse_float(pool.get("a"))
    if home is None or draw is None or away is None:
        return None
    return {
        "home": home,
        "draw": draw,
        "away": away,
        "trends": {
            "home": parse_trend_flag(pool.get("hf")),
            "draw": parse_trend_flag(pool.get("df")),
            "away": parse_trend_flag(pool.get("af")),
        },
        "goal_line": _parse_float(pool.get("goalLineValue") or pool.get("goalLine")),
        "updated_at": f"{pool.get('updateDate', '')} {pool.get('updateTime', '')}".strip(),
    }


def _flatten_matches(payload: dict[str, Any]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for group in payload.get("value", {}).get("matchInfoList", []):
        for raw in group.get("subMatchList", []):
            matches.append(normalize_match(raw))
    return matches


def parse_kickoff_beijing(match: dict[str, Any]) -> datetime | None:
    """解析体彩场次开赛时间（北京时间）。"""
    date = match.get("match_date")
    time = match.get("match_time")
    if not date or not time:
        return None
    time_text = str(time)
    if len(time_text) == 5:
        time_text = f"{time_text}:00"
    try:
        return datetime.fromisoformat(f"{date}T{time_text}").replace(tzinfo=BEIJING_TZ)
    except ValueError:
        return None


def hours_until_kickoff(match: dict[str, Any], *, now: datetime | None = None) -> float | None:
    """距离开赛的剩余小时数；已开赛则返回 0。"""
    kickoff = parse_kickoff_beijing(match)
    if kickoff is None:
        return None
    current = now or datetime.now(BEIJING_TZ)
    seconds = (kickoff - current).total_seconds()
    return max(0.0, seconds / 3600)


def format_countdown(hours: float | None) -> str:
    """人类可读的距开赛文案。"""
    if hours is None:
        return "开赛时间待定"
    if hours <= 0:
        return "已开赛"
    if hours < 1:
        return f"距开赛 {max(1, int(hours * 60))} 分钟"
    if hours < 24:
        return f"距开赛 {hours:.1f} 小时"
    return f"距开赛 {hours / 24:.1f} 天"


def hours_since_kickoff(match: dict[str, Any], *, now: datetime | None = None) -> float | None:
    """距离开赛已过去的小时数；未开赛返回 None。"""
    kickoff = parse_kickoff_beijing(match)
    if kickoff is None:
        return None
    current = now or datetime.now(BEIJING_TZ)
    if kickoff > current:
        return None
    return (current - kickoff).total_seconds() / 3600


def match_lifecycle_phase(match: dict[str, Any], *, now: datetime | None = None) -> str:
    """upcoming · live · awaiting_result"""
    kickoff = parse_kickoff_beijing(match)
    if kickoff is None:
        return "upcoming"
    current = now or datetime.now(BEIJING_TZ)
    if kickoff > current:
        return "upcoming"
    elapsed = hours_since_kickoff(match, now=current) or 0.0
    if elapsed <= MATCH_LIVE_HOURS:
        return "live"
    return "awaiting_result"


def lifecycle_countdown_label(match: dict[str, Any], *, now: datetime | None = None) -> str:
    phase = match_lifecycle_phase(match, now=now)
    if phase == "live":
        return "进行中"
    if phase == "awaiting_result":
        return "待出赛果"
    return format_countdown(hours_until_kickoff(match, now=now))


def is_trackable_announced_match(match: dict[str, Any], *, now: datetime | None = None) -> bool:
    """官网已公布且仍在跟踪窗口内（未开赛、进行中、待出赛果）。"""
    kickoff = parse_kickoff_beijing(match)
    if kickoff is None:
        return True
    current = now or datetime.now(BEIJING_TZ)
    if kickoff > current:
        return True
    elapsed = hours_since_kickoff(match, now=current) or 0.0
    return elapsed <= MATCH_TRACKING_HOURS


def enrich_match_timing(match: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """为比赛对象附加倒计时与生命周期字段。"""
    hours = hours_until_kickoff(match, now=now)
    phase = match_lifecycle_phase(match, now=now)
    enriched = dict(match)
    enriched["hours_until_kickoff"] = round(hours, 2) if hours is not None else None
    enriched["lifecycle_phase"] = phase
    enriched["countdown_label"] = lifecycle_countdown_label(match, now=now)
    return enriched


def is_upcoming_match(match: dict[str, Any], *, now: datetime | None = None) -> bool:
    """仍在售且尚未开赛的体彩场次。"""
    if match.get("pools", {}).get("had") is None:
        return False
    kickoff = parse_kickoff_beijing(match)
    if kickoff is None:
        return True
    current = now or datetime.now(BEIJING_TZ)
    return kickoff > current


def is_announced_match(match: dict[str, Any], *, now: datetime | None = None) -> bool:
    """官网已公布且尚未开赛的场次，包含待开售。"""
    return is_trackable_announced_match(match, now=now)


def fetch_upcoming_matches(*, pool_code: str = "had,hhad") -> list[dict[str, Any]]:
    """拉取体彩竞彩网当前未开赛的足球赛事，按开赛时间排序。"""
    matches = fetch_matches(pool_code=pool_code)
    upcoming = [match for match in matches if is_upcoming_match(match)]
    upcoming.sort(
        key=lambda item: parse_kickoff_beijing(item)
        or datetime.max.replace(tzinfo=BEIJING_TZ)
    )
    return upcoming


def fetch_scheduled_matches() -> list[dict[str, Any]]:
    """拉取官网赛程页公布的全部未开赛场次（含待开售）。"""
    payload = _request(
        MATCH_LIST_URL,
        {"clientCode": "3001"},
    )
    matches = _flatten_matches(payload)
    scheduled = [match for match in matches if is_trackable_announced_match(match)]
    scheduled.sort(
        key=lambda item: parse_kickoff_beijing(item)
        or datetime.max.replace(tzinfo=BEIJING_TZ)
    )
    return scheduled


def fetch_announced_matches(*, pool_code: str = "had,hhad") -> list[dict[str, Any]]:
    """合并赛程页与计算器：已开售场次带赔率，待开售保留赛程占位。"""
    scheduled = fetch_scheduled_matches()
    selling_by_id = {match["match_id"]: match for match in fetch_upcoming_matches(pool_code=pool_code)}
    merged: list[dict[str, Any]] = []
    for match in scheduled:
        selling = selling_by_id.get(match["match_id"])
        if selling:
            merged.append({**match, **selling, "sale_status": "selling", "analysis_available": True})
        else:
            merged.append({**match, "sale_status": "pending", "analysis_available": False})
    return merged


def fetch_fixed_bonus(match_id: str | int) -> dict[str, Any]:
    """拉取单场固定奖金及猜比分历史。"""
    return fetch_fixed_bonus_detail(match_id)["odds_history"]


def fetch_fixed_bonus_detail(match_id: str | int) -> dict[str, Any]:
    """拉取单场固定奖金、赛果结算与猜比分历史。"""
    payload = _request(
        FIXED_BONUS_URL,
        {"clientCode": "3001", "matchId": str(match_id)},
    )
    value = payload["value"]
    return {
        "odds_history": value["oddsHistory"],
        "match_result_list": value.get("matchResultList") or [],
        "is_cancel": bool(value.get("isCancel")),
    }


def parse_match_result_list(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """将体彩 matchResultList 按玩法 code 索引。"""
    parsed: dict[str, dict[str, Any]] = {}
    for item in items:
        code = str(item.get("code", "")).lower()
        if code:
            parsed[code] = item
    return parsed


def fetch_results_by_date(
    begin: str,
    end: str,
    *,
    page: int = 1,
) -> list[dict[str, Any]]:
    """按日期范围拉取体彩足球赛果列表。"""
    payload = _request(
        MATCH_RESULT_URL,
        {
            "matchPage": str(page),
            "pcOrWap": "0",
            "leagueId": "",
            "matchBeginDate": begin,
            "matchEndDate": end,
        },
    )
    value = payload.get("value") or {}
    if isinstance(value, dict):
        return value.get("matchResultList") or []
    return []


def normalize_match(raw: dict[str, Any]) -> dict[str, Any]:
    """将体彩原始比赛对象转为内部结构。"""
    had = _normalize_pool(raw.get("had"))
    hhad = _normalize_pool(raw.get("hhad"))
    sell_status = str(raw.get("sellStatus") or "")
    match_status = raw.get("matchStatus") or sell_status
    return {
        "match_id": str(raw.get("matchId")),
        "match_num": raw.get("matchNumStr"),
        "home": raw.get("homeTeamAbbName") or raw.get("homeTeamAllName"),
        "away": raw.get("awayTeamAbbName") or raw.get("awayTeamAllName"),
        "home_en": resolve_team(raw.get("homeTeamAbbName") or "")["en"],
        "away_en": resolve_team(raw.get("awayTeamAbbName") or "")["en"],
        "league": raw.get("leagueAbbName"),
        "match_date": raw.get("matchDate"),
        "business_date": raw.get("businessDate"),
        "match_time": raw.get("matchTime"),
        "kickoff": f"{raw.get('matchDate', '')}T{raw.get('matchTime', '')}",
        "kickoff_beijing": (
            f"{raw.get('matchDate', '')}T{raw.get('matchTime', '')}+08:00"
            if raw.get("matchDate") and raw.get("matchTime")
            else None
        ),
        "match_status": match_status,
        "sale_status": "selling" if sell_status == "1" or match_status == "Selling" else "pending",
        "analysis_available": had is not None,
        "betting_single": raw.get("bettingSingle"),
        "pools": {
            "had": had,
            "hhad": hhad,
        },
        "source": "sporttery.cn",
        "source_url": "https://www.sporttery.cn/jc/zqszsc/",
    }


def fetch_matches(*, pool_code: str = "had,hhad") -> list[dict[str, Any]]:
    payload = _request(
        MATCH_CALC_URL,
        {"channel": "c", "poolCode": pool_code},
    )
    return _flatten_matches(payload)


def _match_from_journal(match_id: str | int) -> dict[str, Any] | None:
    from .prediction_journal import find_open_entry, journal_entry_to_match

    entry = find_open_entry(match_id)
    if entry is None:
        return None
    match = journal_entry_to_match(entry)
    if match is None:
        return None
    return enrich_match_timing(match)


def find_match(
    *,
    home: str | None = None,
    away: str | None = None,
    match_id: str | None = None,
    pool_code: str = "had,hhad",
    upcoming_only: bool = False,
) -> dict[str, Any]:
    if match_id:
        journal_match = _match_from_journal(match_id)
        if journal_match is not None:
            return journal_match

    matches = fetch_upcoming_matches(pool_code=pool_code) if upcoming_only else fetch_matches(pool_code=pool_code)
    if match_id:
        for match in matches:
            if match["match_id"] == str(match_id):
                return match
        trackable = fetch_scheduled_matches()
        for match in trackable:
            if match["match_id"] == str(match_id):
                return match
        journal_match = _match_from_journal(match_id)
        if journal_match is not None:
            return journal_match
        raise SportteryApiError(f"未找到体彩 matchId={match_id}")

    if home and away:
        for match in matches:
            if resolve_team(match["home"])["en"].casefold() == resolve_team(home)["en"].casefold() and resolve_team(
                match["away"]
            )["en"].casefold() == resolve_team(away)["en"].casefold():
                return match
        raise SportteryApiError(f"未找到体彩赛事：{home} vs {away}")

    raise SportteryApiError("需要提供 match_id 或主客队名称")


def _history_point(item: dict[str, Any]) -> dict[str, Any]:
    point = {
        "recorded_at": f"{item.get('updateDate', '')}T{item.get('updateTime', '')}",
        "home": _parse_float(item.get("h")),
        "draw": _parse_float(item.get("d")),
        "away": _parse_float(item.get("a")),
        "trends": {
            "home": parse_trend_flag(item.get("hf")),
            "draw": parse_trend_flag(item.get("df")),
            "away": parse_trend_flag(item.get("af")),
        },
    }
    line = _parse_float(item.get("goalLineValue") or item.get("goalLine"))
    if line is not None:
        point["goal_line"] = line
    return point


def fetch_odds_history(match_id: str | int) -> dict[str, Any]:
    payload = _request(
        FIXED_BONUS_URL,
        {"clientCode": "3001", "matchId": str(match_id)},
    )
    history = payload["value"]["oddsHistory"]
    return {
        "match_id": str(history.get("matchId")),
        "home": history.get("homeTeamAbbName"),
        "away": history.get("awayTeamAbbName"),
        "league": history.get("leagueAbbName"),
        "had_history": [_history_point(item) for item in history.get("hadList", [])],
        "hhad_history": [_history_point(item) for item in history.get("hhadList", [])],
        "ttg_history": list(history.get("ttgList") or []),
        "hafu_history": list(history.get("hafuList") or []),
        "crs_history": list(history.get("crsList") or []),
        "source": "sporttery.cn/getFixedBonusV1",
    }


def match_to_snapshot(match: dict[str, Any]) -> dict[str, Any]:
    """转为通用快照结构，had 映射为 european。"""
    had = match["pools"].get("had")
    if had is None:
        raise SportteryApiError("该比赛暂无胜平负固定奖金")

    snapshot: dict[str, Any] = {
        "source": "sporttery.cn/had",
        "european": {
            "home": had["home"],
            "draw": had["draw"],
            "away": had["away"],
        },
        "sporttery": {
            "match_id": match["match_id"],
            "had": had,
            "hhad": match["pools"].get("hhad"),
        },
    }
    return snapshot
