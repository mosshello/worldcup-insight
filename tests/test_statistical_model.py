"""Elo + 双 Poisson 模型测试。"""

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from worldcup_mvp.international_data import InternationalMatch
from worldcup_mvp.statistical_model import (
    TeamState,
    predict_with_state,
    score_matrix,
    train_statistical_model,
)


class StatisticalModelTests(unittest.TestCase):
    def test_score_matrix_and_markets_are_consistent(self) -> None:
        matrix = score_matrix(1.8, 0.9)
        self.assertAlmostEqual(sum(sum(row) for row in matrix), 1.0, places=9)
        prediction = predict_with_state(
            TeamState(ratings={"A": 1650, "B": 1450}), "A", "B", neutral=True,
            params={"base_goals": 1.25, "elo_goal_coefficient": 0.4, "home_goal_advantage": 0.12},
        )
        self.assertAlmostEqual(sum(prediction["had"].values()), 1.0, places=5)
        self.assertAlmostEqual(prediction["over_2_5"] + prediction["under_2_5"], 1.0, places=5)
        self.assertGreater(prediction["had"]["home"], prediction["had"]["away"])

    def test_training_keeps_2026_as_sealed_test(self) -> None:
        teams = ["A", "B", "C", "D", "E", "F"]
        matches = []
        start = date(2024, 6, 11)
        for index in range(1100):
            match_date = start + timedelta(days=index % 729)
            home = teams[index % len(teams)]
            away = teams[(index + 1 + index // len(teams)) % len(teams)]
            if home == away:
                away = teams[(teams.index(away) + 1) % len(teams)]
            matches.append(
                InternationalMatch(match_date, home, away, (index * 3) % 4, (index * 5 + 1) % 3, "Friendly", index % 2 == 0)
            )
        for index in range(12):
            matches.append(
                InternationalMatch(date(2026, 6, 11) + timedelta(days=index), teams[index % 6], teams[(index + 2) % 6], index % 3, (index + 1) % 2, "FIFA World Cup", True)
            )
        matches.sort(key=lambda item: item.match_date)
        with tempfile.TemporaryDirectory() as tmp, patch(
            "worldcup_mvp.statistical_model.load_international_matches",
            return_value=(matches, {"sha256": "abc", "source_page": "test"}),
        ), patch("worldcup_mvp.statistical_model.MODEL_FILE", Path(tmp) / "model.json"):
            artifact = train_statistical_model()
        self.assertEqual(artifact["counts"]["world_cup_2026_test"], 12)
        self.assertEqual(artifact["boundaries"]["world_cup_2026_role"], "sealed prequential test; predict then update state")
        self.assertFalse(artifact["activation"]["active"])
        self.assertIsNotNone(artifact["metrics"]["world_cup_2026"]["log_loss"])


if __name__ == "__main__":
    unittest.main()
