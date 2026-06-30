"""三源桥接层测试。"""

import unittest
from unittest.mock import MagicMock, patch

from worldcup_mvp.unified_bridge import (
    PROBABILITY_DELTA_ALERT_PP,
    delta_alerts,
    enrich_prediction_record,
    get_provider_health,
    load_unified_index,
    probability_deltas,
)


class UnifiedBridgeTests(unittest.TestCase):
    def test_probability_deltas(self) -> None:
        deltas = probability_deltas(
            {"home": 0.5, "draw": 0.3, "away": 0.2},
            {"home": 0.4, "draw": 0.35, "away": 0.25},
        )
        self.assertEqual(deltas["home"], -10.0)
        self.assertEqual(deltas["draw"], 5.0)

    def test_delta_alerts(self) -> None:
        alerts = delta_alerts({"home": -10.0, "draw": 5.0, "away": 2.0})
        self.assertEqual(alerts, ["home", "draw"])

    @patch("worldcup_mvp.unified_bridge.UnifiedDataManager.from_env")
    def test_get_provider_health(self, mock_from_env: MagicMock) -> None:
        mock_from_env.return_value.doctor.return_value = {
            "configuration": "ok-no-api-key-required",
            "providers": [
                {"provider": "fifa-public", "ok": True},
                {"provider": "polymarket-public", "ok": True},
                {"provider": "sporttery-public", "ok": False},
            ],
        }
        report = get_provider_health()
        self.assertTrue(report["success"])
        self.assertFalse(report["all_ok"])
        self.assertEqual(len(report["providers"]), 3)

    @patch("worldcup_mvp.unified_bridge.UnifiedDataManager.from_env")
    def test_load_unified_index(self, mock_from_env: MagicMock) -> None:
        from worldcup_mvp import unified_bridge

        unified_bridge._index_cache = None
        unified_bridge._index_date = None
        mock_from_env.return_value.collect.return_value = {
            "data_as_of": "2026-06-30T00:00:00+00:00",
            "matches": [
                {
                    "home": "巴西",
                    "away": "日本",
                    "odds": {"home": 1.6, "draw": 3.8, "away": 5.0},
                    "provider_ids": {"sporttery_match": "2040337", "fifa_match": "123"},
                }
            ],
        }
        index = load_unified_index("2026-06-30", force=True)
        self.assertTrue(index["success"])
        self.assertIn("2040337", index["by_sporttery_id"])

    @patch("worldcup_mvp.unified_bridge.get_unified_match")
    def test_enrich_prediction_record(self, mock_get: MagicMock) -> None:
        mock_get.return_value = {
            "home": "巴西",
            "away": "日本",
            "odds": {"home": 1.6, "draw": 3.8, "away": 5.0},
            "provider_ids": {"sporttery_match": "1", "fifa_match": "99"},
            "team_context": {
                "home": {
                    "group_stats": {
                        "played": 3,
                        "points": 6,
                        "goals_for": 5,
                        "goals_against": 2,
                    },
                    "absences": [],
                    "scorers": [],
                },
                "away": {
                    "group_stats": {
                        "played": 3,
                        "points": 4,
                        "goals_for": 4,
                        "goals_against": 3,
                    },
                    "absences": [],
                    "scorers": [],
                },
            },
        }
        enriched = enrich_prediction_record({"match_id": "1", "home": "巴西", "away": "日本"})
        self.assertEqual(enriched["provider_ids"]["fifa_match"], "99")
        self.assertIn("context_pick", enriched)


if __name__ == "__main__":
    unittest.main()
