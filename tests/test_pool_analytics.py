"""体彩多玩法与凯利分析测试。"""

from __future__ import annotations

import unittest

from worldcup_mvp.pool_analytics import (
    analyze_hafu,
    analyze_ttg,
    build_pool_analysis,
    derive_pool_metrics,
    kelly_value_vs_reference,
    parse_hafu_latest,
    parse_ttg_latest,
)

SAMPLE_TTG = {
    "s0": "11.00",
    "s1": "5.00",
    "s2": "3.55",
    "s3": "3.70",
    "s4": "4.95",
    "s5": "9.00",
    "s6": "17.00",
    "s7": "25.00",
}

SAMPLE_HAFU = {
    "hh": "2.50",
    "hd": "13.50",
    "ha": "29.00",
    "dh": "4.10",
    "dd": "5.70",
    "da": "11.00",
    "ah": "19.00",
    "ad": "13.50",
    "aa": "9.15",
}


class PoolAnalyticsTests(unittest.TestCase):
    def test_parse_ttg_orders_by_lowest_odds(self) -> None:
        rows = parse_ttg_latest(SAMPLE_TTG)
        self.assertEqual(rows[0]["key"], "s2")

    def test_parse_hafu(self) -> None:
        rows = parse_hafu_latest(SAMPLE_HAFU)
        self.assertEqual(rows[0]["label"], "胜胜")

    def test_derive_pool_metrics(self) -> None:
        metrics = derive_pool_metrics({"home": 1.5, "draw": 3.5, "away": 5.0})
        self.assertGreater(metrics["return_rate"], 0.85)
        self.assertAlmostEqual(
            sum(metrics["no_vig_probabilities"].values()),
            1.0,
            places=4,
        )

    def test_kelly_value(self) -> None:
        rows = kelly_value_vs_reference(
            {"home": 1.5, "draw": 3.5, "away": 5.0},
            {"home": 0.7, "draw": 0.2, "away": 0.1},
        )
        self.assertTrue(any(row["is_value"] for row in rows))

    def test_analyze_ttg_bands(self) -> None:
        result = analyze_ttg(parse_ttg_latest(SAMPLE_TTG))
        self.assertTrue(result["available"])
        self.assertIn("under_2_5", result["bands"])

    def test_analyze_hafu_with_had(self) -> None:
        result = analyze_hafu(parse_hafu_latest(SAMPLE_HAFU), had_direction="主胜")
        self.assertTrue(result["available"])
        self.assertTrue(any("胜平负" in line for line in result["summary_bullets"]))

    def test_build_pool_analysis(self) -> None:
        history = {"ttg_history": [SAMPLE_TTG], "hafu_history": [SAMPLE_HAFU]}
        report = build_pool_analysis(
            odds_history=history,
            had_odds={"home": 1.49, "draw": 3.72, "away": 5.28},
            had_direction_key="home",
            foreign_odds={"home": 1.69, "draw": 3.8, "away": 5.2},
        )
        self.assertTrue(report["coverage"]["ttg"])
        self.assertTrue(report["coverage"]["hafu"])
        self.assertTrue(report["coverage"]["kelly_vs_foreign"])


class TeamMapSyncTests(unittest.TestCase):
    def test_sync_adds_new_only(self) -> None:
        from worldcup_mvp.team_names import sync_team_map_entries

        base = {"巴西": {"en": "Brazil", "abbr": "BRA"}}
        mapping, added = sync_team_map_entries(["巴西", "日本"], team_map=base)
        self.assertEqual(added, ["日本"])
        self.assertIn("日本", mapping)


if __name__ == "__main__":
    unittest.main()
