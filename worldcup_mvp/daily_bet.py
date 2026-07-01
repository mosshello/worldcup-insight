"""每日只选一个高概率方向的模拟投注账本。"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path
from typing import Any
BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DAILY_BETS_FILE = PROJECT_ROOT / "data" / "daily_bets.json"
DEFAULT_DAILY_STAKE = 1000.0
SINGLE_STAKE_RATIO = 0.6
MIN_PARLAY_ODDS = 2.0


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


def select_stable_parlay(
    predictions: list[dict[str, Any]], *, business_date: str, min_odds: float = MIN_PARLAY_ODDS
) -> list[dict[str, Any]]:
    """选择联合概率最高且组合 SP 达到下限的二串一。"""
    candidates = select_stable_picks(
        predictions, business_date=business_date, limit=len(predictions)
    )
    pairs: list[tuple[float, float, list[dict[str, Any]]]] = []
    for first, second in combinations(candidates, 2):
        first_pick = str(first["direction_key"])
        second_pick = str(second["direction_key"])
        combined_odds = float(first["had_odds"][first_pick]) * float(
            second["had_odds"][second_pick]
        )
        if combined_odds < min_odds:
            continue
        joint_probability = float(first["market_probability"]) * float(
            second["market_probability"]
        )
        pairs.append((joint_probability, combined_odds, [first, second]))
    if not pairs:
        return []
    pairs.sort(key=lambda item: (-item[0], item[1]))
    return pairs[0][2]


def summarize_ledger(payload: dict[str, Any]) -> dict[str, float | int]:
    """汇总模拟账本投入、已实现盈利和未结算潜在盈利。"""
    entries = payload.get("entries") or []
    total_invested = sum(float(item.get("total_stake", item.get("stake", 0))) for item in entries)
    settled = [item for item in entries if item.get("status") == "settled"]
    open_entries = [item for item in entries if item.get("status") != "settled"]
    realized_profit = sum(float(item.get("realized_pnl") or 0) for item in settled)
    open_potential_profit = sum(
        float((item.get("single") or {}).get("potential_profit", item.get("potential_profit", 0)))
        + float((item.get("parlay") or {}).get("potential_profit", 0))
        for item in open_entries
    )
    return {
        "entry_count": len(entries),
        "settled_count": len(settled),
        "open_count": len(open_entries),
        "total_invested": round(total_invested, 2),
        "realized_profit": round(realized_profit, 2),
        "open_potential_profit": round(open_potential_profit, 2),
    }


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
    ranked = select_stable_picks(predictions, business_date=day, limit=len(predictions))
    if not ranked:
        return None

    primary = ranked[0]
    parlay_selected = select_stable_parlay(predictions, business_date=day)
    pick = str(primary["direction_key"])
    odds = float(primary["had_odds"][pick])
    has_parlay = len(parlay_selected) == 2
    single_stake = float(stake) * SINGLE_STAKE_RATIO if has_parlay else float(stake)
    parlay_stake = float(stake) - single_stake if has_parlay else 0.0
    def as_leg(item: dict[str, Any]) -> dict[str, Any]:
        item_pick = str(item["direction_key"])
        return {
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

    single_leg = as_leg(primary)
    legs = [as_leg(item) for item in parlay_selected]
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
            "leg": single_leg,
            "potential_return": round(single_stake * odds, 2),
            "potential_profit": round(single_stake * (odds - 1), 2),
        },
        "parlay": (
            {
                "type": "2串1",
                "stake": round(parlay_stake, 2),
                "legs": legs,
                "combined_odds": round(combined_odds, 4),
                "minimum_odds": MIN_PARLAY_ODDS,
                "combined_probability": round(combined_probability, 4),
                "potential_return": round(parlay_stake * combined_odds, 2),
                "potential_profit": round(parlay_stake * (combined_odds - 1), 2),
                "status": "open",
            }
            if has_parlay
            else None
        ),
        "status": "open",
        "realized_pnl": None,
        "selection_rule": "单场取稳定方向第一名；二串一须组合SP≥2.00，并取联合去水概率最高组合",
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
