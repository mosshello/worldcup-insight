"""Competition identity is separate from sale dates and provider abbreviations."""

from copy import deepcopy
import os

COMPETITIONS = {
    "JD1": {
        "id": "jp.j1",
        "name": "日职",
        "country": "JP",
        "region": "AFC",
        "official_url": "https://data.j-league.or.jp/SFTP01/",
        "rule_source": "https://aboutj.jleague.jp/corporate/terminology/",
        "providers": {
            "sporttery": "JD1",
            "espn": "jpn.1",
            "odds_api": "soccer_japan_j_league",
        },
        "seasons": [
            {
                "id": "2026-special",
                "label": "2026 百年构想",
                "start": "2026-02-01",
                "end": "2026-07-01",
                "format": "special",
                "stages": ["regional", "playoff"],
                "shootout_separate": True,
            },
            {
                "id": "2026-27",
                "label": "2026/27",
                "start": "2026-08-01",
                "end": "2027-07-01",
                "format": "league",
                "stages": ["regular"],
                "shootout_separate": True,
            },
        ],
    },
    "KD1": {
        "id": "kr.k1",
        "name": "韩职",
        "country": "KR",
        "region": "AFC",
        "official_url": "https://www.kleague.com/",
        "rule_source": "https://www.kleague.com/about/competition.do",
        "providers": {"sporttery": "KD1", "odds_api": "soccer_korea_kleague1"},
        "seasons": [
            {
                "id": "2026",
                "label": "2026",
                "start": "2026-01-01",
                "end": "2027-01-01",
                "format": "split",
                "stages": ["regular", "final_a", "final_b"],
                "shootout_separate": True,
            },
        ],
    },
}


def classify(match):
    spec = COMPETITIONS.get(match.get("league_code"))
    if not spec:
        return {
            "id": "sporttery.league:" + str(match.get("league_id")),
            "season": None,
            "stage": None,
            "period": "regular_time",
        }
    date = match["kickoff"][:10]
    season = next((s for s in spec["seasons"] if s["start"] <= date < s["end"]), None)
    return {
        "id": spec["id"],
        "country": spec["country"],
        "region": spec["region"],
        "season": season["id"] if season else None,
        # Group membership is not guessed from a date or current ranking.
        "stage": "regular" if season and season["stages"] == ["regular"] else None,
        "period": "regular_time",
        "rule_source": spec["rule_source"],
    }


def catalog():
    return deepcopy(list(COMPETITIONS.values()))


def provider_configuration():
    from ..env_config import load_dotenv

    load_dotenv()
    return [
        {
            "id": "api_football",
            "name": "API-Football",
            "scope": "基本面 / 历史赛事",
            "configured": bool(os.getenv("API_FOOTBALL_KEY")),
            "status": (
                "pending_validation"
                if os.getenv("API_FOOTBALL_KEY")
                else "not_configured"
            ),
        },
        {
            "id": "odds_api",
            "name": "The Odds API",
            "scope": "外盘 / 历史快照",
            "configured": bool(os.getenv("ODDS_API_KEY")),
            "status": (
                "pending_validation" if os.getenv("ODDS_API_KEY") else "not_configured"
            ),
        },
        {
            "id": "kleague",
            "name": "K League API",
            "scope": "韩职官方记录",
            "configured": bool(os.getenv("KLEAGUE_API_KEY")),
            "status": (
                "pending_validation"
                if os.getenv("KLEAGUE_API_KEY")
                else "not_configured"
            ),
        },
    ]
