"""三源统一数据中心与仪表盘/融合预测之间的桥接层。"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .analyzer import OUTCOME_LABELS, analyze_match
from .data_manager import ConfigurationError, POLYMARKET_CODE_ALIASES, UnifiedDataManager
from .http_client import DataSourceError
from .local_match_bundle import load_local_match_bundle
from .match_intelligence import (
    apply_overlay_to_match,
    build_intelligence_for_sporttery,
    build_intelligence_report,
    normalize_venue,
)
from .team_names import resolve_team

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
PROBABILITY_DELTA_ALERT_PP = 5.0
HEALTH_CACHE_TTL_SECONDS = 120.0

_index_cache: dict[str, Any] | None = None
_index_date: str | None = None
_index_by_date: dict[str, dict[str, Any]] = {}
_merged_index_cache: dict[str, Any] | None = None
_health_cache: dict[str, Any] | None = None
_health_cache_at: float = 0.0


def get_provider_health(*, force: bool = False) -> dict[str, Any]:
    """检查 FIFA / Polymarket / 体彩三源可用性（失败时不抛错，带缓存）。"""
    global _health_cache, _health_cache_at
    now = time.time()
    if (
        not force
        and _health_cache is not None
        and now - _health_cache_at < HEALTH_CACHE_TTL_SECONDS
    ):
        return _health_cache

    try:
        report = UnifiedDataManager.from_env().doctor()
        providers = report.get("providers") or []
        payload = {
            "success": True,
            "configuration": report.get("configuration"),
            "providers": [
                {
                    "name": item.get("provider", "unknown"),
                    "ok": bool(item.get("ok")),
                }
                for item in providers
            ],
            "all_ok": bool(providers) and all(item.get("ok") for item in providers),
            "cached": False,
        }
    except (ConfigurationError, DataSourceError) as exc:
        payload = {"success": False, "error": str(exc), "providers": [], "all_ok": False, "cached": False}
    except Exception as exc:
        payload = {"success": False, "error": str(exc), "providers": [], "all_ok": False, "cached": False}

    _health_cache = payload
    _health_cache_at = now
    return payload


def load_unified_index(
    fixture_date: str | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """拉取指定赛事日的三源统一快照，并按体彩 match_id 索引。"""
    global _index_cache, _index_date, _index_by_date, _merged_index_cache

    date_str = fixture_date or datetime.now(BEIJING_TZ).date().isoformat()
    if not force and date_str in _index_by_date:
        return _index_by_date[date_str]
    if not force and _index_cache is not None and _index_date == date_str:
        return _index_cache

    try:
        payload = UnifiedDataManager.from_env().collect(date_str)
    except (ConfigurationError, DataSourceError) as exc:
        result = {
            "success": False,
            "fixture_date": date_str,
            "error": str(exc),
            "by_sporttery_id": {},
            "matches": [],
        }
        _index_by_date[date_str] = result
        _index_cache = result
        _index_date = date_str
        _merged_index_cache = None
        return result

    by_sporttery_id: dict[str, dict[str, Any]] = {}
    for match in payload.get("matches", []):
        provider_ids = match.get("provider_ids") or {}
        sporttery_id = provider_ids.get("sporttery_match")
        if sporttery_id:
            normalized = dict(match)
            if normalized.get("venue"):
                venue = normalize_venue(normalized["venue"])
                if venue:
                    normalized["venue"] = venue
                else:
                    normalized.pop("venue", None)
            by_sporttery_id[str(sporttery_id)] = normalized

    result = {
        "success": True,
        "fixture_date": date_str,
        "data_as_of": payload.get("data_as_of"),
        "match_count": len(payload.get("matches", [])),
        "by_sporttery_id": by_sporttery_id,
        "quality_checks": payload.get("quality_checks"),
    }
    _index_by_date[date_str] = result
    _index_cache = result
    _index_date = date_str
    _merged_index_cache = None
    return result


def load_unified_index_merged(*, days: int = 3, force: bool = False) -> dict[str, Any]:
    """合并未来数日三源索引（今天/明天/后天）。"""
    global _merged_index_cache

    if not force and _merged_index_cache is not None:
        return _merged_index_cache

    today = datetime.now(BEIJING_TZ).date()
    merged: dict[str, dict[str, Any]] = {}
    dates_loaded: list[str] = []
    errors: list[str] = []

    for offset in range(max(1, days)):
        date_str = (today + timedelta(days=offset)).isoformat()
        dates_loaded.append(date_str)
        index = load_unified_index(date_str, force=True)
        if index.get("success"):
            merged.update(index.get("by_sporttery_id") or {})
        elif index.get("error"):
            errors.append(f"{date_str}: {index['error']}")

    result = {
        "success": bool(merged),
        "fixture_date": dates_loaded[0] if dates_loaded else today.isoformat(),
        "dates_loaded": dates_loaded,
        "match_count": len(merged),
        "by_sporttery_id": merged,
        "errors": errors,
        "error": "; ".join(errors) if errors and not merged else None,
    }
    _merged_index_cache = result
    return result


def get_unified_match(sporttery_match_id: str) -> dict[str, Any] | None:
    """按体彩 match_id 获取三源统一比赛对象（检索合并索引）。"""
    index = load_unified_index_merged(days=3)
    if not index.get("success"):
        return None
    return index.get("by_sporttery_id", {}).get(str(sporttery_match_id))


def fetch_polymarket_odds(match: dict[str, Any]) -> tuple[dict[str, float] | None, str | None]:
    """从三源索引读取 Polymarket 欧赔；无索引时返回 None。"""
    unified = get_unified_match(str(match.get("match_id", "")))
    if not unified:
        return fetch_polymarket_odds_for_teams(match)
    odds = unified.get("odds")
    if not isinstance(odds, dict):
        return None, None
    try:
        return (
            {
                "home": float(odds["home"]),
                "draw": float(odds["draw"]),
                "away": float(odds["away"]),
            },
            "polymarket-gamma-public",
        )
    except (KeyError, TypeError, ValueError):
        return None, None


def fetch_polymarket_odds_for_teams(
    sporttery_match: dict[str, Any],
) -> tuple[dict[str, float] | None, str | None]:
    """按队名+开赛日尝试 Polymarket slug（不依赖三源索引）。"""
    home = sporttery_match.get("home", "")
    away = sporttery_match.get("away", "")
    kickoff = sporttery_match.get("kickoff_beijing") or sporttery_match.get("match_date") or ""
    fixture_date = str(kickoff)[:10] if kickoff else datetime.now(BEIJING_TZ).date().isoformat()
    home_code = resolve_team(home).get("abbr", "").upper()
    away_code = resolve_team(away).get("abbr", "").upper()
    if not home_code or not away_code:
        return None, None
    market_home = POLYMARKET_CODE_ALIASES.get(home_code, home_code).lower()
    market_away = POLYMARKET_CODE_ALIASES.get(away_code, away_code).lower()
    slug = f"fifwc-{market_home}-{market_away}-{fixture_date}"
    try:
        manager = UnifiedDataManager.from_env()
        event = manager.market.event_by_slug(slug)
        odds, _ = manager._market_odds(event, home, away, datetime.now(BEIJING_TZ))
        return (
            {"home": float(odds["home"]), "draw": float(odds["draw"]), "away": float(odds["away"])},
            "polymarket-gamma-public",
        )
    except (ConfigurationError, DataSourceError, KeyError, TypeError, ValueError):
        return None, None


def build_context_analysis_for_sporttery(
    sporttery_match: dict[str, Any],
    unified_match: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """综合上下文分析：三源优先，否则本地 JSON。"""
    if unified_match:
        return build_context_analysis(unified_match)
    local = load_local_match_bundle(sporttery_match.get("home", ""), sporttery_match.get("away", ""))
    if not local or not local.get("odds"):
        return None
    try:
        payload = {
            "home": local["home"],
            "away": local["away"],
            "odds": local["odds"],
            "stage": local.get("stage"),
            "team_context": local.get("team_context"),
            "tournament_rules": local.get("tournament_rules"),
        }
        return analyze_match(apply_overlay_to_match(payload))
    except ValueError:
        return None


def build_context_analysis(unified_match: dict[str, Any]) -> dict[str, Any] | None:
    """对三源统一比赛运行增强版 analyzer（含 FIFA 上下文 + overlay 伤停）。"""
    try:
        enriched = apply_overlay_to_match(unified_match)
        return analyze_match(enriched)
    except ValueError:
        return None


def build_match_intelligence(unified_match: dict[str, Any]) -> dict[str, Any]:
    """生成赛前情报报告（FIFA 统计 + 可编辑 overlay）。"""
    enriched = apply_overlay_to_match(unified_match)
    return build_intelligence_report(enriched)


def probability_deltas(
    sporttery_probs: dict[str, float],
    foreign_probs: dict[str, float],
) -> dict[str, float]:
    """外网与体彩概率差值（百分点）。"""
    return {
        key: round((foreign_probs[key] - sporttery_probs[key]) * 100, 2)
        for key in ("home", "draw", "away")
        if key in sporttery_probs and key in foreign_probs
    }


def delta_alerts(
    deltas: dict[str, float],
    *,
    threshold_pp: float = PROBABILITY_DELTA_ALERT_PP,
) -> list[str]:
    """超过阈值的赛果键列表。"""
    return [key for key, value in deltas.items() if abs(value) >= threshold_pp]


def enrich_prediction_record(
    prediction: dict[str, Any],
    *,
    unified_match: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """为预测记录附加 provider_ids 与综合信号摘要。"""
    enriched = dict(prediction)
    if unified_match is None:
        unified_match = get_unified_match(str(prediction.get("match_id", "")))
    if not unified_match:
        return enriched

    enriched["provider_ids"] = unified_match.get("provider_ids")
    context = build_context_analysis(unified_match)
    if context and context.get("context_available"):
        enriched["context_pick"] = context.get("context_pick")
        enriched["context_confidence"] = context.get("context_confidence")
        enriched["context_edge"] = context.get("context_edge")
        enriched["context_probabilities"] = context.get("context_probabilities")
    return enriched


def fetch_fifa_fixture_score(
    fifa_match_id: str,
    *,
    lookback_days: int = 21,
) -> dict[str, Any] | None:
    """从 FIFA 官方赛程拉取 90 分钟比分（用于赛后复盘）。"""
    if not fifa_match_id:
        return None
    try:
        manager = UnifiedDataManager.from_env()
    except ConfigurationError:
        return None

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=lookback_days)
    end = now + timedelta(days=2)
    try:
        fixtures = manager.fifa.fixtures(
            manager._iso_utc(start),
            manager._iso_utc(end),
        )
    except DataSourceError:
        return None

    for item in fixtures:
        if str(item.get("IdMatch")) != str(fifa_match_id):
            continue
        home_raw = item.get("HomeTeamScore")
        away_raw = item.get("AwayTeamScore")
        if home_raw is None or away_raw is None:
            return None
        try:
            home_goals = int(home_raw)
            away_goals = int(away_raw)
        except (TypeError, ValueError):
            return None
        outcome_key = "home" if home_goals > away_goals else "away" if away_goals > home_goals else "draw"
        return {
            "fifa_match_id": str(fifa_match_id),
            "home_goals": home_goals,
            "away_goals": away_goals,
            "score_label": f"{home_goals}:{away_goals}",
            "outcome_key": outcome_key,
            "outcome_label": OUTCOME_LABELS[outcome_key],
        }
    return None


def resolve_fifa_match_id(entry: dict[str, Any]) -> str | None:
    """从预测日志或三源索引解析 FIFA match id。"""
    provider_ids = entry.get("provider_ids") or {}
    fifa_id = provider_ids.get("fifa_match")
    if fifa_id:
        return str(fifa_id)
    unified = get_unified_match(str(entry.get("match_id", "")))
    if unified:
        resolved = (unified.get("provider_ids") or {}).get("fifa_match")
        return str(resolved) if resolved else None
    return None
