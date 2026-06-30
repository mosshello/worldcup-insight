"""The Odds API 官方盘口客户端（https://the-odds-api.com）。"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .team_names import find_event_by_teams, resolve_team

BASE_URL = "https://api.the-odds-api.com/v4"
PREFERRED_BOOKMAKERS = ("pinnacle", "bet365", "williamhill", "fanduel", "draftkings", "unibet")


class OddsApiError(RuntimeError):
    """The Odds API 请求失败。"""


def _request(path: str, params: dict[str, str], *, timeout: float = 15.0) -> Any:
    query = urllib.parse.urlencode(params)
    url = f"{BASE_URL}{path}?{query}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            remaining = response.headers.get("x-requests-remaining")
            used = response.headers.get("x-requests-used")
            payload = json.loads(body)
            if isinstance(payload, dict) and payload.get("message"):
                raise OddsApiError(payload["message"])
            return payload, {"remaining": remaining, "used": used}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(detail).get("message", detail)
        except json.JSONDecodeError:
            message = detail or exc.reason
        raise OddsApiError(f"HTTP {exc.code}: {message}") from exc
    except urllib.error.URLError as exc:
        raise OddsApiError(str(exc.reason)) from exc


def list_sports(api_key: str) -> list[dict[str, Any]]:
    payload, _ = _request("/sports/", {"apiKey": api_key})
    return payload


def fetch_odds(
    api_key: str,
    *,
    sport: str,
    regions: str = "uk,eu,us",
    markets: str = "h2h,spreads",
    odds_format: str = "decimal",
) -> tuple[list[dict[str, Any]], dict[str, str | None]]:
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": markets,
        "oddsFormat": odds_format,
    }
    payload, usage = _request(f"/sports/{sport}/odds/", params)
    return payload, usage


def fetch_event_odds(
    api_key: str,
    *,
    sport: str,
    event_id: str,
    regions: str = "uk,eu,us",
    markets: str = "h2h,spreads",
    odds_format: str = "decimal",
) -> tuple[dict[str, Any], dict[str, str | None]]:
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": markets,
        "oddsFormat": odds_format,
    }
    payload, usage = _request(f"/sports/{sport}/events/{event_id}/odds/", params)
    return payload, usage


def _pick_bookmaker(bookmakers: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not bookmakers:
        return None
    by_key = {item["key"]: item for item in bookmakers}
    for key in PREFERRED_BOOKMAKERS:
        if key in by_key:
            return by_key[key]
    return bookmakers[0]


def _market_outcomes(bookmaker: dict[str, Any], market_key: str) -> list[dict[str, Any]] | None:
    for market in bookmaker.get("markets", []):
        if market.get("key") == market_key:
            return market.get("outcomes")
    return None


def _extract_european(
    bookmaker: dict[str, Any],
    home_team: str,
    away_team: str,
) -> dict[str, float] | None:
    outcomes = _market_outcomes(bookmaker, "h2h")
    if not outcomes:
        return None

    home = resolve_team(home_team)["en"].casefold()
    away = resolve_team(away_team)["en"].casefold()
    prices: dict[str, float] = {}
    for outcome in outcomes:
        name = outcome["name"].casefold()
        price = float(outcome["price"])
        if name == home:
            prices["home"] = price
        elif name == away:
            prices["away"] = price
        elif name == "draw":
            prices["draw"] = price
    if len(prices) == 3:
        return prices
    return None


def _extract_asian_handicap(
    bookmaker: dict[str, Any],
    home_team: str,
    away_team: str,
) -> dict[str, float] | None:
    outcomes = _market_outcomes(bookmaker, "spreads")
    if not outcomes or len(outcomes) < 2:
        return None

    home = resolve_team(home_team)["en"].casefold()
    away = resolve_team(away_team)["en"].casefold()
    home_outcome = next((item for item in outcomes if item["name"].casefold() == home), None)
    away_outcome = next((item for item in outcomes if item["name"].casefold() == away), None)
    if not home_outcome or not away_outcome:
        return None

    return {
        "line": float(home_outcome["point"]),
        "home": float(home_outcome["price"]),
        "away": float(away_outcome["price"]),
    }


def event_to_snapshot(event: dict[str, Any], *, source_prefix: str = "the-odds-api") -> dict[str, Any]:
    """将 API 赛事对象转为内部快照结构。"""
    bookmaker = _pick_bookmaker(event.get("bookmakers", []))
    if bookmaker is None:
        raise OddsApiError("该赛事暂无可用博彩公司盘口")

    home_team = event["home_team"]
    away_team = event["away_team"]
    european = _extract_european(bookmaker, home_team, away_team)
    asian = _extract_asian_handicap(bookmaker, home_team, away_team)
    if european is None and asian is None:
        raise OddsApiError("未找到 h2h 或 spreads 盘口")

    snapshot: dict[str, Any] = {
        "source": f"{source_prefix}/{bookmaker['key']}",
    }
    if european is not None:
        snapshot["european"] = european
    if asian is not None:
        snapshot["asian_handicap"] = asian
    return snapshot


def find_snapshot(
    api_key: str,
    *,
    sport: str,
    home: str,
    away: str,
    event_id: str | None = None,
    regions: str = "uk,eu,us",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str | None]]:
    """按 event_id 或主客队名查找并返回快照。"""
    if event_id:
        event, usage = fetch_event_odds(
            api_key,
            sport=sport,
            event_id=event_id,
            regions=regions,
        )
    else:
        events, usage = fetch_odds(api_key, sport=sport, regions=regions)
        event = find_event_by_teams(events, home, away)
        if event is None:
            raise OddsApiError(f"未找到 {home} vs {away} 的赛事，请用 list-events 查看可用比赛")

    snapshot = event_to_snapshot(event)
    meta = {
        "event_id": event["id"],
        "home_team": event["home_team"],
        "away_team": event["away_team"],
        "commence_time": event.get("commence_time"),
        "sport_key": event.get("sport_key", sport),
    }
    return snapshot, meta, usage
