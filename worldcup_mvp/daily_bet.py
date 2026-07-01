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
    selected = select_stable_pick(predictions, business_date=day)
    if selected is None:
        return None

    pick = str(selected["direction_key"])
    odds = float(selected["had_odds"][pick])
    entry = {
        "date": day,
        "recorded_at": datetime.now(BEIJING_TZ).replace(microsecond=0).isoformat(),
        "match_id": str(selected.get("match_id")),
        "match_num": selected.get("match_num"),
        "home": selected.get("home"),
        "away": selected.get("away"),
        "kickoff_beijing": selected.get("kickoff_beijing"),
        "market": "胜平负单关",
        "pick": selected.get("direction"),
        "pick_key": pick,
        "odds": round(odds, 2),
        "market_probability": round(float(selected["market_probability"]), 4),
        "stake": round(float(stake), 2),
        "potential_return": round(float(stake) * odds, 2),
        "potential_profit": round(float(stake) * (odds - 1), 2),
        "status": "open",
        "selection_rule": "当日高信心且内外盘同向场次中，选择胜平负去水概率最高方向",
        "disclaimer": "仅为模拟记账，不会代替用户真实下注，也不构成投注建议。",
    }
    ledger = load_daily_bets(path)
    entries = [item for item in ledger.get("entries", []) if item.get("date") != day]
    entries.append(entry)
    entries.sort(key=lambda item: item.get("date", ""), reverse=True)
    ledger.update({"version": 1, "daily_stake": round(float(stake), 2), "entries": entries})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(ledger, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return entry
