"""仪表盘数据聚合测试。"""

import unittest

from worldcup_mvp.dashboard_data import build_snapshot_series, get_history_dashboard, get_overview


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


if __name__ == "__main__":
    unittest.main()
