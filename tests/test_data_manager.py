"""无密钥FIFA与Polymarket公开数据中心契约测试。"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from worldcup_mvp.data_manager import ConfigurationError, DataConfig, UnifiedDataManager
from worldcup_mvp.http_client import DataSourceError, HttpJsonClient


def fifa_team(team_id: str, name: str, code: str) -> dict:
    return {"IdTeam": team_id, "ShortClubName": name, "Abbreviation": code}


BRAZIL = fifa_team("43924", "Brazil", "BRA")
JAPAN = fifa_team("43819", "Japan", "JPN")
MOROCCO = fifa_team("43872", "Morocco", "MAR")
HAITI = fifa_team("43908", "Haiti", "HAI")
SCOTLAND = fifa_team("43967", "Scotland", "SCO")
NETHERLANDS = fifa_team("43960", "Netherlands", "NED")
SWEDEN = fifa_team("23", "Sweden", "SWE")
TUNISIA = fifa_team("43888", "Tunisia", "TUN")


def finished(match_id, home, away, home_score, away_score):
    return {
        "IdMatch": match_id,
        "IdGroup": "group",
        "MatchStatus": 0,
        "Home": home,
        "Away": away,
        "HomeTeamScore": home_score,
        "AwayTeamScore": away_score,
    }


TARGET = {
    "IdMatch": "target",
    "IdGroup": None,
    "MatchStatus": 1,
    "Date": "2026-06-29T17:00:00Z",
    "Home": BRAZIL,
    "Away": JAPAN,
    "StageName": [{"Description": "Round of 32"}],
}

HISTORY = [
    finished("bra-mar", BRAZIL, MOROCCO, 1, 1),
    finished("bra-hai", BRAZIL, HAITI, 3, 0),
    finished("sco-bra", SCOTLAND, BRAZIL, 0, 3),
    finished("ned-jpn", NETHERLANDS, JAPAN, 2, 2),
    finished("jpn-swe", JAPAN, SWEDEN, 1, 1),
    finished("tun-jpn", TUNISIA, JAPAN, 0, 4),
    TARGET,
]


def goal(team, player, player_id, *, own_goal=False, penalty=False):
    event_type = "Own goal" if own_goal else "Penalty Goal" if penalty else "Goal!"
    action = "scores an own goal." if own_goal else "successfully converts the penalty!" if penalty else "scores!!"
    return {
        "IdTeam": team["IdTeam"],
        "IdPlayer": player_id,
        "TypeLocalized": [{"Locale": "en-GB", "Description": event_type}],
        "EventDescription": [{"Locale": "en-GB", "Description": f"{player} ({team['ShortClubName']}) {action}"}],
    }


TIMELINES = {
    "bra-mar": [goal(BRAZIL, "VINICIUS JUNIOR", "vini"), goal(MOROCCO, "ISMAEL SAIBARI", "sai")],
    "bra-hai": [
        goal(BRAZIL, "MATHEUS CUNHA", "cunha"),
        goal(BRAZIL, "MATHEUS CUNHA", "cunha"),
        goal(BRAZIL, "VINICIUS JUNIOR", "vini"),
    ],
    "sco-bra": [
        goal(BRAZIL, "VINICIUS JUNIOR", "vini"),
        goal(BRAZIL, "VINICIUS JUNIOR", "vini"),
        goal(BRAZIL, "MATHEUS CUNHA", "cunha"),
    ],
    "ned-jpn": [
        goal(NETHERLANDS, "DUTCH ONE", "n1"), goal(NETHERLANDS, "DUTCH TWO", "n2"),
        goal(JAPAN, "AYASE UEDA", "u"), goal(JAPAN, "DAIZEN MAEDA", "m"),
    ],
    "jpn-swe": [goal(JAPAN, "AYASE UEDA", "u", penalty=True), goal(SWEDEN, "SWEDISH PLAYER", "s")],
    "tun-jpn": [
        goal(JAPAN, "AYASE UEDA", "u"), goal(JAPAN, "DAIZEN MAEDA", "m"),
        goal(JAPAN, "TAKEFUSA KUBO", "k"), goal(JAPAN, "RITSU DOAN", "d"),
    ],
}


class FakeFifaProvider:
    def __init__(self, *, conflicting_timeline=False):
        self.conflicting_timeline = conflicting_timeline

    def fixtures(self, from_time: str, to_time: str):
        return [TARGET] if from_time.startswith("2026-06-29") else HISTORY

    def timeline(self, match_id: str):
        events = list(TIMELINES[match_id])
        if self.conflicting_timeline and match_id == "sco-bra":
            events.pop()
        return events

    def doctor(self):
        return {"provider": "fifa-public", "ok": True}


class FakeMarketProvider:
    def __init__(self, *, missing_draw=False, low_liquidity=False, wrong_time=False):
        self.missing_draw = missing_draw
        self.low_liquidity = low_liquidity
        self.wrong_time = wrong_time

    def event_by_slug(self, slug: str):
        markets = [
            self._market("1", "Brazil", 0.575),
            self._market("2", "Draw (Brazil vs. Japan)", 0.255),
            self._market("3", "Japan", 0.175),
        ]
        if self.missing_draw:
            markets.pop(1)
        return {
            "id": "636318", "slug": slug, "title": "Brazil vs. Japan",
            "endDate": "2026-06-29T18:00:00Z" if self.wrong_time else "2026-06-29T17:00:00Z",
            "active": True, "closed": False, "markets": markets,
        }

    def _market(self, market_id, label, yes_price):
        return {
            "id": market_id, "groupItemTitle": label,
            "outcomes": '["Yes", "No"]',
            "outcomePrices": f'["{yes_price}", "{1 - yes_price}"]',
            "liquidity": "100" if self.low_liquidity else "1000000",
            "active": True, "closed": False,
        }

    def doctor(self):
        return {"provider": "polymarket-public", "ok": True}


class FakeSportteryProvider:
    def __init__(
        self, *, missing_had=False, not_selling=False, wrong_team=False, bad_handicap=False
    ):
        self.missing_had = missing_had
        self.not_selling = not_selling
        self.wrong_team = wrong_team
        self.bad_handicap = bad_handicap

    def matches(self):
        item = {
            "matchId": 2040337,
            "matchNumStr": "周一074",
            "businessDate": "2026-06-29",
            "matchStatus": "Closed" if self.not_selling else "Selling",
            "matchDate": "2026-06-30",
            "matchTime": "01:00:00",
            "homeTeamCode": "GER" if self.wrong_team else "BRZ",
            "awayTeamCode": "JPN",
            "had": {
                "h": "1.52", "d": "3.72", "a": "4.95",
                "updateDate": "2026-06-29", "updateTime": "19:50:23",
            },
            "hhad": {
                "goalLine": "-1", "goalLineValue": "-1.50" if self.bad_handicap else "-1.00",
                "h": "2.83", "d": "3.11", "a": "2.20",
                "updateDate": "2026-06-29", "updateTime": "19:50:47",
            },
        }
        if self.missing_had:
            item.pop("had")
        return [item]

    def doctor(self):
        return {"provider": "sporttery-public", "ok": True}


class UnifiedDataManagerTests(unittest.TestCase):
    def setUp(self):
        self.config = DataConfig()
        self.clock = lambda: datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)

    def manager(self, fifa=None, market=None, sporttery=None):
        return UnifiedDataManager(
            fifa or FakeFifaProvider(),
            market or FakeMarketProvider(),
            sporttery or FakeSportteryProvider(),
            self.config,
            clock=self.clock,
        )

    def test_collects_without_api_keys_from_official_public_data(self):
        payload = self.manager().collect("2026-06-29")
        self.assertFalse(payload["quality_checks"]["api_key_required"])
        self.assertFalse(payload["quality_checks"]["injury_coverage"])
        match = payload["matches"][0]
        self.assertEqual(match["home"], "Brazil")
        self.assertEqual(match["team_context"]["home"]["group_stats"]["points"], 7)
        self.assertEqual(
            match["team_context"]["home"]["scorers"][0],
            {"player": "VINICIUS JUNIOR", "goals": 4, "provider_player_id": "vini"},
        )
        self.assertEqual(match["team_context"]["away"]["group_stats"]["goals_for"], 7)
        self.assertEqual(match["team_context"]["home"]["absences"], [])
        self.assertAlmostEqual(sum(1 / value for value in match["odds"].values()), 1.0, places=5)
        self.assertEqual(match["data_provenance"]["odds"]["event_id"], "636318")
        self.assertEqual(match["sporttery"]["match_number"], "周一074")
        self.assertEqual(match["sporttery"]["had"]["odds"]["home"], 1.52)
        self.assertEqual(match["sporttery"]["hhad"]["handicap"], -1)
        self.assertEqual(match["provider_ids"]["sporttery_match"], "2040337")

    def test_missing_sporttery_had_is_rejected(self):
        with self.assertRaisesRegex(DataSourceError, "HAD或HHAD"):
            self.manager(sporttery=FakeSportteryProvider(missing_had=True)).collect("2026-06-29")

    def test_unsold_sporttery_match_is_not_returned(self):
        with self.assertRaisesRegex(DataSourceError, "当前没有"):
            self.manager(sporttery=FakeSportteryProvider(not_selling=True)).collect("2026-06-29")

    def test_sporttery_team_mismatch_is_not_returned(self):
        with self.assertRaisesRegex(DataSourceError, "当前没有"):
            self.manager(sporttery=FakeSportteryProvider(wrong_team=True)).collect("2026-06-29")

    def test_non_integer_sporttery_handicap_is_rejected(self):
        with self.assertRaisesRegex(DataSourceError, "必须是整数"):
            self.manager(sporttery=FakeSportteryProvider(bad_handicap=True)).collect("2026-06-29")

    def test_missing_draw_market_is_rejected(self):
        with self.assertRaisesRegex(DataSourceError, "完整"):
            self.manager(market=FakeMarketProvider(missing_draw=True)).collect("2026-06-29")

    def test_low_liquidity_is_rejected(self):
        with self.assertRaisesRegex(DataSourceError, "流动性不足"):
            self.manager(market=FakeMarketProvider(low_liquidity=True)).collect("2026-06-29")

    def test_market_kickoff_mismatch_is_rejected(self):
        with self.assertRaisesRegex(DataSourceError, "开赛时间不一致"):
            self.manager(market=FakeMarketProvider(wrong_time=True)).collect("2026-06-29")

    def test_fifa_timeline_goal_conflict_is_rejected(self):
        with self.assertRaisesRegex(DataSourceError, "球员进球数"):
            self.manager(fifa=FakeFifaProvider(conflicting_timeline=True)).collect("2026-06-29")

    def test_configuration_requires_no_keys(self):
        config = DataConfig.from_env({})
        self.assertEqual(config.competition_id, 17)
        self.assertNotIn("key", repr(config).casefold())

    def test_invalid_date_is_rejected_before_requests(self):
        with self.assertRaisesRegex(ConfigurationError, "YYYY-MM-DD"):
            self.manager().collect("29/06/2026")

    def test_http_client_rejects_non_https(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            HttpJsonClient("http://example.com", provider_name="测试")

    def test_doctor_covers_two_public_sources(self):
        report = self.manager().doctor()
        self.assertEqual(len(report["providers"]), 3)
        self.assertTrue(all(item["ok"] for item in report["providers"]))


if __name__ == "__main__":
    unittest.main()
