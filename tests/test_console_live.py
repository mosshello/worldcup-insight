import copy
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from worldcup_mvp.console import live, sources
from worldcup_mvp.console.store import Store
from worldcup_mvp.console.service import ConsoleService
from tests.test_console_domain import fixture


class LiveScoresTests(unittest.TestCase):
    def setUp(self):
        self.match = {
            "id": "sporttery:test",
            "home": "光州FC",
            "away": "金泉尚武",
            "home_code": "GWJ",
            "away_code": "GJS",
            "league_code": "KD1",
            "kickoff": "2026-10-31T15:30:00+08:00",
        }
        self.row = {
            "year": 2026,
            "leagueId": 1,
            "meetSeq": 1,
            "gameId": 200,
            "gameDate": "2026.10.31",
            "gameTime": "16:30",
            "homeTeam": "K22",
            "awayTeam": "K35",
            "gameStatus": "1S 24",
            "endYn": "N",
            "homeGoal": 0,
            "awayGoal": 0,
        }

    def parse(self, row=None):
        return live.normalize(self.match, [row or self.row], live.group_key(self.match))

    def test_korean_zero_score_and_clock_are_real_values(self):
        result = self.parse()
        self.assertEqual(
            (result["score"], result["label"], result["clock"]),
            ("0-0", "上半场", "24′"),
        )
        self.assertEqual(
            sources.parse_date(result["source_kickoff"]),
            sources.parse_date(self.match["kickoff"]),
        )

    def test_not_started_does_not_fabricate_zero_zero(self):
        self.assertIsNone(self.parse({**self.row, "gameStatus": None})["score"])

    def test_break_and_end_are_different(self):
        self.assertEqual(
            self.parse({**self.row, "gameStatus": "1E"})["label"], "中场休息"
        )
        self.assertEqual(self.parse({**self.row, "gameStatus": "2E"})["state"], "live")
        self.assertEqual(
            self.parse({**self.row, "gameStatus": "FE"})["state"], "completed"
        )
        self.assertEqual(
            self.parse({**self.row, "gameStatus": "2S 45+3"})["clock"], "45+3′"
        )

    def test_identity_requires_both_teams_time_and_competition(self):
        for change in [
            {"homeTeam": "K35", "awayTeam": "K22"},
            {"leagueId": 2},
            {"gameTime": "18:30"},
        ]:
            with self.subTest(change=change), self.assertRaises(sources.SourceError):
                self.parse({**self.row, **change})
        with self.assertRaises(sources.SourceError):
            live.normalize(self.match, [self.row, self.row], live.group_key(self.match))

    def test_invalid_goals_rejected_not_coerced_to_zero(self):
        for v in [None, "", -1, True, 2.5, "x"]:
            with self.subTest(value=v), self.assertRaises(sources.SourceError):
                self.parse({**self.row, "homeGoal": v})

    def test_score_expiration_and_failure_keep_original_timestamp(self):
        now = datetime(2026, 10, 31, 8, tzinfo=timezone.utc)
        self.assertTrue(
            live.display(
                {
                    "score": "1-0",
                    "state": "live",
                    "observed_at": (now - timedelta(seconds=91)).isoformat(),
                },
                now,
            )["stale"]
        )
        self.assertFalse(
            live.display(
                {
                    "score": "1-0",
                    "state": "completed",
                    "observed_at": (now - timedelta(days=1)).isoformat(),
                },
                now,
            )["stale"]
        )
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "score.db")
            m = sources.normalize_match(fixture())
            store.upsert_matches([m], now.isoformat())
            result = self.parse()
            store.save_live_score(m["id"], result)
            original = store.live_scores()[m["id"]]["observed_at"]
            store.save_live_score(m["id"], error="offline")
            last = store.live_scores()[m["id"]]
            self.assertEqual(last["observed_at"], original)
            self.assertEqual(last["score"], "0-0")
            self.assertTrue(live.display(last)["stale"])
            store.save_live_score(m["id"], {**result, "score": "1-0"})
            store.save_live_score(m["id"], result)
            with store.connect() as db:
                self.assertEqual(
                    db.execute("SELECT count(*) FROM live_score_snapshots").fetchone()[
                        0
                    ],
                    3,
                )

    def test_espn_abbreviation_match_and_halftime(self):
        match = {
            **self.match,
            "league_code": "JD1",
            "home_code": "KAH",
            "away_code": "URA",
        }
        event = {
            "id": "123",
            "date": self.match["kickoff"],
            "competitions": [
                {
                    "competitors": [
                        {
                            "homeAway": "home",
                            "score": "1",
                            "team": {"id": "7476", "abbreviation": "KRE"},
                        },
                        {
                            "homeAway": "away",
                            "score": "0",
                            "team": {"id": "3385", "abbreviation": "URA"},
                        },
                    ]
                }
            ],
            "status": {
                "displayClock": "45'",
                "period": 1,
                "type": {"name": "STATUS_HALFTIME", "state": "in", "completed": False},
            },
        }
        result = live.normalize(match, [event], live.group_key(match))
        self.assertEqual(
            (result["score"], result["label"], result["clock"]),
            ("1-0", "中场休息", None),
        )
        event["status"]["type"].update(name="STATUS_POSTPONED", state="post")
        self.assertEqual(
            live.normalize(match, [event], live.group_key(match))["state"], "postponed"
        )

    def test_collection_batches_matches_and_never_settles(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "score.db")
            app = ConsoleService(store)
            m = sources.normalize_match(fixture())
            m.update(
                self.match, id=m["id"], kickoff=datetime.now(timezone.utc).isoformat()
            )
            store.upsert_matches(
                [m, {**m, "id": "sporttery:second", "provider_id": "second"}],
                datetime.now(timezone.utc).isoformat(),
            )
            with patch.object(
                live, "fetch_group", return_value=[self.row]
            ) as fetch, patch.object(live, "normalize", return_value=self.parse()):
                app.sync_scores()
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(len(app.scores()), 2)
            self.assertEqual(store.versions(), [])
            store.save_analysis(
                m["id"],
                {
                    "model_version": "test-only",
                    "direction_key": "h",
                    "direction": "主胜",
                },
                {},
            )
            review = app.reviews()[0]
            self.assertEqual(review["live_score"]["score"], "0-0")
            self.assertEqual(review["score"], "待赛果")
            self.assertIsNone(review["consistent"])
            with patch.object(
                live, "fetch_group", side_effect=sources.SourceError("offline")
            ):
                app.last_score_sync = 0
                app.sync_scores()
            self.assertTrue(all(v["stale"] for v in app.scores().values()))


if __name__ == "__main__":
    unittest.main()
