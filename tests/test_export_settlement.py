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
    @patch("worldcup_mvp.settlement.fetch_fifa_fixture_score")
    @patch("worldcup_mvp.settlement.fetch_fixed_bonus_detail")
    def test_settle_open_predictions(
        self, mock_detail: unittest.mock.Mock, mock_fifa: unittest.mock.Mock
    ) -> None:
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
        mock_fifa.return_value = None
        result = settle_open_predictions()
        self.assertTrue(result["success"])
        self.assertGreaterEqual(result["settled"], 1)

    @patch("worldcup_mvp.settlement.fetch_fifa_fixture_score")
    @patch("worldcup_mvp.settlement.fetch_fixed_bonus_detail")
    def test_settle_with_fifa_review(
        self, mock_detail: unittest.mock.Mock, mock_fifa: unittest.mock.Mock
    ) -> None:
        from worldcup_mvp.prediction_journal import record_predictions
        from worldcup_mvp.settlement import settle_open_predictions

        record_predictions(
            [
                {
                    **SAMPLE_PREDICTIONS["predictions"][0],
                    "direction_key": "home",
                    "had_odds": {"home": 1.52, "draw": 3.0, "away": 5.0},
                    "provider_ids": {"fifa_match": "999", "sporttery_match": "1"},
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
        mock_fifa.return_value = {
            "score_label": "2:1",
            "outcome_key": "home",
            "outcome_label": "主胜",
        }
        result = settle_open_predictions()
        row = next(item for item in result["results"] if item.get("status") == "settled")
        self.assertTrue(row.get("score_hit_fifa"))
        self.assertTrue(row.get("direction_hit_fifa"))

    @patch("worldcup_mvp.dashboard_data.get_provider_health")
    @patch("worldcup_mvp.dashboard_data.get_settlement_summary")
    @patch("worldcup_mvp.dashboard_data.load_unified_index")
    @patch("worldcup_mvp.dashboard_data.get_upcoming_score_predictions")
    @patch("worldcup_mvp.dashboard_data.get_sporttery_matches")
    def test_overview_unified_mode(
        self,
        mock_matches: unittest.mock.Mock,
        mock_predictions: unittest.mock.Mock,
        mock_index: unittest.mock.Mock,
        mock_settlement: unittest.mock.Mock,
        mock_health: unittest.mock.Mock,
    ) -> None:
        from worldcup_mvp.dashboard_data import get_overview

        mock_matches.return_value = {"success": True, "match_count": 3, "matches": []}
        mock_predictions.return_value = {
            "success": True,
            "predictions": [
                {"match_id": "1", "home": "A", "away": "B"},
                {"match_id": "2", "home": "C", "away": "D"},
            ],
        }
        mock_index.return_value = {
            "success": True,
            "fixture_date": "2026-06-30",
            "match_count": 1,
            "by_sporttery_id": {"1": {"provider_ids": {"sporttery_match": "1"}}},
        }
        mock_settlement.return_value = {"open_count": 0, "settled_count": 0, "total_pnl": 0}
        mock_health.return_value = {"success": True, "providers": [], "all_ok": True}
        payload = get_overview(mode="unified")
        self.assertEqual(payload["mode"], "unified")
        self.assertEqual(payload["predictions"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
