"""已完场比赛复盘：展示、结算与训练语料同步。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .bet_simulator import settle_against_results
from .prediction_journal import (
    find_open_entry,
    journal_entry_to_prediction,
    list_open_entries,
    load_journal,
    upsert_entry,
)
from .sporttery_api import (
    BEIJING_TZ,
    SportteryApiError,
    enrich_match_timing,
    fetch_fixed_bonus_detail,
    parse_kickoff_beijing,
    parse_match_result_list,
)
from .training_store import build_outcome_from_settlement, load_training_corpus, prune_future_outcomes, upsert_outcome

RELATIVE_DAY_LABELS = ("今天", "明天", "后天", "大后天")


def _kickoff_date(item: dict[str, Any]) -> str:
    business = item.get("business_date")
    if business:
        return str(business)[:10]
    kickoff = item.get("kickoff_beijing") or ""
    if len(kickoff) >= 10:
        return kickoff[:10]
    return ""


def _relative_day_label(day: date, today: date) -> str:
    offset = (day - today).days
    if offset == -1:
        return "昨日"
    if 0 <= offset < len(RELATIVE_DAY_LABELS):
        return RELATIVE_DAY_LABELS[offset]
    return day.strftime("%m-%d")


def _entry_kickoff_dt(entry: dict[str, Any]) -> datetime | None:
    kickoff = entry.get("kickoff_beijing")
    if not kickoff:
        return None
    match = {
        "match_date": kickoff[:10] if len(kickoff) >= 10 else None,
        "match_time": kickoff[11:19] if len(kickoff) >= 19 else None,
    }
    return parse_kickoff_beijing(match)


def _has_official_results(detail: dict[str, Any]) -> bool:
    results = parse_match_result_list(detail.get("match_result_list") or [])
    return bool(results.get("had") or results.get("crs"))


def _sales_date(entry: dict[str, Any]) -> str:
    business = entry.get("business_date")
    if business:
        return str(business)[:10]
    kickoff = entry.get("kickoff_beijing") or ""
    if len(kickoff) < 10:
        return ""
    day = date.fromisoformat(kickoff[:10])
    if len(kickoff) >= 13:
        try:
            hour = int(kickoff[11:13])
            if hour < 6:
                day = day - timedelta(days=1)
        except ValueError:
            pass
    return day.isoformat()


def _verified_settlement(entry: dict[str, Any]) -> dict[str, Any] | None:
    settlement = entry.get("settlement")
    if entry.get("status") == "settled" and isinstance(settlement, dict):
        return settlement

    match_id = str(entry.get("match_id") or entry.get("sporttery_match_id") or "")
    if not match_id:
        return None
    try:
        detail = fetch_fixed_bonus_detail(match_id)
    except SportteryApiError:
        return None
    if not _has_official_results(detail):
        return None
    snapshot = _prediction_snapshot(entry)
    results = parse_match_result_list(detail.get("match_result_list") or [])
    row = settle_against_results(
        snapshot,
        results,
        stake_had=float(snapshot.get("stake_had") or 100),
        stake_crs=float(snapshot.get("stake_crs") or 50),
    )
    return row if row.get("status") == "settled" else None


def _prediction_snapshot(entry: dict[str, Any]) -> dict[str, Any]:
    if entry.get("direction"):
        return entry
    predicted = entry.get("predicted") or {}
    return {
        "match_id": entry.get("match_id") or entry.get("sporttery_match_id"),
        "home": entry.get("home"),
        "away": entry.get("away"),
        "kickoff_beijing": entry.get("kickoff_beijing"),
        "business_date": entry.get("business_date"),
        "direction": predicted.get("direction") or entry.get("direction"),
        "direction_key": predicted.get("direction_key") or entry.get("direction_key"),
        "predicted_score": predicted.get("predicted_score") or entry.get("predicted_score"),
        "confidence": predicted.get("confidence") or entry.get("confidence"),
        "had_odds": predicted.get("had_odds") or entry.get("had_odds"),
        "crs_odds": predicted.get("crs_odds") or entry.get("crs_odds"),
        "provider_ids": entry.get("provider_ids"),
        "stake_had": (entry.get("settlement") or {}).get("stake_had") or entry.get("stake_had") or 100.0,
        "stake_crs": (entry.get("settlement") or {}).get("stake_crs") or entry.get("stake_crs") or 50.0,
        "recorded_at": predicted.get("recorded_at") or entry.get("recorded_at"),
    }


def _collect_prediction_sources() -> dict[str, dict[str, Any]]:
    sources: dict[str, dict[str, Any]] = {}

    for entry in load_journal().get("entries", []):
        match_id = str(entry.get("match_id", ""))
        if match_id:
            sources[match_id] = _prediction_snapshot(entry)

    for record in load_training_corpus().get("records", []):
        match_id = str(record.get("sporttery_match_id", ""))
        if match_id and match_id not in sources:
            sources[match_id] = _prediction_snapshot(record)

    return sources


def settle_prediction_if_ready(
    entry: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """对已开赛且官方赛果已出的预测执行结算，并写入日志/训练语料。"""
    current = now or datetime.now(BEIJING_TZ)
    kickoff = _entry_kickoff_dt(entry)
    if kickoff is not None and kickoff > current:
        return None

    match_id = str(entry.get("match_id") or entry.get("sporttery_match_id") or "")
    if not match_id:
        return None

    try:
        detail = fetch_fixed_bonus_detail(match_id)
    except SportteryApiError as exc:
        if getattr(exc, "http_code", None) == 403 or "403" in str(exc):
            raise
        return None

    if not _has_official_results(detail):
        return None

    results = parse_match_result_list(detail.get("match_result_list") or [])
    row = settle_against_results(
        entry,
        results,
        stake_had=float(entry.get("stake_had") or 100),
        stake_crs=float(entry.get("stake_crs") or 50),
    )
    if row.get("status") != "settled":
        return None

    settled_at = current.replace(microsecond=0).isoformat()
    row["settled_at"] = settled_at

    upsert_entry(
        match_id,
        {
            "status": "settled",
            "settled_at": settled_at,
            "settlement": row,
            "recorded_at": entry.get("recorded_at") or settled_at,
            "home": entry.get("home"),
            "away": entry.get("away"),
            "kickoff_beijing": entry.get("kickoff_beijing"),
            "business_date": entry.get("business_date"),
            "direction": entry.get("direction"),
            "direction_key": entry.get("direction_key"),
            "predicted_score": entry.get("predicted_score"),
            "confidence": entry.get("confidence"),
            "had_odds": entry.get("had_odds"),
            "crs_odds": entry.get("crs_odds"),
            "provider_ids": entry.get("provider_ids"),
            "stake_had": entry.get("stake_had") or 100.0,
            "stake_crs": entry.get("stake_crs") or 50.0,
        },
    )
    upsert_outcome(build_outcome_from_settlement(entry, row))
    return row


def sync_finished_matches(*, lookback_days: int = 3) -> dict[str, Any]:
    """扫描预测来源，对已完场且有赛果的场次补结算。"""
    now = datetime.now(BEIJING_TZ)
    removed = prune_future_outcomes(now=now)
    cutoff = now - timedelta(days=lookback_days)
    sources = _collect_prediction_sources()
    settled_rows: list[dict[str, Any]] = []
    pending = 0
    skipped_future = 0
    api_blocked = False
    api_error: str | None = None

    for match_id, entry in sources.items():
        if api_blocked:
            pending += 1
            continue
        kickoff = _entry_kickoff_dt(entry)
        if kickoff is not None and kickoff > now:
            skipped_future += 1
            continue
        if kickoff is not None and kickoff.date() < cutoff.date():
            continue

        journal_entry = find_open_entry(match_id)
        settle_source = journal_entry or entry
        try:
            row = settle_prediction_if_ready(settle_source, now=now)
        except SportteryApiError as exc:
            api_blocked = True
            api_error = str(exc)
            pending += 1
            continue
        if row:
            settled_rows.append(row)
        else:
            pending += 1

    result = {
        "success": not api_blocked,
        "settled": len(settled_rows),
        "pending": pending,
        "skipped_future": skipped_future,
        "removed_future_training": removed,
        "results": settled_rows,
    }
    if api_blocked:
        result["api_blocked"] = True
        result["error"] = api_error or "体彩 API 暂不可用"
    return result


def _finished_card_from_entry(
    entry: dict[str, Any],
    settlement: dict[str, Any] | None,
    *,
    today: date,
) -> dict[str, Any] | None:
    match_id = str(entry.get("match_id") or entry.get("sporttery_match_id") or "")
    if not match_id:
        return None

    kickoff = entry.get("kickoff_beijing") or ""
    tab_day = _sales_date(entry) or (kickoff[:10] if len(kickoff) >= 10 else "")
    tab_label = _relative_day_label(date.fromisoformat(tab_day), today) if tab_day else "完场"

    card = journal_entry_to_prediction(_prediction_snapshot(entry))
    card.update(
        {
            "match_id": match_id,
            "match_date": tab_day,
            "date_tab_label": tab_label,
            "card_type": "finished",
            "lifecycle_phase": "finished",
            "countdown_label": "已完场",
            "analysis_available": True,
            "track_source": "finished_review",
            "sale_status": "finished",
        }
    )

    if settlement:
        card.update(
            {
                "actual_had": settlement.get("actual_had"),
                "actual_score": settlement.get("actual_score"),
                "had_won": settlement.get("had_won"),
                "crs_won": settlement.get("crs_won"),
                "total_pnl": settlement.get("total_pnl"),
                "settlement_status": "settled",
                "direction_note": (
                    f"预测 {settlement.get('predicted_had')} / {settlement.get('predicted_score')} · "
                    f"实际 {settlement.get('actual_had')} / {settlement.get('actual_score')} · "
                    f"盈亏 {settlement.get('total_pnl')} 元"
                ),
            }
        )
        if not settlement.get("had_won") or not settlement.get("crs_won"):
            card["data_tags"] = ["训练样本", "预测偏差"]
        else:
            card["data_tags"] = ["命中"]
    else:
        card["settlement_status"] = "awaiting_result"
        card["direction_note"] = "已完场，等待体彩官方赛果公布。"

    timing = enrich_match_timing(
        {
            "match_date": kickoff[:10] if len(kickoff) >= 10 else None,
            "match_time": kickoff[11:19] if len(kickoff) >= 19 else None,
        }
    )
    card["lifecycle_phase"] = "finished"
    card["countdown_label"] = "已完场"
    card["hours_until_kickoff"] = timing.get("hours_until_kickoff")
    return card


def list_finished_review_cards(*, lookback_days: int = 3) -> list[dict[str, Any]]:
    """返回近几日已完场比赛卡片（含预测 vs 实际）。"""
    now = datetime.now(BEIJING_TZ)
    today = now.date()
    cutoff = now - timedelta(days=lookback_days)
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()

    for entry in load_journal().get("entries", []):
        match_id = str(entry.get("match_id", ""))
        if not match_id or match_id in seen:
            continue
        kickoff = _entry_kickoff_dt(entry)
        if kickoff is None or kickoff > now or kickoff.date() < cutoff.date():
            continue
        settlement = _verified_settlement(entry)
        card = _finished_card_from_entry(entry, settlement, today=today)
        if card:
            cards.append(card)
            seen.add(match_id)

    for record in load_training_corpus().get("records", []):
        match_id = str(record.get("sporttery_match_id", ""))
        if not match_id or match_id in seen:
            continue
        kickoff = _entry_kickoff_dt(record)
        if kickoff is None or kickoff > now or kickoff.date() < cutoff.date():
            continue
        settlement = _verified_settlement(record)
        card = _finished_card_from_entry(record, settlement, today=today)
        if card:
            cards.append(card)
            seen.add(match_id)

    cards.sort(key=lambda item: item.get("kickoff_beijing") or "", reverse=True)
    return cards
