import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from worldcup_mvp.console import sources
from worldcup_mvp.console.catalog import classify
from worldcup_mvp.console.quality import assess, complete, select_markets
from worldcup_mvp.console.predictor import predict, handicap_labels, narrate
from worldcup_mvp.console.store import Store
from worldcup_mvp.console.service import ConsoleService
from worldcup_mvp.console.training import (
    GoalModel,
    validate_dataset,
    walk_forward,
    metrics,
)
from tests.test_console_domain import fixture


class QuotesTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2099, 1, 1, 12, tzinfo=timezone.utc)
        self.match = sources.normalize_match(fixture())
        self.match["captured_at"] = self.now.isoformat()
        self.detail = {
            "markets": copy.deepcopy(self.match["markets"]),
            "captured_at": self.now.isoformat(),
            "odds_fresh": True,
            "context": {},
        }

    def test_history_other_score_buckets_are_not_lost(self):
        raw = fixture()["crs"]
        for key in ("s1sh", "s1sd", "s1sa"):
            raw[key.replace("s1", "s-1", 1)] = raw.pop(key)
        self.assertTrue(complete(sources.normalize_market("crs", raw)))

    def test_complete_current_survives_newer_incomplete_history(self):
        p = copy.deepcopy(self.match["markets"]["had"])
        p["options"].pop()
        p["updated_at"] = self.now.isoformat()
        actual, issues = select_markets(self.match["markets"], {"had": [p]}, self.now)
        self.assertTrue(complete(actual["had"]))
        self.assertEqual(issues[0]["code"], "incomplete_history")

    def test_older_or_future_history_cannot_replace_current(self):
        for time in ("2098-01-01T00:00:00Z", "2100-01-01T00:00:00Z", "invalid"):
            p = copy.deepcopy(self.match["markets"]["had"])
            p["updated_at"] = time
            p["options"][0]["odds"] = 9
            actual, _ = select_markets(self.match["markets"], {"had": [p]}, self.now)
            self.assertEqual(actual["had"]["options"][0]["odds"], 2)

    def test_old_unchanged_quote_with_fresh_heartbeat_is_valid(self):
        self.detail["markets"]["had"]["updated_at"] = "2098-01-01T00:00:00Z"
        quality = assess(self.match, self.detail, self.now)
        self.assertTrue(quality["quotes_ready"])
        self.assertFalse(quality["can_predict"])  # Fresh quotes cannot bypass missing fundamentals.
        self.detail["captured_at"] = (self.now - timedelta(seconds=181)).isoformat()
        self.assertFalse(assess(self.match, self.detail, self.now)["can_predict"])

    def test_suspended_market_or_future_receipt_blocks_prediction(self):
        self.detail["markets"]["had"]["sale_status"] = "suspended"
        self.assertFalse(assess(self.match, self.detail, self.now)["can_predict"])
        self.detail["captured_at"] = "2100-01-01T00:00:00Z"
        self.assertIn(
            "赔率核验时间失效", assess(self.match, self.detail, self.now)["reasons"]
        )

    def test_zero_handicap_and_invalid_quarter_handicap(self):
        p = sources.normalize_market(
            "hhad", {"h": 2, "d": 3, "a": 4, "goalLineValue": 0}
        )
        self.assertEqual(p["line"], 0)
        self.assertTrue(complete(p))
        p["line"] = 0.25
        self.assertFalse(complete(p))
        self.assertEqual(handicap_labels(1)["h"], "主队不败")
        self.assertEqual(handicap_labels(-1)["a"], "主队不胜")
        self.assertEqual(handicap_labels(1)["d"], "主队恰好净负1球")

    def test_form_does_not_silently_modify_market_probabilities(self):
        base = predict(self.match, self.detail, now=self.now)
        self.detail["context"] = {
            "available": True,
            "recent": [[{"gf": 9, "ga": 0}] * 5, [{"gf": 0, "ga": 9}] * 5],
        }
        enhanced = predict(self.match, self.detail, now=self.now)
        self.assertEqual(base["probabilities"], enhanced["probabilities"])
        self.assertIsNotNone(enhanced["form_estimate"])

    def test_llm_cannot_insert_unsupported_claims_into_narrative(self):
        prediction = predict(self.match, self.detail, now=self.now)
        with patch(
            "worldcup_mvp.env_config.get_deepseek_api_key", return_value="test"
        ), patch(
            "worldcup_mvp.ai_analyst._call_deepseek",
            return_value="虚构球员确定首发，必胜100%",
        ):
            text = narrate(self.match, self.detail, prediction)
        self.assertNotIn("虚构", text)
        self.assertNotIn("100%", text)
        self.assertIn("市场基准", text)

    def test_special_japan_season_and_unknown_korean_group(self):
        self.assertEqual(
            classify({"league_code": "JD1", "kickoff": "2026-04-01"})["season"],
            "2026-special",
        )
        self.assertEqual(
            classify({"league_code": "JD1", "kickoff": "2026-10-31"})["season"],
            "2026-27",
        )
        self.assertIsNone(
            classify({"league_code": "KD1", "kickoff": "2026-10-31"})["stage"]
        )


class DatasetTests(unittest.TestCase):
    def dataset(self):
        return {
            "manifest": {
                "training_permitted": True,
                "license_reference": "test-only",
                "source_url": "https://example.org/test",
            },
            "matches": [
                {
                    "id": "1",
                    "competition_id": "jp.j1",
                    "season": "2024",
                    "stage": "regular",
                    "home_id": "a",
                    "away_id": "b",
                    "kickoff": "2024-01-01T10:00:00Z",
                    "result_available_at": "2024-01-01T12:00:00Z",
                    "source_url": "https://example.org/test",
                    "period": "regular_time",
                    "home_goals": 2,
                    "away_goals": 1,
                }
            ],
        }

    def test_license_period_and_duplicates_rejected(self):
        for variant in ("license", "period", "duplicate"):
            d = self.dataset()
            if variant == "license":
                d["manifest"]["training_permitted"] = False
            if variant == "period":
                d["matches"][0]["period"] = "after_penalties"
            if variant == "duplicate":
                d["matches"].append(copy.deepcopy(d["matches"][0]))
            with self.assertRaises(ValueError):
                validate_dataset(d)

    def test_late_odds_cannot_enter_earlier_horizon(self):
        d = self.dataset()
        r = d["matches"][0]
        r.update(
            odds={"h": 2, "d": 3, "a": 4},
            odds_observed_at="2024-01-01T09:30:00Z",
            odds_source_url="https://example.org/odds",
        )
        with self.assertRaises(ValueError):
            validate_dataset(d)

    def test_future_result_never_changes_fitted_parameters(self):
        rows = validate_dataset(self.dataset())
        future = copy.deepcopy(rows[0])
        future.update(
            id="2",
            kickoff="2025-01-01T10:00:00Z",
            result_available_at="2025-01-01T12:00:00Z",
            home_goals=10,
            away_goals=0,
        )
        asof = datetime(2024, 12, 1, tzinfo=timezone.utc)
        first = GoalModel().fit(rows, asof, "jp.j1").predict(future)
        second = GoalModel().fit(rows + [future], asof, "jp.j1").predict(future)
        self.assertEqual(first, second)
        self.assertAlmostEqual(sum(first), 1)

    def test_walk_forward_uses_a_cutoff_before_the_target(self):
        rows = validate_dataset(self.dataset())
        target = copy.deepcopy(rows[0])
        target.update(
            id="2",
            kickoff="2025-01-07T10:00:00Z",
            result_available_at="2025-01-07T12:00:00Z",
        )
        records = walk_forward(rows + [target], [target], "hierarchical", 365)
        self.assertLess(records[0]["trained_before"], records[0]["asof"])
        self.assertEqual(metrics(records)["count"], 1)

    def test_evidence_and_failures_keep_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            s = Store(Path(folder) / "test.db")
            m = sources.normalize_match(fixture())
            s.upsert_matches([m], "2026-01-01T00:00:00Z")
            item = s.add_evidence(
                m["id"],
                {
                    "kind": "opinion",
                    "text": "test",
                    "source_url": "https://example.org/article",
                    "published_at": "2025-01-01T00:00:00Z",
                },
            )
            self.assertFalse(item["used_for_training"])
            self.assertEqual(len(s.evidence(m["id"])), 1)
            s.status(
                "source",
                {"status": "available", "last_success": "2025-01-01T00:00:00Z"},
            )
            s.status("source", {"status": "error", "error": "offline"})
            self.assertEqual(s.statuses()[0]["last_success"], "2025-01-01T00:00:00Z")
            with self.assertRaises(ValueError):
                s.add_evidence(
                    m["id"],
                    {
                        "kind": "fact",
                        "text": "bad",
                        "source_url": "javascript:alert(1)",
                        "published_at": "2025-01-01T00:00:00Z",
                    },
                )

    def test_collector_runs_without_a_browser_and_stops(self):
        with tempfile.TemporaryDirectory() as folder:
            service = ConsoleService(Store(Path(folder) / "test.db"))
            with patch.object(service, "sync") as sync:
                service.start_collector(interval=60)
                service.collector_stop.set()
                service.collector_thread.join(timeout=2)
                service.score_thread.join(timeout=2)
                service.context_thread.join(timeout=2)
                self.assertTrue(sync.called)
                self.assertFalse(service.collector_thread.is_alive())
                self.assertFalse(service.context_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
