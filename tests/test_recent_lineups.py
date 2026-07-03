"""近期首发推断测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from worldcup_mvp.recent_lineups import infer_recent_lineups


def _event(event_id: str, date: str, home: str, away: str) -> dict:
    return {
        "id": event_id,
        "date": date,
        "name": f"{away} at {home}",
        "status": {"type": {"completed": True}},
        "competitions": [
            {
                "competitors": [
                    {"team": {"displayName": home, "abbreviation": home[:3].upper()}},
                    {"team": {"displayName": away, "abbreviation": away[:3].upper()}},
                ]
            }
        ],
    }


def _summary(team: str) -> dict:
    return {
        "rosters": [
            {
                "team": {"displayName": team, "abbreviation": team[:3].upper()},
                "roster": [
                    {
                        "starter": True,
                        "formationPlace": str(index),
                        "athlete": {"id": str(index), "displayName": f"{team} P{index}"},
                        "position": {"abbreviation": "G" if index == 1 else "F"},
                    }
                    for index in range(1, 12)
                ],
            }
        ]
    }


class RecentLineupTests(unittest.TestCase):
    def test_uses_latest_completed_match_before_kickoff(self) -> None:
        scoreboard = {
            "events": [
                _event("old-home", "2026-06-20T10:00:00Z", "Home", "Other"),
                _event("new-home", "2026-06-27T10:00:00Z", "Home", "Other"),
                _event("away", "2026-06-26T10:00:00Z", "Away", "Other"),
            ]
        }

        def fake_get(path: str, query: tuple) -> dict:
            if path == "scoreboard":
                return scoreboard
            event_id = dict(query)["event"]
            return _summary("Home" if event_id == "new-home" else "Away")

        with patch("worldcup_mvp.recent_lineups._get_json", side_effect=fake_get):
            report = infer_recent_lineups(
                home="Home",
                away="Away",
                kickoff_beijing="2026-07-04T02:00:00+08:00",
            )

        self.assertTrue(report["available"])
        self.assertEqual(report["home"]["source_match_id"], "new-home")
        self.assertEqual(len(report["home"]["predicted_lineup"]), 11)
        self.assertFalse(
            report["home"]["recent_availability"]["official_injury_confirmation"]
        )

    def test_requires_complete_starting_eleven(self) -> None:
        scoreboard = {"events": [_event("home", "2026-06-27T10:00:00Z", "Home", "Other")]}
        incomplete = _summary("Home")
        incomplete["rosters"][0]["roster"].pop()
        with patch(
            "worldcup_mvp.recent_lineups._get_json",
            side_effect=lambda path, query: scoreboard if path == "scoreboard" else incomplete,
        ):
            report = infer_recent_lineups(
                home="Home",
                away="Away",
                kickoff_beijing="2026-07-04T02:00:00+08:00",
            )
        self.assertFalse(report["available"])


if __name__ == "__main__":
    unittest.main()
