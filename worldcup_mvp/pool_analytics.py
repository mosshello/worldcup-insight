"""体彩多玩法盘口分析：ttg / hafu / 凯利 / 价值偏差。"""

from __future__ import annotations

from typing import Any

from .analyzer import OUTCOME_LABELS

TTG_KEYS: tuple[tuple[str, str], ...] = (
    ("s0", "0球"),
    ("s1", "1球"),
    ("s2", "2球"),
    ("s3", "3球"),
    ("s4", "4球"),
    ("s5", "5球"),
    ("s6", "6球"),
    ("s7", "7+球"),
)

HAFU_KEYS: tuple[tuple[str, str], ...] = (
    ("hh", "胜胜"),
    ("hd", "胜平"),
    ("ha", "胜负"),
    ("dh", "平胜"),
    ("dd", "平平"),
    ("da", "平负"),
    ("ah", "负胜"),
    ("ad", "负平"),
    ("aa", "负负"),
)

HAFU_HALF: dict[str, str] = {"h": "主胜", "d": "平", "a": "客胜"}


def _parse_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_ttg_latest(item: dict[str, Any]) -> list[dict[str, Any]]:
    """解析总进球玩法最新固定奖金。"""
    rows: list[dict[str, Any]] = []
    for key, label in TTG_KEYS:
        odds = _parse_float(item.get(key))
        if odds is None:
            continue
        rows.append({"key": key, "label": label, "goals": key[1:], "odds": odds})
    rows.sort(key=lambda row: row["odds"])
    return rows


def parse_hafu_latest(item: dict[str, Any]) -> list[dict[str, Any]]:
    """解析半全场玩法最新固定奖金。"""
    rows: list[dict[str, Any]] = []
    for key, label in HAFU_KEYS:
        odds = _parse_float(item.get(key))
        if odds is None:
            continue
        half = HAFU_HALF.get(key[0], key[0])
        full = HAFU_HALF.get(key[1], key[1])
        rows.append(
            {
                "key": key,
                "label": label,
                "half": half,
                "full": full,
                "odds": odds,
            }
        )
    rows.sort(key=lambda row: row["odds"])
    return rows


def devig_probabilities(odds: dict[str, float]) -> dict[str, float]:
    raw = {key: 1 / value for key, value in odds.items()}
    total = sum(raw.values())
    return {key: value / total for key, value in raw.items()}


def derive_pool_metrics(odds: dict[str, float]) -> dict[str, Any]:
    """返还率、overround、去水概率与公平赔率。"""
    implied = {key: 1 / value for key, value in odds.items()}
    overround = sum(implied.values())
    return_rate = 1 / overround if overround else 0.0
    no_vig = devig_probabilities(odds)
    fair_odds = {key: (1 / prob if prob > 0 else None) for key, prob in no_vig.items()}
    margin = overround - 1.0
    return {
        "overround": round(overround, 4),
        "return_rate": round(return_rate, 4),
        "margin": round(margin, 4),
        "implied_probabilities": {key: round(value, 4) for key, value in implied.items()},
        "no_vig_probabilities": {key: round(value, 4) for key, value in no_vig.items()},
        "fair_odds": {key: round(value, 2) if value else None for key, value in fair_odds.items()},
    }


def kelly_value_vs_reference(
    sporttery_odds: dict[str, float],
    reference_probs: dict[str, float],
    *,
    outcome_labels: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """
    凯利指数与价值偏差（参考 SportteryAPI：kelly = odds × p_ref）。

    reference_probs 通常来自外网去水概率或自建模型概率。
    """
    labels = outcome_labels or OUTCOME_LABELS
    rows: list[dict[str, Any]] = []
    for key, odds in sporttery_odds.items():
        ref_prob = reference_probs.get(key)
        if ref_prob is None:
            continue
        kelly_index = odds * ref_prob
        ev = ref_prob * odds - 1.0
        sporttery_implied = 1 / odds
        value_edge_pp = (ref_prob - sporttery_implied) * 100
        rows.append(
            {
                "key": key,
                "label": labels.get(key, key),
                "odds": odds,
                "reference_prob": round(ref_prob, 4),
                "sporttery_implied_prob": round(sporttery_implied, 4),
                "kelly_index": round(kelly_index, 4),
                "expected_value": round(ev, 4),
                "value_edge_pp": round(value_edge_pp, 2),
                "is_value": kelly_index > 1.0,
            }
        )
    rows.sort(key=lambda row: row["kelly_index"], reverse=True)
    return rows


def _ttg_bands(rows: list[dict[str, Any]]) -> dict[str, float]:
    """按去水概率汇总大小球区间。"""
    odds_map = {row["key"]: row["odds"] for row in rows}
    probs = devig_probabilities(odds_map)
    under_25 = sum(probs.get(key, 0) for key in ("s0", "s1", "s2"))
    over_25 = sum(probs.get(key, 0) for key in ("s3", "s4", "s5", "s6", "s7"))
    return {
        "under_2_5": round(under_25, 4),
        "over_2_5": round(over_25, 4),
    }


def analyze_ttg(ttg_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not ttg_rows:
        return {"available": False}
    odds_map = {row["key"]: row["odds"] for row in ttg_rows}
    metrics = derive_pool_metrics(odds_map)
    favorite = ttg_rows[0]
    bands = _ttg_bands(ttg_rows)
    bullets = [
        f"总进球市场最看好 {favorite['label']}（固定奖金 {favorite['odds']:.2f}，去水约 {metrics['no_vig_probabilities'][favorite['key']]:.1%}）。",
        f"大小球参考：≤2球 {bands['under_2_5']:.1%}，≥3球 {bands['over_2_5']:.1%}（由 ttg 去水概率汇总，非独立亚盘）。",
    ]
    top3 = "、".join(f"{row['label']}({row['odds']:.2f})" for row in ttg_rows[:3])
    bullets.append(f"前三选项：{top3}。")
    return {
        "available": True,
        "favorite": favorite,
        "top3": ttg_rows[:3],
        "metrics": metrics,
        "bands": bands,
        "summary_bullets": bullets,
    }


def analyze_hafu(hafu_rows: list[dict[str, Any]], had_direction: str | None = None) -> dict[str, Any]:
    if not hafu_rows:
        return {"available": False}
    odds_map = {row["key"]: row["odds"] for row in hafu_rows}
    metrics = derive_pool_metrics(odds_map)
    favorite = hafu_rows[0]
    bullets = [
        f"半全场最看好 {favorite['label']}（{favorite['half']}/{favorite['full']}，奖金 {favorite['odds']:.2f}）。",
    ]
    consistent = [row for row in hafu_rows if row["full"] == had_direction] if had_direction else []
    if consistent:
        pick = consistent[0]
        bullets.append(
            f"与胜平负首选「{had_direction}」一致的半全场最低项为 {pick['label']}（{pick['odds']:.2f}）。"
        )
    else:
        bullets.append("半全场最低项与胜平负首选不完全一致，存在半场/全场节奏分歧信号。")
    return {
        "available": True,
        "favorite": favorite,
        "top3": hafu_rows[:3],
        "metrics": metrics,
        "summary_bullets": bullets,
    }


def build_pool_analysis(
    *,
    odds_history: dict[str, Any] | None,
    had_odds: dict[str, float],
    had_direction_key: str | None = None,
    foreign_odds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """综合 ttg / hafu / 凯利价值分析。"""
    ttg_list = (odds_history or {}).get("ttg_history") or []
    hafu_list = (odds_history or {}).get("hafu_history") or []

    ttg_rows = parse_ttg_latest(ttg_list[-1]) if ttg_list else []
    hafu_rows = parse_hafu_latest(hafu_list[-1]) if hafu_list else []

    had_direction = OUTCOME_LABELS.get(had_direction_key or "", had_direction_key)
    ttg = analyze_ttg(ttg_rows)
    hafu = analyze_hafu(hafu_rows, had_direction)

    had_metrics = derive_pool_metrics(had_odds)
    kelly_rows: list[dict[str, Any]] = []
    value_bullets: list[str] = []
    if foreign_odds:
        ref_probs = devig_probabilities(foreign_odds)
        kelly_rows = kelly_value_vs_reference(had_odds, ref_probs)
        value_items = [row for row in kelly_rows if row["is_value"]]
        if value_items:
            parts = [
                f"{row['label']}（凯利 {row['kelly_index']:.3f}，偏差 +{row['value_edge_pp']:.1f}pp）"
                for row in value_items
            ]
            value_bullets.append(f"相对外网参考，体彩可能存在价值项：{'；'.join(parts)}。")
        else:
            value_bullets.append("相对外网去水概率，体彩三项凯利指数均 ≤1，无明显价值偏差。")

    summary = []
    if ttg.get("available"):
        summary.extend(ttg["summary_bullets"])
    if hafu.get("available"):
        summary.extend(hafu["summary_bullets"])
    summary.extend(value_bullets)
    summary.append(
        f"胜平负返还率 {had_metrics['return_rate']:.1%}，margin {had_metrics['margin']:.1%}。"
    )

    return {
        "ttg": ttg,
        "hafu": hafu,
        "had_metrics": had_metrics,
        "kelly_had": kelly_rows,
        "summary_bullets": summary,
        "coverage": {
            "ttg": ttg.get("available", False),
            "hafu": hafu.get("available", False),
            "kelly_vs_foreign": bool(kelly_rows),
        },
    }
