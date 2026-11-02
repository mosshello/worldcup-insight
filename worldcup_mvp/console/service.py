"""Application service and versioned API contract; adapters, persistence and models stay separate."""

from __future__ import annotations

import threading
import time
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .store import Store, now_iso
from . import sources, live
from .predictor import predict, narrate
from .quality import select_markets, assess
from .catalog import catalog, classify, provider_configuration


class ConsoleService:
    def __init__(self, store=None):
        self.store = store or Store()
        self.sync_lock = threading.Lock()
        self.last_sync = 0
        self.last_error = None
        self.current_ids = set()
        self._locks = {}
        self._locks_guard = threading.Lock()
        self.collector_stop = threading.Event()
        self.collector_thread = None
        self.score_thread = None
        self.last_score_sync = 0
        from .training_jobs import TrainingJobs
        self.training_jobs = TrainingJobs(self.store)
        from .ai_reviews import ReviewEngine
        self.ai_reviews = ReviewEngine(self)

    def start_training(self, competition_id, target='full_time'):
        return self.training_jobs.start(competition_id, target)

    def start_collector(self, interval=60):
        if self.collector_thread and self.collector_thread.is_alive():
            return self.collector_stop
        self.collector_stop.clear()

        def run():
            while not self.collector_stop.is_set():
                began = time.monotonic()
                try:
                    self.sync(force=True)
                    self.store.status(
                        "collector",
                        {
                            "status": "available" if not self.last_error else "error",
                            "interval_seconds": interval,
                            "last_success": now_iso() if not self.last_error else None,
                            "error": self.last_error,
                            "duration_ms": round((time.monotonic() - began) * 1000),
                        },
                    )
                except Exception:
                    self.store.status(
                        "collector",
                        {
                            "status": "error",
                            "error": "采集任务失败",
                            "interval_seconds": interval,
                        },
                    )
                self.collector_stop.wait(max(1, interval - (time.monotonic() - began)))

        self.collector_thread = threading.Thread(
            target=run, name="football-collector", daemon=True
        )
        self.collector_thread.start()

        def collect_scores():
            while not self.collector_stop.is_set():
                began = time.monotonic()
                try:
                    self.sync_scores()
                except Exception:
                    self.store.status(
                        "live-scores", {"status": "error", "error": "比分采集任务失败"}
                    )
                self.collector_stop.wait(max(1, 30 - (time.monotonic() - began)))

        self.score_thread = threading.Thread(
            target=collect_scores, name="football-scores", daemon=True
        )
        self.score_thread.start()
        return self.collector_stop

    def sync_scores(self):
        with self.lock("live-scores"):
            if time.monotonic() - self.last_score_sync < 10:
                return
            now = datetime.now(timezone.utc)
            groups = {}
            for m in self.store.list_matches():
                age = (now - sources.parse_date(m["kickoff"])).total_seconds()
                key = live.group_key(m)
                if key and -86400 <= age <= 86400:
                    groups.setdefault(key, []).append(m)

            def collect(item):
                key, matches = item
                try:
                    rows = live.fetch_group(key)
                except sources.SourceError as e:
                    for m in matches:
                        self.store.save_live_score(m["id"], error=str(e))
                    return 0, len(matches)
                good = 0
                for m in matches:
                    try:
                        self.store.save_live_score(
                            m["id"], live.normalize(m, rows, key)
                        )
                        good += 1
                    except (sources.SourceError, KeyError, TypeError, ValueError) as e:
                        self.store.save_live_score(
                            m["id"],
                            error=(
                                str(e)
                                if isinstance(e, sources.SourceError)
                                else "比分结构校验失败"
                            ),
                        )
                return good, len(matches) - good

            with ThreadPoolExecutor(max_workers=4) as pool:
                counts = list(pool.map(collect, groups.items()))
            good, bad = sum(c[0] for c in counts), sum(c[1] for c in counts)
            self.last_score_sync = time.monotonic()
            self.store.status(
                "live-scores",
                {
                    "name": "实时比分",
                    "status": "available" if good and not bad else "partial",
                    "count": good,
                    "unmatched_count": bad,
                    "interval_seconds": 30,
                    "last_success": now_iso() if good else None,
                },
            )

    def scores(self):
        return {
            mid: live.display(value) for mid, value in self.store.live_scores().items()
        }

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
        scores = self.scores()
        for m in rows:
            m["competition"] = classify(m)
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
            m["live_score"] = scores.get(
                m["id"],
                {
                    "score": None,
                    "state": "unknown",
                    "label": "待更新" if live.group_key(m) else "未接入",
                    "stale": True,
                },
            )
            m["score"] = m["live_score"].get("score")
            if not m["live_score"].get("stale") and m["live_score"].get("state") in (
                "live",
                "completed",
                "postponed",
                "cancelled",
                "suspended",
            ):
                m["lifecycle"] = m["live_score"]["state"]
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
                data = cached["data"]
                data["quality"] = assess(m, data)
                data["evidence"] = self.store.evidence(match_id)
                data["live_score"] = self.scores().get(match_id)
                return data
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
            markets, market_issues = select_markets(
                m["markets"], odds.get("history", {})
            )
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
                "market_issues": market_issues,
                "competition": classify(m),
                "evidence": self.store.evidence(match_id),
                "live_score": self.scores().get(match_id),
            }
            data["quality"] = assess(m, data)
            self.store.save_detail(match_id, data)
            self.store.status(
                "context:" + m.get("league_code", "unknown"),
                {
                    "name": context.get("source", "基本面")
                    + " · "
                    + m.get("league", ""),
                    "scope": "联赛赛果 / 主客场 / 交锋" if context.get("coverage") == "league_only" else "近期赛果 / 首发",
                    "status": "available" if context.get("available") else "partial",
                    "last_match": match_id,
                    "reason": context.get("reason"),
                    "last_success": context.get("captured_at"),
                },
            )
            return data

    def prediction(self, match_id, with_narrative=False):
        with self.lock("predict:" + match_id):
            self.sync(force=True)
            if self.last_error:
                raise ValueError(
                    "官方场次状态更新失败，暂不生成新的赛前预测。已有分析仍可查看。"
                )
            m = self.store.match(match_id)
            if not m or match_id not in self.current_ids:
                raise ValueError("该比赛不在官方当前场次中，无法确认状态。")
            detail = self.detail(match_id, force=True)
            if (detail.get("live_score") or {}).get("state") in (
                "live",
                "completed",
                "cancelled",
                "suspended",
                "postponed",
            ):
                raise ValueError("比分源显示比赛已开始、暂停、延期或取消，不能生成赛前预测。")
            if detail.get("cancelled"):
                raise ValueError("该比赛已取消，不能生成预测。")
            if not detail["odds_fresh"]:
                raise ValueError("无法确认最新 SP，暂不生成新预测。请稍后重试。")
            quality = assess(m, detail)
            if not quality["can_predict"]:
                raise ValueError("；".join(quality["reasons"]))
            result = predict(m, detail)
            from .model_registry import forecast as shadow_forecast
            result["football_model"] = shadow_forecast(m, detail)
            football = result["football_model"]
            if football.get("status") == "shadow":
                goals = football.get('expected_goals')
                result["evidence"].append({"title": "足球模型", "tab": "analysis",
                    "text": (f"本赛事历史赛果训练；预期进球 {goals[0]:.2f} / {goals[1]:.2f}，由比分分布汇总全场玩法。"
                             if goals else "本赛事评级与近期进失球训练；此分类模型仅输出胜平负。")})
                result["risks"] = [r for r in result["risks"] if not r.startswith("近期表现用于事实对照")]
                result["risks"].append("足球模型未纳入伤停、首发和跨赛事赛程；市场基线单独保留对照。")
                check = football.get("reference_check") or {}
                if check.get("label") == "未超过均匀基线":
                    result["risks"].append("本联赛离线结果未超过均匀基线，足球模型仅作实验对照。")
            result["explanation_method"] = "deterministic"
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
                "evidence": detail.get("evidence", []),
                "football_model": {k: result["football_model"].get(k) for k in ("status", "model_id", "dataset_digest", "trained_before", "competition_id", "run_id", "training_scope")},
                "data_receipt": {
                    "fixtures_verified_at": m.get("captured_at"),
                    "odds_verified_at": detail.get("captured_at"),
                    "quality": quality,
                },
            }
            saved = self.store.save_analysis(match_id, result, inputs)
            return {
                "prediction": saved,
                "detail": {**detail, "versions": self.store.versions(match_id)},
            }

    def reviews(self, refresh=False):
        from .football_markets import settle_markets
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
            if actual in ("h", "d", "a") and latest.get("direction_key") in ("h", "d", "a"):
                consistent = latest["direction_key"] == actual
            if actual not in ("h", "d", "a") and score and "-" in score and latest.get("direction_key") in ("h", "d", "a"):
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
                    "live_score": self.scores().get(m["id"]),
                    "consistent": consistent,
                    "direction_observed": latest.get("direction_key") is None,
                    "market_results": settle_markets(latest, detail.get("results", []), detail.get("cancelled", False)),
                    "model_results": settle_markets({"market_assessments": latest.get("football_model", {}).get("markets", [])}, detail.get("results", []), detail.get("cancelled", False)) if latest.get("football_model", {}).get("status") == "shadow" else [],
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
        for row in rows:
            row["ai_review"] = self.ai_reviews.view(row)
        return rows

    def capabilities(self):
        from .competition_workspace import workspaces, aggregate
        scopes = workspaces(self)
        training = aggregate([v["training"] for v in scopes.values()])
        competitions = self.competitions(training)
        for cid, workspace in scopes.items():
            workspace["competitions"] = [c for c in competitions if c["id"] == cid]
        return {
            "sources":self.store.statuses(), "match_count":len(self.store.list_matches()),
            "analysis_count":len(self.store.versions()),
            "settled_count":sum(r["status"] == "待复盘" for r in self.reviews()),
            "ai_configured":False, "storage":"sqlite", "training":training,
            "models":[m for w in scopes.values() for m in w["models"]],
            "competitions":competitions, "competition_workspaces":scopes,
            "club_training_enabled":True, "provider_configuration":provider_configuration(),
            "collector":{"running":bool(self.collector_thread and self.collector_thread.is_alive()), "interval_seconds":60},
        }

    def competitions(self, training=None):
        result = catalog()
        matches = self.store.list_matches()
        training = self.training_status() if training is None else training
        history = {r["id"]: r for r in training.get("staging", {}).get("coverage", [])}
        imported = {r["id"]: r for r in training.get("season_coverage", [])}
        for c in result:
            rows = [m for m in matches if classify(m)["id"] == c["id"]]
            details = [(self.store.detail(m["id"]) or {}).get("data", {}) for m in rows]
            c["match_count"] = len(rows)
            c["context_count"] = sum(
                bool(d.get("context", {}).get("available")) for d in details
            )
            c["analysis_count"] = sum(len(self.store.versions(m["id"])) for m in rows)
            for season in c["seasons"]:
                season_rows = [m for m in rows if classify(m)["season"] == season["id"]]
                key = c["id"] + ":" + season["id"]
                season.update(match_count=len(season_rows),
                    context_count=sum(bool((self.store.detail(m["id"]) or {}).get("data", {}).get("context", {}).get("available")) for m in season_rows),
                    historical_count=history.get(key, {}).get("collected", 0),
                    training_count=imported.get(key, {}).get("imported", 0))
        return result

    def training_status(self, competition_id=None):
        from .competition_workspace import training_status, aggregate
        if competition_id is not None:
            return training_status(self, competition_id)
        return aggregate([training_status(self, c["id"]) for c in catalog()])


_service = None
_service_lock = threading.Lock()


def service():
    global _service
    with _service_lock:
        if _service is None:
            _service = ConsoleService()
    return _service
