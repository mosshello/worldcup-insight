"""每日稳健模拟投注测试。"""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from worldcup_mvp.daily_bet import (
    record_daily_bet,
    select_stable_parlay,
    select_stable_pick,
    select_stable_picks,
    summarize_ledger,
)


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
            predictions = [
                _prediction("2", 1.16, "2026-07-02T00:00:00+08:00"),
                _prediction("3", 1.22, "2026-07-02T08:00:00+08:00"),
                _prediction("4", 1.80, "2026-07-02T10:00:00+08:00"),
            ]
            record_daily_bet(predictions, today=date(2026, 7, 1), path=path)
            record_daily_bet(predictions, today=date(2026, 7, 1), path=path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["entries"]), 1)
            entry = payload["entries"][0]
            self.assertEqual(entry["total_stake"], 1000.0)
            self.assertEqual(entry["single"]["stake"], 600.0)
            self.assertEqual(entry["parlay"]["stake"], 400.0)
            self.assertEqual(len(entry["parlay"]["legs"]), 2)
            self.assertEqual(entry["single"]["potential_return"], 696.0)
            self.assertGreaterEqual(entry["parlay"]["combined_odds"], 2.0)
            self.assertEqual(entry["parlay"]["potential_return"], 835.2)

    def test_ranks_two_distinct_matches(self) -> None:
        picks = select_stable_picks(
            [_prediction("1", 1.25, "2026-07-02T04:00:00+08:00"), _prediction("2", 1.16, "2026-07-02T00:00:00+08:00")],
            business_date="2026-07-01",
        )
        self.assertEqual([item["match_id"] for item in picks], ["2", "1"])

    def test_custom_budget_keeps_sixty_forty_split(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entry = record_daily_bet(
                [_prediction("2", 1.16, "2026-07-02T00:00:00+08:00"), _prediction("3", 1.80, "2026-07-02T08:00:00+08:00")],
                stake=2000,
                today=date(2026, 7, 1),
                path=Path(tmp) / "daily_bets.json",
            )
            self.assertEqual(entry["single"]["stake"], 1200.0)
            self.assertEqual(entry["parlay"]["stake"], 800.0)

    def test_parlay_rejects_pairs_below_two(self) -> None:
        selected = select_stable_parlay(
            [_prediction("2", 1.16, "2026-07-02T00:00:00+08:00"), _prediction("3", 1.22, "2026-07-02T08:00:00+08:00")],
            business_date="2026-07-01",
        )
        self.assertEqual(selected, [])

    def test_summarizes_investment_realized_and_open_profit(self) -> None:
        summary = summarize_ledger(
            {
                "entries": [
                    {"total_stake": 1000, "status": "settled", "realized_pnl": 150},
                    {"total_stake": 1000, "status": "open", "single": {"potential_profit": 100}, "parlay": {"potential_profit": 400}},
                ]
            }
        )
        self.assertEqual(summary["total_invested"], 2000)
        self.assertEqual(summary["realized_profit"], 150)
        self.assertEqual(summary["open_potential_profit"], 500)


if __name__ == "__main__":
    unittest.main()
