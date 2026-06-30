"""为可视化仪表盘聚合比赛与盘口历史数据。"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from .analyzer import OUTCOME_LABELS, analyze_match
from .bet_simulator import simulate_prediction_bet
from .env_config import get_odds_api_key
from .fox_scraper import fetch_fox_snapshot
from .fusion_predictor import predict_match
from .movement_analyzer import analyze_movement
from .odds_snapshot import load_history
from .score_predictor import list_upcoming_matches, predict_score_for_match, predict_upcoming_scores
from .settlement import get_settlement_summary, settle_open_predictions
from .sporttery_api import SportteryApiError, enrich_match_timing, fetch_odds_history, fetch_upcoming_matches, find_match
from .sporttery_cache import load_snapshot, save_snapshot
from .the_odds_api import find_snapshot
from .unified_bridge import (
    PROBABILITY_DELTA_ALERT_PP,
    build_context_analysis,
    delta_alerts,
    enrich_prediction_record,
    fetch_polymarket_odds,
    get_provider_health,
    get_unified_match,
    load_unified_index,
    probability_deltas,
)

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


def get_upcoming_score_predictions() -> dict[str, Any]:
    try:
        predictions = predict_upcoming_scores()
        matches = fetch_upcoming_matches()
        load_unified_index(force=True)
        predictions = [
            enrich_prediction_record(item) for item in predictions
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
    context_analysis = build_context_analysis(unified_match) if unified_match else None

    deltas: dict[str, float] = {}
    alerts: list[str] = []
    foreign_probs = (prediction.get("foreign") or {}).get("probabilities")
    if foreign_probs and prediction.get("probabilities"):
        deltas = probability_deltas(prediction["probabilities"], foreign_probs)
        alerts = delta_alerts(deltas)

    return {
        "prediction": prediction,
        "score_prediction": score_prediction,
        "history": history,
        "series": build_sporttery_series(history),
        "context_analysis": context_analysis,
        "provider_ids": (unified_match or {}).get("provider_ids"),
        "probability_deltas_pp": deltas,
        "probability_delta_alerts": alerts,
        "probability_delta_threshold_pp": PROBABILITY_DELTA_ALERT_PP,
        "foreign_source_requested": foreign_source,
        "foreign_source_resolved": foreign_src,
    }


def get_overview(data_dir: Path | None = None, *, mode: str = "sporttery") -> dict[str, Any]:
    directory = data_dir or PROJECT_ROOT / "data"
    histories = list_history_files(directory)
    sporttery = get_sporttery_matches()
    predictions = get_upcoming_score_predictions() if sporttery.get("success") else {
        "success": False,
        "predictions": [],
    }
    unified_index = load_unified_index(force=(mode == "unified"))

    if mode == "unified" and unified_index.get("success"):
        unified_ids = set(unified_index.get("by_sporttery_id", {}).keys())
        pred_list = predictions.get("predictions") or []
        filtered = [item for item in pred_list if str(item.get("match_id")) in unified_ids]
        predictions = {
            **predictions,
            "predictions": filtered,
            "count": len(filtered),
            "mode": "unified",
            "filtered_from": len(pred_list),
        }
        sporttery = {
            **sporttery,
            "source": "unified-data-manager",
            "unified_match_count": unified_index.get("match_count", 0),
            "mode": "unified",
        }

    return {
        "sporttery": sporttery,
        "predictions": predictions,
        "histories": histories,
        "settlement_summary": get_settlement_summary(),
        "provider_health": get_provider_health(),
        "mode": mode,
        "unified_index": {
            "success": unified_index.get("success", False),
            "fixture_date": unified_index.get("fixture_date"),
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
