"""导出与结算数据测试。"""

import json
import unittest
from unittest.mock import patch

from worldcup_mvp.dashboard_data import export_predictions_csv, export_predictions_payload


SAMPLE_PREDICTIONS = {
    "success": True,
    "source": "sporttery.cn",
    "count": 1,
    "predictions": [
        {
            "match_id": "1",
            "home": "巴西",
            "away": "日本",
            "league": "世界杯",
            "kickoff_beijing": "2026-06-30T01:00:00+08:00",
            "countdown_label": "距开赛 5.0 小时",
            "direction": "主胜",
            "predicted_score": "2-1",
            "confidence": "高",
            "sporttery_had": "1.52 / 3.72 / 5.28",
            "crs_odds": 5.8,
        }
    ],
}


class ExportTests(unittest.TestCase):
    @patch("worldcup_mvp.dashboard_data.get_upcoming_score_predictions")
    def test_export_predictions_payload(self, mock_get: unittest.mock.Mock) -> None:
        mock_get.return_value = SAMPLE_PREDICTIONS
        payload = export_predictions_payload()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["predictions"][0]["home"], "巴西")

    @patch("worldcup_mvp.dashboard_data.get_upcoming_score_predictions")
    def test_export_predictions_csv(self, mock_get: unittest.mock.Mock) -> None:
        mock_get.return_value = SAMPLE_PREDICTIONS
        csv_text = export_predictions_csv()
        self.assertIn("match_id", csv_text)
        self.assertIn("巴西", csv_text)


class SettlementModuleTests(unittest.TestCase):
    @patch("worldcup_mvp.settlement.fetch_fixed_bonus_detail")
    def test_settle_open_predictions(self, mock_detail: unittest.mock.Mock) -> None:
        from worldcup_mvp.prediction_journal import record_predictions
        from worldcup_mvp.settlement import settle_open_predictions

        record_predictions(
            [
                {
                    **SAMPLE_PREDICTIONS["predictions"][0],
                    "direction_key": "home",
                    "had_odds": {"home": 1.52, "draw": 3.0, "away": 5.0},
                }
            ]
        )
        mock_detail.return_value = {
            "match_result_list": [
                {"code": "HAD", "combination": "H", "odds": "1.52"},
                {"code": "CRS", "combination": "2:1", "odds": "5.80"},
            ],
            "odds_history": {},
            "is_cancel": False,
        }
        result = settle_open_predictions()
        self.assertTrue(result["success"])
        self.assertGreaterEqual(result["settled"], 1)


if __name__ == "__main__":
    unittest.main()
