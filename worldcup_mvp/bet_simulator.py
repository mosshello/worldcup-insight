"""假设金额投注盈亏模拟（参考竞彩单关规则，仅供数据分析演示）。"""

from __future__ import annotations

from typing import Any

from .sporttery_api import HAD_RESULT_KEYS, HAD_RESULT_LABELS

JC_UNIT_STAKE = 2.0


def calc_single_payout(stake: float, odds: float) -> dict[str, float]:
    """单关潜在回报：中奖返还 = 投注额 × 固定奖金。"""
    return {
        "stake": round(stake, 2),
        "odds": round(odds, 2),
        "return_if_win": round(stake * odds, 2),
        "profit_if_win": round(stake * (odds - 1), 2),
        "loss_if_lose": round(-stake, 2),
    }


def simulate_prediction_bet(
    prediction: dict[str, Any],
    *,
    stake_had: float = 100.0,
    stake_crs: float = 50.0,
) -> dict[str, Any]:
    """基于预测方向与比分，模拟胜平负 / 猜比分两笔假设投注。"""
    had_odds = prediction.get("had_odds") or {}
    direction_key = prediction.get("direction_key")
    direction_odds = had_odds.get(direction_key)
    had_sim = None
    if direction_odds is not None:
        had_sim = calc_single_payout(stake_had, float(direction_odds))
        had_sim["pick"] = prediction.get("direction")
        had_sim["units"] = max(1, int(stake_had / JC_UNIT_STAKE))

    crs_odds = prediction.get("crs_odds")
    crs_sim = None
    if crs_odds is not None:
        crs_sim = calc_single_payout(stake_crs, float(crs_odds))
        crs_sim["pick"] = prediction.get("predicted_score")
        crs_sim["units"] = max(1, int(stake_crs / JC_UNIT_STAKE))

    total_stake = stake_had + stake_crs
    best_profit = 0.0
    if had_sim:
        best_profit = max(best_profit, had_sim["profit_if_win"])
    if crs_sim:
        best_profit = max(best_profit, crs_sim["profit_if_win"])

    return {
        "match_id": prediction.get("match_id"),
        "home": prediction.get("home"),
        "away": prediction.get("away"),
        "total_stake": round(total_stake, 2),
        "best_case_profit": round(best_profit, 2),
        "worst_case_loss": round(-total_stake, 2),
        "had": had_sim,
        "crs": crs_sim,
        "disclaimer": "假设投注模拟，非真实购彩建议",
    }


def settle_against_results(
    prediction: dict[str, Any],
    results: dict[str, dict[str, Any]],
    *,
    stake_had: float = 100.0,
    stake_crs: float = 50.0,
) -> dict[str, Any]:
    """将预测与体彩官方赛果结算对比，计算实际盈亏。"""
    had_result = results.get("had")
    crs_result = results.get("crs")
    if not had_result and not crs_result:
        return {
            "match_id": prediction.get("match_id"),
            "status": "pending",
            "message": "赛果尚未公布",
        }

    actual_key = HAD_RESULT_KEYS.get(str(had_result.get("combination", "")).upper()) if had_result else None
    actual_label = HAD_RESULT_LABELS.get(str(had_result.get("combination", "")).upper()) if had_result else None
    predicted_key = prediction.get("direction_key")
    predicted_score = str(prediction.get("predicted_score", "")).replace("-", ":")
    actual_score = str(crs_result.get("combination", "")) if crs_result else None

    had_won = actual_key == predicted_key if actual_key and predicted_key else False
    crs_won = actual_score == predicted_score if actual_score and predicted_score != "—" else False

    had_pnl = 0.0
    if had_result and predicted_key:
        settlement_odds = float(had_result.get("odds") or 0)
        had_pnl = stake_had * (settlement_odds - 1) if had_won else -stake_had

    crs_pnl = 0.0
    if crs_result and predicted_score != "—":
        settlement_odds = float(crs_result.get("odds") or 0)
        crs_pnl = stake_crs * (settlement_odds - 1) if crs_won else -stake_crs

    total_pnl = had_pnl + crs_pnl
    return {
        "match_id": prediction.get("match_id"),
        "home": prediction.get("home"),
        "away": prediction.get("away"),
        "status": "settled",
        "actual_had": actual_label,
        "actual_score": actual_score,
        "predicted_had": prediction.get("direction"),
        "predicted_score": prediction.get("predicted_score"),
        "had_won": had_won,
        "crs_won": crs_won,
        "stake_had": stake_had,
        "stake_crs": stake_crs,
        "had_pnl": round(had_pnl, 2),
        "crs_pnl": round(crs_pnl, 2),
        "total_pnl": round(total_pnl, 2),
    }
