"""假设投注与结算测试。"""

import unittest

from worldcup_mvp.bet_simulator import calc_single_payout, settle_against_results, simulate_prediction_bet
from worldcup_mvp.sporttery_api import format_countdown, hours_until_kickoff


SAMPLE_PREDICTION = {
    "match_id": "2040337",
    "home": "巴西",
    "away": "日本",
    "direction": "主胜",
    "direction_key": "home",
    "predicted_score": "2-1",
    "had_odds": {"home": 1.52, "draw": 3.72, "away": 5.28},
    "crs_odds": 5.8,
}


class BetSimulatorTests(unittest.TestCase):
    def test_calc_single_payout(self) -> None:
        payout = calc_single_payout(100, 1.52)
        self.assertEqual(payout["return_if_win"], 152.0)
        self.assertEqual(payout["profit_if_win"], 52.0)

    def test_simulate_prediction_bet(self) -> None:
        result = simulate_prediction_bet(SAMPLE_PREDICTION, stake_had=100, stake_crs=50)
        self.assertEqual(result["total_stake"], 150)
        self.assertEqual(result["had"]["pick"], "主胜")
        self.assertEqual(result["crs"]["pick"], "2-1")

    def test_settle_had_win_crs_lose(self) -> None:
        results = {
            "had": {"code": "HAD", "combination": "H", "odds": "1.52"},
            "crs": {"code": "CRS", "combination": "1:0", "odds": "7.00"},
        }
        row = settle_against_results(SAMPLE_PREDICTION, results, stake_had=100, stake_crs=50)
        self.assertEqual(row["status"], "settled")
        self.assertTrue(row["had_won"])
        self.assertFalse(row["crs_won"])
        self.assertEqual(row["had_pnl"], 52.0)
        self.assertEqual(row["crs_pnl"], -50.0)
        self.assertEqual(row["total_pnl"], 2.0)


class CountdownTests(unittest.TestCase):
    def test_format_countdown_hours(self) -> None:
        self.assertEqual(format_countdown(5.5), "距开赛 5.5 小时")

    def test_hours_until_kickoff_future(self) -> None:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        beijing = ZoneInfo("Asia/Shanghai")
        match = {"match_date": "2099-01-01", "match_time": "12:00:00"}
        now = datetime(2099, 1, 1, 8, 0, tzinfo=beijing)
        hours = hours_until_kickoff(match, now=now)
        self.assertIsNotNone(hours)
        assert hours is not None
        self.assertAlmostEqual(hours, 4.0)


if __name__ == "__main__":
    unittest.main()
