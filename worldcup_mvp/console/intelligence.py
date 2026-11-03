"""Deterministic evidence summaries. Signals do not alter forecast probabilities."""

import math
from copy import deepcopy
from datetime import datetime, timezone
from .sources import parse_date

LABELS = {"h": "主胜", "d": "平局", "a": "客胜"}


def valid_time(value):
    try:
        dt = parse_date(value)
        return dt if dt.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def prices(row):
    result = {o.get("key"): o.get("odds") for o in row.get("options", [])}
    if set(result) != set("hda") or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                        or not math.isfinite(v) or v <= 1 for v in result.values()):
        return None
    return result


def build(match, detail, as_of=None):
    now = as_of or datetime.now(timezone.utc)
    kickoff = valid_time(match.get("kickoff"))
    pregame = bool(kickoff and now < kickoff)
    context = detail.get("context") or {}
    result = {"version": "evidence-v1", "phase": "pregame" if pregame else "postgame",
              "lineups": [], "markets": [], "evidence": [], "checks": [],
              "limitations": ["赔率变化只反映定价，不能证明资金流向、热度或操控意图。",
                              "阵容、情报和盘口信号用于核查，尚未作为训练特征或概率修正。"]}
    for i, side in enumerate(("home", "away")):
        roster = next((r for r in context.get("lineups", []) if r.get("side") == side), {})
        players = roster.get("players") or []
        confirmed = (roster.get("status") == "confirmed" and len(players) == 11
                     and len({p.get("id") or p.get("name") for p in players}) == 11)
        lineup = {"side": side, "team": match[side], "confirmed": confirmed,
                  "formation": roster.get("formation") if confirmed else None,
                  "players": deepcopy(players) if confirmed else [],
                  "summary": f"{match[side]}：" + ("首发11人已确认" if confirmed else "首发待确认")}
        groups = context.get("schedule_recent") or context.get("recent") or [[], []]
        times = [valid_time(r.get("date")) for r in (groups[i] if len(groups) > i else [])
                 if r.get('date_precision') != 'date' and 'T' in (r.get('date') or '')]
        times = [t for t in times if t and kickoff and t < kickoff and t <= now]
        if times:
            hours = round((kickoff - max(times)).total_seconds()/3600, 1)
            lineup.update(rest_hours=hours)
            lineup["summary"] += f"；距最近已知比赛开球 {hours:g} 小时"
            if hours < 72:
                result["checks"].append(f"{match[side]}已知两场开球间隔不足72小时，核查轮换与恢复情况；不据此断定疲劳。")
        result["lineups"].append(lineup)
        if not confirmed:
            result["checks"].append(f"等待{match[side]}正式首发，确认后重新生成赛前版本。" if pregame
                                    else f"补齐{match[side]}实际首发与换人证据。")
    if not context.get("injuries_verified"):
        result["checks"].append("伤停和参赛资格尚未完整核实，不能将空列表视为无人缺席。")
    result['personnel'] = []
    for item in context.get('injuries', []):
        observed = valid_time(item.get('verified_at') or item.get('captured_at'))
        published = valid_time(item.get('published_at'))
        if not observed or observed > now or (published and published > now): continue
        if pregame and observed >= kickoff: continue
        result['personnel'].append(deepcopy(item))
        result['checks'].append(f"{match.get(item['side'],'球队')} · {item['name']}：{item['status']}（{item['source']}）；核查正式比赛名单。")
    for roster in context.get('estimated_lineups', []):
        if roster.get('conflicts'):
            result['checks'].append(f"{match[roster['side']]}的预计阵容与官方缺阵公告冲突：{'、'.join(roster['conflicts'])}，不可将该预计阵容当作已确认首发。")
    for code in ("had", "hhad"):
        series = []
        for row in detail.get("history", {}).get(code, []):
            when = valid_time(row.get("updated_at"))
            if kickoff and when and when < kickoff and when <= now and prices(row):
                series.append(row)
        series.sort(key=lambda row: valid_time(row["updated_at"]))
        if not series:
            continue
        first, last = series[0], series[-1]
        same_line = all(r.get("line") == first.get("line") for r in series)
        a, b = prices(first), prices(last)
        p0 = {k: 1/v/sum(1/n for n in a.values()) for k, v in a.items()}
        p1 = {k: 1/v/sum(1/n for n in b.values()) for k, v in b.items()}
        rows = []
        for k in "hda":
            seq = [prices(r)[k] for r in series]
            reversal = same_line and any(x < y for x, y in zip(seq, seq[1:])) and any(x > y for x, y in zip(seq, seq[1:]))
            label = LABELS[k] if code == "had" else {"h": "让胜", "d": "让平", "a": "让负"}[k]
            rows.append({"key": k, "label": label, "first": a[k], "last": b[k],
                         "low": min(seq) if same_line else None, "high": max(seq) if same_line else None,
                         "delta_pp": round(100*(p1[k]-p0[k]), 2) if same_line else None,
                         "reversal": reversal})
        line = last.get("line")
        giving = match["home"] if isinstance(line, (int, float)) and line < 0 else (
            match["away"] if isinstance(line, (int, float)) and line > 0 else None)
        result["markets"].append({"code": code, "label": "胜平负" if code == "had" else "让球胜平负",
                                  "count": len(series), "first_at": first["updated_at"], "last_at": last["updated_at"],
                                  "line": line, "giving_team": giving, "same_line": same_line, "rows": rows})
        if not same_line:
            result["checks"].append("让球数发生变化，不直接比较跨让球数的概率变化。")
        elif any(r["reversal"] for r in rows):
            result["checks"].append(f"{'胜平负' if code == 'had' else '让球胜平负'}存在往返调价，不能只取中间低点解释最终结果。")
    if not result["markets"]:
        result["checks"].append("缺少有效赛前盘口时间序列，无法评估调价过程。")
    if not pregame and result["markets"]:
        result["limitations"].append("这里回看的是来源标注的赛前报价时间；赛后获取的数据不冒充当时已归档的预测输入。")
    for item in detail.get("evidence", []):
        published, recorded = valid_time(item.get("published_at")), valid_time(item.get("recorded_at"))
        eligible = bool(kickoff and published and recorded and published <= now and recorded <= now
                        and published < kickoff and recorded < kickoff)
        if pregame and not eligible:
            continue
        result["evidence"].append({**deepcopy(item), "pregame_eligible": eligible,
                                   "label": "来源记录" if item.get("kind") == "fact" else "待验证观点"})
    return result


def review_link(prediction, detail, match):
    """Link fixed pregame evidence to current facts without recomputing the old forecast."""
    current = build(match, detail)
    frozen = prediction.get("intelligence") or {}
    findings = []
    for lineup in current["lineups"]:
        before = next((r for r in frozen.get("lineups", []) if r.get("side") == lineup["side"]), {})
        if before.get("confirmed") and lineup["confirmed"]:
            names = lambda row: {p.get("id") or p.get("name") for p in row.get("players", [])}
            changed = len(names(before) - names(lineup))
            findings.append(f"{lineup['team']}：归档首发与现有首发{'一致' if not changed else f'有{changed}人变化'}。")
        elif lineup["confirmed"]:
            findings.append(f"{lineup['team']}：现已确认11人首发，原预测未留存确认首发，不能倒填为赛前依据。")
    if frozen:
        findings.append(f"已关联赛前盘口分析 {len(frozen.get('markets', []))} 项、情报 {len(frozen.get('evidence', []))} 条；原方向与概率保持不变。")
    return {"pregame": frozen or None, "current": current, "findings": findings}
