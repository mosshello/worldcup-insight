"""完场偏差场次的 AI 复盘缓存：服务端自动生成，前端只读展示。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .ai_analyst import build_finished_fusion_payload, chat_match_analysis, get_analyze_status
from .env_config import get_deepseek_api_key
from .finished_review import list_finished_review_cards

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REVIEW_FILE = PROJECT_ROOT / "data" / "ai_reviews.json"
SCHEMA_VERSION = 1

FINISHED_DEVIATION_PROMPT = (
    "本场已完场且预测与实际存在偏差。请仅基于提供的结构化数据复盘："
    "1) 偏差属于方向、比分还是两者；"
    "2) 赛前 SP/概率/转向信号中哪些支持了实际赛果、哪些误导了预测；"
    "3) 若重来一次，模型应优先关注哪一条信号。"
    "不要编造未提供的事实，不要给投注建议。"
)


def _now_iso() -> str:
    return datetime.now(BEIJING_TZ).replace(microsecond=0).isoformat()


def _default_payload() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": _now_iso(),
        "reviews": {},
    }


def load_ai_reviews() -> dict[str, Any]:
    if not REVIEW_FILE.exists():
        return _default_payload()
    try:
        with REVIEW_FILE.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return _default_payload()
    if not isinstance(payload.get("reviews"), dict):
        payload["reviews"] = {}
    return payload


def _save_ai_reviews(payload: dict[str, Any]) -> None:
    REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = _now_iso()
    payload.setdefault("schema_version", SCHEMA_VERSION)
    with REVIEW_FILE.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _needs_auto_review(card: dict[str, Any]) -> bool:
    if card.get("card_type") != "finished":
        return False
    if card.get("settlement_status") != "settled":
        return False
    return card.get("had_won") is False or card.get("crs_won") is False


def _review_record_from_card(
    card: dict[str, Any],
    *,
    question: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "match_id": str(card.get("match_id") or ""),
        "home": card.get("home"),
        "away": card.get("away"),
        "generated_at": _now_iso(),
        "trigger": "finished_deviation",
        "question": question,
        "reply": result.get("reply"),
        "success": bool(result.get("success")),
        "error": result.get("error"),
        "model": result.get("model"),
        "auto": True,
        "actual_had": card.get("actual_had"),
        "actual_score": card.get("actual_score"),
        "direction": card.get("direction"),
        "predicted_score": card.get("predicted_score"),
        "had_won": card.get("had_won"),
        "crs_won": card.get("crs_won"),
    }


def generate_review_for_card(
    card: dict[str, Any],
    *,
    question: str = FINISHED_DEVIATION_PROMPT,
    force: bool = False,
) -> dict[str, Any]:
    """为单场生成复盘并写入缓存；无 API Key 时跳过。"""
    if not get_deepseek_api_key():
        return {
            "success": False,
            "skipped": True,
            "error": "未配置 DEEPSEEK_API_KEY",
            "configured": False,
        }

    match_id = str(card.get("match_id") or "")
    if not match_id:
        return {"success": False, "error": "缺少 match_id"}

    payload = load_ai_reviews()
    reviews: dict[str, Any] = payload["reviews"]
    if not force and reviews.get(match_id, {}).get("success") and reviews[match_id].get("reply"):
        cached = dict(reviews[match_id])
        cached["success"] = True
        cached["cached"] = True
        return cached

    fusion = build_finished_fusion_payload(card)
    result = chat_match_analysis(fusion, question)
    record = _review_record_from_card(card, question=question, result=result)
    reviews[match_id] = record
    payload["reviews"] = reviews
    _save_ai_reviews(payload)
    return record


def generate_review_for_match_id(match_id: str, *, force: bool = False) -> dict[str, Any]:
    target = str(match_id)
    for card in list_finished_review_cards(lookback_days=14):
        if str(card.get("match_id")) == target:
            return generate_review_for_card(card, force=force)
    return {"success": False, "error": f"未找到场次 {target} 的完场复盘卡片"}


def auto_review_finished_deviations(*, lookback_days: int = 7, force: bool = False) -> dict[str, Any]:
    """扫描近几日偏差场次，批量生成 AI 复盘（已有成功缓存则跳过）。"""
    status = get_analyze_status()
    if not status.get("configured"):
        return {
            "success": True,
            "skipped": True,
            "configured": False,
            "generated": 0,
            "message": "未配置 DEEPSEEK_API_KEY，跳过自动复盘",
        }

    generated = 0
    skipped = 0
    errors: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for card in list_finished_review_cards(lookback_days=lookback_days):
        if not _needs_auto_review(card):
            continue
        match_id = str(card.get("match_id") or "")
        payload = load_ai_reviews()
        cached = payload.get("reviews", {}).get(match_id)
        if not force and cached and cached.get("success") and cached.get("reply"):
            skipped += 1
            continue

        record = generate_review_for_card(card, force=force)
        results.append({"match_id": match_id, "success": record.get("success"), "error": record.get("error")})
        if record.get("success"):
            generated += 1
        else:
            errors.append({"match_id": match_id, "error": record.get("error")})

    return {
        "success": True,
        "configured": True,
        "generated": generated,
        "skipped": skipped,
        "errors": errors,
        "results": results,
        "message": f"自动复盘完成：新生成 {generated} 场，跳过 {skipped} 场",
    }


def get_review(match_id: str) -> dict[str, Any] | None:
    reviews = load_ai_reviews().get("reviews") or {}
    item = reviews.get(str(match_id))
    return dict(item) if isinstance(item, dict) else None


def list_reviews() -> dict[str, Any]:
    payload = load_ai_reviews()
    return {
        "success": True,
        "updated_at": payload.get("updated_at"),
        "count": len(payload.get("reviews") or {}),
        "reviews": payload.get("reviews") or {},
        "configured": bool(get_deepseek_api_key()),
    }
