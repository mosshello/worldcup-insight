"""国家队公开赛果清洗与时间切分测试。"""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from worldcup_mvp.international_data import InternationalMatch, _apply_regulation_overrides, _parse_match, split_training_data


class InternationalDataTests(unittest.TestCase):
    def test_future_na_fixture_is_excluded(self) -> None:
        row = {
            "date": "2026-07-19", "home_team": "A", "away_team": "B",
            "home_score": "NA", "away_score": "NA", "tournament": "FIFA World Cup", "neutral": "TRUE",
        }
        self.assertIsNone(_parse_match(row))

    def test_foundation_and_2026_test_are_disjoint(self) -> None:
        matches = [
            InternationalMatch(date(2022, 12, 18), "Argentina", "France", 3, 3, "FIFA World Cup", True),
            InternationalMatch(date(2025, 3, 1), "A", "B", 1, 0, "Friendly", False),
            InternationalMatch(date(2026, 6, 20), "C", "D", 2, 1, "FIFA World Cup", True),
            InternationalMatch(date(2026, 6, 20), "E", "F", 1, 1, "Friendly", True),
        ]
        split = split_training_data(matches)
        self.assertEqual(len(split["foundation"]), 2)
        self.assertEqual(len(split["world_cup_2026_test"]), 1)
        self.assertTrue(all(item.match_date <= date(2026, 6, 10) for item in split["foundation"]))

    def test_extra_time_score_is_restored_to_90_minutes(self) -> None:
        match = InternationalMatch(date(2026, 7, 3), "Argentina", "Cape Verde", 3, 2, "FIFA World Cup", True)
        payload = {
            "matches": [{
                "date": "2026-07-03", "home_team": "Argentina", "away_team": "Cape Verde",
                "home_goals_90": 1, "away_goals_90": 1,
            }]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "regulation.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch("worldcup_mvp.international_data.REGULATION_FILE", path):
                corrected, changed, appended = _apply_regulation_overrides([match])
        self.assertEqual(changed, 1)
        self.assertEqual(appended, 0)
        self.assertEqual((corrected[0].home_goals, corrected[0].away_goals), (1, 1))

    def test_new_espn_result_is_appended_when_csv_lags(self) -> None:
        payload = {
            "matches": [{
                "date": "2026-07-04", "home_team": "Canada", "away_team": "Morocco",
                "home_goals_90": 0, "away_goals_90": 3, "neutral": True,
            }]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "regulation.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch("worldcup_mvp.international_data.REGULATION_FILE", path):
                corrected, changed, appended = _apply_regulation_overrides([])
        self.assertEqual(changed, 0)
        self.assertEqual(appended, 1)
        self.assertEqual(corrected[0].away_goals, 3)


if __name__ == "__main__":
    unittest.main()
