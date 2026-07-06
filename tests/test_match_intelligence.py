"""赛前情报模块单元测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from worldcup_mvp.match_intelligence import (
    apply_overlay_to_match,
    build_intelligence_report,
    compute_home_away_splits,
    normalize_venue,
    style_matchup_note,
)


def _team(team_id: str, code: str) -> dict:
    return {"IdTeam": team_id, "Abbreviation": code}


def _finished(match_id: str, home: dict, away: dict, hg: int, ag: int) -> dict:
    return {
        "IdMatch": match_id,
        "IdGroup": "g",
        "MatchStatus": 0,
        "Home": home,
        "Away": away,
        "HomeTeamScore": hg,
        "AwayTeamScore": ag,
    }


class MatchIntelligenceTests(unittest.TestCase):
    def test_home_away_splits(self) -> None:
        ger = _team("1", "GER")
        bra = _team("2", "BRA")
        pry = _team("3", "PRY")
        history = [
            _finished("a", ger, bra, 2, 1),
            _finished("b", pry, ger, 0, 3),
            _finished("c", ger, pry, 1, 1),
        ]
        splits = compute_home_away_splits(history, "1")
        self.assertEqual(splits["as_home"]["played"], 2)
        self.assertEqual(splits["as_home"]["wins"], 1)
        self.assertEqual(splits["as_home"]["draws"], 1)
        self.assertEqual(splits["as_away"]["played"], 1)
        self.assertEqual(splits["as_away"]["wins"], 1)
        self.assertAlmostEqual(splits["as_home"]["win_rate"], 0.5)

    def test_style_matchup_europe_vs_south_america(self) -> None:
        home = {"confederation": "UEFA", "region": "欧洲"}
        away = {"confederation": "CONMEBOL", "region": "南美"}
        note = style_matchup_note(home, away)
        self.assertIsNotNone(note)
        self.assertIn("南美", note or "")

    def test_overlay_merges_absences_and_report(self) -> None:
        overlay = {
            "matches": {
                "德国|巴拉圭": {
                    "venue": {"stadium": "Test Arena", "city": "NYC"},
                    "referee": "Test Ref",
                    "home": {
                        "absences": [
                            {"player": "A", "status": "out", "impact": 0.8, "line": "defense"}
                        ],
                        "predicted_lineup": ["GK", "DF1"],
                        "tactics": "高位压迫",
                    },
                    "away": {
                        "absences": [
                            {"player": "B", "status": "suspended", "impact": 0.9, "line": "both"}
                        ],
                    },
                }
            }
        }
        match = {
            "home": "德国",
            "away": "巴拉圭",
            "team_context": {
                "home": {
                    "group_stats": {"points": 6, "goals_for": 10, "goals_against": 3},
                    "absences": [],
                    "home_away": {
                        "as_home": {"played": 2, "wins": 1, "draws": 1, "losses": 0, "goals_for": 5, "goals_against": 2, "win_rate": 0.5},
                        "as_away": {"played": 1, "wins": 1, "draws": 0, "losses": 0, "goals_for": 3, "goals_against": 0, "win_rate": 1.0},
                    },
                },
                "away": {
                    "group_stats": {"points": 4, "goals_for": 2, "goals_against": 4},
                    "absences": [],
                },
            },
            "data_provenance": {"injuries": "not-available-from-verified-anonymous-public-api"},
        }
        enriched = apply_overlay_to_match(match, overlay)
        self.assertEqual(len(enriched["team_context"]["home"]["absences"]), 1)
        report = build_intelligence_report(enriched, overlay=overlay)
        self.assertTrue(report["coverage"]["overlay_used"])
        self.assertFalse(report["coverage"]["injury_api"])
        self.assertEqual(report["venue"]["stadium"], "Test Arena")
        self.assertTrue(any("风格对阵" in line for line in report["summary_bullets"]))
        self.assertTrue(any("施洛特贝克" not in line and "A" in line or "伤停" in line for line in report["summary_bullets"]))

    def test_load_example_overlay_from_temp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overlay.json"
            path.write_text(json.dumps({"matches": {}}), encoding="utf-8")
            report = build_intelligence_report(
                {"home": "荷兰", "away": "摩洛哥", "team_context": {"home": {}, "away": {}}},
                overlay={"matches": {}, "teams": {}},
            )
            self.assertIn("summary_bullets", report)

    def test_recent_lineup_coverage_is_separate_from_injury_api(self) -> None:
        recent = {
            "predicted_lineup": [f"P{index}" for index in range(1, 12)],
            "recent_availability": {
                "status": "recently_started",
                "official_injury_confirmation": False,
            },
        }
        report = build_intelligence_report(
            {
                "home": "Home",
                "away": "Away",
                "team_context": {"home": recent, "away": recent},
                "data_provenance": {
                    "injuries": "not-available-from-verified-anonymous-public-api"
                },
            },
            overlay={"matches": {}, "teams": {}},
        )
        self.assertTrue(report["coverage"]["recent_lineup_inference"])
        self.assertFalse(report["coverage"]["injury_api"])
        self.assertEqual(len(report["home_predicted_lineup"]), 11)


    def test_normalize_venue_fifa_raw_dict(self) -> None:
        fifa_stadium = {
            "IdStadium": "400257536",
            "Name": [{"Locale": "en-GB", "Description": "New York/New Jersey Stadium"}],
            "CityName": [{"Locale": "en-GB", "Description": "New Jersey"}],
            "IdCountry": "USA",
        }
        venue = normalize_venue(fifa_stadium)
        self.assertIsNotNone(venue)
        assert venue is not None
        self.assertEqual(venue["label"], "New York/New Jersey Stadium · New Jersey · USA")

    def test_normalize_venue_python_repr_string(self) -> None:
        blob = (
            "{'IdStadium': '400257536', 'Name': [{'Locale': 'en-GB', "
            "'Description': 'Mexico City Stadium'}], 'CityName': [{'Locale': 'en-GB', "
            "'Description': 'Mexico City'}], 'IdCountry': 'MEX'}"
        )
        venue = normalize_venue(blob)
        self.assertIsNotNone(venue)
        assert venue is not None
        self.assertIn("Mexico City Stadium", venue["label"])


if __name__ == "__main__":
    unittest.main()
