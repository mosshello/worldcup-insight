"""FOX 爬虫与 The Odds API 解析测试。"""

import json
import unittest
from unittest.mock import patch

from worldcup_mvp.fox_scraper import american_to_decimal, fetch_fox_snapshot, parse_moneyline_blocks
from worldcup_mvp.team_names import find_event_by_teams, resolve_team
from worldcup_mvp.the_odds_api import event_to_snapshot


SAMPLE_FOX_PAGE = """
Brazil vs. Japan
To Advance: BRA -320, JPN +245
Moneyline: BRA -145, Draw +280, JPN +420

Germany vs. Paraguay
To Advance: GER -950, PRY +600
Moneyline: GER -340, Draw +440, PRY +1000
"""

SAMPLE_EVENT = {
    "id": "event-123",
    "home_team": "Brazil",
    "away_team": "Japan",
    "bookmakers": [
        {
            "key": "fanduel",
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Brazil", "price": 1.69},
                        {"name": "Draw", "price": 3.8},
                        {"name": "Japan", "price": 5.2},
                    ],
                },
                {
                    "key": "spreads",
                    "outcomes": [
                        {"name": "Brazil", "price": 0.92, "point": -1.0},
                        {"name": "Japan", "price": 0.98, "point": 1.0},
                    ],
                },
            ],
        }
    ],
}


class FoxScraperTests(unittest.TestCase):
    def test_american_to_decimal(self) -> None:
        self.assertAlmostEqual(american_to_decimal(-145), 1.689655, places=5)
        self.assertAlmostEqual(american_to_decimal(280), 3.8, places=5)

    def test_parse_moneyline_blocks(self) -> None:
        matches = parse_moneyline_blocks(SAMPLE_FOX_PAGE)
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0]["home"], "巴西")
        self.assertAlmostEqual(matches[0]["european"]["home"], 1.689655, places=5)

    @patch("worldcup_mvp.fox_scraper._fetch_page", return_value=SAMPLE_FOX_PAGE)
    def test_fetch_fox_snapshot(self, _mock_fetch: object) -> None:
        snapshot = fetch_fox_snapshot("巴西", "日本")
        self.assertEqual(snapshot["source"], "fox-sports/fanduel")
        self.assertIn("european", snapshot)


class TheOddsApiTests(unittest.TestCase):
    def test_event_to_snapshot(self) -> None:
        snapshot = event_to_snapshot(SAMPLE_EVENT)
        self.assertEqual(snapshot["european"]["home"], 1.69)
        self.assertEqual(snapshot["asian_handicap"]["line"], -1.0)

    def test_find_event_by_teams(self) -> None:
        events = [{"home_team": "Brazil", "away_team": "Japan", "id": "1"}]
        event = find_event_by_teams(events, "巴西", "日本")
        self.assertIsNotNone(event)
        self.assertEqual(event["id"], "1")

    def test_resolve_team(self) -> None:
        self.assertEqual(resolve_team("巴西")["en"], "Brazil")


if __name__ == "__main__":
    unittest.main()
