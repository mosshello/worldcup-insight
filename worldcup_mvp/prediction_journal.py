"""预测记录日志，用于赛后结算对比。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .training_store import SETTLEMENT_EPOCH, archive_dev_settlements
from .sporttery_api import enrich_match_timing, parse_kickoff_beijing

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
    now = datetime.now(BEIJING_TZ)
    indexed = {
        str(entry["match_id"]): entry
        for entry in entries
        if entry.get("status") != "settled"
    }

    recorded_at = _now_iso()
    for prediction in predictions:
        match_id = str(prediction.get("match_id", ""))
        if not match_id:
            continue
        previous = indexed.get(match_id)
        if previous:
            kickoff = previous.get("kickoff_beijing") or ""
            if len(kickoff) >= 19:
                kickoff_dt = parse_kickoff_beijing(
                    {"match_date": kickoff[:10], "match_time": kickoff[11:19]}
                )
                if kickoff_dt is not None and kickoff_dt <= now:
                    continue
        indexed[match_id] = {
            "match_id": match_id,
            "recorded_at": recorded_at,
            "home": prediction.get("home"),
            "away": prediction.get("away"),
            "kickoff_beijing": prediction.get("kickoff_beijing"),
            "business_date": prediction.get("business_date"),
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
    upsert_entry(match_id, updates)


def upsert_entry(match_id: str, updates: dict[str, Any]) -> None:
    journal = load_journal()
    target = str(match_id)
    for entry in journal.get("entries", []):
        if entry.get("match_id") == target:
            entry.update(updates)
            _save_journal(journal)
            return
    journal.setdefault("entries", []).append({"match_id": target, **updates})
    _save_journal(journal)


def list_settled_entries() -> list[dict[str, Any]]:
    return [entry for entry in load_journal().get("entries", []) if entry.get("status") == "settled"]


def list_open_entries() -> list[dict[str, Any]]:
    return [entry for entry in load_journal().get("entries", []) if entry.get("status") == "open"]


def find_open_entry(match_id: str | int) -> dict[str, Any] | None:
    target = str(match_id)
    for entry in load_journal().get("entries", []):
        if entry.get("match_id") == target and entry.get("status") == "open":
            return entry
    return None


def journal_entry_to_match(entry: dict[str, Any]) -> dict[str, Any] | None:
    """将未结算日志条目还原为体彩比赛结构，便于详情/结算接口复用。"""
    had = entry.get("had_odds")
    if not isinstance(had, dict):
        return None
    kickoff = entry.get("kickoff_beijing") or ""
    match_date = kickoff[:10] if len(kickoff) >= 10 else None
    match_time = kickoff[11:19] if len(kickoff) >= 19 else None
    return {
        "match_id": str(entry.get("match_id", "")),
        "home": entry.get("home"),
        "away": entry.get("away"),
        "match_date": match_date,
        "match_time": match_time,
        "business_date": entry.get("business_date"),
        "kickoff_beijing": kickoff,
        "kickoff": kickoff.replace("+08:00", "") if kickoff else None,
        "pools": {
            "had": {
                "home": float(had["home"]),
                "draw": float(had["draw"]),
                "away": float(had["away"]),
            },
            "hhad": None,
        },
        "source": "prediction_journal",
        "analysis_available": True,
        "sale_status": "selling",
    }


def journal_entry_to_prediction(entry: dict[str, Any]) -> dict[str, Any]:
    """将未结算日志转为列表卡片结构。"""
    match = journal_entry_to_match(entry) or {}
    enriched = enrich_match_timing(match) if match else {}
    return {
        "match_id": entry.get("match_id"),
        "home": entry.get("home"),
        "away": entry.get("away"),
        "business_date": entry.get("business_date"),
        "kickoff_beijing": entry.get("kickoff_beijing"),
        "hours_until_kickoff": enriched.get("hours_until_kickoff"),
        "countdown_label": enriched.get("countdown_label") or "待出赛果",
        "lifecycle_phase": enriched.get("lifecycle_phase") or "awaiting_result",
        "direction": entry.get("direction"),
        "direction_key": entry.get("direction_key"),
        "second": "—",
        "confidence": entry.get("confidence"),
        "predicted_score": entry.get("predicted_score"),
        "alt_scores": [],
        "had_odds": entry.get("had_odds"),
        "crs_odds": entry.get("crs_odds"),
        "sporttery_had": (
            f"{entry['had_odds']['home']:.2f} / {entry['had_odds']['draw']:.2f} / {entry['had_odds']['away']:.2f}"
            if entry.get("had_odds")
            else "—"
        ),
        "sporttery_hhad": "—",
        "hhad_direction": None,
        "fox_moneyline": "—",
        "fox_source": "日志回放",
        "direction_note": "该场已开赛或待出赛果，展示为预测日志快照。",
        "sale_status": "journal",
        "analysis_available": bool(entry.get("had_odds")),
        "track_source": "journal",
        "provider_ids": entry.get("provider_ids"),
    }


def get_open_direction_key(match_id: str | int) -> str | None:
    """返回该场次未结算日志中的 direction_key（若有）。"""
    target = str(match_id)
    for entry in load_journal().get("entries", []):
        if entry.get("match_id") == target and entry.get("status") == "open":
            key = entry.get("direction_key")
            return str(key) if key else None
    return None
