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



def classify(match, specs=None):
    spec = next((c for c in (catalog() if specs is None else specs) if c.get('providers', {}).get('sporttery') == match.get('league_code') and match.get('league_code')), None)
    if not spec:
        return {
            "id": "unregistered:" + str(match.get('provider') or 'sporttery') + ':' + str(match.get('league_code') or ('fixture:' + str(match.get('id')))),
            "season": None,
            "stage": None,
            "period": None,
        }
    date = match["kickoff"][:10]
    season = next((s for s in spec["seasons"] if s["start"] <= date < s["end"]), None)
    return {
        "id": spec["id"],
        "country": spec.get("country"),
        "region": spec.get("region"),
        "season": season["id"] if season else None,
        # Group membership is not guessed from a date or current ranking.
        "stage": "regular" if season and season["stages"] == ["regular"] else None,
        "period": "regular_time",
        "rule_source": spec.get("rule_source"),
        "rule_revision": rule_revision(spec),
    }


def catalog():
    values = json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
    if not isinstance(values, list) or len({c['id'] for c in values}) != len(values):
        raise ValueError('赛事登记标识重复或格式无效')
    codes = [c.get('providers', {}).get('sporttery') for c in values if c.get('providers', {}).get('sporttery')]
    if len(codes) != len(set(codes)):
        raise ValueError('体彩赛事映射重复')
    return values


def rule_revision(spec):
    from .store import fingerprint
    return fingerprint(spec)


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
