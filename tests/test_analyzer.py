"""胜平负分析器测试。"""

import unittest

from worldcup_mvp.analyzer import analyze_match


class AnalyzeMatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.match = {
            "home": "主队",
            "away": "客队",
            "stage": "淘汰赛",
            "odds": {"home": 2.0, "draw": 4.0, "away": 5.0},
        }

    def test_probabilities_are_devigged(self) -> None:
        result = analyze_match(self.match)
        self.assertAlmostEqual(sum(result["probabilities"].values()), 1.0)
        self.assertAlmostEqual(result["probabilities"]["home"], 0.526315789, places=6)

    def test_ranking_and_pick(self) -> None:
        result = analyze_match(self.match)
        self.assertEqual(result["ranking"], ["home", "draw", "away"])
        self.assertEqual(result["pick"], "home")
        self.assertEqual(result["second_pick"], "draw")

    def test_knockout_analysis_explains_ninety_minutes(self) -> None:
        result = analyze_match(self.match)
        self.assertTrue(any("90 分钟" in line for line in result["analysis"]))
        self.assertTrue(any("加时赛和点球大战" in line for line in result["analysis"]))

    def test_invalid_odds_are_rejected(self) -> None:
        self.match["odds"]["draw"] = 1.0
        with self.assertRaisesRegex(ValueError, "必须大于"):
            analyze_match(self.match)


if __name__ == "__main__":
    unittest.main()
