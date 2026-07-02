"""变盘后预测测试。"""

import unittest

from worldcup_mvp.shift_prediction import best_score_for_direction, build_shift_prediction, outcome_key


class ShiftPredictionTests(unittest.TestCase):
    def test_outcome_key(self) -> None:
        self.assertEqual(outcome_key(2, 1), "home")
        self.assertEqual(outcome_key(1, 1), "draw")
        self.assertEqual(outcome_key(0, 2), "away")

    def test_best_score_for_direction(self) -> None:
        crs_top = [(2, 0, 8.0), (1, 1, 6.5), (0, 2, 7.0)]
        self.assertEqual(best_score_for_direction(crs_top, "home"), "2-0")
        self.assertEqual(best_score_for_direction(crs_top, "draw"), "1-1")

    def test_build_shift_prediction_on_flip(self) -> None:
        direction_shift = {
            "available": True,
            "direction_flipped": True,
            "recent_flipped": False,
            "severity": "high",
            "opening_pick": "home",
            "current_pick": "away",
            "opening_label": "主胜",
            "current_label": "客胜",
            "alerts": ["方向转向：初盘倾向主胜，当前去水首选客胜。"],
            "movement_lines": ["客胜 SP 下调 5.0%（受热）"],
        }
        crs_top = [(2, 0, 8.0), (1, 1, 6.5), (0, 2, 7.0)]
        result = build_shift_prediction(
            direction_shift,
            crs_top,
            journal_entry=None,
            current_direction_key="away",
            current_predicted_score="0-2",
        )
        self.assertTrue(result["active"])
        self.assertTrue(result["changed"])
        self.assertEqual(result["initial"]["direction_key"], "home")
        self.assertEqual(result["initial"]["predicted_score"], "2-0")
        self.assertEqual(result["adjusted"]["direction_key"], "away")
        self.assertEqual(result["adjusted"]["predicted_score"], "0-2")

    def test_journal_initial_snapshot(self) -> None:
        direction_shift = {
            "available": True,
            "direction_flipped": False,
            "recent_flipped": True,
            "severity": "medium",
            "opening_pick": "home",
            "current_pick": "draw",
            "opening_label": "主胜",
            "current_label": "平",
            "alerts": ["近期转向"],
            "movement_lines": [],
        }
        journal_entry = {
            "initial_direction_key": "home",
            "initial_direction": "主胜",
            "initial_predicted_score": "2-0",
        }
        result = build_shift_prediction(
            direction_shift,
            [(2, 0, 8.0), (1, 1, 6.0)],
            journal_entry=journal_entry,
            current_direction_key="draw",
            current_predicted_score="1-1",
        )
        self.assertEqual(result["initial"]["label"], "首次记录预测")
        self.assertEqual(result["initial"]["predicted_score"], "2-0")
        self.assertEqual(result["adjusted"]["direction"], "平")


if __name__ == "__main__":
    unittest.main()
