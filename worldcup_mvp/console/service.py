"""Application service and versioned API contract; adapters, persistence and models stay separate."""

from __future__ import annotations

import threading
import os
import time
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from .store import Store, now_iso
from . import sources, live
from .predictor import predict, narrate
from .quality import select_markets, assess
from .catalog import catalog, classify, provider_configuration
from . import intelligence, legacy_predictions, briefing
from .team_labels import localize_context


class ConsoleService:
    def __init__(self, store=None, *, observation_enabled=None):
        self.observation_enabled = store is None if observation_enabled is None else observation_enabled
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
        self.context_thread = None
        self.context_check_times = {}
        self.last_score_sync = 0
        self.result_check_times = {}
        from .daily_sweep import DailySweep
        self.daily_sweep = DailySweep(self)
        from .training_jobs import TrainingJobs
        self.training_jobs = TrainingJobs(self.store)
        from .ai_reviews import ReviewEngine
        self.ai_reviews = ReviewEngine(self)
        from .identity import IdentityRepository
        self.identities = IdentityRepository(self.store)
        from .model_tasks import ModelTasks
        self.model_tasks = ModelTasks(self.store)
        from .data_tasks import DataTasks
        self.data_tasks = DataTasks(self.store)
        from .observation_runtime import ObservationRuntime
        self.observation_runtime = ObservationRuntime(self.store)
        self.observations = self.observation_runtime.workspace
        if store is None:
            self.reconcile_identity()
            self.model_tasks.reconcile()
            for spec in catalog():
                if spec['id'] in self.data_tasks.supported:
                    self.data_tasks.list(spec['id'],reconcile=True)

    def reconcile_identity(self):
        from . import model_registry
        self.identities.index_matches(self.store.list_matches())
        for spec in catalog():
            try:
                data = model_registry.dataset(spec['id'])
                self.identities.index_dataset(spec['id'],data)
            except (ValueError,OSError,KeyError):
                continue

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
                    # Formal reconciliation must not depend on an LLM key being configured.
                    self.reviews(refresh=True)
                    self.refresh_review_context()
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
        def collect_context():
            while not self.collector_stop.is_set():
                try:
                    self.refresh_upcoming_context()
                except Exception:
                    self.store.status('fundamentals-collector', {'status': 'error', 'error': '基本面补采失败，下一轮重试'})
                self.collector_stop.wait(60)
        self.context_thread = threading.Thread(target=collect_context, name='football-fundamentals', daemon=True)
        self.context_thread.start()
        def collect_daily():
            while not self.collector_stop.is_set():
                try:
                    self.daily_sweep.tick()
                except Exception:
                    self.store.status('daily-sweep', {'status': 'error', 'error': '每日扫盘暂未更新，下一轮自动重试'})
                self.collector_stop.wait(60)
        self.daily_thread = threading.Thread(target=collect_daily, name='football-daily-sweep', daemon=True)
        self.daily_thread.start()
        if self.observation_enabled:
            self.observation_runtime.start(self.collector_stop)
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
                if key and -86400 <= age <= 7 * 86400:
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
            # Persist a local report as soon as the score feed confirms full time.
            # No official-result request or LLM call is on this path.
            self.reviews()

    def scores(self):
        with self.store.connection_scope():
            return self._scores()

    def _scores(self):
        scores = {
            mid: live.display(value) for mid, value in self.store.live_scores().items()
        }
        # Explicit official-result fallback for providers without live coverage.
        # Keep its source/timestamp visible; do not call it an ESPN live update.
        for m in self.store.list_matches():
            current = scores.get(m["id"], {})
            if current.get("score") and not current.get("stale") and current.get("state") == "completed":
                continue
            cached = self.store.detail(m["id"])
            data = (cached or {}).get("data", {})
            results = {str(r.get("code", "")).lower(): str(r.get("combination", "")).lower()
                       for r in data.get("results", [])}
            exact = data.get("official_score") or results.get("crs", "").replace(":", "-")
            exact = exact if re.fullmatch(r"\d{1,2}-\d{1,2}", exact) else None
            if exact or results.get("had") in ("h", "d", "a") or data.get("cancelled"):
                scores[m["id"]] = {
                    "score": None if data.get("cancelled") else exact,
                    "state": "cancelled" if data.get("cancelled") else "completed",
                    "label": "已取消" if data.get("cancelled") else "全场结束" if exact else "已结束·比分待核实",
                    "source": "体彩官方", "source_url": m.get("source_url"),
                    "provider_event_id": m["provider_id"], "source_status": "official_result",
                    "score_period": "regular_time" if exact else "unknown", "stale": False,
                    "observed_at": data.get("result_captured_at") or (cached or {}).get("captured_at"),
                }
        return scores

    def lock(self, key):
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def sync(self, force=False):
        with self.sync_lock:
            if not force and time.monotonic() - self.last_sync < 60:
                return
            try:
                snapshot_file = os.environ.get("FOOTBALL_SCHEDULE_SNAPSHOT")
                last_capture = None
                if snapshot_file:
                    last_capture = next((s.get("last_success") for s in self.store.statuses()
                                         if s["id"] == "sporttery"), None)
                matches, captured, transport = sources.read_official_schedule(snapshot_file, last_capture)
                self.store.upsert_matches(matches, captured)
                self.identities.index_matches([{**m,'captured_at':captured} for m in matches])
                self.current_ids = {m["id"] for m in matches}
                self.last_error = None
                self.store.status(
                    "sporttery",
                    {
                        "status": "available",
                        "count": len(matches),
                        "last_success": captured,
                        "transport": transport,
                    },
                )
            except sources.SourceError as exc:
                self.last_error = str(exc)
                self.store.status("sporttery", {"status": "error", "error": str(exc)})
            self.last_sync = time.monotonic()

    def list_matches(self, date=None):
        self.sync()
        with self.store.connection_scope():
            return self._list_matches(date)

    def score_fixtures(self, date=None, refresh=False):
        if refresh:
            self.sync_scores()
        with self.store.connection_scope():
            data = self._list_matches(date)
            source = next((s for s in self.store.statuses() if s["id"] == "live-scores"), {})
            for fixture in data["matches"]:
                fixture.pop("markets", None)
            data["score_source"] = source
            data["cached"] = source.get("status") == "error" or (not source.get("count") and bool(source.get("unmatched_count")))
            data["warning"] = "比分更新失败，显示上次核验结果" if data["cached"] else None
            data["schedule_warning"] = self.last_error
            return data

    def _list_matches(self, date=None):
        rows = self.store.list_matches(date)
        counts = self.store.analysis_counts()
        scores = self.scores()
        legacy_entries = self.legacy_entries()
        specs = catalog()
        for m in rows:
            m["competition"] = classify(m, specs)
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
            m["analysis_count"] = counts.get(m["id"], 0)
            m["prediction_archive"] = {"legacy": bool(legacy_predictions.for_match(m, legacy_entries))}
            cached = self.store.detail(m["id"])
            if cached:
                context = cached["data"].get("context", {})
                coverage = (cached['data'].get('briefing') or {}).get('coverage') or []
                m['data_coverage'] = {'available': sum(r['status'] == 'available' for r in coverage),
                                      'total': len(coverage), 'checked_at': cached['data'].get('context_checked_at')}
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

    def refresh_review_context(self):
        # Enrichment follows result reconciliation; a slow lineup provider cannot block initial reports.
        now = datetime.now(timezone.utc)
        completed = self.scores()
        matches = [m for m in self.store.list_matches()
                   if 0 < (now - sources.parse_date(m["kickoff"])).total_seconds() < 7*86400
                   and completed.get(m["id"], {}).get("state") == "completed"]
        def enrich(m):
            with self.lock("detail:" + m["id"]):
                data = (self.store.detail(m["id"]) or {}).get("data", {})
                checked = intelligence.valid_time(data.get("postmatch_context_checked_at"))
                if checked and (now - checked).total_seconds() < 900:
                    return
                try:
                    from .context_merge import merge
                    previous = data.get('context') or {}
                    context = sources.fetch_context({**m, '_source_identity': previous.get('source_identity')})
                    data['context'] = merge(m, context, previous)
                except sources.SourceError:
                    pass
                data["postmatch_context_checked_at"] = now_iso()
                self.store.save_detail(m["id"], data)
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(enrich, matches[:30]))
        self.reviews()

    def legacy_entries(self):
        return legacy_predictions.read_journal(self.store.path.parent.parent / "cache" / "prediction_journal.json")

    def refresh_upcoming_context(self, now=None, limit=6):
        """Bounded fair sweep; independent of result/score collectors and page visits."""
        now = now or datetime.now(timezone.utc)
        from .model_registry import selection
        selected = set()
        for spec in catalog():
            try:
                if selection(spec['id']).get('run_id'):
                    selected.add(spec['id'])
            except (ValueError,OSError,KeyError):
                pass
        pending = []
        for match in self.store.list_matches():
            start = intelligence.valid_time(match.get('kickoff'))
            if not start or not 0 < (start - now).total_seconds() < 7*86400: continue
            cached = (self.store.detail(match['id']) or {}).get('data', {})
            checked = intelligence.valid_time(cached.get('context_checked_at'))
            interval = 60 if classify(match)['id'] in selected else 180 if (start - now).total_seconds() < 7200 else 900
            if checked and 0 <= (now-checked).total_seconds() < interval: continue
            pending.append(match)
        pending.sort(key=lambda m: (classify(m)['id'] not in selected,self.context_check_times.get(m['id'], 0), m['kickoff']))
        def collect(match):
            try:
                self.detail(match['id'], force=True)
                return True
            except (ValueError, sources.SourceError, OSError):
                return False
            finally:
                self.context_check_times[match['id']] = time.monotonic()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(collect, pending[:limit]))
        self.store.status('fundamentals-collector', {'status': 'available' if all(results) else 'partial',
                         'checked': len(results), 'remaining': max(0, len(pending)-limit),
                         'last_success': now_iso(), 'interval_seconds': 60})
        return len(results)

    def enrich_detail(self, match, data):
        data["context"] = localize_context(data.get("context") or {})
        data["evidence"] = self.store.evidence(match["id"])
        data["intelligence"] = intelligence.build(match, data)
        data["legacy_prediction"] = legacy_predictions.for_match(match, self.legacy_entries())
        from .model_registry import forecast
        model = forecast(match, data) if sources.parse_date(match['kickoff']) > datetime.now(timezone.utc) else {'status':'unavailable','reason':'比赛已开赛'}
        data['quality'] = assess(match,data,football=model)
        data['briefing'] = briefing.build(match, data, model=model)
        return data

    def detail(self, match_id, force=False):
        # A scheduled refresh must not block the first paint of a fresh saved detail.
        if not force:
            match = self.store.match(match_id)
            cached = self.store.detail(match_id) if match else None
            if cached and (datetime.now(timezone.utc)-sources.parse_date(cached['captured_at'])).total_seconds() < 60:
                data = cached['data']
                data['quality'] = assess(match, data)
                data['live_score'] = self.scores().get(match_id)
                return self.enrich_detail(match, data)
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
                return self.enrich_detail(m, data)
            errors = []
            with ThreadPoolExecutor(max_workers=2) as pool:
                odds_task = pool.submit(sources.fetch_odds, m)
                previous = (cached or {}).get('data', {}).get('context', {})
                context_task = pool.submit(sources.fetch_context, {**m, '_source_identity': previous.get('source_identity')})
                try:
                    odds = odds_task.result()
                except sources.SourceError as exc:
                    errors.append("赔率详情：" + str(exc))
                    old = (cached or {}).get("data", {})
                    odds = {k: old.get(k) for k in ("results", "official_score", "cancelled") if k in old}
                    odds.update(history={}, captured_at=m["captured_at"])
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
            from .context_merge import merge
            context = merge(m, context, previous)
            self.identities.index_context(m,context)
            if context.get('stale_fields'):
                errors.append('部分资料更新失败，保留上次资料及原采集时间')
            markets, market_issues = select_markets(
                m["markets"], odds.get("history", {})
            )
            data = {
                "match_id": match_id,
                "markets": markets,
                "history": odds.get("history", {}),
                "context": context,
                "context_checked_at": now_iso(),
                "results": odds.get("results", []),
                "official_score": odds.get("official_score"),
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
            self.enrich_detail(m, data)
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

    def prediction(self, match_id, with_narrative=False, request_key=None):
        with self.lock("predict:" + match_id):
            previous = self.store.saved_request(request_key,match_id)
            if previous:
                return {'prediction':previous,'detail':(self.store.detail(match_id) or {}).get('data',{})}
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
            from . import model_registry
            from .forecast_result import build
            football = model_registry.forecast(m,detail)
            quality = assess(m, detail, football=football)
            detail['quality'] = quality
            if not quality["can_predict"]:
                raise ValueError("；".join(quality["reasons"]))
            result = build(m, detail, football)
            result["intelligence"] = intelligence.build(m, detail)
            result['briefing'] = detail.get('briefing')
            for item in result["intelligence"]["lineups"]:
                result["evidence"].append({"title": "阵容与赛程", "text": item["summary"], "tab": "lineups"})
            result["risks"].extend(result["intelligence"]["checks"])
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
            from .readiness import fixture_key
            if fixture_key(self.store.match(match_id) or {}) != fixture_key(m):
                raise ValueError('处理期间比赛身份或开球时间已变更，请重新核查。')
            quality = assess(m, detail, football=football)
            if not quality['can_predict']:
                raise ValueError('归档前核查未通过：' + '；'.join(quality['reasons']))
            inputs = {
                "match": {k: v for k, v in m.items() if k != "captured_at"},
                "markets": detail["markets"],
                "context": {
                    k: v for k, v in detail["context"].items() if k != "captured_at"
                },
                "history": detail["history"],
                "evidence": detail.get("evidence", []),
                "intelligence": result["intelligence"],
                "football_model": {k: result["football_model"].get(k) for k in ("status", "model_id", "dataset_digest", "trained_before", "competition_id", "run_id", "training_scope")},
                "data_receipt": {
                    "fixtures_verified_at": m.get("captured_at"),
                    "odds_verified_at": detail.get("captured_at"),
                    "fundamentals_captured_at": detail['context'].get('captured_at'),
                    "quality": quality,
                },
            }
            from .store import fingerprint
            receipt = {'policy':'model-inputs-v2','competition_id':football['competition_id'],
                'run_id':football['run_id'],'selection_revision':football['selection_revision'],
                'rule_revision':football['rule_revision'],'fixture_revision':fingerprint(fixture_key(m)),
                'team_identity':football['team_identity'],'input_digest':fingerprint(inputs)}
            result['input_receipt'] = receipt
            inputs['input_receipt'] = receipt
            # Long computation/network work finished; short file lock precedes the SQLite write transaction.
            with model_registry.commit_lock(football['competition_id']):
                current = model_registry.selection(football['competition_id'])
                if current.get('run_id') != football['run_id'] or current['revision'] != football['selection_revision']:
                    raise ValueError('处理期间模型选用已变更，请重新预测')
                if fingerprint(model_registry.competition_spec(football['competition_id'])) != football['rule_revision']:
                    raise ValueError('处理期间赛事规则已变更，请重新预测')
                saved = self.store.save_analysis(match_id, result, inputs,expected_fixture=fixture_key(m),request_key=request_key)
            return {
                "prediction": saved,
                "detail": {**detail, "versions": self.store.versions(match_id)},
            }

    def reviews(self, refresh=False):
        with self.store.connection_scope():
            return self._reviews(refresh)

    def _reviews(self, refresh=False):
        from .football_markets import settle_markets
        from .postmatch import analyze
        if refresh:
            daily_pending = self.daily_sweep.unresolved_matches()
            pending = [
                m
                for m in self.store.list_matches()
                if sources.parse_date(m["kickoff"]) < datetime.now(timezone.utc)
                and ((datetime.now(timezone.utc) - sources.parse_date(m["kickoff"])).total_seconds() <= 7*86400
                     or m['id'] in daily_pending or self.store.versions(m["id"]))
            ]

            def refresh_one(m):
                try:
                    # Only fetch results here; unrelated fundamental requests must
                    # not delay post-match reconciliation or erase a valid result.
                    with self.lock("detail:" + m["id"]):
                        odds = sources.fetch_odds(m)
                        data = (self.store.detail(m["id"]) or {}).get("data", {})
                        self.store.save_detail(m["id"], {
                            **data, "results": odds.get("results", []),
                            "history": odds.get("history", {}),
                            "official_score": odds.get("official_score"),
                            "cancelled": odds.get("cancelled", False),
                            "result_captured_at": odds.get("captured_at"),
                        })
                except (ValueError, sources.SourceError):
                    pass
                finally:
                    self.result_check_times[m["id"]] = time.monotonic()

            pending.sort(key=lambda m: (self.result_check_times.get(m["id"], 0), m["kickoff"]))
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(refresh_one, pending[:30]))
        rows = []
        scores = self.scores()
        legacy_entries = self.legacy_entries()
        for m in sorted(
            self.store.list_matches(),
            key=lambda match: sources.parse_date(match["kickoff"]),
            reverse=True,
        ):
            versions = self.store.versions(m["id"])
            cached = self.store.detail(m["id"])
            detail = (cached or {}).get("data", {})
            context = detail.get("context", {})
            results = {
                str(r.get("code", "")).lower(): r for r in detail.get("results", [])
            }
            official_score = str(detail.get("official_score") or results.get("crs", {}).get("combination", ""))
            # Sports feeds may include extra time. Settle this 90-minute product only against official results.
            score = (
                official_score.replace(":", "-")
                if re.fullmatch(r"\d+[:-]\d+", official_score)
                else None
            )
            latest = versions[0] if versions else {}
            from .forecast_result import read_archived
            archived_parts = read_archived(latest)
            legacy = legacy_predictions.for_match(m, legacy_entries)
            detail["evidence"] = self.store.evidence(m["id"])
            consistent = None
            actual = str(results.get("had", {}).get("combination", "")).lower()
            if not latest and not legacy and not (score or actual in ("h", "d", "a") or detail.get("cancelled")
                                   or scores.get(m["id"], {}).get("state") in ("completed", "cancelled")):
                continue
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
                    "date": sources.parse_date(m["kickoff"]).astimezone(timezone(timedelta(hours=8))).date().isoformat(),
                    "kickoff": m["kickoff"],
                    "league": m["league"],
                    "home": m["home"],
                    "away": m["away"],
                    "pick": latest.get("direction", "未归档"),
                    "legacy_prediction": legacy,
                    "intelligence_review": intelligence.review_link(latest, detail, m),
                    "match_facts": context.get('match_facts'),
                    "has_prediction": bool(latest),
                    "score": "已取消" if detail.get("cancelled") else score or "待赛果",
                    "live_score": scores.get(m["id"]),
                    "postmatch_review": analyze(self.store, m, latest or None, detail, scores.get(m["id"])),
                    "consistent": consistent,
                    "direction_observed": latest.get("direction_key") is None,
                    "market_results": settle_markets(archived_parts['market_reference'], detail.get("results", []), detail.get("cancelled", False)) if archived_parts['market_reference'] else [],
                    "model_results": settle_markets({"market_assessments": latest.get("football_model", {}).get("markets", [])}, detail.get("results", []), detail.get("cancelled", False)) if latest.get("football_model", {}).get("status") == "shadow" else [],
                    "status": (
                        "已取消"
                        if detail.get("cancelled")
                        else (
                            "待复盘" if score or actual in ("h", "d", "a") else "待赛果"
                        )
                    ),
                    "prediction": latest or None,
                    "versions": versions,
                    "reply": latest.get("narrative"),
                    "result_source": (
                        "体彩官方" if results else "ESPN" if score else None
                    ),
                }
            )
        for row in rows:
            if not row["has_prediction"] and row["postmatch_review"].get("id"):
                row["status"] = "已分析"
            if row["postmatch_review"].get("status") == "conflict":
                row["consistent"] = None
                row["status"] = "赛果待核实"
                for item in row["market_results"] + row["model_results"]:
                    item.update(hit=None, status="赛果冲突", unit_return=None, unit_profit=None)
            row["ai_review"] = self.ai_reviews.view(row)
            settlement_match = self.store.match(row['id'])
            settlement_detail = (self.store.detail(row['id']) or {}).get('data',{})
            if row.get('prediction') and (settlement_detail.get('official_score') or settlement_detail.get('results') or settlement_detail.get('cancelled')):
                relevant = [p for p in row['versions'] if p.get('football_model',{}).get('status')=='shadow'
                    and sources.parse_date(p['created']) < sources.parse_date(settlement_match['kickoff'])]
                first = min(relevant,key=lambda p:(p['created'],p['version'])) if relevant else None
                from .store import fingerprint
                row['settlement'] = self.store.save_settlement(row['prediction']['id'],{
                    'result_digest':fingerprint({'results':settlement_detail.get('results',[]),'score':settlement_detail.get('official_score'),'cancelled':settlement_detail.get('cancelled',False)}),
                    'market_results':row['market_results'],'model_results':row['model_results'],
                    'status':row['status'],'policy':'first-football-forecast-per-fixture-v1',
                    'evaluation_eligible':bool(first and first['id']==row['prediction']['id']),
                    'evaluation_prediction_id':first['id'] if first else None})
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
