"""Application service and versioned API contract; adapters, persistence and models stay separate."""

from __future__ import annotations

import threading
import time
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .store import Store, now_iso
from . import sources
from .predictor import predict, narrate


class ConsoleService:
    def __init__(self, store=None):
        self.store = store or Store()
        self.sync_lock = threading.Lock()
        self.last_sync = 0
        self.last_error = None
        self.current_ids = set()
        self._locks = {}
        self._locks_guard = threading.Lock()

    def lock(self, key):
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def sync(self, force=False):
        with self.sync_lock:
            if not force and time.monotonic() - self.last_sync < 60:
                return
            try:
                matches = sources.fetch_matches()
                captured = now_iso()
                self.store.upsert_matches(matches, captured)
                self.current_ids = {m["id"] for m in matches}
                self.last_error = None
                self.store.status(
                    "sporttery",
                    {
                        "status": "available",
                        "count": len(matches),
                        "last_success": captured,
                    },
                )
            except sources.SourceError as exc:
                self.last_error = str(exc)
                self.store.status("sporttery", {"status": "error", "error": str(exc)})
            self.last_sync = time.monotonic()

    def list_matches(self, date=None):
        self.sync()
        rows = self.store.list_matches(date)
        state = self.store.state()
        for m in rows:
            start = sources.parse_date(m["kickoff"])
            m["lifecycle"] = (
                "upcoming" if start > datetime.now(timezone.utc) else "started"
            )
            if m["id"] not in self.current_ids:
                m["sale_status"] = "unknown" if self.last_error else "closed"
            if m["lifecycle"] != "upcoming":
                m["sale_status"] = "closed"
            m["stale"] = (
                bool(self.last_error)
                or (
                    datetime.now(timezone.utc) - sources.parse_date(m["captured_at"])
                ).total_seconds()
                > 180
            )
            if m["stale"] and m["sale_status"] == "selling":
                m["sale_status"] = "unknown"
            m["analysis_count"] = len(state["versions"].get(m["id"], []))
            cached = self.store.detail(m["id"])
            if cached:
                context = cached["data"].get("context", {})
                if context.get("state") in ("live", "completed"):
                    m["lifecycle"] = context["state"]
                m["score"] = context.get("score")
        if not rows and self.last_error:
            raise sources.SourceError(self.last_error)
        return {
            "matches": rows,
            "dates": sorted({m["sale_date"] for m in self.store.list_matches()}),
            "cached": bool(self.last_error),
            "warning": self.last_error,
            "server_time": now_iso(),
        }

    def detail(self, match_id, force=False):
        with self.lock("detail:" + match_id):
            m = self.store.match(match_id)
            if not m:
                raise ValueError("未找到该比赛，请返回比赛中心刷新。")
            cached = self.store.detail(match_id)
            if (
                cached
                and not force
                and (
                    datetime.now(timezone.utc)
                    - sources.parse_date(cached["captured_at"])
                ).total_seconds()
                < 60
            ):
                return cached["data"]
            errors = []
            with ThreadPoolExecutor(max_workers=2) as pool:
                odds_task = pool.submit(sources.fetch_odds, m)
                context_task = pool.submit(sources.fetch_context, m)
                try:
                    odds = odds_task.result()
                except sources.SourceError as exc:
                    errors.append("赔率详情：" + str(exc))
                    odds = {"history": {}, "captured_at": m["captured_at"]}
                try:
                    context = context_task.result()
                except sources.SourceError as exc:
                    errors.append("基本面：" + str(exc))
                    context = {
                        "available": False,
                        "reason": str(exc),
                        "recent": [[], []],
                        "lineups": [],
                    }
            markets = {k: dict(v) for k, v in m["markets"].items()}
            for code, history in odds.get("history", {}).items():
                if history and history[-1]["options"]:
                    existing = markets.get(code, {})
                    markets[code] = {
                        **history[-1],
                        "single": existing.get("single", False),
                        "sale_status": existing.get("sale_status", "unknown"),
                    }
            data = {
                "match_id": match_id,
                "markets": markets,
                "history": odds.get("history", {}),
                "context": context,
                "results": odds.get("results", []),
                "cancelled": odds.get("cancelled", False),
                "captured_at": odds["captured_at"],
                "errors": errors,
                "odds_fresh": not any(e.startswith("赔率") for e in errors),
                "versions": self.store.versions(match_id),
            }
            self.store.save_detail(match_id, data)
            self.store.status(
                "espn",
                {
                    "status": "available" if context.get("available") else "partial",
                    "last_match": match_id,
                    "reason": context.get("reason"),
                    "last_success": context.get("captured_at"),
                },
            )
            return data

    def prediction(self, match_id, with_narrative=False):
        with self.lock("predict:" + match_id):
            self.sync()
            if self.last_error:
                raise ValueError(
                    "官方场次状态更新失败，暂不生成新的赛前预测。已有分析仍可查看。"
                )
            m = self.store.match(match_id)
            if not m or match_id not in self.current_ids:
                raise ValueError("该比赛不在官方当前场次中，无法确认状态。")
            detail = self.detail(match_id, force=True)
            if detail.get("cancelled"):
                raise ValueError("该比赛已取消，不能生成预测。")
            if not detail["odds_fresh"]:
                raise ValueError("无法确认最新 SP，暂不生成新预测。请稍后重试。")
            result = predict(m, detail)
            if with_narrative:
                result["narrative"] = narrate(m, detail, result)
            if sources.parse_date(m["kickoff"]) <= datetime.now(timezone.utc):
                raise ValueError("处理期间比赛已开赛，本次结果不写入赛前预测版本。")
            inputs = {
                "match": {k: v for k, v in m.items() if k != "captured_at"},
                "markets": detail["markets"],
                "context": {
                    k: v for k, v in detail["context"].items() if k != "captured_at"
                },
                "history": detail["history"],
            }
            saved = self.store.save_analysis(match_id, result, inputs)
            return {
                "prediction": saved,
                "detail": {**detail, "versions": self.store.versions(match_id)},
            }

    def reviews(self, refresh=False):
        if refresh:
            pending = [
                m
                for m in self.store.list_matches()
                if sources.parse_date(m["kickoff"]) < datetime.now(timezone.utc)
                and self.store.versions(m["id"])
            ]

            def refresh_one(m):
                try:
                    self.detail(m["id"], force=True)
                except (ValueError, sources.SourceError):
                    pass

            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(refresh_one, pending[-30:]))
        rows = []
        for m in self.store.list_matches():
            versions = self.store.versions(m["id"])
            if not versions:
                continue
            cached = self.store.detail(m["id"])
            detail = (cached or {}).get("data", {})
            context = detail.get("context", {})
            results = {
                str(r.get("code", "")).lower(): r for r in detail.get("results", [])
            }
            official_score = str(results.get("crs", {}).get("combination", ""))
            # Sports feeds may include extra time. Settle this 90-minute product only against official results.
            score = (
                official_score.replace(":", "-")
                if re.fullmatch(r"\d+[:-]\d+", official_score)
                else None
            )
            latest = versions[0]
            consistent = None
            actual = str(results.get("had", {}).get("combination", "")).lower()
            if actual in ("h", "d", "a"):
                consistent = latest["direction_key"] == actual
            if actual not in ("h", "d", "a") and score and "-" in score:
                try:
                    h, a = map(int, score.split("-"))
                    consistent = latest["direction_key"] == (
                        "h" if h > a else "a" if a > h else "d"
                    )
                except ValueError:
                    pass
            if detail.get("cancelled"):
                consistent = None
            rows.append(
                {
                    "id": m["id"],
                    "date": m["kickoff"][:10],
                    "league": m["league"],
                    "home": m["home"],
                    "away": m["away"],
                    "pick": latest["direction"],
                    "score": "已取消" if detail.get("cancelled") else score or "待赛果",
                    "consistent": consistent,
                    "status": (
                        "已取消"
                        if detail.get("cancelled")
                        else (
                            "待复盘" if score or actual in ("h", "d", "a") else "待赛果"
                        )
                    ),
                    "prediction": latest,
                    "versions": versions,
                    "reply": latest.get("narrative"),
                    "result_source": (
                        "体彩官方" if results else "ESPN" if score else None
                    ),
                }
            )
        return rows

    def capabilities(self):
        from ..env_config import get_deepseek_api_key

        versions = self.store.versions()
        reviews = [r for r in self.reviews() if r["consistent"] is not None]
        return {
            "sources": self.store.statuses(),
            "match_count": len(self.store.list_matches()),
            "analysis_count": len(versions),
            "settled_count": len(reviews),
            "ai_configured": bool(get_deepseek_api_key()),
            "storage": "sqlite",
            "models": [
                {
                    "id": "market-baseline-v1",
                    "name": "市场基线",
                    "method": "完整玩法去水概率",
                    "status": "可用基线",
                    "validation": "待积累样本外验证",
                },
                {
                    "id": "market-form-poisson-v1",
                    "name": "市场与近期进失球",
                    "method": "80% 市场 + 20% 收缩 Poisson",
                    "status": "条件可用",
                    "validation": "双方至少3场赛前赛果；尚未校准",
                },
            ],
            "club_training_enabled": False,
        }


_service = None
_service_lock = threading.Lock()


def service():
    global _service
    with _service_lock:
        if _service is None:
            _service = ConsoleService()
    return _service
