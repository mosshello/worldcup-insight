"""预测记录日志，用于赛后结算对比。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .training_store import SETTLEMENT_EPOCH, archive_dev_settlements

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
JOURNAL_FILE = PROJECT_ROOT / "data" / "cache" / "prediction_journal.json"
JOURNAL_VERSION = 2


def _now_iso() -> str:
    return datetime.now(BEIJING_TZ).replace(microsecond=0).isoformat()


def _migrate_journal(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if payload.get("journal_version", 1) >= JOURNAL_VERSION:
        return payload, False

    entries = payload.get("entries", [])
    settled = [entry for entry in entries if entry.get("status") == "settled"]
    open_entries = [entry for entry in entries if entry.get("status") == "open"]

    if settled:
        archive_dev_settlements(
            settled,
            reason="开发期重复结算测试数据，不计入实盘；实盘结算自 settlement_epoch 重新累计。",
        )

    open_by_id: dict[str, dict[str, Any]] = {}
    for entry in open_entries:
        match_id = str(entry.get("match_id", ""))
        if not match_id:
            continue
        previous = open_by_id.get(match_id)
        if previous is None or entry.get("recorded_at", "") >= previous.get("recorded_at", ""):
            open_by_id[match_id] = entry

    migrated = {
        "journal_version": JOURNAL_VERSION,
        "settlement_epoch": SETTLEMENT_EPOCH,
        "migrated_at": _now_iso(),
        "entries": list(open_by_id.values()),
    }
    return migrated, True


def load_journal() -> dict[str, Any]:
    if not JOURNAL_FILE.exists():
        return {
            "journal_version": JOURNAL_VERSION,
            "settlement_epoch": SETTLEMENT_EPOCH,
            "entries": [],
        }
    try:
        with JOURNAL_FILE.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {
            "journal_version": JOURNAL_VERSION,
            "settlement_epoch": SETTLEMENT_EPOCH,
            "entries": [],
        }
    if not isinstance(payload.get("entries"), list):
        payload["entries"] = []

    payload, changed = _migrate_journal(payload)
    if changed:
        _save_journal(payload)
    return payload


def _save_journal(payload: dict[str, Any]) -> None:
    JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL_FILE.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def record_predictions(
    predictions: list[dict[str, Any]],
    *,
    stake_had: float = 100.0,
    stake_crs: float = 50.0,
) -> None:
    """将最新预测写入日志（同 match_id 仅保留最新一条未结算记录）。"""
    if not predictions:
        return

    journal = load_journal()
    entries: list[dict[str, Any]] = journal.get("entries", [])
    indexed = {entry["match_id"]: entry for entry in entries if entry.get("status") != "settled"}

    recorded_at = _now_iso()
    for prediction in predictions:
        match_id = str(prediction.get("match_id", ""))
        if not match_id:
            continue
        indexed[match_id] = {
            "match_id": match_id,
            "recorded_at": recorded_at,
            "home": prediction.get("home"),
            "away": prediction.get("away"),
            "kickoff_beijing": prediction.get("kickoff_beijing"),
            "direction": prediction.get("direction"),
            "direction_key": prediction.get("direction_key"),
            "predicted_score": prediction.get("predicted_score"),
            "confidence": prediction.get("confidence"),
            "had_odds": prediction.get("had_odds"),
            "crs_odds": prediction.get("crs_odds"),
            "provider_ids": prediction.get("provider_ids"),
            "stake_had": stake_had,
            "stake_crs": stake_crs,
            "status": "open",
        }

    merged = [entry for entry in entries if entry.get("status") == "settled"]
    merged.extend(indexed.values())
    merged.sort(key=lambda item: item.get("recorded_at", ""), reverse=True)
    journal["entries"] = merged
    journal.setdefault("journal_version", JOURNAL_VERSION)
    journal.setdefault("settlement_epoch", SETTLEMENT_EPOCH)
    _save_journal(journal)


def update_entry(match_id: str, updates: dict[str, Any]) -> None:
    journal = load_journal()
    for entry in journal.get("entries", []):
        if entry.get("match_id") == str(match_id):
            entry.update(updates)
            break
    _save_journal(journal)


def list_open_entries() -> list[dict[str, Any]]:
    return [entry for entry in load_journal().get("entries", []) if entry.get("status") == "open"]


def get_open_direction_key(match_id: str | int) -> str | None:
    """返回该场次未结算日志中的 direction_key（若有）。"""
    target = str(match_id)
    for entry in load_journal().get("entries", []):
        if entry.get("match_id") == target and entry.get("status") == "open":
            key = entry.get("direction_key")
            return str(key) if key else None
    return None
