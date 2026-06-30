"""历史赛果语料库：供后续 Elo / Poisson 等基准模型训练。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRAINING_DIR = PROJECT_ROOT / "data" / "training"
TRAINING_FILE = TRAINING_DIR / "historical_outcomes.json"
ARCHIVE_DEV_FILE = TRAINING_DIR / "archive_dev_settlements.json"

SCHEMA_VERSION = 1
SETTLEMENT_EPOCH = "2026-06-30"


def _now_iso() -> str:
    return datetime.now(BEIJING_TZ).replace(microsecond=0).isoformat()


def _default_corpus() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "purpose": "historical match outcomes for baseline model training",
        "settlement_epoch": SETTLEMENT_EPOCH,
        "updated_at": _now_iso(),
        "records": [],
    }


def load_training_corpus() -> dict[str, Any]:
    if not TRAINING_FILE.exists():
        return _default_corpus()
    try:
        with TRAINING_FILE.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return _default_corpus()
    if not isinstance(payload.get("records"), list):
        payload["records"] = []
    return payload


def _save_corpus(payload: dict[str, Any]) -> None:
    TRAINING_DIR.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = _now_iso()
    with TRAINING_FILE.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def archive_dev_settlements(entries: list[dict[str, Any]], *, reason: str) -> None:
    """归档开发期假结算，不计入实盘统计与训练语料。"""
    if not entries:
        return
    TRAINING_DIR.mkdir(parents=True, exist_ok=True)
    archive = {
        "archived_at": _now_iso(),
        "reason": reason,
        "settlement_epoch": SETTLEMENT_EPOCH,
        "entry_count": len(entries),
        "entries": entries,
    }
    with ARCHIVE_DEV_FILE.open("w", encoding="utf-8") as handle:
        json.dump(archive, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def build_outcome_from_settlement(
    entry: dict[str, Any],
    settlement_row: dict[str, Any],
) -> dict[str, Any]:
    """将实盘结算结果转为训练语料记录。"""
    return {
        "sporttery_match_id": str(entry.get("match_id", "")),
        "home": entry.get("home"),
        "away": entry.get("away"),
        "kickoff_beijing": entry.get("kickoff_beijing"),
        "stage": entry.get("stage"),
        "provider_ids": entry.get("provider_ids"),
        "predicted": {
            "direction": entry.get("direction"),
            "direction_key": entry.get("direction_key"),
            "predicted_score": entry.get("predicted_score"),
            "confidence": entry.get("confidence"),
            "had_odds": entry.get("had_odds"),
            "crs_odds": entry.get("crs_odds"),
            "recorded_at": entry.get("recorded_at"),
        },
        "actual": {
            "had": settlement_row.get("actual_had"),
            "score": settlement_row.get("actual_score"),
            "fifa": settlement_row.get("fifa_actual"),
        },
        "settlement": {
            "had_won": settlement_row.get("had_won"),
            "crs_won": settlement_row.get("crs_won"),
            "total_pnl": settlement_row.get("total_pnl"),
            "direction_hit_fifa": settlement_row.get("direction_hit_fifa"),
            "score_hit_fifa": settlement_row.get("score_hit_fifa"),
            "settled_at": settlement_row.get("settled_at"),
        },
        "source": "live_settlement",
        "ingested_at": _now_iso(),
    }


def append_outcome(record: dict[str, Any]) -> bool:
    """追加一条训练语料；同 sporttery_match_id + source 不重复写入。"""
    match_id = str(record.get("sporttery_match_id") or "")
    source = str(record.get("source") or "")
    if not match_id:
        return False

    corpus = load_training_corpus()
    records: list[dict[str, Any]] = corpus["records"]
    for existing in records:
        if (
            str(existing.get("sporttery_match_id")) == match_id
            and str(existing.get("source")) == source
        ):
            return False

    records.append(record)
    corpus["records"] = records
    _save_corpus(corpus)
    return True


def get_training_summary() -> dict[str, Any]:
    corpus = load_training_corpus()
    records = corpus.get("records") or []
    live_count = sum(1 for item in records if item.get("source") == "live_settlement")
    imported_count = sum(1 for item in records if item.get("source") == "imported")
    return {
        "training_count": len(records),
        "live_count": live_count,
        "imported_count": imported_count,
        "settlement_epoch": corpus.get("settlement_epoch") or SETTLEMENT_EPOCH,
        "updated_at": corpus.get("updated_at"),
    }
