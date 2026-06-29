"""根据十进制欧赔生成胜平负市场概率和中文规则分析。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


OUTCOME_LABELS = {
    "home": "主胜",
    "draw": "平",
    "away": "客胜",
}


def _validate_odds(odds: dict[str, Any]) -> dict[str, float]:
    """校验并返回按主胜、平、客胜排序的十进制赔率。"""
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


def _build_analysis(
    match: dict[str, Any], probabilities: dict[str, float], ranking: list[str]
) -> list[str]:
    favorite = ranking[0]
    favorite_probability = probabilities[favorite]
    draw_probability = probabilities["draw"]
    lines: list[str] = []

    if match.get("stage") == "淘汰赛":
        lines.append(
            "本场为淘汰赛；这里的胜平负仅指常规 90 分钟（含伤停补时），不包含加时赛和点球大战。"
        )

    lines.append(
        f"市场第一倾向为{OUTCOME_LABELS[favorite]}，去水后概率约 {favorite_probability:.1%}。"
    )

    if favorite_probability >= 0.65:
        lines.append("第一倾向优势明显，但高概率仍不等于比赛结果确定。")
    elif favorite_probability >= 0.50:
        lines.append("第一倾向较清晰，仍需防范平局或弱方爆冷。")
    else:
        lines.append("没有任何单项超过五成，比赛分歧较大，不宜视为稳胆。")

    if draw_probability >= 0.30:
        lines.append("平局概率达到三成左右，90 分钟内僵持的风险值得重点关注。")
    elif draw_probability >= 0.25:
        lines.append("平局不是第一选择，但占比不可忽略。")
    else:
        lines.append("当前赔率对平局的支持相对有限。")

    lines.append("结论来自单一赔率快照，只反映市场定价，不包含实时阵容、伤停或临场赔率变化。")
    return lines


def analyze_match(match: dict[str, Any]) -> dict[str, Any]:
    """分析单场比赛，并返回可直接序列化的结构。"""
    for field in ("home", "away", "odds"):
        if field not in match:
            raise ValueError(f"比赛缺少字段：{field}")

    odds = _validate_odds(match["odds"])
    raw_probabilities = {outcome: 1 / price for outcome, price in odds.items()}
    overround = sum(raw_probabilities.values())
    probabilities = {
        outcome: probability / overround
        for outcome, probability in raw_probabilities.items()
    }
    ranking = sorted(probabilities, key=probabilities.get, reverse=True)

    return {
        "id": match.get("id"),
        "competition": match.get("competition"),
        "stage": match.get("stage"),
        "fixture_date": match.get("fixture_date"),
        "kickoff_beijing": match.get("kickoff_beijing"),
        "home": match["home"],
        "away": match["away"],
        "odds": odds,
        "probabilities": probabilities,
        "overround": overround - 1,
        "ranking": ranking,
        "pick": ranking[0],
        "second_pick": ranking[1],
        "confidence": _confidence(probabilities),
        "analysis": _build_analysis(match, probabilities, ranking),
    }


def load_match_file(path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """读取比赛文件，返回元数据与比赛分析结果。"""
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    matches = payload.get("matches")
    if not isinstance(matches, list) or not matches:
        raise ValueError("比赛文件必须包含非空 matches 数组")

    metadata = {
        "data_as_of": payload.get("data_as_of"),
        "source": payload.get("source"),
        "source_url": payload.get("source_url"),
        "odds_format": payload.get("odds_format", "decimal"),
    }
    return metadata, [analyze_match(match) for match in matches]
