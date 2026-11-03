"""Deterministic, versioned market baselines and local explanations. No LLM calls."""

from __future__ import annotations

import math
import json
from datetime import datetime, timezone

from .sources import parse_date, EXPECTED_OPTIONS, MARKETS
from .football_markets import assessment

OUTCOMES = {"h": "主胜", "d": "平局", "a": "客胜"}


def handicap_labels(line):
    n = abs(int(line))
    if line == 0:
        return dict(OUTCOMES)
    if line < 0:
        return {
            "h": f"主队净胜{n+1}球及以上",
            "d": f"主队恰好净胜{n}球",
            "a": "主队不胜" if n == 1 else f"主队净胜不足{n}球",
        }
    return {
        "h": "主队不败" if n == 1 else f"主队最多净负{n-1}球（含主胜及平局）",
        "d": f"主队恰好净负{n}球",
        "a": f"主队净负{n+1}球及以上",
    }


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
    if "quality" in detail:
        if not detail["quality"]["can_predict"]:
            raise ValueError("；".join(detail["quality"]["reasons"]))
        markets = {
            k: v
            for k, v in markets.items()
            if k in detail["quality"]["eligible_markets"]
        }
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
    # Small-sample form is evidence, not an unvalidated fixed 20% production weight.
    probabilities = market_probs
    ranked = sorted(probabilities, key=probabilities.get, reverse=True)
    pick = ranked[0]
    pick_name = (
        match["home"] + "胜"
        if pick == "h"
        else match["away"] + "胜" if pick == "a" else "平局"
    )
    gap = probabilities[ranked[0]] - probabilities[ranked[1]]
    summary = (
        "胜平负方向接近，暂不单押胜负。"
        if gap < 0.03
        else f"{pick_name}略占优，仍需防范次选方向。"
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
    external = [
        e
        for e in detail.get("evidence", [])
        if parse_date(e["published_at"]) <= now
        and parse_date(e["recorded_at"]) <= now
        and parse_date(e["recorded_at"]) < parse_date(match["kickoff"])
    ]
    for entry in external:
        evidence.append(
            {
                "title": "来源事实" if entry["kind"] == "fact" else "外部观点",
                "text": entry["text"],
                "tab": "lineups",
                "source_url": entry["source_url"],
                "published_at": entry["published_at"],
            }
        )
    risks = []
    if context.get('stale'):
        risks.append('基本面本轮更新失败，近期资料为上次采集结果；请复核来源时间。')
    if not form:
        risks.append("近期表现不足，当前仅为市场基线预测。")
    else:
        risks.append("近期表现用于事实对照；联赛模型未通过回测前，不调整市场概率。")
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
    model = "market-baseline-v2"
    market_views = []
    for code in MARKETS:
        p = distribution(markets.get(code))
        if set(p) != set(EXPECTED_OPTIONS[code]):
            market_views.append({"code": code, "label": MARKETS[code], "origin": "market",
                "options": [], "selected_keys": [], "selection": "观望", "coverage": 0,
                "selection_count": 0, "status": "不可用", "reason": "未开售或数据不完整", "policy": "observation-top-v1"})
            continue
        market_views.append(assessment(code, p, markets[code]))
    views = {v["code"]: v for v in market_views}
    return {
        "model_version": model,
        "engine": "市场基线",
        "title": summary,
        "summary": "预测使用当前官方 SP 与已核实赛前数据；概率为基线估计，非经过验证的命中率。",
        "direction": OUTCOMES[pick] if gap >= 0.03 else "观望",
        "direction_key": pick if gap >= 0.03 else None,
        "leading_outcome": pick,
        "probabilities": [round(probabilities[k] * 100, 2) for k in ("h", "d", "a")],
        "probLabel": "基线预测概率",
        "market_probabilities": market_probs,
        "form_estimate": form,
        "goals": views["ttg"]["selection"] if views["ttg"]["options"] else "暂无总进球数据",
        "scores": views["crs"]["selection"] if views["crs"]["options"] else "暂无比分数据",
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
        "competition": match.get("competition"),
        "market_assessments": market_views,
        "quality": detail.get("quality"),
        "external_evidence": external,
    }


def narrate(match, detail, prediction):
    # Kept for existing callers; all explanation is local and deterministic.
    indices = list(range(min(4, len(prediction["evidence"]))))
    lines = [
        prediction["evidence"][i]["title"] + "：" + prediction["evidence"][i]["text"]
        for i in indices
    ]
    scenario = (
        "若客队率先进球，主胜方向可能失效。"
        if prediction["direction_key"] == "h"
        else "若主队率先进球，客胜方向可能失效。"
    )
    if max(prediction["probabilities"]) - sorted(prediction["probabilities"])[-2] < 3:
        scenario = "双方差距小；任一方率先进球，都可能改变比赛走势。"
    context = detail.get("context", {})
    pending = []
    if len(context.get("lineups", [])) != 2 or any(
        r.get("status") != "confirmed" for r in context.get("lineups", [])
    ):
        pending.append("双方首发")
    if not context.get("injuries_verified"):
        pending.append("伤停情况")
    return (
        "结论："
        + prediction["title"]
        + "\n\n依据：\n"
        + "\n".join("• " + line for line in lines)
        + "\n\n反方情景（假设）："
        + scenario
        + "\n\n待核实："
        + ("、".join(pending) if pending else "临场阵容变动")
    )
