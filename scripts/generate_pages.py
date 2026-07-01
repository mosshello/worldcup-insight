"""生成 GitHub Pages 静态模拟投注看板。"""

from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data" / "daily_bets.json"
DOCS = ROOT / "docs"


def main() -> int:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    entries = payload.get("entries", [])
    cards = []
    for item in entries:
        probability = float(item.get("market_probability", 0)) * 100
        cards.append(
            f"""<article class=\"card\"><div class=\"date\">{html.escape(item['date'])}</div>
<h2>{html.escape(str(item['home']))} vs {html.escape(str(item['away']))}</h2>
<div class=\"pick\">{html.escape(str(item['pick']))} · SP {item['odds']:.2f}</div>
<div class=\"grid\"><span>模拟本金<strong>¥{item['stake']:.0f}</strong></span><span>去水概率<strong>{probability:.1f}%</strong></span><span>命中返还<strong>¥{item['potential_return']:.0f}</strong></span></div>
<p>{html.escape(str(item['kickoff_beijing']))} · {html.escape(str(item['status']))}</p></article>"""
        )
    page = """<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>DreamV 每日稳健方向</title><style>
:root{color-scheme:dark;font-family:Inter,system-ui,sans-serif;background:#07111f;color:#eaf2ff}body{margin:0;background:radial-gradient(circle at top,#183454,#07111f 55%);min-height:100vh}.wrap{max-width:920px;margin:auto;padding:56px 20px}header{margin-bottom:36px}h1{font-size:clamp(34px,7vw,64px);margin:0 0 12px}.sub,p{color:#9fb2ca}.badge{display:inline-block;color:#79e6b3;background:#10352f;padding:7px 12px;border-radius:999px}.card{background:#0d1d30;border:1px solid #25415f;border-radius:20px;padding:24px;margin:18px 0;box-shadow:0 18px 48px #0005}.date{color:#79e6b3}.pick{font-size:28px;font-weight:800;margin:18px 0}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.grid span{background:#132941;padding:15px;border-radius:12px;color:#9fb2ca}.grid strong{display:block;color:#fff;font-size:22px;margin-top:5px}.warn{border-left:3px solid #ffb44c;padding-left:14px}@media(max-width:600px){.grid{grid-template-columns:1fr}}
</style></head><body><main class=\"wrap\"><header><span class=\"badge\">每日一场 · 固定 1000</span><h1>DreamV 稳健方向账本</h1><p class=\"sub\">从当日高信心、内外盘同向场次中，只选胜平负去水概率最高的一场。</p></header>""" + "".join(cards) + """<p class=\"warn\">仅为公开模拟记账，不会执行真实投注，不构成投注建议。赔率与概率会变化，历史结果不保证未来表现。</p></main></body></html>"""
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    (DOCS / "daily_bets.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (DOCS / "CNAME").write_text("dreamv.top\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
