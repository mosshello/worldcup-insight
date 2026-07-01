"""每日只选一个高概率方向的模拟投注账本。"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DAILY_BETS_FILE = PROJECT_ROOT / "data" / "daily_bets.json"
DEFAULT_DAILY_STAKE = 1000.0
SINGLE_STAKE_RATIO = 0.6


def _devig_probability(odds: dict[str, Any], pick: str) -> float:
    inverse = {key: 1.0 / float(value) for key, value in odds.items() if float(value) > 0}
    total = sum(inverse.values())
    return inverse.get(pick, 0.0) / total if total else 0.0


def select_stable_pick(
    predictions: list[dict[str, Any]], *, business_date: str
) -> dict[str, Any] | None:
    """从指定销售日中选择市场去水概率最高且双源同向的胜平负方向。"""
    candidates: list[tuple[float, str, dict[str, Any]]] = []
    for prediction in predictions:
        if prediction.get("business_date") != business_date:
            continue
        pick = str(prediction.get("direction_key") or "")
        odds = prediction.get("had_odds") or {}
        if pick not in odds or prediction.get("confidence") != "高":
            continue
        if prediction.get("aligned_with_fox") is False:
            continue
        probability = _devig_probability(odds, pick)
        candidates.append((probability, str(prediction.get("kickoff_beijing") or ""), prediction))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    selected = dict(candidates[0][2])
    selected["market_probability"] = candidates[0][0]
    return selected


def select_stable_picks(
    predictions: list[dict[str, Any]], *, business_date: str, limit: int = 2
) -> list[dict[str, Any]]:
    """返回指定销售日按市场去水概率排序的稳定方向。"""
    ranked: list[dict[str, Any]] = []
    remaining = list(predictions)
    while remaining and len(ranked) < limit:
        selected = select_stable_pick(remaining, business_date=business_date)
        if selected is None:
            break
        ranked.append(selected)
        selected_id = str(selected.get("match_id"))
        remaining = [item for item in remaining if str(item.get("match_id")) != selected_id]
    return ranked


def load_daily_bets(path: Path = DAILY_BETS_FILE) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "daily_stake": DEFAULT_DAILY_STAKE, "entries": []}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def record_daily_bet(
    predictions: list[dict[str, Any]],
    *,
    stake: float = DEFAULT_DAILY_STAKE,
    today: date | None = None,
    path: Path = DAILY_BETS_FILE,
) -> dict[str, Any] | None:
    """按北京日期幂等写入当日一笔模拟投注。"""
    local_today = today or datetime.now(BEIJING_TZ).date()
    day = local_today.isoformat()
    selected = select_stable_picks(predictions, business_date=day, limit=2)
    if not selected:
        return None

    primary = selected[0]
    pick = str(primary["direction_key"])
    odds = float(primary["had_odds"][pick])
    has_parlay = len(selected) >= 2
    single_stake = float(stake) * SINGLE_STAKE_RATIO if has_parlay else float(stake)
    parlay_stake = float(stake) - single_stake if has_parlay else 0.0
    legs: list[dict[str, Any]] = []
    for item in selected[:2]:
        item_pick = str(item["direction_key"])
        legs.append(
            {
                "match_id": str(item.get("match_id")),
                "match_num": item.get("match_num"),
                "home": item.get("home"),
                "away": item.get("away"),
                "kickoff_beijing": item.get("kickoff_beijing"),
                "pick": item.get("direction"),
                "pick_key": item_pick,
                "odds": round(float(item["had_odds"][item_pick]), 2),
                "market_probability": round(float(item["market_probability"]), 4),
            }
        )
    combined_odds = 1.0
    combined_probability = 1.0
    for leg in legs:
        combined_odds *= float(leg["odds"])
        combined_probability *= float(leg["market_probability"])
    entry = {
        "date": day,
        "recorded_at": datetime.now(BEIJING_TZ).replace(microsecond=0).isoformat(),
        "total_stake": round(float(stake), 2),
        "match_id": str(primary.get("match_id")),
        "match_num": primary.get("match_num"),
        "home": primary.get("home"),
        "away": primary.get("away"),
        "kickoff_beijing": primary.get("kickoff_beijing"),
        "market": "胜平负单关 + 二串一",
        "pick": primary.get("direction"),
        "pick_key": pick,
        "odds": round(odds, 2),
        "market_probability": round(float(primary["market_probability"]), 4),
        "stake": round(single_stake, 2),
        "potential_return": round(single_stake * odds, 2),
        "potential_profit": round(single_stake * (odds - 1), 2),
        "single": {
            "stake": round(single_stake, 2),
            "leg": legs[0],
            "potential_return": round(single_stake * odds, 2),
            "potential_profit": round(single_stake * (odds - 1), 2),
        },
        "parlay": (
            {
                "type": "2串1",
                "stake": round(parlay_stake, 2),
                "legs": legs,
                "combined_odds": round(combined_odds, 4),
                "combined_probability": round(combined_probability, 4),
                "potential_return": round(parlay_stake * combined_odds, 2),
                "potential_profit": round(parlay_stake * (combined_odds - 1), 2),
                "status": "open",
            }
            if has_parlay
            else None
        ),
        "status": "open",
        "selection_rule": "单场取当日稳定方向第一名；二串一取前两名；每日自动刷新且同日不重复追加",
        "disclaimer": "仅为模拟记账，不会代替用户真实下注，也不构成投注建议。",
    }
    ledger = load_daily_bets(path)
    entries = [item for item in ledger.get("entries", []) if item.get("date") != day]
    entries.append(entry)
    entries.sort(key=lambda item: item.get("date", ""), reverse=True)
    ledger.update({"version": 2, "daily_stake": round(float(stake), 2), "entries": entries})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(ledger, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return entry
