"""后台定时刷新体彩缓存。"""

from __future__ import annotations

import threading
from typing import Any

from .dashboard_data import get_upcoming_score_predictions
from .ai_review_cache import auto_review_finished_deviations
from .sporttery_api import SportteryApiError


def refresh_sporttery_cache() -> dict[str, Any]:
    """拉取最新未开赛赛事并写入本地缓存，同时尝试结算已完场。"""
    try:
        payload = get_upcoming_score_predictions()
    except SportteryApiError as exc:
        return {"success": False, "error": str(exc)}

    if not payload.get("success"):
        return {"success": False, "error": payload.get("error", "完整分析失败")}

    predictions = payload.get("predictions") or []
    auto_settlement = payload.get("auto_settlement") or {}
    settled = auto_settlement.get("settled", 0) if isinstance(auto_settlement, dict) else 0
    message = f"已缓存 {len(predictions)} 场未开赛预测"
    if payload.get("cached"):
        message += "（本地缓存）"
    if settled:
        message += f"，自动结算 {settled} 场"
    if auto_settlement.get("api_blocked"):
        message += "；体彩 API 暂不可用，已跳过剩余结算"

    ai_result: dict[str, Any] = {"skipped": True, "configured": False}
    if payload.get("cached") is not True:
        ai_result = auto_review_finished_deviations(lookback_days=7)
        if ai_result.get("generated"):
            message += f"，AI 复盘 {ai_result['generated']} 场"

    return {
        "success": True,
        "count": len(predictions),
        "settlement": auto_settlement,
        "ai_reviews": ai_result,
        "cached": bool(payload.get("cached")),
        "message": message,
    }


def _refresh_loop(interval_seconds: float, stop_event: threading.Event) -> None:
    while not stop_event.wait(interval_seconds):
        result = refresh_sporttery_cache()
        status = "ok" if result.get("success") else "fail"
        detail = result.get("message") or result.get("error") or ""
        print(f"[cache-refresh] {status}: {detail}")


def start_initial_refresh() -> threading.Thread:
    """异步执行首次刷新，避免上游接口慢时阻塞 HTTP 端口启动。"""
    def _run() -> None:
        result = refresh_sporttery_cache()
        detail = result.get("message") or result.get("error") or result
        print(f"[cache-refresh] startup: {detail}")

    thread = threading.Thread(target=_run, name="sporttery-initial-refresh", daemon=True)
    thread.start()
    return thread


def start_background_refresh(
    interval_seconds: float,
) -> tuple[threading.Thread | None, threading.Event | None]:
    """启动守护线程定时刷新；interval_seconds <= 0 时不启动。"""
    if interval_seconds <= 0:
        return None, None

    stop_event = threading.Event()
    thread = threading.Thread(
        target=_refresh_loop,
        args=(interval_seconds, stop_event),
        name="sporttery-cache-refresh",
        daemon=True,
    )
    thread.start()
    return thread, stop_event
