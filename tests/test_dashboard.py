"""仪表盘数据聚合测试。"""

import unittest
from unittest import mock
from unittest.mock import patch

from worldcup_mvp.dashboard_data import (
    _enrich_prediction_timing,
    _merge_finished_reviews,
    _pending_prediction_from_match,
    _split_date_tabs,
    build_snapshot_series,
    get_history_dashboard,
    get_overview,
    get_upcoming_score_predictions,
)


class DashboardDataTests(unittest.TestCase):
    def test_overview_includes_yesterday_date(self) -> None:
        payload = get_overview()
        stats = payload.get("dashboard_stats") or {}
        self.assertIn("yesterday_date", stats)
        self.assertTrue(stats["yesterday_date"])
        self.assertIn("predictions", payload)
        self.assertIn("provider_health", payload)
        self.assertIn("unified_index", payload)

    def test_split_date_tabs_hides_before_yesterday(self) -> None:
        tabs = [
            {"date": "", "label": "全部"},
            {"date": "2026-06-29", "label": "06-29"},
            {"date": "2026-06-30", "label": "昨日"},
            {"date": "2026-07-01", "label": "今天"},
        ]
        visible, older = _split_date_tabs(tabs, "2026-06-30")
        self.assertEqual([tab["date"] for tab in visible], ["", "2026-06-30", "2026-07-01"])
        self.assertEqual(len(older), 1)

    def test_overview_date_tabs_default(self) -> None:
        payload = get_overview()
        stats = payload.get("dashboard_stats") or {}
        self.assertIn("date_tabs_default", stats)
        default_dates = [tab["date"] for tab in stats["date_tabs_default"] if tab.get("date")]
        if stats.get("yesterday_date") and stats.get("older_date_count", 0) > 0:
            self.assertTrue(all(day >= stats["yesterday_date"] for day in default_dates))

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
            "worldcup_mvp.dashboard_data._auto_settle_finished", return_value={}
        ), patch(
            "worldcup_mvp.dashboard_data.fetch_announced_matches", return_value=[match]
        ), patch(
            "worldcup_mvp.dashboard_data._build_fusion_prediction", return_value=full
        ) as build, patch(
            "worldcup_mvp.dashboard_data._merge_journal_predictions", side_effect=lambda items: items
        ), patch(
            "worldcup_mvp.dashboard_data._merge_finished_reviews", side_effect=lambda items: items
        ), patch(
            "worldcup_mvp.dashboard_data._enrich_prediction_timing", side_effect=lambda item: item
        ), patch(
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

    def test_selling_without_had_keeps_intelligence_context(self) -> None:
        match = {
            "match_id": "2040348",
            "home": "阿根廷",
            "away": "佛得角",
            "league": "世界杯",
            "match_num": "周五087",
            "match_date": "2026-07-04",
            "kickoff_beijing": "2026-07-04T06:00+08:00",
            "match_status": "Selling",
            "sale_status": "pending",
            "pools": {"had": None, "hhad": None},
        }
        bonus = {"crsList": [{"s02s00": "4.75", "s01s00": "6.50"}]}
        with patch("worldcup_mvp.dashboard_data.get_unified_match", return_value=None), patch(
            "worldcup_mvp.dashboard_data.fetch_fixed_bonus", return_value=bonus
        ):
            card = _pending_prediction_from_match(match)

        self.assertEqual(card["sale_status"], "selling_partial")
        self.assertEqual(card["direction"], "已开售")
        self.assertEqual(card["predicted_score"], "2-0")
        self.assertTrue(card["ai_context_available"])
        self.assertTrue(card["match_intelligence"]["available"])

    def test_enrich_prediction_timing_preserves_finished(self) -> None:
        finished = {
            "match_id": "2040345",
            "card_type": "finished",
            "settlement_status": "settled",
            "lifecycle_phase": "finished",
            "countdown_label": "已完场",
            "kickoff_beijing": "2026-07-01T01:00:00+08:00",
        }
        enriched = _enrich_prediction_timing(finished)
        self.assertEqual(enriched["lifecycle_phase"], "finished")
        self.assertEqual(enriched["countdown_label"], "已完场")

    @mock.patch("worldcup_mvp.dashboard_data.list_finished_review_cards")
    def test_merge_finished_reviews_replaces_upcoming(
        self,
        mock_finished: mock.Mock,
    ) -> None:
        mock_finished.return_value = [
            {
                "match_id": "2040351",
                "card_type": "finished",
                "settlement_status": "settled",
                "lifecycle_phase": "finished",
                "countdown_label": "已完场",
            }
        ]
        upcoming = [
            {
                "match_id": "2040351",
                "card_type": "upcoming",
                "lifecycle_phase": "awaiting_result",
                "countdown_label": "待出赛果",
            },
            {"match_id": "2040400", "card_type": "upcoming"},
        ]
        merged = _merge_finished_reviews(upcoming)
        by_id = {item["match_id"]: item for item in merged}
        self.assertEqual(by_id["2040351"]["card_type"], "finished")
        self.assertEqual(by_id["2040351"]["settlement_status"], "settled")
        self.assertIn("2040400", by_id)


if __name__ == "__main__":
    unittest.main()
