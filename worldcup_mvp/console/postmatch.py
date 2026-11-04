"""Evidence-bound, local post-match analysis, independent of official settlement/LLMs."""

import json
import math
import re
from datetime import datetime, timezone

from .sources import parse_date

VERSION = "postmatch-local-v4"
LABELS = {"h": "主胜", "d": "平局", "a": "客胜"}


def score_pair(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,2}[:-]\d{1,2}", value):
        return None
    return tuple(map(int, re.split("[:-]", value)))


def had_probabilities(prediction, model=False):
    if prediction.get('schema_version') == 2 and not model:
        prediction = prediction.get('market_reference') or {}
    assessments = (prediction.get("football_model", {}).get("markets", [])
                   if model else prediction.get("market_assessments", []))
    had = next((m for m in assessments if m.get("code") == "had"), {})
    probs = {o.get("key"): o.get("probability") for o in had.get("options", [])}
    if not probs and not model:
        probs = dict(zip("hda", prediction.get("probabilities", [])))
    if set(probs) != set("hda") or any(
        isinstance(p, bool) or not isinstance(p, (float, int)) or not math.isfinite(p) or not 0 <= p <= 100
        for p in probs.values()
    ) or abs(sum(probs.values()) - 100) > 0.2:
        return None, had
    return probs, had


def analyze(store, match, prediction, detail, live_score):
    """Read frozen pregame evidence. Never writes analyses or settlement fields."""
    waiting = lambda reason: {"status": "waiting", "reason": reason}
    if detail.get("cancelled") or (live_score or {}).get("state") == "cancelled":
        return waiting("比赛已取消，不生成完场分析")
    if prediction is not None:
        with store.connect() as db:
            archived = db.execute(
                "SELECT created_at,input_snapshot,payload FROM analyses WHERE id=? AND match_id=?",
                (prediction.get("id"), match["id"]),
            ).fetchone()
        try:
            if not archived:
                return waiting("没有赛前预测归档，不能事后补写预测")
            original = json.loads(archived["input_snapshot"])
            frozen = json.loads(archived["payload"])
            kickoff = parse_date(original.get("match", {}).get("kickoff") or match["kickoff"])
            if (parse_date(archived["created_at"]) >= kickoff
                    or parse_date(frozen["created"]) >= kickoff or kickoff >= datetime.now(timezone.utc)):
                return waiting("缺少有效赛前归档，不进行事后对照")
        except (ValueError, KeyError, TypeError):
            return waiting("赛前归档无法校验")
        prediction = frozen
    else:
        prediction = {}
    results = {str(r.get("code", "")).lower(): str(r.get("combination", "")).lower()
               for r in detail.get("results", [])}
    official = score_pair(detail.get("official_score")) or score_pair(results.get("crs"))
    live_score = live_score or {}
    observed = score_pair(live_score.get("score"))
    live_usable = (live_score.get("state") == "completed"
                   and live_score.get("score_period") == "regular_time"
                   and not live_score.get("stale", True) and not live_score.get("error")
                   and live_score.get("provider_event_id") and observed is not None)
    if official:
        h, a = official
        actual = "h" if h > a else "a" if a > h else "d"
        conflict = results.get("had") in LABELS and results["had"] != actual
        status = "conflict" if conflict else "confirmed"
        source = "体彩官方"
        source_url = match.get("source_url")
        observed_at = detail.get("result_captured_at") or detail.get("captured_at")
    elif live_usable:
        h, a = observed
        actual = "h" if h > a else "a" if a > h else "d"
        status, source = "preliminary", live_score.get("source", "即时比分")
        source_url, observed_at = live_score.get("source_url"), live_score.get("observed_at")
    else:
        if live_score.get("state") == "completed":
            return waiting("完场比分尚未确认90分钟口径或来源暂不可用，等待重新核实")
        return waiting("等待比分源确认全场结束")

    report = {
        "version": VERSION, "status": status, "prediction_id": prediction.get("id"),
        "has_prediction": bool(prediction),
        "fixture": f"{match['home']} vs {match['away']}", "score": f"{h}-{a}",
        "period": "regular_time", "source": source, "source_url": source_url,
        "provider_event_id": live_score.get("provider_event_id") if status == "preliminary" else None,
        "observed_at": observed_at, "prediction_version": prediction.get("version"),
        "prediction_created": prediction.get("created"), "actual": LABELS[actual],
        "findings": [], "recommendations": [], "comparisons": [],
        "missing_data": ["比赛事件时间线", "红牌与换人", "射门与xG"],
    }
    if status == "conflict":
        report["summary"] = "正式比分与胜平负结果不一致，暂停分析结论。"
        report["recommendations"] = ["重新核实来源赛果；冲突解除前不判断方向或玩法结果。"]
        return store.save_postmatch_report(match["id"], prediction.get("id"), report)

    report["summary"] = (
        f"{'完场初评' if status == 'preliminary' else '赛果已确认'}：90分钟比分 {h}-{a}，"
        f"{LABELS[actual]}，总进球 {h+a}。"
    )
    process_available = add_match_facts(report, detail)
    if status == "preliminary":
        report["recommendations"].append("先依据完场比分查看分析；正式赛果到达后自动核对，初评不计入正式命中或结算。")
    elif live_usable and official != observed:
        report["findings"].append(f"比分源记录 {observed[0]}-{observed[1]} 与正式比分不同，本次以正式结果重新对照。")
        report["recommendations"].append("核查比分源修正记录和比赛时段，保留先前初评供追溯。")

    if not prediction:
        report["findings"].append(f"{match['home']}进球 {h}、失球 {a}；{match['away']}进球 {a}、失球 {h}。")
        if h == a:
            report["findings"].append("双方战平，净胜球为0。")
        else:
            winner = match["home"] if h > a else match["away"]
            report["findings"].append(f"{winner}净胜 {abs(h-a)} 球。")
        report["findings"].append("双方均有进球。" if h and a else "至少一方未进球；零封不能单独证明防守过程占优。")
        report["findings"].append("本场没有赛前预测归档，仅作赛后事实分析，不评估预测命中或模型误差。")
        if not process_available:
            report["findings"].append("尚无本场事件和射门证据，无法确认比赛原因或场面优势。")
        report["recommendations"].extend([
            "优先核实进球时间、红牌、首发和射门资料，再解释比分形成的原因。",
            "后续比赛在开赛前生成并归档预测，完场后即可自动对照概率和判断偏差。",
        ])
        add_intelligence(report, prediction, detail, match)
        return store.save_postmatch_report(match["id"], None, report)

    for label, is_model in (("市场参考", False), ("足球模型", True)):
        if is_model and prediction.get("football_model", {}).get("status") != "shadow":
            report["findings"].append("赛前未归档可用足球模型，本场不能事后补算模型预测。")
            continue
        probs, had = had_probabilities(prediction, is_model)
        selected = had.get("selected_keys")
        if selected is None and not is_model and prediction.get('schema_version') != 2:
            selected = [prediction["direction_key"]] if prediction.get("direction_key") in LABELS else []
        comparison = {"label": label, "selection": " / ".join(LABELS[k] for k in selected or [] if k in LABELS) or "观望 / 未归档",
                      "alignment": None if not selected else actual in selected}
        if selected:
            report["findings"].append(f"{label}归档方向 {comparison['selection']}，与本场结果{'一致' if comparison['alignment'] else '存在偏差'}。")
        else:
            report["findings"].append(f"{label}没有归档明确方向，不补记命中或失误。")
        if probs:
            ranked = sorted(probs, key=probs.get, reverse=True)
            gap = probs[ranked[0]] - probs[ranked[1]]
            comparison.update(actual_probability=probs[actual], top_gap=round(gap, 2))
            report["findings"].append(f"{label}赛前给{LABELS[actual]}的概率为 {probs[actual]:.1f}%；第一与第二方向相差 {gap:.1f} 个百分点。")
            if gap < 5:
                report["recommendations"].append(f"{label}前两项差距不足5个百分点，属于接近分布；回看同类样本，避免将微弱领先解读为明确优势。")
            if probs[actual] < 20:
                report["recommendations"].append(f"{label}对本次结果赋予较低概率；累计同赛事前瞻样本后检查低概率区间的实际发生频率。")
        else:
            report["findings"].append(f"{label}完整胜平负概率缺失，无法计算概率差距。")
        report["comparisons"].append(comparison)

    model = prediction.get("football_model", {})
    expected = model.get("expected_goals")
    if model.get("status") == "shadow" and isinstance(expected, list) and len(expected) == 2 and all(
        not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in expected
    ):
        report["findings"].append(f"归档模型预期进球 {expected[0]:.2f} / {expected[1]:.2f}；实际为 {h} / {a}。预期均值不等于比分承诺。")
    report["findings"].append("现有证据只能对照结果，无法确认红牌、战术或人员变化是否造成偏差。")
    report["recommendations"].extend([
        "补齐本场事件、射门及首发资料后再判断比赛原因；赛前近期战绩不能代替本场事件证据。",
        "将本场纳入同赛事的连续前瞻观察，累计检查概率校准；不因单场结果直接修改模型或回写赛前记录。",
    ])
    add_intelligence(report, prediction, detail, match)
    return store.save_postmatch_report(match["id"], prediction["id"], report)


def add_match_facts(report, detail):
    facts = (detail.get('context') or {}).get('match_facts') or {}
    if not facts.get('provider_event_id') or not facts.get('captured_at'): return False
    statistics, events = facts.get('statistics', []), facts.get('events', [])
    report['process'] = {'statistics': statistics, 'events': events, 'source': facts.get('source'),
                         'source_url': facts.get('source_url'), 'statistics_scope': facts.get('statistics_scope')}
    for item in statistics:
        if item.get('code') in ('totalShots', 'shotsOnTarget', 'possessionPct', 'redCards') and item.get('home') is not None and item.get('away') is not None:
            report['findings'].append(f"{item['label']}（主 / 客）：{item['home']} / {item['away']}；来源比赛累计统计。")
    relevant = [e for e in events if e.get('type') in ('goal', 'own-goal', 'penalty', 'penalty---scored', 'red-card', 'yellow-red-card')]
    for event in relevant[:10]:
        report['findings'].append(f"{event['period_label']} {event['clock']}：{event['team']}，{event['label']}。")
    report['missing_data'] = []
    if not events: report['missing_data'].append('比赛关键事件')
    if not any(e.get('type') == 'substitution' for e in events): report['missing_data'].append('完整换人记录')
    if not any(s.get('code') == 'totalShots' for s in statistics): report['missing_data'].append('射门统计')
    if not any(s.get('code') == 'expectedGoals' for s in statistics): report['missing_data'].append('xG')
    report['missing_data'].append('战术与因果核验')
    if statistics or events:
        report['recommendations'].append('对照进球、红牌和换人的时间顺序及射门统计；事件发生不等于已证明输赢原因。')
    return bool(statistics or events)


def add_intelligence(report, prediction, detail, match):
    from .intelligence import review_link
    linked = review_link(prediction, detail, match)
    report["findings"].extend(linked["findings"])
    for market in linked["current"]["markets"]:
        if not market["same_line"]:
            continue
        changes = "；".join(f"{r['label']} {r['first']:.2f}→{r['last']:.2f}" for r in market["rows"])
        report["findings"].append(f"回看来源赛前{market['label']}共{market['count']}条：{changes}。")
    if linked["current"]["markets"]:
        report["recommendations"].append("结合完整调价序列核查反向变化；目前没有资金流与操控证据，不能以输赢认定诱盘。")
