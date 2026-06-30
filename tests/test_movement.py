"""盘口变动分析测试。"""

import json
import tempfile
import unittest
from pathlib import Path

from worldcup_mvp.collector import FileFeedCollector, StaticCollector
from worldcup_mvp.movement_analyzer import analyze_movement
from worldcup_mvp.odds_snapshot import append_snapshot, load_history


SAMPLE_HISTORY = {
    "match_id": "test-match",
    "home": "主队",
    "away": "客队",
    "snapshots": [
        {
            "recorded_at": "2026-06-29T10:00:00+08:00",
            "european": {"home": 2.0, "draw": 3.5, "away": 4.0},
            "asian_handicap": {"line": -0.5, "home": 0.95, "away": 0.95},
        },
        {
            "recorded_at": "2026-06-29T12:00:00+08:00",
            "european": {"home": 1.9, "draw": 3.5, "away": 4.2},
            "asian_handicap": {"line": -0.75, "home": 0.92, "away": 0.98},
        },
    ],
}


class OddsSnapshotTests(unittest.TestCase):
    def test_append_and_load_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            append_snapshot(
                path,
                {"european": {"home": 2.0, "draw": 3.5, "away": 4.0}},
                match_id="test-match",
                home="主队",
                away="客队",
            )
            append_snapshot(
                path,
                {"european": {"home": 1.9, "draw": 3.5, "away": 4.2}},
            )
            payload = load_history(path)
            self.assertEqual(len(payload["snapshots"]), 2)

    def test_duplicate_snapshot_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            snapshot = {"european": {"home": 2.0, "draw": 3.5, "away": 4.0}}
            append_snapshot(path, snapshot, match_id="test-match", home="主队", away="客队")
            append_snapshot(path, snapshot)
            payload = load_history(path)
            self.assertEqual(len(payload["snapshots"]), 1)


class MovementAnalyzerTests(unittest.TestCase):
    def test_detects_home_strengthening(self) -> None:
        result = analyze_movement(SAMPLE_HISTORY)
        home_move = next(item for item in result["european_movement"] if item["label"] == "主胜")
        self.assertEqual(home_move["direction"], "down")
        self.assertTrue(any("主队受热" in line for line in result["analysis"]))

    def test_requires_at_least_two_snapshots(self) -> None:
        history = {**SAMPLE_HISTORY, "snapshots": SAMPLE_HISTORY["snapshots"][:1]}
        with self.assertRaisesRegex(ValueError, "至少"):
            analyze_movement(history)


class CollectorTests(unittest.TestCase):
    def test_file_feed_collector(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feed_path = Path(tmp) / "feed.json"
            feed_path.write_text(
                json.dumps(
                    {
                        "match_id": "test-match",
                        "european": {"home": 1.8, "draw": 3.4, "away": 4.5},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            collector = FileFeedCollector(feed_path)
            snapshot = collector.fetch("test-match")
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot["european"]["home"], 1.8)

    def test_static_collector(self) -> None:
        collector = StaticCollector({"european": {"home": 2.1, "draw": 3.3, "away": 3.8}})
        snapshot = collector.fetch("any")
        self.assertEqual(snapshot["european"]["draw"], 3.3)


if __name__ == "__main__":
    unittest.main()
