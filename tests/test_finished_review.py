"""已完场复盘测试。"""

import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo("Asia/Shanghai")


class FinishedReviewTests(unittest.TestCase):
    @patch("worldcup_mvp.finished_review.fetch_fixed_bonus_detail")
    @patch("worldcup_mvp.finished_review.upsert_entry")
    @patch("worldcup_mvp.finished_review.upsert_outcome")
    def test_settle_prediction_if_ready(
        self,
        mock_upsert_outcome: unittest.mock.Mock,
        mock_upsert_entry: unittest.mock.Mock,
        mock_detail: unittest.mock.Mock,
    ) -> None:
        from worldcup_mvp.finished_review import settle_prediction_if_ready

        mock_detail.return_value = {
            "match_result_list": [
                {"code": "HAD", "combination": "A", "odds": "1.80"},
                {"code": "CRS", "combination": "1:2", "odds": "5.00"},
            ],
            "is_cancel": False,
        }
        entry = {
            "match_id": "2040345",
            "home": "科特迪瓦",
            "away": "挪威",
            "kickoff_beijing": "2026-07-01T01:00:00+08:00",
            "direction_key": "away",
            "direction": "客胜",
            "predicted_score": "1-2",
            "stake_had": 100,
            "stake_crs": 50,
        }
        now = datetime(2026, 7, 1, 8, 0, tzinfo=BEIJING)
        row = settle_prediction_if_ready(entry, now=now)
        self.assertIsNotNone(row)
        self.assertTrue(row["had_won"])
        self.assertTrue(row["crs_won"])
        mock_upsert_entry.assert_called_once()
        mock_upsert_outcome.assert_called_once()


if __name__ == "__main__":
    unittest.main()
