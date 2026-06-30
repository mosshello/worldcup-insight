"""体彩 SP 走势驱动的方向转向与冷门提醒。"""

from __future__ import annotations

from typing import Any

from .analyzer import OUTCOME_LABELS
from .pool_analytics import devig_probabilities

HEAT_PCT_THRESHOLD = 0.03
PROB_GAP_THRESHOLD = 0.04


def _pool_odds(point: dict[str, Any]) -> dict[str, float] | None:
    try:
        return {
            "home": float(point["home"]),
            "draw": float(point["draw"]),
            "away": float(point["away"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _pick_key(probabilities: dict[str, float]) -> str:
    return max(probabilities, key=probabilities.get)


def _pct_change(old: float, new: float) -> float:
    if old == 0:
        return 0.0
    return (new - old) / old


def _movement_label(key: str, old: float, new: float) -> str | None:
    pct = _pct_change(old, new)
    if abs(pct) < HEAT_PCT_THRESHOLD:
        return None
    name = OUTCOME_LABELS[key]
    if new < old:
        return f"{name} SP 下调 {abs(pct):.1%}（受热）"
    return f"{name} SP 上调 {abs(pct):.1%}（受冷）"


def analyze_direction_shift(
    had_history: list[dict[str, Any]] | None,
    *,
    current_direction_key: str | None = None,
    foreign_probs: dict[str, float] | None = None,
    journal_direction_key: str | None = None,
) -> dict[str, Any]:
    """
    基于体彩 had 历史检测方向是否转向，并生成冷门提醒。

    - opening：最早一条 SP 去水首选
    - current：最新一条 SP 去水首选
    - previous：倒数第二条（若有）
    """
    empty: dict[str, Any] = {
        "available": False,
        "direction_flipped": False,
        "recent_flipped": False,
        "severity": "none",
        "opening_pick": None,
        "previous_pick": None,
        "current_pick": None,
        "opening_label": None,
        "previous_label": None,
        "current_label": None,
        "alerts": [],
        "summary_bullets": [],
        "movement_lines": [],
        "upset_candidates": [],
    }
    if not had_history or len(had_history) < 2:
        return empty

    points: list[dict[str, Any]] = []
    for item in had_history:
        odds = _pool_odds(item)
        if odds is None:
            continue
        probs = devig_probabilities(odds)
        points.append(
            {
                "recorded_at": item.get("recorded_at"),
                "odds": odds,
                "probs": probs,
                "pick": _pick_key(probs),
            }
        )
    if len(points) < 2:
        return empty

    opening = points[0]
    current = points[-1]
    previous = points[-2] if len(points) >= 2 else opening

    opening_pick = opening["pick"]
    previous_pick = previous["pick"]
    current_pick = current_direction_key or current["pick"]

    direction_flipped = opening_pick != current_pick
    recent_flipped = previous_pick != current_pick and len(points) >= 2

    movement_lines: list[str] = []
    for key in ("home", "draw", "away"):
        line = _movement_label(key, opening["odds"][key], current["odds"][key])
        if line:
            movement_lines.append(line)

    upset_candidates: list[str] = []
    opening_favorite = opening_pick
    for key in ("home", "draw", "away"):
        if key == opening_favorite:
            continue
        old, new = opening["odds"][key], current["odds"][key]
        if new < old and _pct_change(old, new) <= -HEAT_PCT_THRESHOLD:
            upset_candidates.append(key)

    favorite_weakened = _pct_change(opening["odds"][opening_favorite], current["odds"][opening_favorite]) >= HEAT_PCT_THRESHOLD

    alerts: list[str] = []
    summary: list[str] = []

    if direction_flipped:
        alerts.append(
            f"方向转向：初盘倾向{OUTCOME_LABELS[opening_pick]}，当前去水首选{OUTCOME_LABELS[current_pick]}。"
        )
    if recent_flipped and not direction_flipped:
        alerts.append(
            f"近期转向：上一节点倾向{OUTCOME_LABELS[previous_pick]}，最新已变为{OUTCOME_LABELS[current_pick]}。"
        )
    if upset_candidates:
        labels = "、".join(OUTCOME_LABELS[key] for key in upset_candidates)
        alerts.append(f"冷门受热：{labels} SP 持续下调，资金向该方向聚集。")
    if favorite_weakened and current_pick != opening_favorite:
        alerts.append(
            f"热门退潮：初盘热门{OUTCOME_LABELS[opening_favorite]} SP 上调，市场信心减弱。"
        )
    if journal_direction_key and journal_direction_key != current_pick:
        alerts.append(
            f"与首次记录方向不一致：日志为{OUTCOME_LABELS[journal_direction_key]}，"
            f"当前 SP 指向{OUTCOME_LABELS[current_pick]}。"
        )
    if foreign_probs:
        foreign_pick = _pick_key(foreign_probs)
        if foreign_pick != current_pick and foreign_pick in upset_candidates:
            alerts.append(
                f"外网同步指向{OUTCOME_LABELS[foreign_pick]}，与体彩当前转向方向一致，冷门信号增强。"
            )
        elif foreign_pick != current_pick and direction_flipped:
            alerts.append(
                f"外网仍倾向{OUTCOME_LABELS[foreign_pick]}，与体彩最新首选存在分歧，宜谨慎。"
            )

    gap_now = current["probs"][current_pick] - sorted(current["probs"].values(), reverse=True)[1]
    if direction_flipped and gap_now < PROB_GAP_THRESHOLD:
        alerts.append("转向后三项概率胶着，方向并不稳固，更像震荡而非单边。")

    severity = "none"
    if direction_flipped or (recent_flipped and upset_candidates):
        severity = "high"
    elif recent_flipped or upset_candidates or favorite_weakened:
        severity = "medium"
    elif movement_lines:
        severity = "low"

    if movement_lines:
        summary.append("SP 变动：" + "；".join(movement_lines) + "。")
    summary.extend(alerts)
    if not summary:
        summary.append(
            f"SP 走势未触发转向：初盘与现盘均倾向{OUTCOME_LABELS[current_pick]}。"
        )

    return {
        "available": True,
        "direction_flipped": direction_flipped,
        "recent_flipped": recent_flipped,
        "severity": severity,
        "opening_pick": opening_pick,
        "previous_pick": previous_pick,
        "current_pick": current_pick,
        "opening_label": OUTCOME_LABELS[opening_pick],
        "previous_label": OUTCOME_LABELS[previous_pick],
        "current_label": OUTCOME_LABELS[current_pick],
        "favorite_weakened": favorite_weakened,
        "upset_candidates": [OUTCOME_LABELS[key] for key in upset_candidates],
        "movement_lines": movement_lines,
        "alerts": alerts,
        "summary_bullets": summary,
        "history_points": len(points),
        "from_snapshot": opening.get("recorded_at"),
        "to_snapshot": current.get("recorded_at"),
    }
