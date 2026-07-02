"""生成带累计指标和历史记录的 Jun 风格 GitHub Pages 看板。"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from worldcup_mvp.daily_bet import summarize_ledger

SOURCE = ROOT / "data" / "daily_bets.json"
AI_REVIEWS = ROOT / "data" / "ai_reviews.json"
DOCS = ROOT / "docs"

FLAT_CSS = """
:root{--bg:#f3f7fc;--soft:#ffffff;--card:#ffffff;--card2:#eef5ff;--line:#d9e4f2;--text:#152238;--muted:#63738a;--green:#12b981;--blue:#2878ff;--orange:#ff9f1c;--shadow:none}
body{background:#f3f7fc;color:var(--text)}
.page{padding-top:32px}
.eyebrow{color:#2878ff}
.subtitle,.panel-head p,.match-card p{color:var(--muted)}
.meta div,.summary div,.panel,.strategy-card{background:#fff;border:1px solid var(--line);box-shadow:none}
.meta div{border-radius:10px}
.summary div{border-radius:12px;border-top:4px solid #2878ff}
.summary div:nth-child(2){border-top-color:#12b981}
.summary div:nth-child(3){border-top-color:#8b5cf6}
.summary div:nth-child(4){border-top-color:#ff9f1c}
.summary strong{color:#152238}
.profit{color:#07966b!important}
.panel{border-radius:14px}
.strategy-card{border-radius:12px}
.strategy-card.primary{background:#f2fff9;border:2px solid #66d6ad}
.match-card{background:#eef5ff;border:1px solid #d6e6ff;border-radius:10px}
.stats div{background:#f5f8fc;border:1px solid #e4ebf4;border-radius:8px}
.tag.live,.tag.single{background:#dff8ee;color:#087a58}
.tag.combo{background:#e3efff;color:#175dcc}
.pick-side b,.hero-pick{color:#087a58}
.match-num{color:#2878ff}
.status{color:#b96800}
th{background:#f5f8fc;color:#52647b}
th,td{border-color:#e2e9f2}
tbody tr:hover{background:#f8fbff}
.note{background:#fff8e8;border-left-color:#ff9f1c;padding:12px 14px;border-radius:0 8px 8px 0;color:#59687b}
footer{color:#74839a}
"""


def _escape(value: object) -> str:
    return html.escape(str(value if value is not None else "—"))


def _entry_panel(item: dict) -> str:
    single = item.get("single") or {"stake": item.get("stake", 0), "leg": item}
    parlay = item.get("parlay")
    leg = single.get("leg") or {}
    parlay_html = '<div class="empty">没有组合 SP 达到 2.00 的模型候选，今日不生成二串一。</div>'
    if parlay:
        legs = "".join(
            f'''<div class="match-card"><div><span class="match-num">{_escape(row.get("match_num"))}</span><h3>{_escape(row.get("home"))} vs {_escape(row.get("away"))}</h3><p>{_escape(row.get("kickoff_beijing"))}</p></div><div class="pick-side"><b>{_escape(row.get("pick"))}</b><span>SP {float(row.get("odds", 0)):.2f}</span><small>去水 {float(row.get("market_probability", 0))*100:.1f}%</small></div></div>'''
            for row in parlay.get("legs", [])
        )
        parlay_html = f'''<div class="strategy-head"><div><span class="tag combo">2 串 1 · ≥2倍</span><h2>模型最优组合</h2></div><div class="amount">¥{float(parlay.get("stake", 0)):.0f}</div></div>{legs}<div class="stats"><div><span>组合 SP</span><strong>{float(parlay.get("combined_odds", 0)):.2f}</strong></div><div><span>联合概率</span><strong>{float(parlay.get("combined_probability", 0))*100:.1f}%</strong></div><div><span>全中返还</span><strong>¥{float(parlay.get("potential_return", 0)):.2f}</strong></div></div>'''
    return f'''<section class="panel"><div class="panel-head"><div><p class="eyebrow">{_escape(item.get("date"))} · DAILY PLAN</p><h2>今日自动策略</h2><p>总模拟预算 ¥{float(item.get("total_stake", item.get("stake", 0))):.0f}，每日刷新后自动覆盖当天方案。</p></div><span class="tag live">{("已结算" if item.get("status") == "settled" else "待结算")}</span></div><div class="strategy-grid"><article class="strategy-card primary"><div class="strategy-head"><div><span class="tag single">稳健单场</span><h2>{_escape(leg.get("home"))} vs {_escape(leg.get("away"))}</h2></div><div class="amount">¥{float(single.get("stake", 0)):.0f}</div></div><div class="hero-pick">{_escape(leg.get("pick"))}<small>SP {float(leg.get("odds", 0)):.2f}</small></div><div class="stats"><div><span>去水概率</span><strong>{float(leg.get("market_probability", 0))*100:.1f}%</strong></div><div><span>命中返还</span><strong>¥{float(single.get("potential_return", 0)):.2f}</strong></div><div><span>开赛时间</span><strong class="time">{_escape(leg.get("kickoff_beijing"))}</strong></div></div></article><article class="strategy-card">{parlay_html}</article></div></section>'''


def _ai_review_panel() -> str:
    if not AI_REVIEWS.exists():
        return ""
    try:
        payload = json.loads(AI_REVIEWS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    reviews = [
        item
        for item in (payload.get("reviews") or {}).values()
        if isinstance(item, dict) and item.get("success") and item.get("reply")
    ]
    if not reviews:
        return ""
    reviews.sort(key=lambda item: item.get("generated_at") or "", reverse=True)
    cards = []
    for item in reviews[:6]:
        title = f"{_escape(item.get('home'))} vs {_escape(item.get('away'))}"
        meta = f"预测 {_escape(item.get('direction'))} / {_escape(item.get('predicted_score'))} · 实际 {_escape(item.get('actual_had'))} / {_escape(item.get('actual_score'))}"
        body = _escape(item.get("reply")).replace("\n", "<br>")
        cards.append(
            f'''<article class="strategy-card"><div class="strategy-head"><div><span class="tag combo">AI 复盘</span><h2>{title}</h2></div></div><p class="note">{meta}</p><div class="note">{body}</div></article>'''
        )
    return f'''<section class="panel"><div class="panel-head"><div><p class="eyebrow">POST-MATCH REVIEW</p><h2>完场偏差自动复盘</h2><p>由 GitHub Actions 调用 DeepSeek 生成，密钥仅存于 Secrets，不进入公开仓库。</p></div></div><div class="strategy-grid">{"".join(cards)}</div></section>'''


def _history_rows(entries: list[dict]) -> str:
    rows = []
    for item in entries:
        parlay = item.get("parlay") or {}
        legs = " × ".join(
            f"{row.get('home')} vs {row.get('away')} {row.get('pick')}"
            for row in parlay.get("legs", [])
        ) or "无合格二串一"
        settled = item.get("status") == "settled"
        pnl = f"¥{float(item.get('realized_pnl') or 0):+.2f}" if settled else "—"
        rows.append(
            f'''<tr><td>{_escape(item.get("date"))}</td><td>¥{float(item.get("total_stake", item.get("stake", 0))):.0f}</td><td>{_escape(legs)}</td><td>{float(parlay.get("combined_odds", 0)):.2f}</td><td><span class="status">{("已结算" if settled else "待结算")}</span></td><td>{pnl}</td></tr>'''
        )
    return "".join(rows)


def main() -> int:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    entries = payload.get("entries", [])
    latest = entries[0] if entries else {}
    summary = summarize_ledger(payload)
    panel = _entry_panel(latest) if latest else ""
    ai_panel = _ai_review_panel()
    history = _history_rows(entries)
    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DreamV 世界杯自动策略</title><style>
:root{{--bg:#0b1220;--soft:#121b2e;--card:#162033;--card2:#1b2740;--line:rgba(255,255,255,.08);--text:#edf2ff;--muted:#94a3b8;--green:#22c55e;--blue:#38bdf8;--orange:#f59e0b;--shadow:0 18px 50px rgba(0,0,0,.35)}}*{{box-sizing:border-box}}body{{margin:0;font-family:"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;background:radial-gradient(circle at top left,rgba(34,197,94,.12),transparent 28%),radial-gradient(circle at top right,rgba(56,189,248,.12),transparent 24%),var(--bg);color:var(--text);min-height:100vh}}.page{{max-width:1280px;margin:auto;padding:28px}}.hero{{display:flex;justify-content:space-between;align-items:end;gap:24px;margin:12px 0 28px}}.eyebrow{{margin:0 0 8px;color:var(--green);letter-spacing:.12em;font-size:12px;font-weight:800}}h1{{font-size:clamp(32px,5vw,52px);margin:0}}.subtitle,.panel-head p,.match-card p{{color:var(--muted)}}.meta{{display:grid;grid-template-columns:repeat(2,minmax(150px,1fr));gap:12px}}.meta div{{background:rgba(255,255,255,.04);border:1px solid var(--line);padding:14px 16px;border-radius:14px}}.meta span,.stats span{{display:block;color:var(--muted);font-size:12px;margin-bottom:6px}}.summary{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:24px}}.summary div{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px}}.summary span{{display:block;color:var(--muted);font-size:12px}}.summary strong{{display:block;font-size:26px;margin-top:8px}}.profit{{color:#86efac}}.panel{{background:rgba(18,27,46,.9);border:1px solid var(--line);border-radius:22px;padding:22px;box-shadow:var(--shadow);margin-bottom:24px}}.panel-head,.strategy-head,.match-card{{display:flex;justify-content:space-between;gap:16px;align-items:center}}.panel-head h2,.strategy-head h2{{margin:4px 0}}.strategy-grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:20px}}.strategy-card{{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px}}.strategy-card.primary{{border-color:rgba(34,197,94,.4);background:linear-gradient(145deg,rgba(34,197,94,.08),var(--card) 45%)}}.tag{{display:inline-flex;padding:5px 11px;border-radius:999px;font-size:12px;font-weight:800}}.tag.live,.tag.single{{background:rgba(34,197,94,.18);color:#86efac}}.tag.combo{{background:rgba(56,189,248,.18);color:#7dd3fc}}.amount{{font-size:28px;font-weight:800}}.hero-pick{{font-size:38px;font-weight:900;margin:26px 0}}.hero-pick small{{font-size:16px;color:var(--muted);margin-left:12px}}.stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.stats div{{background:rgba(255,255,255,.035);border-radius:12px;padding:13px}}.stats strong{{font-size:20px}}.stats .time{{font-size:12px;line-height:1.5}}.match-card{{background:var(--card2);padding:14px;margin:10px 0;border-radius:14px}}.match-card h3{{font-size:16px;margin:5px 0}}.match-card p{{font-size:12px;margin:0}}.match-num{{font-size:11px;color:var(--blue)}}.pick-side{{text-align:right}}.pick-side b,.pick-side span,.pick-side small{{display:block}}.pick-side b{{color:#86efac;font-size:18px}}.pick-side span,.pick-side small{{color:var(--muted);margin-top:4px}}.table-wrap{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;min-width:760px}}th,td{{padding:13px 12px;border-bottom:1px solid var(--line);text-align:left}}th{{color:var(--muted);font-size:12px}}.status{{color:#fcd34d}}.note{{border-left:3px solid var(--orange);padding:4px 0 4px 14px;color:var(--muted);line-height:1.7}}.empty{{color:var(--muted);padding:25px 0}}footer{{color:var(--muted);text-align:center;padding:8px 0 30px}}@media(max-width:800px){{.hero{{display:block}}.meta{{margin-top:20px}}.strategy-grid{{grid-template-columns:1fr}}.summary{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:520px){{.page{{padding:16px}}.stats,.meta,.summary{{grid-template-columns:1fr}}}}
</style><link rel="stylesheet" href="flat.css"></head><body><main class="page"><header class="hero"><div><p class="eyebrow">WORLD CUP INSIGHT · DREAMV</p><h1>世界杯AI+内部模型策略</h1><p class="subtitle">模型候选 · 组合 SP ≥ 2.00 · 累计盈亏 · 历史账本</p></div><div class="meta"><div><span>运行状态</span><strong>每日北京时间 20:05</strong></div><div><span>最新账本</span><strong>{_escape(latest.get("recorded_at"))}</strong></div></div></header><section class="summary"><div><span>累计总投入</span><strong>¥{summary['total_invested']:.2f}</strong></div><div><span>已结算盈利</span><strong class="profit">¥{summary['realized_profit']:+.2f}</strong></div><div><span>未结算潜在盈利</span><strong>¥{summary['open_potential_profit']:.2f}</strong></div><div><span>历史方案</span><strong>{summary['entry_count']} 天</strong></div></section>{panel}{ai_panel}<section class="panel"><div class="panel-head"><div><p class="eyebrow">HISTORY</p><h2>历史记录</h2><p>待结算记录不计入已实现盈利。</p></div></div><div class="table-wrap"><table><thead><tr><th>日期</th><th>总投入</th><th>二串一</th><th>组合 SP</th><th>状态</th><th>实际盈亏</th></tr></thead><tbody>{history}</tbody></table></div></section><footer>仅供数据分析与模拟记账，不构成投注建议；历史表现不保证未来结果。</footer></main></body></html>'''
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    (DOCS / "flat.css").write_text(FLAT_CSS.strip() + "\n", encoding="utf-8")
    (DOCS / "daily_bets.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if AI_REVIEWS.exists():
        (DOCS / "ai_reviews.json").write_text(AI_REVIEWS.read_text(encoding="utf-8"), encoding="utf-8")
    (DOCS / "CNAME").write_text("dreamv.top\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
