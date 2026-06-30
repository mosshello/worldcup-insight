"""为可视化仪表盘聚合比赛与盘口历史数据。"""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .analyzer import OUTCOME_LABELS, analyze_match
from .bet_simulator import simulate_prediction_bet
from .env_config import get_odds_api_key
from .fox_scraper import fetch_fox_snapshot
from .fusion_predictor import predict_match
from .movement_analyzer import analyze_movement
from .odds_snapshot import load_history
from .local_match_bundle import has_overlay_entry, load_local_match_bundle
from .match_intelligence import build_intelligence_for_sporttery, normalize_venue, safe_display_text
from .pool_analytics import build_pool_analysis
from .score_predictor import list_upcoming_matches, predict_score_for_match, predict_upcoming_scores
from .settlement import get_settlement_summary, settle_open_predictions
from .sporttery_api import (
    SportteryApiError,
    enrich_match_timing,
    fetch_announced_matches,
    fetch_odds_history,
    fetch_upcoming_matches,
    find_match,
)
from .sporttery_cache import load_snapshot, save_snapshot
from .the_odds_api import find_snapshot
from .unified_bridge import (
    PROBABILITY_DELTA_ALERT_PP,
    build_context_analysis,
    build_context_analysis_for_sporttery,
    build_match_intelligence,
    delta_alerts,
    enrich_prediction_record,
    fetch_polymarket_odds,
    get_provider_health,
    get_unified_match,
    load_unified_index,
    load_unified_index_merged,
    probability_deltas,
)

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
DATE_TAB_DAYS = 3  # 仅作 unified 索引最小窗口；Tab 由体彩实际销售日动态生成
WEEKDAY_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
RELATIVE_DAY_LABELS = ("今天", "明天", "后天")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MATCHES = PROJECT_ROOT / "data" / "matches_2026-06-29.json"
HISTORY_GLOB = "odds_history_*.json"


def _enrich_prediction_timing(prediction: dict[str, Any]) -> dict[str, Any]:
    if prediction.get("countdown_label"):
        return prediction
    kickoff = prediction.get("kickoff_beijing") or ""
    if len(kickoff) < 19:
        return prediction
    enriched = enrich_match_timing(
        {"match_date": kickoff[:10], "match_time": kickoff[11:19]}
    )
    updated = dict(prediction)
    updated["hours_until_kickoff"] = enriched["hours_until_kickoff"]
    updated["countdown_label"] = enriched["countdown_label"]
    return updated


def _history_label(path: Path) -> str:
    stem = path.stem.replace("odds_history_", "")
    return stem.replace("-", " ").upper()


def list_history_files(data_dir: Path | None = None) -> list[dict[str, Any]]:
    directory = data_dir or PROJECT_ROOT / "data"
    files = sorted(directory.glob(HISTORY_GLOB))
    items: list[dict[str, Any]] = []
    for path in files:
        try:
            payload = load_history(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        items.append(
            {
                "id": path.stem,
                "filename": path.name,
                "match_id": payload.get("match_id"),
                "home": payload.get("home"),
                "away": payload.get("away"),
                "snapshots": len(payload.get("snapshots", [])),
                "label": f"{payload.get('home')} vs {payload.get('away')}",
            }
        )
    return items


def load_matches_file(path: Path | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from .analyzer import load_match_file

    return load_match_file(path or DEFAULT_MATCHES)


def build_snapshot_series(history: dict[str, Any]) -> dict[str, Any]:
    """将历史快照转为前端图表序列。"""
    times: list[str] = []
    european = {"home": [], "draw": [], "away": []}
    probabilities = {"home": [], "draw": [], "away": []}
    asian = {"line": [], "home": [], "away": []}
    sources: list[str] = []

    for snapshot in history["snapshots"]:
        times.append(snapshot["recorded_at"])
        sources.append(snapshot.get("source") or "unknown")

        if "european" in snapshot:
            odds = snapshot["european"]
            for key in ("home", "draw", "away"):
                european[key].append(odds[key])

            analysis = analyze_match(
                {
                    "home": history["home"],
                    "away": history["away"],
                    "odds": odds,
                }
            )
            for key in ("home", "draw", "away"):
                probabilities[key].append(round(analysis["probabilities"][key] * 100, 2))
        else:
            for key in ("home", "draw", "away"):
                european[key].append(None)
                probabilities[key].append(None)

        if "asian_handicap" in snapshot:
            market = snapshot["asian_handicap"]
            asian["line"].append(market["line"])
            asian["home"].append(market["home"])
            asian["away"].append(market["away"])
        else:
            asian["line"].append(None)
            asian["home"].append(None)
            asian["away"].append(None)

    return {
        "times": times,
        "sources": sources,
        "european": european,
        "probabilities": probabilities,
        "asian_handicap": asian,
        "outcome_labels": OUTCOME_LABELS,
    }


def get_history_dashboard(filename: str, *, data_dir: Path | None = None) -> dict[str, Any]:
    directory = data_dir or PROJECT_ROOT / "data"
    path = directory / filename
    if not path.exists() or not path.name.startswith("odds_history_"):
        raise FileNotFoundError(f"未找到盘口历史：{filename}")

    history = load_history(path)
    series = build_snapshot_series(history)
    movement = None
    if len(history["snapshots"]) >= 2:
        movement = analyze_movement(history)

    latest = history["snapshots"][-1]
    latest_analysis = None
    if "european" in latest:
        latest_analysis = analyze_match(
            {
                "home": history["home"],
                "away": history["away"],
                "odds": latest["european"],
            }
        )

    return {
        "history": {
            "match_id": history["match_id"],
            "home": history["home"],
            "away": history["away"],
            "snapshots_count": len(history["snapshots"]),
        },
        "series": series,
        "movement": movement,
        "latest_snapshot": latest,
        "latest_analysis": latest_analysis,
    }


def build_sporttery_series(history: dict[str, Any]) -> dict[str, Any]:
    """体彩固定奖金历史序列。"""
    def _series(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "times": [item["recorded_at"] for item in items],
            "home": [item.get("home") for item in items],
            "draw": [item.get("draw") for item in items],
            "away": [item.get("away") for item in items],
        }

    return {
        "had": _series(history.get("had_history", [])),
        "hhad": _series(history.get("hhad_history", [])),
    }


FOREIGN_SOURCE_CHAIN: dict[str, list[str]] = {
    "auto": ["polymarket", "fox", "api"],
    "polymarket": ["polymarket", "fox"],
    "fox": ["fox"],
    "api": ["api"],
    "none": ["none"],
}


def _fetch_foreign_odds_once(
    home: str,
    away: str,
    source: str,
    *,
    match: dict[str, Any] | None = None,
) -> tuple[dict[str, float] | None, str | None]:
    if source == "none":
        return None, None
    if source == "polymarket" and match is not None:
        odds, label = fetch_polymarket_odds(match)
        if odds is not None:
            return odds, label
        return None, None
    if source == "fox":
        try:
            snapshot = fetch_fox_snapshot(home, away)
            return snapshot.get("european"), snapshot.get("source")
        except RuntimeError:
            return None, None
    if source == "api":
        api_key = get_odds_api_key()
        if not api_key:
            return None, None
        try:
            snapshot, _, _ = find_snapshot(api_key, sport="soccer_fifa_world_cup", home=home, away=away)
            return snapshot.get("european"), snapshot.get("source")
        except Exception:
            return None, None
    return None, None


def _fetch_foreign_odds(
    home: str,
    away: str,
    source: str = "auto",
    *,
    match: dict[str, Any] | None = None,
) -> tuple[dict[str, float] | None, str | None]:
    chain = FOREIGN_SOURCE_CHAIN.get(source, FOREIGN_SOURCE_CHAIN["auto"])
    for candidate in chain:
        odds, label = _fetch_foreign_odds_once(home, away, candidate, match=match)
        if odds is not None:
            return odds, label
    return None, None


def _kickoff_date(item: dict[str, Any]) -> str:
    """体彩销售日优先（businessDate），与竞彩网「今日场次」口径一致。"""
    business = item.get("business_date")
    if business:
        return str(business)[:10]
    kickoff = item.get("kickoff_beijing") or item.get("kickoff") or ""
    if isinstance(kickoff, str) and len(kickoff) >= 10:
        return kickoff[:10]
    match_date = item.get("match_date")
    if match_date:
        return str(match_date)[:10]
    return ""


def _relative_day_label(day: date, today: date) -> str:
    offset = (day - today).days
    if 0 <= offset < len(RELATIVE_DAY_LABELS):
        return RELATIVE_DAY_LABELS[offset]
    return day.strftime("%m-%d")


def _build_date_tabs(predictions: list[dict[str, Any]]) -> list[dict[str, str]]:
    """按体彩官网公布的销售日生成 Tab（不限固定三天）。"""
    today = datetime.now(BEIJING_TZ).date()
    dates = sorted({_kickoff_date(item) for item in predictions if _kickoff_date(item)})
    tabs: list[dict[str, str]] = [
        {
            "offset": "all",
            "date": "",
            "label": "全部",
            "weekday": "",
            "display": f"全部 · {len(predictions)} 场",
        }
    ]
    for index, day_str in enumerate(dates):
        day = date.fromisoformat(day_str)
        label = _relative_day_label(day, today)
        weekday = WEEKDAY_ZH[day.weekday()]
        tabs.append(
            {
                "offset": str(index + 1),
                "date": day_str,
                "label": label,
                "weekday": weekday,
                "display": f"{label} · {day.strftime('%m-%d')} {weekday}",
            }
        )
    return tabs


def _unified_index_days(predictions: list[dict[str, Any]], *, minimum: int = 3) -> int:
    """三源索引窗口：覆盖体彩已公布销售日的跨度。"""
    today = datetime.now(BEIJING_TZ).date()
    dates = [_kickoff_date(item) for item in predictions if _kickoff_date(item)]
    if not dates:
        return minimum
    last = max(date.fromisoformat(day_str) for day_str in dates)
    return max(minimum, (last - today).days + 1)


def _beijing_date_labels() -> list[dict[str, str]]:
    """兼容旧调用：无预测数据时回退今天/明天/后天。"""
    today = datetime.now(BEIJING_TZ).date()
    result: list[dict[str, str]] = []
    for offset in range(DATE_TAB_DAYS):
        day = today + timedelta(days=offset)
        label = _relative_day_label(day, today)
        weekday = WEEKDAY_ZH[day.weekday()]
        result.append(
            {
                "offset": str(offset),
                "date": day.isoformat(),
                "label": label,
                "weekday": weekday,
                "display": f"{label} · {day.strftime('%m-%d')} {weekday}",
            }
        )
    return result


def _build_date_buckets(predictions: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    buckets: dict[str, dict[str, int]] = {
        "": {"total": len(predictions), "unified": 0, "high_confidence": 0},
    }
    for tab in _build_date_tabs(predictions):
        if tab["date"] and tab["date"] not in buckets:
            buckets[tab["date"]] = {"total": 0, "unified": 0, "high_confidence": 0}
    for item in predictions:
        day = _kickoff_date(item)
        if day not in buckets:
            buckets[day] = {"total": 0, "unified": 0, "high_confidence": 0}
        buckets[day]["total"] += 1
        if item.get("unified_linked"):
            buckets[day]["unified"] += 1
            buckets[""]["unified"] += 1
        if item.get("confidence") == "高":
            buckets[day]["high_confidence"] += 1
            buckets[""]["high_confidence"] += 1
    return buckets


def _dashboard_stats(predictions: list[dict[str, Any]], unified_index: dict[str, Any]) -> dict[str, Any]:
    today = datetime.now(BEIJING_TZ)
    high = sum(1 for item in predictions if item.get("confidence") == "高")
    unified_count = sum(1 for item in predictions if item.get("unified_linked"))
    overlay_count = sum(1 for item in predictions if "overlay" in (item.get("data_tags") or []))
    return {
        "beijing_now": today.isoformat(timespec="seconds"),
        "beijing_date": today.date().isoformat(),
        "total_upcoming": len(predictions),
        "unified_linked": unified_count,
        "high_confidence": high,
        "overlay_matches": overlay_count,
        "unified_index_total": unified_index.get("match_count", 0),
        "date_tabs": _build_date_tabs(predictions) if predictions else _beijing_date_labels(),
        "date_buckets": _build_date_buckets(predictions),
    }


def _enrich_prediction_card(
    item: dict[str, Any],
    unified_index: dict[str, Any],
) -> dict[str, Any]:
    """为列表卡片附加三源/赛区/情报标签。"""
    enriched = dict(item)
    enriched["match_date"] = _kickoff_date(item)
    by_id = unified_index.get("by_sporttery_id") or {}
    unified_match = by_id.get(str(item.get("match_id")))
    enriched["unified_linked"] = unified_match is not None
    enriched["data_tags"] = []

    if unified_match:
        enriched["data_tags"].append("三源")
        enriched["stage"] = safe_display_text(unified_match.get("stage"), fallback="淘汰赛")
        enriched["fifa_stage"] = safe_display_text(
            (unified_match.get("data_provenance") or {}).get("fifa_stage_name")
        )
        enriched["provider_ids"] = unified_match.get("provider_ids")
        venue = normalize_venue(unified_match.get("venue") or {})
        venue_label = safe_display_text(venue.get("label") if venue else None)
        if venue_label:
            enriched["venue_label"] = venue_label
    else:
        enriched["data_tags"].append("体彩")
    if enriched.get("analysis_available") is False:
        enriched["data_tags"].append("待开售")

    local = load_local_match_bundle(item.get("home", ""), item.get("away", ""))
    if local:
        enriched["data_tags"].append("本地情报")
        enriched["stage"] = enriched.get("stage") or local.get("stage")
        enriched["competition"] = local.get("competition") or item.get("league")

    if has_overlay_entry(item.get("home", ""), item.get("away", "")):
        enriched["data_tags"].append("overlay")

    enriched["region_label"] = (
        f"{item.get('league') or '世界杯'}"
        f" · {enriched.get('stage') or '阶段待定'}"
        f"{(' · ' + enriched['venue_label']) if enriched.get('venue_label') else ''}"
    )
    return enriched


def get_sporttery_matches() -> dict[str, Any]:
    payload = list_upcoming_matches()
    if payload.get("success"):
        return payload
    cached = load_snapshot()
    if cached:
        matches = [enrich_match_timing(match) for match in cached["matches"]]
        return {
            "success": True,
            "source": "sporttery.cn",
            "source_url": "https://www.sporttery.cn/jc/zqszsc/",
            "matches": matches,
            "match_count": len(matches),
            "cached": True,
            "cached_at": cached.get("cached_at"),
        }
    return payload


def _pending_prediction_from_match(match: dict[str, Any]) -> dict[str, Any]:
    """待开售场次占位：展示赛程，不生成赔率预测。"""
    return {
        "match_id": match.get("match_id"),
        "home": match.get("home"),
        "away": match.get("away"),
        "league": match.get("league"),
        "match_num": match.get("match_num"),
        "business_date": match.get("business_date"),
        "kickoff_beijing": match.get("kickoff_beijing"),
        "hours_until_kickoff": match.get("hours_until_kickoff"),
        "countdown_label": match.get("countdown_label"),
        "direction": "待开售",
        "direction_key": None,
        "second": "—",
        "confidence": "待开售",
        "predicted_score": "—",
        "alt_scores": [],
        "had_odds": None,
        "crs_odds": None,
        "sporttery_had": "待开售",
        "sporttery_hhad": "待开售",
        "hhad_direction": None,
        "fox_moneyline": "暂无",
        "fox_source": "待开售",
        "direction_note": "体彩官网已公布赛程，但固定奖金暂未开售；开售后自动补齐预测、走势和多玩法分析。",
        "sale_status": "pending",
        "analysis_available": False,
        "match_status": match.get("match_status"),
    }


def get_upcoming_score_predictions() -> dict[str, Any]:
    try:
        predictions = predict_upcoming_scores()
        matches = [enrich_match_timing(match) for match in fetch_announced_matches()]
        prediction_by_id = {str(item.get("match_id")): item for item in predictions}
        merged_predictions: list[dict[str, Any]] = []
        for match in matches:
            prediction = prediction_by_id.get(str(match.get("match_id")))
            if prediction:
                merged_predictions.append(
                    {
                        **prediction,
                        "business_date": match.get("business_date") or prediction.get("business_date"),
                        "sale_status": "selling",
                        "analysis_available": True,
                        "match_status": match.get("match_status"),
                    }
                )
            else:
                merged_predictions.append(_pending_prediction_from_match(match))
        index_days = _unified_index_days(matches)
        load_unified_index_merged(days=index_days, force=False)
        predictions = [
            enrich_prediction_record(item) for item in merged_predictions
        ]
        save_snapshot(matches=matches, predictions=predictions)
        return {
            "success": True,
            "source": "sporttery.cn",
            "count": len(predictions),
            "predictions": predictions,
            "cached": False,
        }
    except SportteryApiError:
        cached = load_snapshot()
        if cached and cached.get("predictions"):
            predictions = [
                enrich_prediction_record(_enrich_prediction_timing(item))
                for item in cached["predictions"]
            ]
            return {
                "success": True,
                "source": "sporttery.cn",
                "count": len(cached["predictions"]),
                "predictions": predictions,
                "cached": True,
                "cached_at": cached.get("cached_at"),
            }
        return {"success": False, "error": "体彩 API 暂不可用，且无本地缓存", "predictions": []}


def get_fusion_prediction(
    *,
    match_id: str | None = None,
    home: str | None = None,
    away: str | None = None,
    foreign_source: str = "auto",
) -> dict[str, Any]:
    match = find_match(match_id=match_id, home=home, away=away, upcoming_only=True)
    history = fetch_odds_history(match["match_id"])
    foreign_odds, foreign_src = _fetch_foreign_odds(
        match["home"],
        match["away"],
        foreign_source,
        match=match,
    )
    prediction = predict_match(
        match,
        sporttery_history=history,
        foreign_odds=foreign_odds,
        foreign_source=foreign_src,
    )
    score_prediction = predict_score_for_match(match)
    unified_match = get_unified_match(match["match_id"])
    context_analysis = build_context_analysis_for_sporttery(match, unified_match)
    match_intelligence = build_intelligence_for_sporttery(match, unified_match)

    had = match["pools"]["had"]
    pool_analysis = build_pool_analysis(
        odds_history=history,
        had_odds={"home": had["home"], "draw": had["draw"], "away": had["away"]},
        had_direction_key=prediction.get("direction_key"),
        foreign_odds=foreign_odds,
    )

    deltas: dict[str, float] = {}
    alerts: list[str] = []
    foreign_probs = (prediction.get("foreign") or {}).get("probabilities")
    if foreign_probs and prediction.get("probabilities"):
        deltas = probability_deltas(prediction["probabilities"], foreign_probs)
        alerts = delta_alerts(deltas)

    direction_shift = prediction.get("direction_shift") or score_prediction.get("direction_shift")

    return {
        "prediction": prediction,
        "score_prediction": score_prediction,
        "direction_shift": direction_shift,
        "history": history,
        "series": build_sporttery_series(history),
        "context_analysis": context_analysis,
        "match_intelligence": match_intelligence,
        "pool_analysis": pool_analysis,
        "provider_ids": (unified_match or {}).get("provider_ids"),
        "probability_deltas_pp": deltas,
        "probability_delta_alerts": alerts,
        "probability_delta_threshold_pp": PROBABILITY_DELTA_ALERT_PP,
        "foreign_source_requested": foreign_source,
        "foreign_source_resolved": foreign_src,
        "data_sources": {
            "sporttery": True,
            "unified": unified_match is not None,
            "foreign": foreign_src,
            "intelligence": (match_intelligence or {}).get("data_sources") or [],
            "pool_ttg": (pool_analysis or {}).get("coverage", {}).get("ttg", False),
            "pool_hafu": (pool_analysis or {}).get("coverage", {}).get("hafu", False),
        },
    }


def get_overview(data_dir: Path | None = None, *, mode: str = "sporttery") -> dict[str, Any]:
    directory = data_dir or PROJECT_ROOT / "data"
    histories = list_history_files(directory)
    sporttery = get_sporttery_matches()
    predictions = get_upcoming_score_predictions() if sporttery.get("success") else {
        "success": False,
        "predictions": [],
    }
    pred_list = predictions.get("predictions") or []
    index_days = _unified_index_days(pred_list)
    unified_index = load_unified_index_merged(days=index_days, force=False)
    unified_mode_note = None

    if pred_list:
        enriched_list = [
            _enrich_prediction_card(
                item,
                unified_index if unified_index.get("success") else {"by_sporttery_id": {}},
            )
            for item in pred_list
        ]
        predictions = {**predictions, "predictions": enriched_list, "count": len(enriched_list)}
        pred_list = enriched_list

    if mode == "unified":
        if unified_index.get("success"):
            unified_mode_note = (
                f"三源增强：展示体彩官网已公布的全部 {len(pred_list)} 场；"
                f"其中 {sum(1 for p in pred_list if p.get('unified_linked'))} 场已接入 FIFA + Polymarket 辅盘。"
                "未接入场次仍显示体彩预测与固定奖金走势。"
            )
        else:
            unified_mode_note = (
                f"三源索引未就绪：{unified_index.get('error') or '未知错误'}。"
                "已回退展示体彩全量，请切换「体彩全量」或稍后刷新。"
            )
            predictions = {**predictions, "mode": "unified-fallback"}

    provider_health = get_provider_health()
    dashboard_stats = _dashboard_stats(pred_list, unified_index)

    return {
        "sporttery": sporttery,
        "predictions": predictions,
        "histories": histories,
        "settlement_summary": get_settlement_summary(),
        "provider_health": provider_health,
        "dashboard_stats": dashboard_stats,
        "mode": mode,
        "unified_mode_note": unified_mode_note,
        "unified_index": {
            "success": unified_index.get("success", False),
            "fixture_date": unified_index.get("fixture_date"),
            "dates_loaded": unified_index.get("dates_loaded", []),
            "match_count": unified_index.get("match_count", 0),
            "error": unified_index.get("error"),
        },
    }


def export_predictions_payload() -> dict[str, Any]:
    payload = get_upcoming_score_predictions()
    return {
        "exported_at": payload.get("cached_at"),
        "source": payload.get("source", "sporttery.cn"),
        "count": payload.get("count", 0),
        "predictions": payload.get("predictions", []),
    }


def export_predictions_csv() -> str:
    payload = export_predictions_payload()
    buffer = io.StringIO()
    fieldnames = [
        "match_id",
        "home",
        "away",
        "league",
        "kickoff_beijing",
        "countdown_label",
        "direction",
        "predicted_score",
        "confidence",
        "sporttery_had",
        "crs_odds",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for item in payload["predictions"]:
        writer.writerow(item)
    return buffer.getvalue()


def get_bet_simulation(
    *,
    match_id: str | None = None,
    stake_had: float = 100.0,
    stake_crs: float = 50.0,
) -> dict[str, Any]:
    payload = get_upcoming_score_predictions()
    predictions = payload.get("predictions") or []
    if match_id:
        selected = next((item for item in predictions if item["match_id"] == str(match_id)), None)
        if selected is None:
            raise SportteryApiError(f"未找到预测 matchId={match_id}")
        return simulate_prediction_bet(selected, stake_had=stake_had, stake_crs=stake_crs)

    return {
        "count": len(predictions),
        "simulations": [
            simulate_prediction_bet(item, stake_had=stake_had, stake_crs=stake_crs)
            for item in predictions
        ],
    }
