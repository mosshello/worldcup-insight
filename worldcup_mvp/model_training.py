"""小样本训练治理与概率校准评估。"""

from __future__ import annotations

import math
from typing import Any

from .training_store import (
    append_outcome,
    build_outcome_from_settlement,
    load_training_corpus,
    validate_outcome_record,
)

MIN_ACTIVATION_SAMPLES = 500
LABEL_TO_KEY = {"主胜": "home", "平": "draw", "客胜": "away"}


def ingest_settled_predictions() -> dict[str, int]:
    """把日志中已结算但曾因无 HAD 玩法漏掉的场次补入模型语料。"""
    from .prediction_journal import load_journal

    attempted = 0
    ingested = 0
    for entry in load_journal().get("entries", []):
        settlement = entry.get("settlement")
        if entry.get("status") != "settled" or not isinstance(settlement, dict):
            continue
        attempted += 1
        if append_outcome(build_outcome_from_settlement(entry, settlement)):
            ingested += 1
    return {"attempted": attempted, "ingested": ingested}


def _probability_vector(record: dict[str, Any]) -> dict[str, float] | None:
    predicted = record.get("predicted") or {}
    raw = predicted.get("probabilities") or predicted.get("market_probabilities")
    if not isinstance(raw, dict):
        odds = predicted.get("had_odds")
        if not isinstance(odds, dict):
            return None
        try:
            implied = {key: 1.0 / float(odds[key]) for key in ("home", "draw", "away")}
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return None
        total = sum(implied.values())
        raw = {key: value / total for key, value in implied.items()}
    try:
        values = {key: float(raw[key]) for key in ("home", "draw", "away")}
    except (KeyError, TypeError, ValueError):
        return None
    total = sum(values.values())
    if total <= 0 or any(value < 0 for value in values.values()):
        return None
    return {key: value / total for key, value in values.items()}


def build_training_report() -> dict[str, Any]:
    from .statistical_model import load_statistical_model

    records = [
        item
        for item in load_training_corpus().get("records", [])
        if not validate_outcome_record(item) and item.get("use_for_training", True)
    ]
    direction_hits = sum(bool((item.get("settlement") or {}).get("direction_hit")) for item in records)
    score_hits = sum(bool((item.get("settlement") or {}).get("score_hit")) for item in records)
    probability_rows: list[tuple[dict[str, float], str]] = []
    for item in records:
        actual_key = LABEL_TO_KEY.get((item.get("actual") or {}).get("had"))
        probs = _probability_vector(item)
        if actual_key and probs:
            probability_rows.append((probs, actual_key))

    brier = None
    log_loss = None
    if probability_rows:
        brier = sum(
            sum((probs[key] - (1.0 if key == actual else 0.0)) ** 2 for key in probs)
            for probs, actual in probability_rows
        ) / len(probability_rows)
        log_loss = -sum(math.log(max(probs[actual], 1e-12)) for probs, actual in probability_rows) / len(probability_rows)

    count = len(records)
    activated = count >= MIN_ACTIVATION_SAMPLES
    statistical = load_statistical_model()
    return {
        "status": "active" if activated else "collecting",
        "activated": activated,
        "valid_samples": count,
        "minimum_samples": MIN_ACTIVATION_SAMPLES,
        "remaining_samples": max(MIN_ACTIVATION_SAMPLES - count, 0),
        "direction_hit_rate": round(direction_hits / count, 4) if count else None,
        "score_hit_rate": round(score_hits / count, 4) if count else None,
        "probability_samples": len(probability_rows),
        "brier_score": round(brier, 4) if brier is not None else None,
        "log_loss": round(log_loss, 4) if log_loss is not None else None,
        "active_model": "calibrated" if activated else "market_plus_transparent_heuristics",
        "note": (
            "达到样本门槛，可进入时间切分校准训练。"
            if activated
            else "样本不足，仅评估市场与透明启发式基线，不拟合或启用新参数。"
        ),
        "statistical_model": (
            {
                "available": True,
                "model_version": statistical.get("model_version"),
                "status": statistical.get("status"),
                "trained_at": statistical.get("trained_at"),
                "counts": statistical.get("counts"),
                "metrics": statistical.get("metrics"),
                "activation": statistical.get("activation"),
            }
            if statistical
            else {"available": False, "status": "not_trained"}
        ),
    }
