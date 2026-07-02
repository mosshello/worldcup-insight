"""DeepSeek 云端对话分析：基于单场结构化盘口上下文回答用户问题。"""

from __future__ import annotations

import json
from typing import Any
from urllib import error, request

from .env_config import (
    get_deepseek_api_key,
    get_deepseek_model,
    get_deepseek_reasoning_effort,
    get_deepseek_thinking_enabled,
)

DEEPSEEK_CHAT_URL = "https://api.deepseek.com/v1/chat/completions"
MAX_HISTORY_TURNS = 6

SYSTEM_PROMPT = """你是世界杯竞彩赛前分析助手。你只能基于用户提供的结构化数据进行分析，不能编造赛果、伤停或新闻。
职责：
1. 帮助用户评估「冷门 / 转向 / 爆冷」假设是否有数据支撑
2. 对比体彩 SP 走势、外网概率、多玩法定价（总进球 / 半全场 / 凯利）与赛前情报
3. 明确指出：数据支持点、数据矛盾点、信息缺口
4. 不提供具体投注金额、购彩建议或赛果保证

输出要求：简洁中文，先给 1–2 句结论，再分点列依据；若数据不足请直说。"""


class AiAnalystError(RuntimeError):
    """AI 分析请求失败。"""


def get_analyze_status() -> dict[str, Any]:
    """检查 DeepSeek 是否已配置（不暴露密钥）。"""
    return {
        "configured": bool(get_deepseek_api_key()),
        "provider": "deepseek",
        "model": get_deepseek_model(),
    }


def build_match_context(fusion: dict[str, Any]) -> str:
    """将融合预测载荷压缩为 LLM 可读上下文。"""
    prediction = fusion.get("prediction") or {}
    score = fusion.get("score_prediction") or {}
    shift = fusion.get("direction_shift") or {}
    pool = fusion.get("pool_analysis") or {}
    intel = fusion.get("match_intelligence") or {}
    context = fusion.get("context_analysis") or {}
    match = prediction.get("match") or {}

    home = prediction.get("home") or score.get("home") or match.get("home") or "—"
    away = prediction.get("away") or score.get("away") or match.get("away") or "—"
    kickoff = score.get("kickoff_beijing") or match.get("kickoff_beijing") or "—"

    probs = prediction.get("probabilities") or {}
    foreign = (prediction.get("foreign") or {}).get("probabilities") or {}
    deltas = fusion.get("probability_deltas_pp") or {}

    lines = [
        f"## 场次",
        f"- 对阵：{home} vs {away}",
        f"- 体彩编号：{score.get('match_id') or prediction.get('match_id') or '—'}",
        f"- 开赛（北京）：{kickoff}",
        f"- 销售日：{score.get('business_date') or match.get('business_date') or '—'}",
        "",
        "## 模型预测",
        f"- 方向：{score.get('direction') or prediction.get('direction') or '—'}（信心 {score.get('confidence') or '—'}）",
        f"- 比分：{score.get('predicted_score') or '—'}",
        f"- 说明：{score.get('direction_note') or prediction.get('direction_note') or '—'}",
        "",
        "## 体彩胜平负去水概率",
        f"- 主胜 {probs.get('home', 0) * 100:.1f}% · 平 {probs.get('draw', 0) * 100:.1f}% · 客胜 {probs.get('away', 0) * 100:.1f}%",
    ]

    if foreign:
        lines.extend(
            [
                "",
                "## 外网辅盘概率（" + str(fusion.get("foreign_source_resolved") or "外网") + "）",
                f"- 主胜 {foreign.get('home', 0) * 100:.1f}% · 平 {foreign.get('draw', 0) * 100:.1f}% · 客胜 {foreign.get('away', 0) * 100:.1f}%",
            ]
        )
        if deltas:
            lines.append(
                "- 与体彩差值（pp）："
                + " · ".join(f"{key} {value:+.1f}" for key, value in deltas.items())
            )
        alerts = fusion.get("probability_delta_alerts") or []
        if alerts:
            lines.append(f"- 高亮告警项：{', '.join(alerts)}")

    if shift.get("available"):
        lines.extend(
            [
                "",
                "## 水位转向 / 冷门信号",
                f"- 严重度：{shift.get('severity')}",
                f"- 初盘首选：{shift.get('opening_label') or '—'} → 现盘首选：{shift.get('current_label') or '—'}",
            ]
        )
        if shift.get("upset_candidates"):
            lines.append(f"- 冷门受热：{'、'.join(shift['upset_candidates'])}")
        for alert in shift.get("alerts") or []:
            lines.append(f"- {alert}")

    shift_pred = fusion.get("shift_prediction") or score.get("shift_prediction") or {}
    if shift_pred.get("active"):
        initial = shift_pred.get("initial") or {}
        adjusted = shift_pred.get("adjusted") or {}
        lines.extend(
            [
                "",
                "## 变盘策略对照（告警为主，仅供参考）",
                f"- {initial.get('label') or '初盘'}：{initial.get('direction') or '—'} · 比分 {initial.get('predicted_score') or '—'}",
                f"- {adjusted.get('label') or '变盘后'}：{adjusted.get('direction') or '—'} · 比分 {adjusted.get('predicted_score') or '—'}",
            ]
        )
        if shift_pred.get("note"):
            lines.append(f"- 说明：{shift_pred['note']}")

    pool_lines = pool.get("summary_bullets") or []
    if pool_lines:
        lines.extend(["", "## 多玩法分析"])
        lines.extend(f"- {line}" for line in pool_lines[:8])

    kelly = pool.get("kelly_had") or []
    if kelly:
        kelly_text = " · ".join(
            f"{row.get('label')} EV {row.get('expected_value', 0) * 100:.1f}%"
            for row in kelly[:3]
            if row.get("expected_value") is not None
        )
        if kelly_text:
            lines.append(f"- 凯利 EV：{kelly_text}")

    intel_bullets = intel.get("summary_bullets") or []
    if intel_bullets:
        lines.extend(["", "## 赛前情报"])
        lines.extend(f"- {line}" for line in intel_bullets[:6])

    if context.get("context_available"):
        lines.extend(
            [
                "",
                "## 综合上下文信号",
                f"- 综合首选：{context.get('context_pick')}（信心 {context.get('context_confidence')}，边际 {context.get('context_edge')}）",
            ]
        )

    fusion_analysis = prediction.get("analysis") or []
    if fusion_analysis:
        lines.extend(["", "## 融合分析要点"])
        lines.extend(f"- {line}" for line in fusion_analysis[:5])

    finished = fusion.get("finished_review") or {}
    if finished or score.get("card_type") == "finished":
        lines.extend(["", "## 完场复盘"])
        lines.append(f"- 实际赛果：{finished.get('actual_had') or score.get('actual_had') or '—'}")
        lines.append(f"- 实际比分：{finished.get('actual_score') or score.get('actual_score') or '—'}")
        lines.append(
            f"- 结算：{finished.get('settlement_status') or score.get('settlement_status') or '—'}"
            f" · 盈亏 {finished.get('total_pnl') if finished.get('total_pnl') is not None else score.get('total_pnl', '—')} 元"
        )
        had_won = finished.get("had_won") if "had_won" in finished else score.get("had_won")
        crs_won = finished.get("crs_won") if "crs_won" in finished else score.get("crs_won")
        if had_won is not None or crs_won is not None:
            lines.append(f"- 方向 {'命中' if had_won else '偏差'} · 比分 {'命中' if crs_won else '偏差'}")

    return "\n".join(lines)


def build_finished_fusion_payload(card: dict[str, Any]) -> dict[str, Any]:
    """将完场复盘卡片转为 AI 上下文结构。"""
    return {
        "score_prediction": card,
        "prediction": {
            "home": card.get("home"),
            "away": card.get("away"),
            "match_id": card.get("match_id"),
            "direction": card.get("direction"),
            "direction_note": card.get("direction_note"),
            "probabilities": {},
            "analysis": [],
        },
        "finished_review": {
            "actual_had": card.get("actual_had"),
            "actual_score": card.get("actual_score"),
            "had_won": card.get("had_won"),
            "crs_won": card.get("crs_won"),
            "total_pnl": card.get("total_pnl"),
            "settlement_status": card.get("settlement_status"),
        },
    }


def _normalize_history(history: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    if not history:
        return []
    cleaned: list[dict[str, str]] = []
    for item in history[-MAX_HISTORY_TURNS * 2 :]:
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            cleaned.append({"role": role, "content": content})
    return cleaned[-MAX_HISTORY_TURNS * 2 :]


def _build_request_payload(model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": 2000,
        "stream": False,
    }
    reasoning = get_deepseek_reasoning_effort()
    thinking = get_deepseek_thinking_enabled()
    if reasoning or thinking or model.startswith("deepseek-v4"):
        payload["reasoning_effort"] = reasoning or "high"
        payload["thinking"] = {"type": "enabled"}
    return payload


def _call_deepseek(
    messages: list[dict[str, str]],
    *,
    api_key: str,
    model: str,
    timeout: float = 120.0,
) -> str:
    payload = _build_request_payload(model, messages)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        DEEPSEEK_CHAT_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise AiAnalystError(f"DeepSeek 请求失败（HTTP {exc.code}）") from None
    except (error.URLError, TimeoutError, OSError) as exc:
        raise AiAnalystError("DeepSeek 网络请求失败，请稍后重试") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AiAnalystError("DeepSeek 返回了无效 JSON") from exc

    choices = parsed.get("choices") or []
    if not choices:
        raise AiAnalystError("DeepSeek 未返回有效回复")
    message = choices[0].get("message") or {}
    content = str(message.get("content") or "").strip()
    if not content:
        raise AiAnalystError("DeepSeek 返回空内容")
    return content


def chat_match_analysis(
    fusion: dict[str, Any],
    question: str,
    *,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """基于已加载的融合预测上下文，向 DeepSeek 发起对话分析。"""
    api_key = get_deepseek_api_key()
    if not api_key:
        return {
            "success": False,
            "error": "未配置 DEEPSEEK_API_KEY，请在项目根目录 .env 中设置",
            "configured": False,
        }

    question = (question or "").strip()
    if not question:
        return {"success": False, "error": "请输入分析问题"}

    context_text = build_match_context(fusion)
    model = get_deepseek_model()
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "以下为本场比赛的结构化分析数据，请仅基于这些数据回答后续问题。\n\n"
                f"{context_text}"
            ),
        },
        {"role": "assistant", "content": "已收到本场结构化数据，请提出你的分析问题或冷门假设。"},
    ]
    messages.extend(_normalize_history(history))
    messages.append({"role": "user", "content": question})

    try:
        reply = _call_deepseek(messages, api_key=api_key, model=model)
    except AiAnalystError as exc:
        return {"success": False, "error": str(exc), "configured": True}

    score = fusion.get("score_prediction") or fusion.get("prediction") or {}
    return {
        "success": True,
        "reply": reply,
        "model": model,
        "provider": "deepseek",
        "match_id": score.get("match_id") or fusion.get("prediction", {}).get("match_id"),
        "context_chars": len(context_text),
    }
