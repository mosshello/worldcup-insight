"""赛后结算：对比预测日志与体彩官方赛果。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .bet_simulator import settle_against_results
from .prediction_journal import list_open_entries, load_journal, update_entry
from .training_store import (
    SETTLEMENT_EPOCH,
    append_outcome,
    build_outcome_from_settlement,
    get_training_summary,
)
from .unified_bridge import fetch_fifa_fixture_score, resolve_fifa_match_id
from .sporttery_api import (
    BEIJING_TZ,
    HAD_RESULT_KEYS,
    SportteryApiError,
    fetch_fixed_bonus_detail,
    fetch_results_by_date,
    parse_match_result_list,
)

def _match_id_from_result(item: dict[str, Any]) -> str:
    for key in ("matchId", "match_id"):
        if item.get(key) not in (None, ""):
            return str(item[key])
    return ""


def _fetch_result_map(begin: str, end: str) -> dict[str, dict[str, Any]]:
    """合并日期范围赛果列表与单场详情。"""
    merged: dict[str, dict[str, Any]] = {}
    try:
        items = fetch_results_by_date(begin, end)
    except SportteryApiError:
        items = []
    for item in items:
        match_id = _match_id_from_result(item)
        if match_id:
            merged[match_id] = {"list_item": item}
    return merged


def settle_open_predictions(*, lookback_days: int = 7) -> dict[str, Any]:
    """尝试结算所有未结预测；优先从 getFixedBonus 读取官方赛果。"""
    open_entries = list_open_entries()
    if not open_entries:
        return {"success": True, "settled": 0, "pending": 0, "results": [], "message": "暂无待结算预测"}

    today = datetime.now(BEIJING_TZ).date()
    begin = (today - timedelta(days=lookback_days)).isoformat()
    end = today.isoformat()
    result_map = _fetch_result_map(begin, end)

    settled_rows: list[dict[str, Any]] = []
    pending = 0

    for entry in open_entries:
        match_id = str(entry["match_id"])
        try:
            detail = fetch_fixed_bonus_detail(match_id)
        except SportteryApiError as exc:
            pending += 1
            settled_rows.append({"match_id": match_id, "status": "error", "message": str(exc)})
            continue

        results = parse_match_result_list(detail.get("match_result_list") or [])
        if not results:
            pending += 1
            settled_rows.append({"match_id": match_id, "status": "pending"})
            continue

        row = settle_against_results(
            entry,
            results,
            stake_had=float(entry.get("stake_had") or 100),
            stake_crs=float(entry.get("stake_crs") or 50),
        )
        row["recorded_at"] = entry.get("recorded_at")

        fifa_id = resolve_fifa_match_id(entry)
        if fifa_id:
            fifa_actual = fetch_fifa_fixture_score(fifa_id)
            if fifa_actual:
                row["fifa_actual"] = fifa_actual
                predicted_key = entry.get("direction_key")
                row["direction_hit_fifa"] = predicted_key == fifa_actual.get("outcome_key")
                predicted_score = str(entry.get("predicted_score", "")).replace("-", ":")
                row["score_hit_fifa"] = predicted_score == fifa_actual.get("score_label")
                sporttery_outcome = HAD_RESULT_KEYS.get(
                    str((results.get("had") or {}).get("combination", "")).upper()
                )
                row["sporttery_fifa_had_agree"] = (
                    sporttery_outcome == fifa_actual.get("outcome_key")
                    if sporttery_outcome
                    else None
                )

        if result_map.get(match_id, {}).get("list_item"):
            row["official_list"] = result_map[match_id]["list_item"]

        settled_at = datetime.now(BEIJING_TZ).replace(microsecond=0).isoformat()
        row["settled_at"] = settled_at

        settled_rows.append(row)

        update_entry(
            match_id,
            {
                "status": "settled",
                "settled_at": settled_at,
                "settlement": row,
            },
        )
        append_outcome(build_outcome_from_settlement(entry, row))

    settled_count = sum(1 for row in settled_rows if row.get("status") == "settled")
    total_pnl = sum(row.get("total_pnl", 0) for row in settled_rows if row.get("status") == "settled")

    return {
        "success": True,
        "settled": settled_count,
        "pending": pending,
        "total_pnl": round(total_pnl, 2),
        "results": settled_rows,
    }


def get_settlement_summary() -> dict[str, Any]:
    """汇总实盘结算与训练语料概况。"""
    journal = load_journal()
    entries = journal.get("entries", [])
    epoch = journal.get("settlement_epoch") or SETTLEMENT_EPOCH
    settled = [entry for entry in entries if entry.get("status") == "settled"]
    open_entries = [entry for entry in entries if entry.get("status") == "open"]
    total_pnl = sum(
        (entry.get("settlement") or {}).get("total_pnl", 0)
        for entry in settled
    )
    training = get_training_summary()
    return {
        "open_count": len(open_entries),
        "settled_count": len(settled),
        "total_pnl": round(total_pnl, 2),
        "settlement_epoch": epoch,
        "training_count": training.get("training_count", 0),
        "training_live_count": training.get("live_count", 0),
        "training_imported_count": training.get("imported_count", 0),
        "open": open_entries[:20],
        "recent_settled": settled[:20],
    }
