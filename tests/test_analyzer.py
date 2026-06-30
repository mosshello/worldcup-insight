"""世界杯综合分析器测试。"""

import copy
import unittest

from worldcup_mvp.analyzer import analyze_match, backtest_match


class AnalyzeMatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.match = {
            "home": "主队",
            "away": "客队",
            "stage": "淘汰赛",
            "kickoff_beijing": "2026-06-30T01:00:00+08:00",
            "odds": {"home": 2.0, "draw": 4.0, "away": 5.0},
        }

    def test_probabilities_are_devigged(self) -> None:
        result = analyze_match(self.match)
        self.assertAlmostEqual(sum(result["probabilities"].values()), 1.0)
        self.assertAlmostEqual(result["probabilities"]["home"], 0.526315789, places=6)

    def test_old_input_falls_back_to_market(self) -> None:
        result = analyze_match(self.match)
        self.assertFalse(result["context_available"])
        self.assertEqual(result["market_probabilities"], result["context_probabilities"])
        self.assertEqual(result["pick"], result["context_pick"])

    def test_sporttery_details_are_preserved(self) -> None:
        self.match["sporttery"] = {
            "match_number": "周一075",
            "had": {"odds": {"home": 1.22, "draw": 5.0, "away": 9.1}},
            "hhad": {
                "handicap": -1,
                "odds": {"home": 1.69, "draw": 4.05, "away": 3.44},
            },
        }
        result = analyze_match(self.match)
        self.assertEqual(result["sporttery"], self.match["sporttery"])
        self.assertAlmostEqual(sum(result["sporttery_probabilities"].values()), 1.0)
        self.assertGreater(
            result["sporttery_probabilities"]["home"],
            result["sporttery_probabilities"]["draw"],
        )

    def test_context_probabilities_are_normalized_and_explained(self) -> None:
        self.match["team_context"] = {
            "home": {
                "group_stats": {
                    "played": 3,
                    "points": 9,
                    "goals_for": 8,
                    "goals_against": 1,
                },
                "absences": [],
                "scorers": [{"player": "甲", "goals": 3}],
            },
            "away": {
                "group_stats": {
                    "played": 3,
                    "points": 3,
                    "goals_for": 2,
                    "goals_against": 5,
                },
                "absences": [
                    {"player": "乙", "status": "suspended", "impact": 0.8}
                ],
                "scorers": [],
            },
        }
        result = analyze_match(self.match)
        self.assertTrue(result["context_available"])
        self.assertAlmostEqual(sum(result["context_probabilities"].values()), 1.0)
        self.assertGreater(
            result["context_probabilities"]["home"], result["market_probabilities"]["home"]
        )
        self.assertTrue(any(line.startswith("状态：") for line in result["analysis"]))
        self.assertTrue(any(line.startswith("人员：") for line in result["analysis"]))
        self.assertTrue(any(line.startswith("进球点：") for line in result["analysis"]))

    def test_actual_result_never_changes_prediction(self) -> None:
        baseline = analyze_match(self.match)
        leaked = copy.deepcopy(self.match)
        leaked["actual_result"] = {"home_goals": 0, "away_goals": 9, "outcome": "away"}
        self.assertEqual(analyze_match(leaked)["context_probabilities"], baseline["context_probabilities"])

    def test_backtest_hit_and_cutoff(self) -> None:
        self.match["actual_result"] = {"home_goals": 1, "away_goals": 0, "outcome": "home"}
        result = backtest_match(self.match, "2026-06-29T20:00:00+08:00")
        self.assertTrue(result["hit"])
        self.assertIn("仅1场", result["sample_note"])
        with self.assertRaisesRegex(ValueError, "早于开赛时间"):
            backtest_match(self.match, "2026-06-30T01:00:00+08:00")

    def test_backtest_requires_timezone(self) -> None:
        self.match["actual_result"] = {"home_goals": 1, "away_goals": 0, "outcome": "home"}
        with self.assertRaisesRegex(ValueError, "包含时区"):
            backtest_match(self.match, "2026-06-29T20:00:00")

    def test_backtest_rejects_outcome_that_conflicts_with_score(self) -> None:
        self.match["actual_result"] = {"home_goals": 0, "away_goals": 1, "outcome": "home"}
        with self.assertRaisesRegex(ValueError, "比分方向不一致"):
            backtest_match(self.match, "2026-06-29T20:00:00+08:00")

    def test_invalid_odds_are_rejected(self) -> None:
        self.match["odds"]["draw"] = 1.0
        with self.assertRaisesRegex(ValueError, "必须大于"):
            analyze_match(self.match)

    def test_invalid_absence_impact_is_rejected(self) -> None:
        self.match["team_context"] = {
            "home": {
                "group_stats": {"played": 3, "points": 3, "goals_for": 2, "goals_against": 2},
                "absences": [{"player": "甲", "status": "out", "impact": 1.2}],
            },
            "away": {
                "group_stats": {"played": 3, "points": 3, "goals_for": 2, "goals_against": 2}
            },
        }
        with self.assertRaisesRegex(ValueError, "小于等于 1"):
            analyze_match(self.match)


if __name__ == "__main__":
    unittest.main()
