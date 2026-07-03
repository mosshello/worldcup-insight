"""未开赛赛事筛选与比分预测测试。"""

import unittest
from datetime import datetime
from unittest import mock
from unittest.mock import patch
from zoneinfo import ZoneInfo

from worldcup_mvp.score_predictor import top_crs_scores
from worldcup_mvp.sporttery_api import (
    fetch_announced_matches,
    is_announced_match,
    is_upcoming_match,
    parse_kickoff_beijing,
)

BEIJING = ZoneInfo("Asia/Shanghai")


class UpcomingMatchTests(unittest.TestCase):
    def test_parse_kickoff_beijing(self) -> None:
        kickoff = parse_kickoff_beijing({"match_date": "2026-06-30", "match_time": "01:00:00"})
        self.assertIsNotNone(kickoff)
        self.assertEqual(kickoff.tzinfo, BEIJING)

    def test_is_upcoming_before_kickoff(self) -> None:
        match = {
            "match_date": "2099-01-01",
            "match_time": "12:00:00",
            "pools": {"had": {"home": 1.5, "draw": 3.5, "away": 5.0}},
        }
        self.assertTrue(is_upcoming_match(match))

    def test_is_not_upcoming_after_kickoff(self) -> None:
        match = {
            "match_date": "2020-01-01",
            "match_time": "12:00:00",
            "pools": {"had": {"home": 1.5, "draw": 3.5, "away": 5.0}},
        }
        now = datetime(2026, 6, 29, tzinfo=BEIJING)
        self.assertFalse(is_upcoming_match(match, now=now))

    def test_trackable_keeps_recently_started(self) -> None:
        from worldcup_mvp.sporttery_api import is_trackable_announced_match, match_lifecycle_phase

        match = {
            "match_date": "2026-07-01",
            "match_time": "01:00:00",
            "pools": {"had": {"home": 1.5, "draw": 3.5, "away": 5.0}},
        }
        now = datetime(2026, 7, 1, 2, 30, tzinfo=BEIJING)
        self.assertTrue(is_trackable_announced_match(match, now=now))
        self.assertEqual(match_lifecycle_phase(match, now=now), "live")

    def test_trackable_drops_old_finished(self) -> None:
        from worldcup_mvp.sporttery_api import is_trackable_announced_match

        match = {
            "match_date": "2020-01-01",
            "match_time": "12:00:00",
            "pools": {"had": {"home": 1.5, "draw": 3.5, "away": 5.0}},
        }
        now = datetime(2026, 7, 1, tzinfo=BEIJING)
        self.assertFalse(is_trackable_announced_match(match, now=now))

    def test_without_had_is_excluded(self) -> None:
        match = {"match_date": "2099-01-01", "match_time": "12:00:00", "pools": {"had": None}}
        self.assertFalse(is_upcoming_match(match))

    def test_announced_match_allows_pending_sale(self) -> None:
        match = {
            "match_date": "2099-01-01",
            "match_time": "12:00",
            "pools": {"had": None},
            "sale_status": "pending",
        }
        self.assertTrue(is_announced_match(match))

    @patch("worldcup_mvp.sporttery_api.fetch_matches")
    def test_fetch_upcoming_matches_sorted(self, mock_fetch: unittest.mock.Mock) -> None:
        from worldcup_mvp.sporttery_api import fetch_upcoming_matches

        mock_fetch.return_value = [
            {
                "match_id": "2",
                "match_date": "2099-06-02",
                "match_time": "20:00:00",
                "pools": {"had": {"home": 2.0, "draw": 3.0, "away": 3.5}},
            },
            {
                "match_id": "1",
                "match_date": "2099-06-02",
                "match_time": "01:00:00",
                "pools": {"had": {"home": 1.5, "draw": 3.5, "away": 5.0}},
            },
        ]
        upcoming = fetch_upcoming_matches()
        self.assertEqual([item["match_id"] for item in upcoming], ["1", "2"])

    @patch("worldcup_mvp.sporttery_api.fetch_scheduled_matches")
    @patch("worldcup_mvp.sporttery_api.fetch_upcoming_matches")
    def test_fetch_announced_matches_merges_pending(
        self,
        mock_upcoming: unittest.mock.Mock,
        mock_scheduled: unittest.mock.Mock,
    ) -> None:
        mock_scheduled.return_value = [
            {"match_id": "1", "home": "A", "away": "B", "sale_status": "pending"},
            {"match_id": "2", "home": "C", "away": "D", "sale_status": "pending"},
        ]
        mock_upcoming.return_value = [
            {
                "match_id": "1",
                "home": "A",
                "away": "B",
                "pools": {"had": {"home": 1.5, "draw": 3.5, "away": 5.0}},
            }
        ]
        announced = fetch_announced_matches()
        self.assertTrue(announced[0]["analysis_available"])
        self.assertFalse(announced[1]["analysis_available"])
        self.assertEqual(announced[1]["sale_status"], "pending")

    @patch("worldcup_mvp.sporttery_api.fetch_fixed_bonus")
    @patch("worldcup_mvp.sporttery_api.fetch_upcoming_matches")
    @patch("worldcup_mvp.sporttery_api.fetch_scheduled_matches")
    def test_announced_match_recovers_odds_from_fixed_bonus_detail(
        self,
        scheduled: mock.Mock,
        upcoming: mock.Mock,
        fixed_bonus: mock.Mock,
    ) -> None:
        scheduled.return_value = [
            {
                "match_id": "9",
                "home": "A",
                "away": "B",
                "match_status": "Selling",
                "pools": {"had": None, "hhad": None},
            }
        ]
        upcoming.return_value = []
        fixed_bonus.return_value = {
            "hadList": [{"h": "1.05", "d": "12.00", "a": "30.00"}],
            "hhadList": [
                {"h": "2.10", "d": "3.45", "a": "2.75", "goalLine": "-2"}
            ],
        }

        announced = fetch_announced_matches()

        self.assertTrue(announced[0]["analysis_available"])
        self.assertEqual(announced[0]["sale_status"], "selling")
        self.assertEqual(announced[0]["pools"]["had"]["home"], 1.05)
        self.assertEqual(announced[0]["pools"]["hhad"]["goal_line"], -2.0)
        self.assertTrue(announced[0]["odds_recovered_from_detail"])

    @patch("worldcup_mvp.sporttery_api.fetch_fixed_bonus")
    @patch("worldcup_mvp.sporttery_api.fetch_upcoming_matches", return_value=[])
    @patch("worldcup_mvp.sporttery_api.fetch_scheduled_matches")
    def test_announced_match_derives_unpriced_had_from_hafu(
        self,
        scheduled: mock.Mock,
        _upcoming: mock.Mock,
        fixed_bonus: mock.Mock,
    ) -> None:
        scheduled.return_value = [
            {
                "match_id": "10",
                "match_status": "Selling",
                "pools": {"had": None, "hhad": None},
            }
        ]
        fixed_bonus.return_value = {
            "hadList": [],
            "hhadList": [{"h": "2.06", "d": "3.45", "a": "2.82", "goalLine": "-2"}],
            "hafuList": [
                {
                    "hh": "1.30", "hd": "30", "ha": "100",
                    "dh": "3.90", "dd": "12.5", "da": "40",
                    "ah": "20", "ad": "40", "aa": "80",
                }
            ],
        }

        match = fetch_announced_matches()[0]

        self.assertTrue(match["analysis_available"])
        self.assertFalse(match["had_market_available"])
        self.assertEqual(match["had_derived_from"], "hafu-no-vig")
        self.assertLess(match["pools"]["had"]["home"], 2.0)


class CrsParserTests(unittest.TestCase):
    def test_top_crs_scores(self) -> None:
        history = {
            "crsList": [
                {"s01s00": "7.0", "s02s01": "6.0", "s01s01": "8.0", "updateDate": "2026-06-29"},
            ]
        }
        top = top_crs_scores(history)
        self.assertEqual(top[0], (2, 1, 6.0))


if __name__ == "__main__":
    unittest.main()
