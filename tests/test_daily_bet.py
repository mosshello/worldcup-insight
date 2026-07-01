"""每日稳健模拟投注测试。"""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from worldcup_mvp.daily_bet import record_daily_bet, select_stable_pick


def _prediction(match_id: str, odds: float, kickoff: str) -> dict:
    return {
        "match_id": match_id,
        "match_num": f"周三{match_id}",
        "business_date": "2026-07-01",
        "home": f"主队{match_id}",
        "away": f"客队{match_id}",
        "kickoff_beijing": kickoff,
        "direction": "主胜",
        "direction_key": "home",
        "confidence": "高",
        "aligned_with_fox": True,
        "had_odds": {"home": odds, "draw": 5.0, "away": 10.0},
    }


class DailyBetTests(unittest.TestCase):
    def test_selects_highest_devig_probability(self) -> None:
        selected = select_stable_pick(
            [_prediction("1", 1.25, "2026-07-02T04:00:00+08:00"), _prediction("2", 1.16, "2026-07-02T00:00:00+08:00")],
            business_date="2026-07-01",
        )
        self.assertEqual(selected["match_id"], "2")

    def test_records_one_thousand_yuan_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "daily_bets.json"
            predictions = [_prediction("2", 1.16, "2026-07-02T00:00:00+08:00")]
            record_daily_bet(predictions, today=date(2026, 7, 1), path=path)
            record_daily_bet(predictions, today=date(2026, 7, 1), path=path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["entries"]), 1)
            self.assertEqual(payload["entries"][0]["stake"], 1000.0)
            self.assertEqual(payload["entries"][0]["potential_return"], 1160.0)


if __name__ == "__main__":
    unittest.main()
