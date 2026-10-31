"""Deterministic, versioned baselines. LLM narration never rewrites probabilities."""

from __future__ import annotations

import math
import json
from datetime import datetime, timezone

from .sources import parse_date, EXPECTED_OPTIONS

OUTCOMES = {"h": "主胜", "d": "平局", "a": "客胜"}


def distribution(market):
    odds = {
        o["key"]: float(o["odds"])
        for o in (market or {}).get("options", [])
        if isinstance(o.get("odds"), (int, float))
        and o["odds"] > 1
        and math.isfinite(o["odds"])
    }
    total = sum(1 / v for v in odds.values())
    return {k: (1 / v) / total for k, v in odds.items()} if total else {}


def form_estimate(recent):
    if len(recent) != 2 or any(len(rows) < 3 for rows in recent):
        return None
    # Three pseudo-games at 1.35 goals shrink the small recent sample.
    rates = [
        (
            (sum(r["gf"] for r in rows) + 4.05) / (len(rows) + 3),
            (sum(r["ga"] for r in rows) + 4.05) / (len(rows) + 3),
        )
        for rows in recent
    ]
    home = max(0.2, min(4.5, (rates[0][0] + rates[1][1]) / 2))
    away = max(0.2, min(4.5, (rates[1][0] + rates[0][1]) / 2))
    probs = {"h": 0.0, "d": 0.0, "a": 0.0}
    for h in range(11):
        for a in range(11):
            p = (
                math.exp(-home - away)
                * home**h
                / math.factorial(h)
                * away**a
                / math.factorial(a)
            )
            probs["h" if h > a else "a" if a > h else "d"] += p
    total = sum(probs.values())
    return {
        "probabilities": {k: v / total for k, v in probs.items()},
        "expected_goals": [round(home, 3), round(away, 3)],
    }


def predict(match, detail, now=None):
    now = now or datetime.now(timezone.utc)
    if parse_date(match["kickoff"]) <= now or detail.get("context", {}).get(
        "state"
    ) in ("live", "completed"):
        raise ValueError("比赛已经开赛，不能补写赛前预测。可查看已有版本及赛后复盘。")
    markets = detail["markets"]
    market_probs = distribution(markets.get("had"))
    origin = "体彩胜平负"
    if set(market_probs) != set(OUTCOMES):
        hf = distribution(markets.get("hafu"))
        if len(hf) == 9:
            market_probs = {
                k: sum(v for code, v in hf.items() if code.endswith(k))
                for k in OUTCOMES
            }
            origin = "体彩半全场汇总（普通胜平负未开售）"
        else:
            raise ValueError("没有完整、有效的胜平负或半全场定价，暂不能生成预测。")
    context = detail.get("context", {})
    recent = context.get("recent") or [[], []]
    form = form_estimate(recent) if context.get("available") else None
    probabilities = (
        {k: 0.8 * market_probs[k] + 0.2 * form["probabilities"][k] for k in OUTCOMES}
        if form
        else market_probs
    )
    ranked = sorted(probabilities, key=probabilities.get, reverse=True)
    pick = ranked[0]
    pick_name = (
        match["home"] + "胜"
        if pick == "h"
        else match["away"] + "胜" if pick == "a" else "平局"
    )
    gap = probabilities[ranked[0]] - probabilities[ranked[1]]
    summary = f"{pick_name}是当前首选方向" + (
        "，与次选差距较小。" if gap < 0.1 else "，仍需在首发公布后复核。"
    )
    ttg = distribution(markets.get("ttg"))
    if set(ttg) != set(EXPECTED_OPTIONS["ttg"]):
        ttg = {}
    goals = sorted(ttg, key=ttg.get, reverse=True)[:2]
    goal_label = (
        " / ".join(k[1:] + ("+" if k == "s7" else "") for k in goals) + " 球"
        if goals
        else "暂无总进球数据"
    )
    crs = distribution(markets.get("crs"))
    if set(crs) != set(EXPECTED_OPTIONS["crs"]):
        crs = {}
    options = {o["key"]: o for o in markets.get("crs", {}).get("options", [])}
    score_keys = sorted((k for k in crs if len(k) == 6), key=crs.get, reverse=True)[:3]
    evidence = [
        {
            "title": "市场基准",
            "text": f"{origin}的当前定价支持{OUTCOMES[max(market_probs,key=market_probs.get)]}方向。",
            "tab": "odds",
        }
    ]
    for i, rows in enumerate(recent):
        if not rows:
            continue
        team = match["home"] if i == 0 else match["away"]
        w = sum(r["gf"] > r["ga"] for r in rows)
        d = sum(r["gf"] == r["ga"] for r in rows)
        evidence.append(
            {
                "title": f"{team}近期表现",
                "text": f"已核实近{len(rows)}场：{w}胜{d}平{len(rows)-w-d}负，进{sum(r['gf'] for r in rows)}球、失{sum(r['ga'] for r in rows)}球。",
                "tab": "basic",
            }
        )
    for code in ("had", "hhad"):
        series = detail.get("history", {}).get(code, [])
        if len(series) > 1:
            first = {o["key"]: o["odds"] for o in series[0]["options"]}
            last = {o["key"]: o["odds"] for o in series[-1]["options"]}
            if "h" in first and "h" in last:
                evidence.append(
                    {
                        "title": "SP变化",
                        "text": f"{'主胜' if code=='had' else '让球主胜'}：最早可得记录 {first['h']:.2f} → 最新记录 {last['h']:.2f}。",
                        "tab": "odds",
                    }
                )
    risks = []
    if not form:
        risks.append("近期表现不足，当前仅为市场基线预测。")
    else:
        risks.append("近期样本较少；80%市场与20%近期进失球的组合尚未经过样本外校准。")
    if not all(
        r.get("status") == "confirmed" for r in context.get("lineups", [])
    ) or not context.get("lineups"):
        risks.append("正式首发尚未完整确认，临场人员变化可能改变判断。")
    if not context.get("injuries_verified"):
        risks.append("伤停信息尚未核实，未默认所有球员健康。")
    if score_keys:
        label = options[score_keys[0]]["label"]
        h, a = map(int, label.split("-"))
        if ("h" if h > a else "a" if a > h else "d") != pick:
            risks.append(
                f"单一比分首选 {label} 与胜平负首选方向不同：比分是单项概率，胜平负汇总了多个比分。"
            )
    model = "market-form-poisson-v1" if form else "market-baseline-v1"
    return {
        "model_version": model,
        "engine": "市场 + 近期进失球基线" if form else "市场基线",
        "title": summary,
        "summary": "预测使用当前官方 SP 与已核实赛前数据；概率为基线估计，非经过验证的命中率。",
        "direction": OUTCOMES[pick],
        "direction_key": pick,
        "probabilities": [round(probabilities[k] * 100, 2) for k in ("h", "d", "a")],
        "probLabel": "基线预测概率",
        "market_probabilities": market_probs,
        "form_estimate": form,
        "goals": goal_label,
        "scores": " / ".join(options[k]["label"] for k in score_keys) or "暂无比分数据",
        "score_probabilities": [
            {"score": options[k]["label"], "probability": crs[k]} for k in score_keys
        ],
        "evidence": evidence,
        "risks": risks,
        "source_times": {
            "markets": detail.get("captured_at"),
            "context": context.get("captured_at"),
        },
        "narrative": None,
        "validation": "baseline_not_calibrated",
    }


def narrate(match, detail, prediction):
    from ..env_config import get_deepseek_api_key, get_deepseek_model
    from ..ai_analyst import _call_deepseek

    key = get_deepseek_api_key()
    if not key:
        raise ValueError("尚未配置 AI 解读服务；基线预测仍可使用。")
    context = {
        "match": {k: match.get(k) for k in ("home", "away", "league", "kickoff")},
        "recent": detail.get("context", {}).get("recent"),
        "lineups": detail.get("context", {}).get("lineups"),
        "prediction": prediction,
    }
    messages = [
        {
            "role": "system",
            "content": "你是足球赛前分析师。只使用给定数据，用简洁中文解释比赛。不得编造球员、伤停、战术习惯或新闻，不得把模型概率当命中率，不改变已计算的预测概率。输出：一句话结论、关键依据、反方情景、临场需复核事项。无相关事实时直接说明无法判断，最多500字。",
        },
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
    ]
    return _call_deepseek(messages, api_key=key, model=get_deepseek_model(), timeout=60)
