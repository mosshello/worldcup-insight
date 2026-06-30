"""未开赛赛事筛选与比分预测测试。"""

import unittest
from datetime import datetime
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
