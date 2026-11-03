"""Model/settlement regressions. All generated data is test-only, never production history."""
import copy
import json
import math
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from worldcup_mvp.console import training as t
from worldcup_mvp.console import model_registry as registry
from worldcup_mvp.console.football_markets import (
    score_grid, market_probabilities, assessment, settle_markets, official_outcomes,
)
from worldcup_mvp.console.sources import EXPECTED_OPTIONS, normalize_match
from worldcup_mvp.console.predictor import predict, narrate
from worldcup_mvp.console.store import fingerprint
from worldcup_mvp.console.store import Store
from worldcup_mvp.console.service import ConsoleService
from tests.test_console_domain import fixture


def history(count=40, league="jp.j1", half=False):
    start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    return [{"id": f"{league}:{i}", "competition_id": league, "season": "2023",
        "stage": "regular", "home_id": "a" if i % 2 else "b", "away_id": "b" if i % 2 else "a",
        "kickoff": (start+timedelta(days=i)).isoformat(),
        "result_available_at": (start+timedelta(days=i, hours=3)).isoformat(),
        "source_url": "https://example.org/test-only", "period": "regular_time",
        "home_goals": i % 4, "away_goals": (i+1) % 3,
        **({"home_half_goals": (i % 4)//2, "away_half_goals": ((i+1) % 3)//2} if half else {})}
        for i in range(count)]


def dataset(rows):
    return {"manifest": {"training_permitted": True, "license_reference": "synthetic-test-only",
        "source_url": "https://example.org/test-only"}, "matches": rows,
        "team_mappings": [{"competition_id": "jp.j1", "provider": "sporttery", "provider_id": str(i), "team_id": team}
                          for i, team in ((1, "a"), (2, "b"))]}


class MarketTests(unittest.TestCase):
    def test_all_market_mass_and_htft_marginals(self):
        for rates in ((0.2, 0.3), (1.5, 1.1), (5.5, 5.5)):
            grid = score_grid(*rates, rho=-0.12)
            markets = market_probabilities(grid, -1, [0.43, 0.45])
            for code, p in markets.items():
                self.assertEqual(set(p), set(EXPECTED_OPTIONS[code]))
                self.assertAlmostEqual(sum(p.values()), 1, places=10)
                self.assertTrue(all(v >= 0 for v in p.values()))
            for key in "hda":
                self.assertAlmostEqual(sum(v for k, v in markets["hafu"].items() if k.endswith(key)), markets["had"][key], places=10)

    def test_zero_zero_and_high_score_buckets(self):
        grid = [[0.0]*9 for _ in range(9)]
        grid[0][0] = 1
        self.assertEqual(market_probabilities(grid, 0, [.4, .4])["hafu"]["dd"], 1)
        grid[0][0], grid[8][0] = 0, 1
        result = market_probabilities(grid, -2)
        self.assertEqual(result["ttg"]["s7"], 1)
        self.assertEqual(result["crs"]["s1sh"], 1)
        self.assertEqual(result["hhad"]["h"], 1)

    def test_dc_correction_and_invalid_inputs(self):
        self.assertGreater(score_grid(1.3, 1.1, -.1)[1][1], score_grid(1.3, 1.1)[1][1])
        for rate in (float("nan"), float("inf"), -1):
            with self.assertRaises(ValueError): score_grid(rate, 1)
        with self.assertRaises(ValueError): market_probabilities(score_grid(1, 1), .25)

    def test_close_direction_is_abstention_not_forced_pick(self):
        view = assessment("had", {"h": .3523, "d": .2924, "a": .3553})
        self.assertEqual(view["selected_keys"], [])
        match = normalize_match(fixture())
        match["markets"]["had"]["options"] = [{"key": k, "label": k, "odds": v} for k, v in zip("hda", [2.49, 3, 2.47])]
        p = predict(match, {"markets": match["markets"], "context": {}}, now=datetime(2099, 1, 1, tzinfo=timezone.utc))
        self.assertIsNone(p["direction_key"])
        self.assertEqual(p["direction"], "观望")

    def test_two_real_score_regressions_and_equal_unit_costs(self):
        ttg = assessment("ttg", {f"s{i}": p for i, p in enumerate([.03, .1, .25, .27, .17, .1, .05, .03])},
                         {"options": [{"key": "s2", "odds": 3.2}, {"key": "s3", "odds": 3.6}]})
        crs = assessment("crs", {"s02s01": .15, "s01s01": .13, "s01s02": .1, "s04s00": .02})
        prediction = {"market_assessments": [ttg, crs]}
        first = {r["code"]: r for r in settle_markets(prediction, [{"code": "TTG", "combination": "4"}, {"code": "CRS", "combination": "4:0"}])}
        second = {r["code"]: r for r in settle_markets(prediction, [{"code": "TTG", "combination": "2"}, {"code": "CRS", "combination": "1:1"}])}
        self.assertFalse(first["ttg"]["hit"])
        self.assertFalse(first["crs"]["hit"])
        self.assertTrue(second["ttg"]["hit"])
        self.assertTrue(second["crs"]["hit"])
        self.assertEqual(second["ttg"]["unit_profit"], 1.2)
        self.assertEqual(second["crs"]["unit_cost"], 3)
        self.assertIsNone(second["crs"]["unit_return"])

    def test_no_retrospective_rank_picks_and_cancelled_unsettled(self):
        old = {"direction_key": "a", "goals": "2 / 3 球", "score_probabilities": [{"score": "1-1"}],
               "market_assessments": [{"code": "hhad", "options": [{"key": "h", "probability": 60}]}]}
        rows = {r["code"]: r for r in settle_markets(old, [{"code": "HHAD", "combination": "h"}, {"code": "TTG", "combination": "2"}])}
        self.assertEqual(rows["hhad"]["status"], "未归档选择")
        self.assertTrue(rows["ttg"]["hit"])
        self.assertTrue(all(r["hit"] is None for r in settle_markets(old, [], True)))
        self.assertEqual(official_outcomes([{"code": "HAFU", "combination": "D:D"}])["hafu"], "dd")

    def test_local_explanation_never_calls_llm(self):
        match = normalize_match(fixture())
        detail = {"markets": match["markets"], "context": {}}
        prediction = predict(match, detail, now=datetime(2099, 1, 1, tzinfo=timezone.utc))
        with patch("worldcup_mvp.ai_analyst._call_deepseek", side_effect=AssertionError("LLM forbidden")):
            self.assertIn("依据", narrate(match, detail, prediction))


class TrainingTests(unittest.TestCase):
    def test_background_training_is_singleton_and_reports_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            app = ConsoleService(Store(Path(folder)/"test.db"))
            started, release = threading.Event(), threading.Event()
            def run(competition_id, progress):
                started.set()
                release.wait(2)
                progress({"phase": "fit"})
                return {"model_id": "test-only", "run_id":"run"}
            with patch.object(registry, "status", return_value={"ready": True, "reason": "test"}), patch.object(registry, "train", side_effect=run) as execute:
                self.assertEqual(app.start_training("jp.j1")["status"], "running")
                self.assertTrue(started.wait(1))
                self.assertEqual(app.start_training("jp.j1")["status"], "running")
                release.set()
                app.training_jobs.threads["jp.j1"].join(2)
                self.assertFalse(app.training_jobs.threads["jp.j1"].is_alive())
                self.assertEqual(execute.call_count, 1)
                self.assertEqual(app.training_jobs.status("jp.j1")["status"], "completed")
            app.store.status("training-job:jp.j1", {"status": "running"})
            with patch.object(registry, "status", return_value={"ready": True}):
                self.assertEqual(app.training_jobs.status("jp.j1")["status"], "interrupted")

    def test_training_error_is_visible_without_a_fake_model(self):
        with tempfile.TemporaryDirectory() as folder:
            app = ConsoleService(Store(Path(folder)/"test.db"))
            with patch.object(registry, "status", return_value={"ready": True}), patch.object(registry, "train", side_effect=ValueError("invalid data")):
                app.start_training("jp.j1")
                app.training_jobs.threads["jp.j1"].join(2)
                job = app.training_jobs.status("jp.j1")
                self.assertEqual(job["status"], "failed")
                self.assertEqual(job["error"], "invalid data")

    def test_half_labels_duplicate_fixtures_and_identity_mapping_rejected(self):
        data = dataset(history(3))
        variants = []
        v = copy.deepcopy(data); v["matches"][0]["home_half_goals"] = 9; variants.append(v)
        v = copy.deepcopy(data); r = copy.deepcopy(v["matches"][0]); r["id"] = "dup"; v["matches"].append(r); variants.append(v)
        v = copy.deepcopy(data); v["team_mappings"].append(v["team_mappings"][0]); variants.append(v)
        for v in variants:
            with self.assertRaises(ValueError): t.validate_dataset(v)

    def test_serialized_model_and_future_mutation(self):
        rows = history(110, half=True)
        at = datetime(2023, 4, 20, tzinfo=timezone.utc)
        model = t.GoalModel(kind="dc").fit(rows, at, "jp.j1")
        restored = t.GoalModel.from_dict(json.loads(json.dumps(model.to_dict())))
        self.assertEqual(model.forecast(rows[-1]), restored.forecast(rows[-1]))
        self.assertIn("hafu", restored.forecast(rows[-1])["markets"])
        future = copy.deepcopy(rows[-1]); future["home_goals"] = 20; future["result_available_at"] = "2024-01-01T00:00:00Z"
        self.assertEqual(model.to_dict(), t.GoalModel(kind="dc").fit(rows+[future], at, "jp.j1").to_dict())

    def test_missing_half_data_does_not_fabricate_htft(self):
        rows = history()
        model = t.GoalModel().fit(rows, datetime(2024, 1, 1, tzinfo=timezone.utc), "jp.j1")
        self.assertNotIn("hafu", model.forecast(rows[-1])["markets"])

    def test_walk_forward_future_mutation_keeps_all_earlier_markets(self):
        rows = history()
        first = t.walk_forward(rows, rows[28:30], "separate", 180, "dc")
        changed = copy.deepcopy(rows)
        for r in changed[35:]: r["home_goals"] = 15
        self.assertEqual(first, t.walk_forward(changed, changed[28:30], "separate", 180, "dc"))

    def test_single_league_evaluation_bundle_and_shadow_guards(self):
        data = dataset(history())
        with patch.multiple(t, MIN_ROWS=20, MIN_SEASONS=1, MIN_TEST=3):
            report = t.evaluate(data)
        self.assertEqual(list(report["selected_by_competition"]), ["jp.j1"])
        self.assertEqual(report["markets"]["ttg"]["count"], 8)
        self.assertEqual(report["markets"]["hafu"]["count"], 0)
        self.assertFalse(report["production_enabled"])
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        bundle = t.fit_bundle(data, report, now)
        report["model_id"] = bundle["id"]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.multiple(t, DATASET=root/"data.json", REPORT=root/"report.json", BUNDLE=root/"model.json"):
                t.atomic_json(t.DATASET, data); t.atomic_json(t.REPORT, report); t.atomic_json(t.BUNDLE, bundle)
                match = {"league_code": "JD1", "kickoff": "2024-01-02T12:00:00Z", "home_id": "1", "away_id": "2"}
                self.assertEqual(t.shadow_forecast(match, {}, now)["status"], "shadow")
                match["home_id"] = "wrong"
                self.assertEqual(t.shadow_forecast(match, {}, now)["reason"], "球队标识尚未映射")
                with patch('worldcup_mvp.console.league_data.load_history', return_value={}), \
                     patch('worldcup_mvp.console.league_data.match_fixture', return_value={
                         'home_id':'a','away_id':'b','source_url':'https://example.org/matched-fixture'}):
                    mapped=t.shadow_forecast(match, {}, now)
                self.assertEqual(mapped['status'],'shadow')
                self.assertEqual(mapped['team_identity']['home_id'],'a')
                self.assertEqual(mapped['team_identity']['source'],'https://example.org/matched-fixture')
                changed = copy.deepcopy(data); changed["matches"][0]["home_goals"] = 4
                t.atomic_json(t.DATASET, changed)
                self.assertEqual(t.shadow_forecast(match, {}, now)["status"], "unavailable")
                bundle["models"]["jp.j1"]["params"]["base"] = 10
                t.atomic_json(t.BUNDLE, bundle)
                with self.assertRaises(ValueError): t.read_bundle()

    def test_insufficient_dataset_is_not_training_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.multiple(t, DATASET=Path(folder)/"data.json", REPORT=Path(folder)/"report.json", BUNDLE=Path(folder)/"model.json"):
                t.atomic_json(t.DATASET, dataset(history(10)))
                status = t.training_status()
                self.assertFalse(status["ready"])
                self.assertEqual(status["rows"], 10)


if __name__ == "__main__":
    unittest.main()
