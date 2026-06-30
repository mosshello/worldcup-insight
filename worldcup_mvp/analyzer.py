"""根据赔率与赛前上下文生成可解释的世界杯胜平负分析。"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any


OUTCOME_LABELS = {"home": "主胜", "draw": "平", "away": "客胜"}
ABSENCE_FACTORS = {"out": 1.0, "suspended": 1.0, "doubtful": 0.5, "available": 0.0}
CONTEXT_WEIGHTS = {
    "points_per_game": 0.35,
    "goal_difference_per_game": 0.30,
    "goals_for_per_game": 0.20,
    "availability": 0.15,
}
CONTEXT_LOG_SCALE = 0.35


def _clamp(value: float, lower: float = -1.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _validate_odds(odds: dict[str, Any]) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for outcome in OUTCOME_LABELS:
        value = odds.get(outcome)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{OUTCOME_LABELS[outcome]}赔率必须是数字")
        if value <= 1:
            raise ValueError(f"{OUTCOME_LABELS[outcome]}赔率必须大于 1.00")
        normalized[outcome] = float(value)
    return normalized


def _confidence(probabilities: dict[str, float]) -> str:
    ranked = sorted(probabilities.values(), reverse=True)
    gap = ranked[0] - ranked[1]
    if gap >= 0.20:
        return "高"
    if gap >= 0.08:
        return "中"
    return "低"


def _number(value: Any, field: str, *, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        raise ValueError(f"{field}必须是大于等于 {minimum:g} 的数字")
    return float(value)


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    parsed = _number(value, field, minimum=minimum)
    if not parsed.is_integer():
        raise ValueError(f"{field}必须是整数")
    return int(parsed)


def _validate_team_context(team: dict[str, Any], side: str) -> dict[str, Any]:
    stats = team.get("group_stats")
    if not isinstance(stats, dict):
        raise ValueError(f"{side}.group_stats 必须是对象")
    played = _integer(stats.get("played"), f"{side}.group_stats.played", minimum=1)
    normalized_stats = {
        "played": played,
        "points": _number(stats.get("points"), f"{side}.group_stats.points"),
        "goals_for": _number(stats.get("goals_for"), f"{side}.group_stats.goals_for"),
        "goals_against": _number(
            stats.get("goals_against"), f"{side}.group_stats.goals_against"
        ),
        "finish": stats.get("finish"),
    }

    absences: list[dict[str, Any]] = []
    for index, absence in enumerate(team.get("absences", [])):
        if not isinstance(absence, dict):
            raise ValueError(f"{side}.absences[{index}] 必须是对象")
        status = absence.get("status")
        if status not in ABSENCE_FACTORS:
            raise ValueError(f"{side}.absences[{index}].status 无效")
        impact = _number(absence.get("impact", 0.5), f"{side}.absences[{index}].impact")
        if impact > 1:
            raise ValueError(f"{side}.absences[{index}].impact 必须小于等于 1")
        absences.append(
            {
                "player": absence.get("player", "未知球员"),
                "status": status,
                "impact": impact,
                "note": absence.get("note"),
            }
        )

    scorers: list[dict[str, Any]] = []
    for index, scorer in enumerate(team.get("scorers", [])):
        if not isinstance(scorer, dict) or not scorer.get("player"):
            raise ValueError(f"{side}.scorers[{index}] 缺少球员名称")
        item = {"player": scorer["player"], "note": scorer.get("note")}
        if scorer.get("goals") is not None:
            item["goals"] = _integer(scorer["goals"], f"{side}.scorers[{index}].goals")
        scorers.append(item)

    burden = sum(item["impact"] * ABSENCE_FACTORS[item["status"]] for item in absences)
    return {
        "group_stats": normalized_stats,
        "absences": absences,
        "scorers": scorers,
        "availability_burden": burden,
    }


def _context_model(
    match: dict[str, Any], market_probabilities: dict[str, float]
) -> dict[str, Any]:
    context = match.get("team_context")
    if context is None:
        return {
            "available": False,
            "probabilities": dict(market_probabilities),
            "edge": 0.0,
            "signals": {},
            "teams": None,
        }
    if not isinstance(context, dict) or not isinstance(context.get("home"), dict) or not isinstance(
        context.get("away"), dict
    ):
        raise ValueError("team_context 必须包含 home 和 away 对象")

    home = _validate_team_context(context["home"], "team_context.home")
    away = _validate_team_context(context["away"], "team_context.away")
    hs, aws = home["group_stats"], away["group_stats"]

    raw_signals = {
        "points_per_game": _clamp(
            ((hs["points"] / hs["played"]) - (aws["points"] / aws["played"])) / 1.5
        ),
        "goal_difference_per_game": _clamp(
            (
                ((hs["goals_for"] - hs["goals_against"]) / hs["played"])
                - ((aws["goals_for"] - aws["goals_against"]) / aws["played"])
            )
            / 2.0
        ),
        "goals_for_per_game": _clamp(
            ((hs["goals_for"] / hs["played"]) - (aws["goals_for"] / aws["played"]))
            / 2.5
        ),
        "availability": _clamp(
            (away["availability_burden"] - home["availability_burden"]) / 2.0
        ),
    }
    signals = {
        name: {
            "value": value,
            "weight": CONTEXT_WEIGHTS[name],
            "contribution": value * CONTEXT_WEIGHTS[name],
        }
        for name, value in raw_signals.items()
    }
    edge = sum(item["contribution"] for item in signals.values())
    multiplier = math.exp(edge * CONTEXT_LOG_SCALE)
    adjusted_raw = {
        "home": market_probabilities["home"] * multiplier,
        "draw": market_probabilities["draw"],
        "away": market_probabilities["away"] / multiplier,
    }
    total = sum(adjusted_raw.values())
    adjusted = {outcome: value / total for outcome, value in adjusted_raw.items()}
    return {
        "available": True,
        "probabilities": adjusted,
        "edge": edge,
        "signals": signals,
        "teams": {"home": home, "away": away},
    }


def _format_absences(team: dict[str, Any]) -> str:
    active = [item for item in team["absences"] if item["status"] != "available"]
    if not active:
        return "当前结构化数据未记录伤停或停赛"
    labels = {"out": "缺阵", "suspended": "停赛", "doubtful": "出场存疑"}
    return "、".join(f"{item['player']}（{labels[item['status']]}）" for item in active)


def _format_scorers(team: dict[str, Any]) -> str:
    if not team["scorers"]:
        return "未录入球员级主要进球点"
    parts = []
    for item in team["scorers"]:
        goals = f"{item['goals']}球" if "goals" in item else "主要进攻点"
        parts.append(f"{item['player']}（{goals}）")
    return "、".join(parts)


def _build_analysis(
    match: dict[str, Any],
    market_probabilities: dict[str, float],
    ranking: list[str],
    context: dict[str, Any],
) -> list[str]:
    favorite = ranking[0]
    lines: list[str] = []
    rules = match.get("tournament_rules", {})
    if match.get("stage") == "淘汰赛":
        suffix = "；90分钟战平后将进入加时，仍平则点球决胜" if rules.get("extra_time_if_draw") else ""
        lines.append(f"规则：预测只覆盖常规90分钟（含伤停补时）{suffix}。")

    lines.append(
        f"市场：第一倾向为{OUTCOME_LABELS[favorite]}，去水后概率约 "
        f"{market_probabilities[favorite]:.1%}，平局为 {market_probabilities['draw']:.1%}。"
    )
    if not context["available"]:
        lines.append("上下文：未提供球队与人员数据，综合概率退化为市场概率。")
        return lines

    home, away = context["teams"]["home"], context["teams"]["away"]
    hs, aws = home["group_stats"], away["group_stats"]
    lines.append(
        "状态："
        f"{match['home']}小组赛 {hs['points']:.0f} 分、{hs['goals_for']:.0f}:{hs['goals_against']:.0f}；"
        f"{match['away']} {aws['points']:.0f} 分、{aws['goals_for']:.0f}:{aws['goals_against']:.0f}。"
    )
    lines.append(
        f"人员：{match['home']}—{_format_absences(home)}；"
        f"{match['away']}—{_format_absences(away)}。"
    )
    lines.append(
        f"进球点：{match['home']}—{_format_scorers(home)}；"
        f"{match['away']}—{_format_scorers(away)}。"
    )
    direction = "主队" if context["edge"] > 0.03 else "客队" if context["edge"] < -0.03 else "双方接近"
    adjusted = context["probabilities"]
    lines.append(
        f"综合：四项赛前信号总体偏向{direction}（边际 {context['edge']:+.3f}）；"
        f"启发式概率为主胜 {adjusted['home']:.1%}、平 {adjusted['draw']:.1%}、客胜 {adjusted['away']:.1%}。"
    )
    lines.append("限制：综合概率采用固定权重、未经历史样本校准，不等同于训练模型。")
    return lines


def analyze_match(match: dict[str, Any]) -> dict[str, Any]:
    """分析单场比赛；赛果字段即使存在也不会参与计算。"""
    for field in ("home", "away", "odds"):
        if field not in match:
            raise ValueError(f"比赛缺少字段：{field}")
    odds = _validate_odds(match["odds"])
    raw_probabilities = {outcome: 1 / price for outcome, price in odds.items()}
    overround = sum(raw_probabilities.values())
    probabilities = {
        outcome: probability / overround for outcome, probability in raw_probabilities.items()
    }
    ranking = sorted(probabilities, key=probabilities.get, reverse=True)
    context = _context_model(match, probabilities)
    context_probabilities = context["probabilities"]
    context_ranking = sorted(context_probabilities, key=context_probabilities.get, reverse=True)
    sporttery = match.get("sporttery")
    sporttery_probabilities = None
    if sporttery is not None:
        if not isinstance(sporttery, dict) or not isinstance(sporttery.get("had"), dict):
            raise ValueError("sporttery.had 必须是对象")
        sporttery_odds = _validate_odds(sporttery["had"].get("odds", {}))
        sporttery_raw = {outcome: 1 / price for outcome, price in sporttery_odds.items()}
        sporttery_overround = sum(sporttery_raw.values())
        sporttery_probabilities = {
            outcome: value / sporttery_overround for outcome, value in sporttery_raw.items()
        }

    return {
        "id": match.get("id"),
        "competition": match.get("competition"),
        "stage": match.get("stage"),
        "fixture_date": match.get("fixture_date"),
        "kickoff_beijing": match.get("kickoff_beijing"),
        "home": match["home"],
        "away": match["away"],
        "odds": odds,
        "sporttery": sporttery,
        "sporttery_probabilities": sporttery_probabilities,
        "probabilities": probabilities,
        "market_probabilities": probabilities,
        "overround": overround - 1,
        "ranking": ranking,
        "pick": ranking[0],
        "second_pick": ranking[1],
        "confidence": _confidence(probabilities),
        "context_available": context["available"],
        "context_probabilities": context_probabilities,
        "context_ranking": context_ranking,
        "context_pick": context_ranking[0],
        "context_confidence": _confidence(context_probabilities),
        "context_edge": context["edge"],
        "context_signals": context["signals"],
        "analysis": _build_analysis(match, probabilities, ranking, context),
    }


def _parse_aware_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是带时区的 ISO 8601 时间")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} 不是有效的 ISO 8601 时间") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} 必须包含时区")
    return parsed


def backtest_match(match: dict[str, Any], data_as_of: str) -> dict[str, Any]:
    """使用赛前快照评价单场结果，并强制检查时间以阻止赛后泄漏。"""
    cutoff = _parse_aware_datetime(data_as_of, "data_as_of")
    kickoff = _parse_aware_datetime(match.get("kickoff_beijing"), "kickoff_beijing")
    if cutoff >= kickoff:
        raise ValueError("回测数据截止时间必须早于开赛时间")
    actual = match.get("actual_result")
    if not isinstance(actual, dict) or actual.get("outcome") not in OUTCOME_LABELS:
        raise ValueError("回测比赛必须包含有效的 actual_result.outcome")
    home_goals = _integer(actual.get("home_goals"), "actual_result.home_goals")
    away_goals = _integer(actual.get("away_goals"), "actual_result.away_goals")
    score_outcome = "home" if home_goals > away_goals else "away" if away_goals > home_goals else "draw"
    if score_outcome != actual["outcome"]:
        raise ValueError("actual_result.outcome 与比分方向不一致")
    prediction = analyze_match(match)
    return {
        "id": prediction["id"],
        "home": prediction["home"],
        "away": prediction["away"],
        "data_as_of": data_as_of,
        "kickoff_beijing": prediction["kickoff_beijing"],
        "predicted_outcome": prediction["context_pick"],
        "predicted_label": OUTCOME_LABELS[prediction["context_pick"]],
        "actual_outcome": actual["outcome"],
        "actual_label": OUTCOME_LABELS[actual["outcome"]],
        "score": f"{home_goals}:{away_goals}",
        "hit": prediction["context_pick"] == actual["outcome"],
        "market_probabilities": prediction["market_probabilities"],
        "context_probabilities": prediction["context_probabilities"],
        "analysis": prediction["analysis"],
        "sample_note": "仅1场冒烟回测，不能据此评价长期准确率或统计显著性。",
    }


def _load_payload(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_match_file(path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = _load_payload(path)
    matches = payload.get("matches")
    if not isinstance(matches, list) or not matches:
        raise ValueError("比赛文件必须包含非空 matches 数组")
    metadata = {
        "data_as_of": payload.get("data_as_of"),
        "source": payload.get("source"),
        "source_url": payload.get("source_url"),
        "sources": payload.get("sources", []),
        "odds_format": payload.get("odds_format", "decimal"),
        "model": "market-plus-transparent-context-v1",
    }
    return metadata, [analyze_match(match) for match in matches]


def load_backtest_file(path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = _load_payload(path)
    matches = payload.get("matches")
    if not isinstance(matches, list) or not matches:
        raise ValueError("回测文件必须包含非空 matches 数组")
    data_as_of = payload.get("data_as_of")
    results = [backtest_match(match, data_as_of) for match in matches]
    return {"data_as_of": data_as_of, "sources": payload.get("sources", [])}, results
