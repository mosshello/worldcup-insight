"""生成参考 Jun 控制台视觉的 GitHub Pages 静态看板。"""

from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data" / "daily_bets.json"
DOCS = ROOT / "docs"


def _escape(value: object) -> str:
    return html.escape(str(value if value is not None else "—"))


def _entry_panel(item: dict) -> str:
    single = item.get("single") or {
        "stake": item.get("stake", 0),
        "potential_return": item.get("potential_return", 0),
        "leg": item,
    }
    parlay = item.get("parlay")
    leg = single.get("leg") or {}
    single_probability = float(leg.get("market_probability", item.get("market_probability", 0))) * 100
    parlay_html = '<div class="empty">当日符合条件的场次不足两场，未生成二串一。</div>'
    if parlay:
        legs = "".join(
            f'''<div class="match-card"><div><span class="match-num">{_escape(row.get("match_num"))}</span><h3>{_escape(row.get("home"))} vs {_escape(row.get("away"))}</h3><p>{_escape(row.get("kickoff_beijing"))}</p></div><div class="pick-side"><b>{_escape(row.get("pick"))}</b><span>SP {float(row.get("odds", 0)):.2f}</span><small>去水 {float(row.get("market_probability", 0))*100:.1f}%</small></div></div>'''
            for row in parlay.get("legs", [])
        )
        parlay_html = f'''<div class="strategy-head"><div><span class="tag combo">2 串 1</span><h2>自动组合</h2></div><div class="amount">¥{float(parlay.get("stake", 0)):.0f}</div></div>{legs}<div class="stats"><div><span>组合 SP</span><strong>{float(parlay.get("combined_odds", 0)):.2f}</strong></div><div><span>组合概率</span><strong>{float(parlay.get("combined_probability", 0))*100:.1f}%</strong></div><div><span>全中返还</span><strong>¥{float(parlay.get("potential_return", 0)):.2f}</strong></div></div>'''
    return f'''<section class="panel"><div class="panel-head"><div><p class="eyebrow">{_escape(item.get("date"))} · DAILY PLAN</p><h2>今日自动策略</h2><p>总模拟预算 ¥{float(item.get("total_stake", item.get("stake", 0))):.0f}，每日刷新后自动覆盖当天方案。</p></div><span class="tag live">自动运行</span></div><div class="strategy-grid"><article class="strategy-card primary"><div class="strategy-head"><div><span class="tag single">稳健单场</span><h2>{_escape(leg.get("home"))} vs {_escape(leg.get("away"))}</h2></div><div class="amount">¥{float(single.get("stake", 0)):.0f}</div></div><div class="hero-pick">{_escape(leg.get("pick"))}<small>SP {float(leg.get("odds", 0)):.2f}</small></div><div class="stats"><div><span>去水概率</span><strong>{single_probability:.1f}%</strong></div><div><span>命中返还</span><strong>¥{float(single.get("potential_return", 0)):.2f}</strong></div><div><span>开赛时间</span><strong class="time">{_escape(leg.get("kickoff_beijing"))}</strong></div></div></article><article class="strategy-card">{parlay_html}</article></div></section>'''


def main() -> int:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    entries = payload.get("entries", [])
    latest = entries[0] if entries else {}
    panels = "".join(_entry_panel(item) for item in entries)
    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DreamV 世界杯自动策略</title><style>
:root{{--bg:#0b1220;--soft:#121b2e;--card:#162033;--card2:#1b2740;--line:rgba(255,255,255,.08);--text:#edf2ff;--muted:#94a3b8;--green:#22c55e;--blue:#38bdf8;--orange:#f59e0b;--shadow:0 18px 50px rgba(0,0,0,.35)}}*{{box-sizing:border-box}}body{{margin:0;font-family:"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;background:radial-gradient(circle at top left,rgba(34,197,94,.12),transparent 28%),radial-gradient(circle at top right,rgba(56,189,248,.12),transparent 24%),var(--bg);color:var(--text);min-height:100vh}}.page{{max-width:1280px;margin:auto;padding:28px}}.hero{{display:flex;justify-content:space-between;align-items:end;gap:24px;margin:12px 0 28px}}.eyebrow{{margin:0 0 8px;color:var(--green);letter-spacing:.12em;font-size:12px;font-weight:800}}h1{{font-size:clamp(32px,5vw,52px);margin:0}}.subtitle,.panel-head p,.match-card p{{color:var(--muted)}}.meta{{display:grid;grid-template-columns:repeat(2,minmax(150px,1fr));gap:12px}}.meta div{{background:rgba(255,255,255,.04);border:1px solid var(--line);padding:14px 16px;border-radius:14px}}.meta span,.stats span{{display:block;color:var(--muted);font-size:12px;margin-bottom:6px}}.panel{{background:rgba(18,27,46,.9);border:1px solid var(--line);border-radius:22px;padding:22px;box-shadow:var(--shadow);margin-bottom:24px}}.panel-head,.strategy-head,.match-card{{display:flex;justify-content:space-between;gap:16px;align-items:center}}.panel-head h2,.strategy-head h2{{margin:4px 0}}.strategy-grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:20px}}.strategy-card{{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px}}.strategy-card.primary{{border-color:rgba(34,197,94,.4);background:linear-gradient(145deg,rgba(34,197,94,.08),var(--card) 45%)}}.tag{{display:inline-flex;padding:5px 11px;border-radius:999px;font-size:12px;font-weight:800}}.tag.live,.tag.single{{background:rgba(34,197,94,.18);color:#86efac}}.tag.combo{{background:rgba(56,189,248,.18);color:#7dd3fc}}.amount{{font-size:28px;font-weight:800}}.hero-pick{{font-size:38px;font-weight:900;margin:26px 0}}.hero-pick small{{font-size:16px;color:var(--muted);margin-left:12px}}.stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.stats div{{background:rgba(255,255,255,.035);border-radius:12px;padding:13px}}.stats strong{{font-size:20px}}.stats .time{{font-size:12px;line-height:1.5}}.match-card{{background:var(--card2);padding:14px;margin:10px 0;border-radius:14px}}.match-card h3{{font-size:16px;margin:5px 0}}.match-card p{{font-size:12px;margin:0}}.match-num{{font-size:11px;color:var(--blue)}}.pick-side{{text-align:right}}.pick-side b,.pick-side span,.pick-side small{{display:block}}.pick-side b{{color:#86efac;font-size:18px}}.pick-side span,.pick-side small{{color:var(--muted);margin-top:4px}}.note{{border-left:3px solid var(--orange);padding:4px 0 4px 14px;color:var(--muted);line-height:1.7}}.empty{{color:var(--muted);padding:25px 0}}footer{{color:var(--muted);text-align:center;padding:8px 0 30px}}@media(max-width:800px){{.hero{{display:block}}.meta{{margin-top:20px}}.strategy-grid{{grid-template-columns:1fr}}}}@media(max-width:520px){{.page{{padding:16px}}.stats{{grid-template-columns:1fr}}.meta{{grid-template-columns:1fr}}}}
</style></head><body><main class="page"><header class="hero"><div><p class="eyebrow">WORLD CUP INSIGHT · DREAMV</p><h1>世界杯自动策略台</h1><p class="subtitle">Jun 风格 · 稳健单场 · 每日至少一组二串一 · 公开模拟账本</p></div><div class="meta"><div><span>运行状态</span><strong>每日北京时间 20:05</strong></div><div><span>最新账本</span><strong>{_escape(latest.get("recorded_at"))}</strong></div></div></header>{panels}<section class="panel"><h2>它是怎样自动运行的？</h2><p class="note">GitHub Actions 每日拉取可售赛事，筛出高信心且内外盘同向方向，按去水概率排序；第一名投入 600 元模拟单场，前两名投入 400 元模拟二串一，随后自动保存 JSON 并发布本站。你不需要每天修改。若体彩拒绝海外 Runner 访问，网站会保留最后一次已验证账本并明确不冒充实时数据。</p><p class="note">当前没有合法投注平台下单 API，因此不会真实扣款或自动购彩。接入真实下单必须由平台提供正式授权、余额与风控接口。</p></section><footer>仅供数据分析与模拟记账，不构成投注建议；历史表现不保证未来结果。</footer></main></body></html>'''
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    (DOCS / "daily_bets.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (DOCS / "CNAME").write_text("dreamv.top\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
