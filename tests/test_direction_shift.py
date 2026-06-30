"""水位转向与冷门提醒测试。"""

import unittest

from worldcup_mvp.direction_shift import analyze_direction_shift


def _point(home: float, draw: float, away: float, recorded_at: str) -> dict:
    return {
        "recorded_at": recorded_at,
        "home": home,
        "draw": draw,
        "away": away,
    }


class DirectionShiftTests(unittest.TestCase):
    def test_detects_direction_flip(self) -> None:
        history = [
            _point(1.50, 3.80, 6.50, "2026-06-29T10:00:00"),
            _point(2.80, 3.20, 2.40, "2026-06-29T18:00:00"),
        ]
        result = analyze_direction_shift(history)
        self.assertTrue(result["available"])
        self.assertTrue(result["direction_flipped"])
        self.assertEqual(result["opening_pick"], "home")
        self.assertEqual(result["current_pick"], "away")
        self.assertEqual(result["severity"], "high")
        self.assertTrue(any("方向转向" in alert for alert in result["alerts"]))

    def test_detects_upset_heating(self) -> None:
        history = [
            _point(1.45, 4.00, 7.00, "2026-06-29T10:00:00"),
            _point(1.55, 3.90, 5.50, "2026-06-29T18:00:00"),
        ]
        result = analyze_direction_shift(history)
        self.assertTrue(result["available"])
        self.assertFalse(result["direction_flipped"])
        self.assertIn("客胜", result["upset_candidates"])
        self.assertTrue(any("冷门受热" in alert for alert in result["alerts"]))

    def test_requires_two_points(self) -> None:
        history = [_point(1.80, 3.40, 4.20, "2026-06-29T10:00:00")]
        result = analyze_direction_shift(history)
        self.assertFalse(result["available"])

    def test_journal_mismatch_alert(self) -> None:
        history = [
            _point(1.60, 3.60, 5.80, "2026-06-29T10:00:00"),
            _point(1.58, 3.55, 6.00, "2026-06-29T18:00:00"),
        ]
        result = analyze_direction_shift(history, journal_direction_key="away")
        self.assertTrue(any("首次记录方向" in alert for alert in result["alerts"]))


if __name__ == "__main__":
    unittest.main()
