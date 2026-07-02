"""训练语料与日志迁移测试。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worldcup_mvp.prediction_journal import JOURNAL_FILE, load_journal
from worldcup_mvp.training_store import (
    TRAINING_FILE,
    append_outcome,
    audit_training_corpus,
    build_outcome_from_settlement,
    get_training_summary,
    load_training_corpus,
)


class TrainingStoreTests(unittest.TestCase):
    def test_append_outcome_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            training_path = Path(tmp) / "historical_outcomes.json"
            with patch("worldcup_mvp.training_store.TRAINING_FILE", training_path):
                record = {
                    "sporttery_match_id": "2040345",
                    "home": "科特迪瓦",
                    "away": "挪威",
                    "kickoff_beijing": "2026-07-01T01:00:00+08:00",
                    "predicted": {"recorded_at": "2026-06-30T20:00:00+08:00"},
                    "actual": {"had": "客胜", "score": "1:2"},
                    "settlement": {"settled_at": "2026-07-01T03:00:00+08:00"},
                    "source": "live_settlement",
                }
                self.assertTrue(append_outcome(record))
                self.assertFalse(append_outcome(record))
                summary = get_training_summary()
                self.assertEqual(summary["training_count"], 1)
                self.assertEqual(summary["invalid_count"], 0)

    def test_rejects_pre_kickoff_settlement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            training_path = Path(tmp) / "historical_outcomes.json"
            with patch("worldcup_mvp.training_store.TRAINING_FILE", training_path):
                record = {
                    "sporttery_match_id": "2040353",
                    "home": "比利时",
                    "away": "塞内加尔",
                    "kickoff_beijing": "2026-07-02T04:00:00+08:00",
                    "predicted": {"recorded_at": "2026-07-01T20:00:00+08:00"},
                    "actual": {"had": "平", "score": "2:2"},
                    "settlement": {"settled_at": "2026-07-01T21:00:00+08:00"},
                    "source": "live_settlement",
                }
                self.assertFalse(append_outcome(record))
                audit = audit_training_corpus()
                self.assertEqual(audit["total_count"], 0)

    def test_build_outcome_from_settlement(self) -> None:
        entry = {
            "match_id": "2040345",
            "home": "科特迪瓦",
            "away": "挪威",
            "direction_key": "away",
            "predicted_score": "1-2",
            "kickoff_beijing": "2026-07-01T01:00:00+08:00",
            "recorded_at": "2026-06-30T20:00:00+08:00",
        }
        row = {
            "actual_had": "客胜",
            "actual_score": "1:2",
            "total_pnl": 80.0,
            "settled_at": "2026-07-01T10:00:00+08:00",
        }
        outcome = build_outcome_from_settlement(entry, row)
        self.assertEqual(outcome["sporttery_match_id"], "2040345")
        self.assertEqual(outcome["actual"]["score"], "1:2")
        self.assertEqual(outcome["source"], "live_settlement")


class JournalMigrationTests(unittest.TestCase):
    def test_migrate_clears_dev_settlements(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "prediction_journal.json"
            archive_path = Path(tmp) / "archive_dev_settlements.json"
            training_path = Path(tmp) / "historical_outcomes.json"
            journal_path.write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "match_id": "1",
                                "status": "settled",
                                "recorded_at": "2026-06-30T10:00:00+08:00",
                            },
                            {
                                "match_id": "2040345",
                                "status": "open",
                                "recorded_at": "2026-06-30T16:50:43+08:00",
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch("worldcup_mvp.prediction_journal.JOURNAL_FILE", journal_path), patch(
                "worldcup_mvp.training_store.ARCHIVE_DEV_FILE", archive_path
            ), patch("worldcup_mvp.training_store.TRAINING_FILE", training_path):
                payload = load_journal()
                self.assertEqual(payload.get("journal_version"), 2)
                self.assertEqual(len(payload["entries"]), 1)
                self.assertEqual(payload["entries"][0]["match_id"], "2040345")
                self.assertTrue(archive_path.exists())


if __name__ == "__main__":
    unittest.main()
