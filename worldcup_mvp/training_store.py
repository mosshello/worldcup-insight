"""历史赛果语料库：供后续 Elo / Poisson 等基准模型训练。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .sporttery_api import parse_kickoff_beijing

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRAINING_DIR = PROJECT_ROOT / "data" / "training"
TRAINING_FILE = TRAINING_DIR / "historical_outcomes.json"
ARCHIVE_DEV_FILE = TRAINING_DIR / "archive_dev_settlements.json"

SCHEMA_VERSION = 1
SETTLEMENT_EPOCH = "2026-06-30"
VALID_HAD_LABELS = {"主胜", "平", "客胜"}


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
    had_won = bool(settlement_row.get("had_won"))
    crs_won = bool(settlement_row.get("crs_won"))
    direction_hit = had_won
    score_hit = crs_won
    use_for_training = not direction_hit or not score_hit
    return {
        "sporttery_match_id": str(entry.get("match_id") or entry.get("sporttery_match_id") or ""),
        "home": entry.get("home"),
        "away": entry.get("away"),
        "kickoff_beijing": entry.get("kickoff_beijing"),
        "business_date": entry.get("business_date"),
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
            "had_won": had_won,
            "crs_won": crs_won,
            "direction_hit": direction_hit,
            "score_hit": score_hit,
            "total_pnl": settlement_row.get("total_pnl"),
            "direction_hit_fifa": settlement_row.get("direction_hit_fifa"),
            "score_hit_fifa": settlement_row.get("score_hit_fifa"),
            "settled_at": settlement_row.get("settled_at"),
        },
        "use_for_training": use_for_training,
        "training_reason": (
            "direction_miss"
            if not direction_hit and score_hit
            else "score_miss"
            if direction_hit and not score_hit
            else "both_miss"
            if not direction_hit and not score_hit
            else "full_hit"
        ),
        "source": "live_settlement",
        "ingested_at": _now_iso(),
    }


def _aware_datetime(value: Any, field: str) -> tuple[datetime | None, str | None]:
    if not isinstance(value, str) or not value:
        return None, f"{field}缺失"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None, f"{field}格式无效"
    if parsed.tzinfo is None:
        return None, f"{field}缺少时区"
    return parsed, None


def validate_outcome_record(record: dict[str, Any]) -> list[str]:
    """校验训练记录，阻止时间泄漏、模拟赛果和不完整标签入库。"""
    errors: list[str] = []
    if not str(record.get("sporttery_match_id") or ""):
        errors.append("sporttery_match_id缺失")
    if not record.get("home") or not record.get("away"):
        errors.append("对阵信息缺失")

    kickoff, error = _aware_datetime(record.get("kickoff_beijing"), "kickoff_beijing")
    if error:
        errors.append(error)
    predicted = record.get("predicted") if isinstance(record.get("predicted"), dict) else {}
    recorded_at, error = _aware_datetime(predicted.get("recorded_at"), "predicted.recorded_at")
    if error:
        errors.append(error)
    settlement = record.get("settlement") if isinstance(record.get("settlement"), dict) else {}
    settled_at, error = _aware_datetime(settlement.get("settled_at"), "settlement.settled_at")
    if error:
        errors.append(error)

    if kickoff and recorded_at and recorded_at >= kickoff:
        errors.append("预测时间不得晚于或等于开赛时间")
    if kickoff and settled_at and settled_at < kickoff:
        errors.append("结算时间不得早于开赛时间")

    actual = record.get("actual") if isinstance(record.get("actual"), dict) else {}
    score = actual.get("score")
    if not isinstance(score, str) or not re.fullmatch(r"\d+:\d+", score):
        errors.append("actual.score缺失或格式无效")
    if actual.get("had") not in VALID_HAD_LABELS:
        errors.append("actual.had缺失或无效")
    return errors


def upsert_outcome(record: dict[str, Any]) -> bool:
    """写入或更新训练语料（按 sporttery_match_id + source）。"""
    match_id = str(record.get("sporttery_match_id") or "")
    source = str(record.get("source") or "")
    if not match_id:
        return False
    if validate_outcome_record(record):
        return False

    corpus = load_training_corpus()
    records: list[dict[str, Any]] = corpus["records"]
    replaced = False
    for index, existing in enumerate(records):
        if str(existing.get("sporttery_match_id")) == match_id and str(existing.get("source")) == source:
            records[index] = record
            replaced = True
            break
    if not replaced:
        records.append(record)
    corpus["records"] = records
    _save_corpus(corpus)
    return True


def append_outcome(record: dict[str, Any]) -> bool:
    """追加一条训练语料；同 sporttery_match_id + source 不重复写入。"""
    return upsert_outcome(record)


def audit_training_corpus() -> dict[str, Any]:
    """只读审计当前语料，列出不允许参与训练的记录。"""
    corpus = load_training_corpus()
    records = corpus.get("records") or []
    invalid_records: list[dict[str, Any]] = []
    for record in records:
        errors = validate_outcome_record(record)
        if errors:
            invalid_records.append(
                {
                    "sporttery_match_id": record.get("sporttery_match_id"),
                    "home": record.get("home"),
                    "away": record.get("away"),
                    "errors": errors,
                }
            )
    return {
        "total_count": len(records),
        "valid_count": len(records) - len(invalid_records),
        "invalid_count": len(invalid_records),
        "invalid_records": invalid_records,
        "settlement_epoch": corpus.get("settlement_epoch") or SETTLEMENT_EPOCH,
        "updated_at": corpus.get("updated_at"),
    }


def prune_future_outcomes(*, now: datetime | None = None) -> int:
    """移除开赛前被误写入的训练语料。"""
    current = now or datetime.now(BEIJING_TZ)
    corpus = load_training_corpus()
    kept: list[dict[str, Any]] = []
    removed = 0
    for record in corpus.get("records", []):
        kickoff = record.get("kickoff_beijing") or ""
        if len(kickoff) >= 19:
            kickoff_dt = parse_kickoff_beijing(
                {"match_date": kickoff[:10], "match_time": kickoff[11:19]}
            )
            if kickoff_dt is not None and kickoff_dt > current:
                removed += 1
                continue
        kept.append(record)
    if removed:
        corpus["records"] = kept
        _save_corpus(corpus)
    return removed


def get_training_summary() -> dict[str, Any]:
    corpus = load_training_corpus()
    records = corpus.get("records") or []
    live_count = sum(1 for item in records if item.get("source") == "live_settlement")
    imported_count = sum(1 for item in records if item.get("source") == "imported")
    audit = audit_training_corpus()
    training_samples = sum(1 for item in records if item.get("use_for_training"))
    miss_count = sum(
        1
        for item in records
        if item.get("training_reason") in {"direction_miss", "score_miss", "both_miss"}
    )
    return {
        "training_count": len(records),
        "training_samples": training_samples,
        "training_miss_count": miss_count,
        "live_count": live_count,
        "imported_count": imported_count,
        "settlement_epoch": corpus.get("settlement_epoch") or SETTLEMENT_EPOCH,
        "updated_at": corpus.get("updated_at"),
        "valid_count": audit["valid_count"],
        "invalid_count": audit["invalid_count"],
    }

