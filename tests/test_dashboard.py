"""仪表盘数据聚合测试。"""

import unittest
from unittest.mock import patch

from worldcup_mvp.dashboard_data import (
    build_snapshot_series,
    get_history_dashboard,
    get_overview,
    get_upcoming_score_predictions,
)


class DashboardDataTests(unittest.TestCase):
    def test_overview_has_sporttery(self) -> None:
        payload = get_overview()
        self.assertIn("sporttery", payload)
        self.assertIn("predictions", payload)
        self.assertIn("provider_health", payload)
        self.assertIn("unified_index", payload)

    def test_history_dashboard(self) -> None:
        payload = get_history_dashboard("odds_history_bra-jpn.json")
        self.assertEqual(payload["history"]["home"], "巴西")
        self.assertIn("series", payload)
        self.assertIn("movement", payload)
        self.assertEqual(len(payload["series"]["times"]), payload["history"]["snapshots_count"])

    def test_build_snapshot_series(self) -> None:
        payload = get_history_dashboard("odds_history_bra-jpn.json")
        series = build_snapshot_series(
            {
                "home": "巴西",
                "away": "日本",
                "snapshots": [
                    {
                        "recorded_at": "t1",
                        "european": {"home": 2.0, "draw": 3.5, "away": 4.0},
                    }
                ],
            }
        )
        self.assertEqual(series["european"]["home"], [2.0])
        self.assertIsNotNone(payload)

    def test_batch_prediction_runs_full_pipeline(self) -> None:
        match = {
            "match_id": "99",
            "home": "甲",
            "away": "乙",
            "business_date": "2099-01-01",
            "match_date": "2099-01-02",
            "match_time": "00:00:00",
            "kickoff_beijing": "2099-01-02T00:00:00+08:00",
            "analysis_available": True,
            "match_status": "Selling",
            "pools": {"had": {"home": 2.0, "draw": 3.0, "away": 4.0}},
        }
        full = {
            "score_prediction": {
                "match_id": "99",
                "home": "甲",
                "away": "乙",
                "direction": "主胜",
                "predicted_score": "1-0",
            },
            "prediction": {"direction_key": "home"},
            "pool_analysis": {"coverage": {"ttg": True, "hafu": True}},
            "context_analysis": {"available": True},
            "match_intelligence": {"available": True},
            "provider_ids": {"sporttery_match": "99"},
            "probability_deltas_pp": {},
            "probability_delta_alerts": [],
            "data_sources": {"sporttery": True, "pool_ttg": True, "pool_hafu": True},
            "pipeline_status": "complete",
        }
        with patch(
            "worldcup_mvp.dashboard_data.fetch_announced_matches", return_value=[match]
        ), patch(
            "worldcup_mvp.dashboard_data._build_fusion_prediction", return_value=full
        ) as build, patch(
            "worldcup_mvp.dashboard_data.load_unified_index_merged", return_value={}
        ), patch(
            "worldcup_mvp.dashboard_data.enrich_prediction_record", side_effect=lambda item: item
        ), patch(
            "worldcup_mvp.dashboard_data.record_predictions"
        ) as record, patch(
            "worldcup_mvp.dashboard_data.record_daily_bet"
        ), patch("worldcup_mvp.dashboard_data.save_snapshot"):
            payload = get_upcoming_score_predictions()

        self.assertTrue(payload["success"])
        self.assertEqual(payload["predictions"][0]["pipeline_status"], "complete")
        self.assertIn("pool_analysis", payload["predictions"][0])
        build.assert_called_once()
        record.assert_called_once()


if __name__ == "__main__":
    unittest.main()
