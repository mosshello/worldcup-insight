"""基于体彩未开赛赛事的比分与方向预测。"""

from __future__ import annotations

import re
from typing import Any

from .analyzer import OUTCOME_LABELS
from .fox_scraper import _fetch_page, parse_moneyline_blocks
from .fusion_predictor import _devig, _pick_from_probs
from .sporttery_api import (
    SportteryApiError,
    enrich_match_timing,
    fetch_announced_matches,
    fetch_fixed_bonus,
    fetch_upcoming_matches,
    is_upcoming_match,
)
from .sporttery_cache import load_snapshot, save_snapshot
from .direction_shift import analyze_direction_shift
from .prediction_journal import get_open_direction_key, record_predictions


def _had_history_from_bonus(bonus: dict[str, Any]) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for item in bonus.get("hadList") or []:
        try:
            history.append(
                {
                    "recorded_at": f"{item.get('updateDate', '')}T{item.get('updateTime', '')}",
                    "home": float(item["h"]),
                    "draw": float(item["d"]),
                    "away": float(item["a"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return history


def _pool_from_history(item: dict[str, Any]) -> dict[str, Any]:
    line = item.get("goalLine")
    return {
        "home": float(item["h"]),
        "draw": float(item["d"]),
        "away": float(item["a"]),
        "goal_line": float(line) if line not in (None, "") else None,
    }


def top_crs_scores(history: dict[str, Any], limit: int = 3) -> list[tuple[int, int, float]]:
    crs_list = history.get("crsList") or []
    if not crs_list:
        return []
    latest = crs_list[-1]
    scores: list[tuple[int, int, float]] = []
    for key, value in latest.items():
        matched = re.fullmatch(r"s(\d+)s(\d+)", key)
        if not matched:
            continue
        try:
            scores.append((int(matched.group(1)), int(matched.group(2)), float(value)))
        except ValueError:
            continue
    scores.sort(key=lambda item: item[2])
    return scores[:limit]


def _confidence(gap: float, aligned: bool) -> str:
    score = gap + (0.05 if aligned else 0)
    if score >= 0.20:
        return "高"
    if score >= 0.08:
        return "中"
    return "低"


def _load_fox_map() -> dict[tuple[str, str], dict[str, Any]]:
    try:
        page = _fetch_page()
        return {(item["home"], item["away"]): item for item in parse_moneyline_blocks(page)}
    except RuntimeError:
        return {}


def predict_score_for_match(
    sporttery_match: dict[str, Any],
    *,
    fox_map: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """对单个体彩未开赛场次做方向 + 比分预测。"""
    home = sporttery_match["home"]
    away = sporttery_match["away"]
    match_id = sporttery_match["match_id"]

    had = sporttery_match["pools"]["had"]
    hhad = sporttery_match["pools"].get("hhad") or {}

    fox_map = fox_map if fox_map is not None else _load_fox_map()
    fox = fox_map.get((home, away), {})
    fox_odds = fox.get("european")
    if fox_odds is None and had:
        fox_odds = {"home": had["home"], "draw": had["draw"], "away": had["away"]}

    sporttery_probs = _devig({"home": had["home"], "draw": had["draw"], "away": had["away"]})
    fox_probs = _devig(
        {"home": fox_odds["home"], "draw": fox_odds["draw"], "away": fox_odds["away"]}
    )

    s_pick, s_second, s_gap = _pick_from_probs(sporttery_probs)
    f_pick, _, _ = _pick_from_probs(fox_probs)
    aligned = s_pick == f_pick
    confidence = _confidence(s_gap, aligned)

    bonus = fetch_fixed_bonus(match_id)
    had_hist = _pool_from_history(bonus["hadList"][-1])
    hhad_hist = _pool_from_history(bonus["hhadList"][-1]) if bonus.get("hhadList") else {}

    crs_top = top_crs_scores(bonus)
    primary = f"{crs_top[0][0]}-{crs_top[0][1]}" if crs_top else "—"
    alt_scores = [f"{h}-{a}（固定奖金 {o:.2f}）" for h, a, o in crs_top]

    direction_note = None
    if crs_top and crs_top[0][0] == crs_top[0][1] and s_pick != "draw":
        direction_note = (
            f"胜平负方向倾向{OUTCOME_LABELS[s_pick]}，但体彩猜比分最低固定奖金为 {primary}（平局），"
            "90 分钟僵持概率不可忽视。"
        )

    hhad_pick = None
    if hhad_hist.get("goal_line") is not None:
        hhad_probs = _devig(
            {
                "home": hhad_hist["home"],
                "draw": hhad_hist["draw"],
                "away": hhad_hist["away"],
            }
        )
        hhad_pick, _, _ = _pick_from_probs(hhad_probs)

    direction_shift = analyze_direction_shift(
        _had_history_from_bonus(bonus),
        current_direction_key=s_pick,
        foreign_probs=fox_probs,
        journal_direction_key=get_open_direction_key(match_id),
    )

    return {
        "match_id": match_id,
        "home": home,
        "away": away,
        "league": sporttery_match.get("league"),
        "match_num": sporttery_match.get("match_num"),
        "business_date": sporttery_match.get("business_date"),
        "kickoff_beijing": sporttery_match.get("kickoff_beijing"),
        "hours_until_kickoff": sporttery_match.get("hours_until_kickoff"),
        "countdown_label": sporttery_match.get("countdown_label"),
        "direction": OUTCOME_LABELS[s_pick],
        "direction_key": s_pick,
        "second": OUTCOME_LABELS[s_second],
        "confidence": confidence,
        "aligned_with_fox": aligned,
        "predicted_score": primary,
        "alt_scores": alt_scores,
        "had_odds": {
            "home": had_hist["home"],
            "draw": had_hist["draw"],
            "away": had_hist["away"],
        },
        "crs_odds": crs_top[0][2] if crs_top else None,
        "sporttery_had": f"{had_hist['home']:.2f} / {had_hist['draw']:.2f} / {had_hist['away']:.2f}",
        "sporttery_hhad": (
            f"让{hhad_hist['goal_line']:+.0f} → "
            f"{hhad_hist['home']:.2f} / {hhad_hist['draw']:.2f} / {hhad_hist['away']:.2f}"
            if hhad_hist
            else "—"
        ),
        "hhad_direction": OUTCOME_LABELS[hhad_pick] if hhad_pick else None,
        "fox_moneyline": (
            f"{fox_odds['home']:.2f} / {fox_odds['draw']:.2f} / {fox_odds['away']:.2f}"
            if fox_odds
            else "—"
        ),
        "fox_source": fox.get("source") or ("sporttery.cn" if not fox else "fox-sports/fanduel"),
        "direction_note": direction_note,
        "direction_shift": direction_shift,
    }


def predict_upcoming_scores() -> list[dict[str, Any]]:
    """拉取体彩全部未开赛赛事并逐场预测。"""
    matches = [enrich_match_timing(match) for match in fetch_upcoming_matches()]
    if not matches:
        return []
    fox_map = _load_fox_map()
    predictions = [predict_score_for_match(match, fox_map=fox_map) for match in matches]
    save_snapshot(matches=matches, predictions=predictions)
    record_predictions(predictions)
    return predictions


def list_upcoming_matches() -> dict[str, Any]:
    """返回未开赛体彩赛事列表（不含比分预测）。"""
    try:
        matches = fetch_announced_matches()
    except SportteryApiError as exc:
        cached = load_snapshot()
        if cached:
            return {
                "success": True,
                "source": "sporttery.cn",
                "source_url": "https://www.sporttery.cn/jc/zqszsc/",
                "matches": cached["matches"],
                "match_count": len(cached["matches"]),
                "cached": True,
                "cached_at": cached.get("cached_at"),
            }
        return {"success": False, "error": str(exc), "matches": [], "match_count": 0}

    return {
        "success": True,
        "source": "sporttery.cn",
        "source_url": "https://www.sporttery.cn/jc/zqszsc/",
        "matches": matches,
        "match_count": len(matches),
        "cached": False,
    }
