"""体彩主盘 + 外网辅盘融合预测。"""

from __future__ import annotations

from typing import Any

from .analyzer import OUTCOME_LABELS, analyze_match
from .direction_shift import analyze_direction_shift
from .prediction_journal import get_open_direction_key
from .statistical_model import predict_statistical_match


def _devig(odds: dict[str, float]) -> dict[str, float]:
    raw = {key: 1 / value for key, value in odds.items()}
    total = sum(raw.values())
    return {key: value / total for key, value in raw.items()}


def _return_rate(odds: dict[str, float]) -> float:
    return 1 / sum(1 / value for value in odds.values())


def _pick_from_probs(probabilities: dict[str, float]) -> tuple[str, str, float]:
    ranking = sorted(probabilities, key=probabilities.get, reverse=True)
    gap = probabilities[ranking[0]] - probabilities[ranking[1]]
    return ranking[0], ranking[1], gap


def _confidence(gap: float, aligned: bool) -> str:
    score = gap
    if aligned:
        score += 0.05
    if score >= 0.20:
        return "高"
    if score >= 0.08:
        return "中"
    return "低"


def _latest_trends(history: list[dict[str, Any]]) -> dict[str, str] | None:
    if not history:
        return None
    return history[-1].get("trends")


def _trend_summary(history: list[dict[str, Any]], label: str) -> str | None:
    if len(history) < 2:
        return None
    first = history[0]
    last = history[-1]
    moves: list[str] = []
    for key, name in OUTCOME_LABELS.items():
        old = first.get(key)
        new = last.get(key)
        if old is None or new is None or old == new:
            continue
        direction = "下调" if new < old else "上调"
        moves.append(f"{name}{direction}（{old:.2f}→{new:.2f}）")
    if not moves:
        return f"体彩{label}近期整体持平。"
    return f"体彩{label}走势：" + "；".join(moves) + "。"


def _foreign_alignment(
    sporttery_probs: dict[str, float],
    foreign_probs: dict[str, float],
) -> tuple[bool, str | None]:
    s_pick, _, _ = _pick_from_probs(sporttery_probs)
    f_pick, _, _ = _pick_from_probs(foreign_probs)
    if s_pick == f_pick:
        return True, f"外网博彩公司与体彩首选一致，均倾向{OUTCOME_LABELS[s_pick]}。"

    s_second = sorted(sporttery_probs, key=sporttery_probs.get, reverse=True)[1]
    if f_pick == s_second:
        return False, (
            f"外网首选{OUTCOME_LABELS[f_pick]}，体彩首选{OUTCOME_LABELS[s_pick]}，"
            f"存在分歧但外网次倾向与体彩次选接近。"
        )
    return False, (
        f"外网首选{OUTCOME_LABELS[f_pick]}，体彩首选{OUTCOME_LABELS[s_pick]}，"
        "主方向存在明显分歧，宜降低信心。"
    )


def predict_match(
    sporttery_match: dict[str, Any],
    *,
    sporttery_history: dict[str, Any] | None = None,
    foreign_odds: dict[str, float] | None = None,
    foreign_source: str | None = None,
) -> dict[str, Any]:
    """
    以体彩固定奖金为主，外网欧赔为辅，给出方向判断。

    返回结构可直接用于 API / 仪表盘展示。
    """
    had = sporttery_match["pools"].get("had")
    hhad = sporttery_match["pools"].get("hhad")
    if had is None:
        raise ValueError("体彩比赛缺少胜平负固定奖金")

    had_odds = {"home": had["home"], "draw": had["draw"], "away": had["away"]}
    had_probs = _devig(had_odds)
    pick, second, gap = _pick_from_probs(had_probs)

    hhad_pick = None
    hhad_probs = None
    if hhad and hhad.get("goal_line") is not None:
        hhad_odds = {"home": hhad["home"], "draw": hhad["draw"], "away": hhad["away"]}
        hhad_probs = _devig(hhad_odds)
        hhad_pick, _, _ = _pick_from_probs(hhad_probs)

    aligned = False
    foreign_note = None
    foreign_probs = None
    if foreign_odds:
        foreign_probs = _devig(foreign_odds)
        aligned, foreign_note = _foreign_alignment(had_probs, foreign_probs)

    confidence = _confidence(gap, aligned)
    analysis: list[str] = [
        "预测以中国体育彩票竞彩固定奖金为主口径，外网博彩走势仅作辅助参考。",
        f"体彩胜平负返还率约 {_return_rate(had_odds):.1%}，去水后首选{OUTCOME_LABELS[pick]}（{had_probs[pick]:.1%}）。",
    ]

    if hhad and hhad.get("goal_line") is not None and hhad_probs:
        line = hhad["goal_line"]
        analysis.append(
            f"体彩让球胜平负（主{line:+.0f}）去水后首选{OUTCOME_LABELS[hhad_pick]}，"
            f"反映机构对净胜球结构的定价。"
        )
        if hhad_pick == pick:
            analysis.append("无让球与让球玩法方向一致，市场主线较清晰。")
        else:
            analysis.append(
                f"无让球首选{OUTCOME_LABELS[pick]}，让球玩法首选{OUTCOME_LABELS[hhad_pick]}，"
                "说明存在赢球但难穿盘或平局防线的结构分歧。"
            )

    direction_shift: dict[str, Any] | None = None
    if sporttery_history:
        had_trend = _trend_summary(sporttery_history.get("had_history", []), "胜平负")
        hhad_trend = _trend_summary(sporttery_history.get("hhad_history", []), "让球胜平负")
        if had_trend:
            analysis.append(had_trend)
        if hhad_trend:
            analysis.append(hhad_trend)
        direction_shift = analyze_direction_shift(
            sporttery_history.get("had_history", []),
            current_direction_key=pick,
            foreign_probs=foreign_probs,
            journal_direction_key=get_open_direction_key(sporttery_match["match_id"]),
        )
        if direction_shift.get("available"):
            for alert in direction_shift.get("alerts") or []:
                analysis.append(f"⚠ {alert}")

    if foreign_note:
        analysis.append(foreign_note + (f"（来源：{foreign_source}）" if foreign_source else ""))

    if confidence == "高":
        analysis.append("综合体彩与外辅数据，当前方向一致性较高，但仍仅为市场定价而非赛果保证。")
    elif confidence == "中":
        analysis.append("方向有一定依据，建议关注临场固定奖金变动与外网是否转向。")
    else:
        analysis.append("分歧较大或优势不明显，更适合观察走势而非强方向判断。")

    analysis.append("输出仅供数据分析演示，不构成投注建议。")
    statistical_model = predict_statistical_match(
        sporttery_match["home"],
        sporttery_match["away"],
        neutral=bool(sporttery_match.get("neutral", True)),
    )

    return {
        "match_id": sporttery_match["match_id"],
        "home": sporttery_match["home"],
        "away": sporttery_match["away"],
        "league": sporttery_match.get("league"),
        "kickoff": sporttery_match.get("kickoff"),
        "direction": OUTCOME_LABELS[pick],
        "direction_key": pick,
        "second_direction": OUTCOME_LABELS[second],
        "confidence": confidence,
        "return_rate": _return_rate(had_odds),
        "probabilities": had_probs,
        "hhad": {
            "goal_line": hhad.get("goal_line") if hhad else None,
            "probabilities": hhad_probs,
            "direction_key": hhad_pick,
            "direction": OUTCOME_LABELS[hhad_pick] if hhad_pick else None,
        },
        "foreign": {
            "source": foreign_source,
            "probabilities": foreign_probs,
        },
        "analysis": analysis,
        "direction_shift": direction_shift,
        "statistical_model": statistical_model,
        "sporttery": sporttery_match,
    }
