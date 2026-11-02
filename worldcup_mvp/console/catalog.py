"""Competition identity is separate from sale dates and provider abbreviations."""

from copy import deepcopy
import os

import json
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parents[2] / "config" / "football_competitions.json"
REGISTERED = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
if len({c["id"] for c in REGISTERED}) != len(REGISTERED):
    raise ValueError("赛事登记标识重复")
COMPETITIONS = {c["providers"]["sporttery"]: c for c in REGISTERED if c["providers"].get("sporttery")}
if len(COMPETITIONS) != sum(bool(c["providers"].get("sporttery")) for c in REGISTERED):
    raise ValueError("体彩赛事映射重复")



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
    return deepcopy(REGISTERED)


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
