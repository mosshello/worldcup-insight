"""Regression tests for source identity, market semantics, temporal leakage and storage."""

import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from worldcup_mvp.console import sources
from worldcup_mvp.console.predictor import predict, distribution, form_estimate
from worldcup_mvp.console.store import Store, now_iso
from worldcup_mvp.console.service import ConsoleService


def fixture():
    return {
        "matchId": 100,
        "matchDate": "2099-01-02",
        "matchTime": "20:00:00",
        "businessDate": "2099-01-01",
        "matchStatus": "Selling",
        "homeTeamAbbName": "主队",
        "awayTeamAbbName": "客队",
        "homeTeamCode": "CHE",
        "awayTeamCode": "MNU",
        "leagueCode": "EPL",
        "leagueAbbName": "英超",
        "homeTeamId": 1,
        "awayTeamId": 2,
        "bettingSingle": 0,
        "had": {
            "h": "2.0",
            "d": "3.2",
            "a": "3.8",
            "updateDate": "2099-01-01",
            "updateTime": "12:00:00",
        },
        "hafu": {
            k: "6.0" for k in ["hh", "hd", "ha", "dh", "dd", "da", "ah", "ad", "aa"]
        },
        "ttg": {f"s{i}": str(4 + i) for i in range(8)},
        "crs": {
            **{key: "12.0" for key in sources.EXPECTED_OPTIONS["crs"]},
            "s01s00": "5.0",
            "s01s01": "6.0",
            "s00s01": "7.0",
            "s1sh": "10.0",
            "s1sd": "20.0",
            "s1sa": "15.0",
        },
        "poolList": [
            {"poolCode": "HAD", "bettingSingle": 0, "poolStatus": "Selling"},
            {"poolCode": "TTG", "bettingSingle": 1, "poolStatus": "Selling"},
        ],
    }


class SourceTests(unittest.TestCase):
    def test_single_qualification_is_per_market(self):
        m = sources.normalize_match(fixture())
        self.assertFalse(m["markets"]["had"]["single"])
        self.assertTrue(m["markets"]["ttg"]["single"])
        self.assertEqual(m["id"], "sporttery:100")
        self.assertNotEqual(m["sale_date"], m["kickoff"][:10])
        reverse = {"a": "3.8", "d": "3.2", "h": "2"}
        self.assertEqual(
            [o["key"] for o in sources.normalize_market("had", reverse)["options"]],
            ["h", "d", "a"],
        )

    def test_score_distribution_uses_other_buckets(self):
        d = distribution(sources.normalize_match(fixture())["markets"]["crs"])
        self.assertAlmostEqual(sum(d.values()), 1)
        self.assertLess(d["s01s00"] + d["s01s01"] + d["s00s01"], 1)

    def test_team_and_time_must_both_match(self):
        m = sources.normalize_match(fixture())
        event = {
            "id": "x",
            "date": "2099-01-02T12:00:00Z",
            "competitions": [
                {
                    "competitors": [
                        {"homeAway": "home", "team": {"abbreviation": "CHE"}},
                        {"homeAway": "away", "team": {"abbreviation": "MAN"}},
                    ]
                }
            ],
        }
        self.assertIsNotNone(sources.match_event(m, [event], "eng.1"))
        self.assertIsNone(sources.match_event(m, [event, event], "eng.1"))
        event["date"] = "2099-01-03T12:00:00Z"
        self.assertIsNone(sources.match_event(m, [event], "eng.1"))
        event["date"] = "2099-01-02T12:00:00Z"
        event["competitions"][0]["competitors"][1]["team"]["abbreviation"] = "LIV"
        self.assertIsNone(sources.match_event(m, [event], "eng.1"))

    def test_future_results_never_enter_recent_form(self):
        summary = {
            "lastFiveGames": [
                {
                    "team": {"id": "1"},
                    "events": [
                        {
                            "id": "before",
                            "gameDate": "2099-01-01T00:00:00Z",
                            "homeTeamId": "1",
                            "homeTeamScore": "10",
                            "awayTeamScore": "2",
                            "opponent": {"displayName": "X"},
                        },
                        {
                            "id": "after",
                            "gameDate": "2099-01-04T00:00:00Z",
                            "homeTeamId": "1",
                            "homeTeamScore": "9",
                            "awayTeamScore": "0",
                        },
                    ],
                }
            ]
        }
        rows = sources.recent_rows(summary, "1", "2099-01-02T12:00:00Z")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["gf"], 10)


class PredictionTests(unittest.TestCase):
    def setUp(self):
        self.m = sources.normalize_match(fixture())
        self.detail = {
            "markets": self.m["markets"],
            "context": {"available": False},
            "history": {},
        }

    def test_prediction_is_complete_without_fake_fundamentals(self):
        p = predict(self.m, self.detail)
        self.assertEqual(p["model_version"], "market-baseline-v1")
        self.assertAlmostEqual(sum(p["probabilities"]), 100, places=1)
        self.assertNotEqual(p["scores"], "暂无比分数据")
        self.assertTrue(any("不足" in r for r in p["risks"]))

    def test_post_kickoff_prediction_is_rejected(self):
        with self.assertRaises(ValueError):
            predict(self.m, self.detail, now=datetime(2099, 1, 3, tzinfo=timezone.utc))

    def test_partial_had_does_not_become_a_complete_distribution(self):
        self.detail["markets"]["had"]["options"].pop()
        self.detail["markets"].pop("hafu")
        with self.assertRaises(ValueError):
            predict(self.m, self.detail)

    def test_partial_secondary_markets_are_not_used_as_complete_distributions(self):
        self.detail["markets"]["ttg"]["options"].pop()
        self.detail["markets"]["crs"]["options"].pop()
        result = predict(self.m, self.detail)
        self.assertEqual(result["goals"], "暂无总进球数据")
        self.assertEqual(result["scores"], "暂无比分数据")

    def test_full_hafu_can_supply_market_baseline(self):
        self.detail["markets"].pop("had")
        result = predict(self.m, self.detail)
        self.assertAlmostEqual(sum(result["probabilities"]), 100, places=1)
        self.assertIn("半全场", result["evidence"][0]["text"])

    def test_form_estimation_requires_both_teams_and_is_normalized(self):
        self.assertIsNone(form_estimate([[{"gf": 2, "ga": 0}] * 5, []]))
        form = form_estimate([[{"gf": 2, "ga": 0}] * 5, [{"gf": 1, "ga": 1}] * 5])
        self.assertAlmostEqual(sum(form["probabilities"].values()), 1)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.store = Store(self.path)
        self.m = sources.normalize_match(fixture())
        self.store.upsert_matches([self.m], now_iso())

    def tearDown(self):
        self.temp.cleanup()

    def test_versions_are_atomic_immutable_and_same_input_idempotent(self):
        p = predict(
            self.m, {"markets": self.m["markets"], "context": {}, "history": {}}
        )
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(
                pool.map(
                    lambda _: self.store.save_analysis(self.m["id"], p, {"input": 1}),
                    range(4),
                )
            )
        self.assertEqual(len({r["id"] for r in results}), 1)
        second = self.store.save_analysis(
            self.m["id"], {**p, "title": "changed"}, {"input": 2}
        )
        versions = Store(self.path).versions(self.m["id"])
        self.assertEqual([v["version"] for v in versions], [2, 1])
        self.assertEqual(versions[-1]["title"], p["title"])
        self.assertEqual(second["title"], "changed")

    def test_notes_and_favorites_persist_and_are_namespaced(self):
        self.store.set_state("notes", self.m["id"], "笔记")
        self.store.set_state("favorites", self.m["id"], True)
        self.assertEqual(Store(self.path).state()["notes"][self.m["id"]], "笔记")
        self.assertEqual(self.store.state("another")["favorites"], [])
        self.store.set_state("favorites", self.m["id"], False)
        self.assertEqual(self.store.state()["favorites"], [])

    def test_source_failure_marks_cache_and_refuses_new_predictions(self):
        app = ConsoleService(self.store)
        with patch(
            "worldcup_mvp.console.sources.fetch_matches",
            side_effect=sources.SourceError("offline"),
        ):
            data = app.list_matches()
            self.assertTrue(data["cached"])
            self.assertEqual(data["matches"][0]["sale_status"], "unknown")
            with self.assertRaises(ValueError):
                app.prediction(self.m["id"])

    def test_dates_are_not_limited_to_today(self):
        other = {
            **self.m,
            "id": "sporttery:200",
            "provider_id": "200",
            "sale_date": "2099-02-01",
            "kickoff": "2099-02-02T20:00:00+08:00",
        }
        self.store.upsert_matches([other], now_iso())
        self.assertEqual(len(self.store.list_matches()), 2)
        self.assertEqual(self.store.list_matches("2099-02-01")[0]["id"], other["id"])

    def test_kickoff_during_generation_cannot_create_a_pregame_version(self):
        app = ConsoleService(self.store)
        app.current_ids = {self.m["id"]}
        with patch.object(app, "sync"), patch.object(
            app,
            "detail",
            return_value={
                "markets": self.m["markets"],
                "context": {},
                "history": {},
                "odds_fresh": True,
            },
        ), patch("worldcup_mvp.console.service.datetime") as clock:
            clock.now.return_value = datetime(2100, 1, 1, tzinfo=timezone.utc)
            with self.assertRaises(ValueError):
                app.prediction(self.m["id"])
        self.assertEqual(self.store.versions(self.m["id"]), [])

    def test_official_result_precedes_secondary_result(self):
        p = predict(
            self.m, {"markets": self.m["markets"], "context": {}, "history": {}}
        )
        self.store.save_analysis(self.m["id"], p, {"input": 1})
        self.store.save_detail(
            self.m["id"],
            {
                "results": [
                    {"code": "CRS", "combination": "0:1"},
                    {"code": "HAD", "combination": "A"},
                ],
                "context": {"score": "3-0"},
            },
        )
        row = ConsoleService(self.store).reviews()[0]
        self.assertEqual(row["score"], "0-1")
        self.assertFalse(row["consistent"])
        self.assertEqual(row["result_source"], "体彩官方")

    def test_secondary_score_does_not_settle_a_ninety_minute_prediction(self):
        p = predict(
            self.m, {"markets": self.m["markets"], "context": {}, "history": {}}
        )
        self.store.save_analysis(self.m["id"], p, {"input": 1})
        self.store.save_detail(
            self.m["id"],
            {"results": [], "context": {"state": "completed", "score": "3-0"}},
        )
        row = ConsoleService(self.store).reviews()[0]
        self.assertIsNone(row["consistent"])
        self.assertEqual(row["score"], "待赛果")


if __name__ == "__main__":
    unittest.main()
