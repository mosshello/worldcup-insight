"""后台定时刷新体彩缓存。"""

from __future__ import annotations

import threading
from typing import Any

from .dashboard_data import get_upcoming_score_predictions
from .sporttery_api import SportteryApiError


def refresh_sporttery_cache() -> dict[str, Any]:
    """拉取最新未开赛赛事并写入本地缓存。"""
    try:
        payload = get_upcoming_score_predictions()
        if not payload.get("success"):
            return {"success": False, "error": payload.get("error", "完整分析失败")}
        predictions = payload.get("predictions") or []
        return {
            "success": True,
            "count": len(predictions),
            "message": f"已缓存 {len(predictions)} 场未开赛预测",
        }
    except SportteryApiError as exc:
        return {"success": False, "error": str(exc)}
    except RuntimeError as exc:
        return {"success": False, "error": str(exc)}


def _refresh_loop(interval_seconds: float, stop_event: threading.Event) -> None:
    while not stop_event.wait(interval_seconds):
        result = refresh_sporttery_cache()
        status = "ok" if result.get("success") else "fail"
        detail = result.get("message") or result.get("error") or ""
        print(f"[cache-refresh] {status}: {detail}")


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
