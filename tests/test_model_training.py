"""训练门槛与概率评估测试。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worldcup_mvp.model_training import build_training_report


class ModelTrainingTests(unittest.TestCase):
    def test_small_sample_never_activates_calibration(self) -> None:
        record = {
            "sporttery_match_id": "1",
            "home": "A",
            "away": "B",
            "kickoff_beijing": "2026-07-01T10:00:00+08:00",
            "predicted": {
                "recorded_at": "2026-07-01T09:00:00+08:00",
                "probabilities": {"home": 0.6, "draw": 0.25, "away": 0.15},
            },
            "actual": {"had": "主胜", "score": "2:0"},
            "settlement": {
                "settled_at": "2026-07-01T12:00:00+08:00",
                "direction_hit": True,
                "score_hit": False,
            },
            "use_for_training": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "training.json"
            path.write_text(json.dumps({"schema_version": 2, "records": [record]}), encoding="utf-8")
            with patch("worldcup_mvp.training_store.TRAINING_FILE", path):
                report = build_training_report()
        self.assertFalse(report["activated"])
        self.assertEqual(report["valid_samples"], 1)
        self.assertEqual(report["direction_hit_rate"], 1.0)
        self.assertIsNotNone(report["brier_score"])

    @patch("worldcup_mvp.statistical_model.load_statistical_model")
    def test_report_exposes_statistical_shadow_model(self, mock_load) -> None:
        mock_load.return_value = {
            "model_version": "elo-poisson-test", "status": "shadow", "trained_at": "2026-07-05T00:00:00+08:00",
            "counts": {"train": 1000}, "metrics": {"world_cup_2026": {"log_loss": 1.0}},
            "activation": {"active": False},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "training.json"
            path.write_text(json.dumps({"schema_version": 2, "records": []}), encoding="utf-8")
            with patch("worldcup_mvp.training_store.TRAINING_FILE", path):
                report = build_training_report()
        self.assertTrue(report["statistical_model"]["available"])
        self.assertEqual(report["statistical_model"]["status"], "shadow")


if __name__ == "__main__":
    unittest.main()
