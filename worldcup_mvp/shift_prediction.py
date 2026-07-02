"""变盘后预测：在告警为主的前提下，对比初盘/首次与当前 SP 策略。"""

from __future__ import annotations

from typing import Any

from .analyzer import OUTCOME_LABELS


def outcome_key(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home"
    if home_goals < away_goals:
        return "away"
    return "draw"


def best_score_for_direction(
    crs_top: list[tuple[int, int, float]],
    direction_key: str,
) -> str | None:
    """从猜比分候选中选取与方向一致且 SP 最低的一档。"""
    matching = [
        (home, away, odds)
        for home, away, odds in crs_top
        if outcome_key(home, away) == direction_key
    ]
    if not matching:
        return None
    home, away, _ = min(matching, key=lambda item: item[2])
    return f"{home}-{away}"


def build_shift_prediction(
    direction_shift: dict[str, Any],
    crs_top: list[tuple[int, int, float]],
    *,
    journal_entry: dict[str, Any] | None = None,
    current_direction_key: str,
    current_predicted_score: str,
) -> dict[str, Any]:
    """
    当 SP 出现明显变动时，给出「初盘/首次预测」与「变盘后预测」对照。
    告警仍由 direction_shift 负责；此处仅补充可随盘调整的策略参考。
    """
    inactive: dict[str, Any] = {"active": False}
    if not direction_shift.get("available"):
        return inactive

    movement_active = bool(
        direction_shift.get("direction_flipped")
        or direction_shift.get("recent_flipped")
        or direction_shift.get("severity") in {"high", "medium", "low"}
        or direction_shift.get("alerts")
        or direction_shift.get("movement_lines")
    )
    if not movement_active:
        return inactive

    opening_key = direction_shift.get("opening_pick")
    current_key = direction_shift.get("current_pick") or current_direction_key

    if journal_entry and journal_entry.get("initial_direction_key"):
        initial_key = str(journal_entry["initial_direction_key"])
        initial_direction = journal_entry.get("initial_direction") or OUTCOME_LABELS.get(initial_key, "—")
        initial_score = journal_entry.get("initial_predicted_score") or "—"
        initial_label = "首次记录预测"
    elif opening_key:
        initial_key = str(opening_key)
        initial_direction = direction_shift.get("opening_label") or OUTCOME_LABELS.get(initial_key, "—")
        initial_score = best_score_for_direction(crs_top, initial_key) or "—"
        initial_label = "初盘 SP 预测"
    else:
        initial_key = current_direction_key
        initial_direction = OUTCOME_LABELS.get(initial_key, "—")
        initial_score = current_predicted_score
        initial_label = "当前预测"

    adjusted_key = str(current_key)
    adjusted_direction = direction_shift.get("current_label") or OUTCOME_LABELS.get(adjusted_key, "—")
    adjusted_score = best_score_for_direction(crs_top, adjusted_key) or current_predicted_score

    changed = initial_key != adjusted_key or initial_score != adjusted_score
    if not changed and direction_shift.get("severity") == "none":
        return inactive

    return {
        "active": True,
        "changed": changed,
        "initial": {
            "label": initial_label,
            "direction": initial_direction,
            "direction_key": initial_key,
            "predicted_score": initial_score,
        },
        "adjusted": {
            "label": "变盘后预测",
            "direction": adjusted_direction,
            "direction_key": adjusted_key,
            "predicted_score": adjusted_score,
        },
        "note": "告警为主；变盘后方向与比分供临场策略参考，系统不会自动换单。",
    }
