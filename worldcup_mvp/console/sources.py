"""Provider adapters. Raw provider schemas never leak into prediction code."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib import request, parse, error

from .store import now_iso
from .competition_labels import label as competition_label

BEIJING = timezone(timedelta(hours=8))
SPORTTERY = "https://webapi.sporttery.cn/gateway/uniform/football/"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer/"
MARKETS = {
    "had": "胜平负",
    "hhad": "让球胜平负",
    "ttg": "总进球",
    "crs": "比分",
    "hafu": "半全场",
}
SCORE_WINS = [(h, a) for h in range(1, 6) for a in range(min(h, 3))]
EXPECTED_OPTIONS = {
    "had": ["h", "d", "a"],
    "hhad": ["h", "d", "a"],
    "ttg": [f"s{i}" for i in range(8)],
    "hafu": [h + a for h in "hda" for a in "hda"],
    "crs": [f"s{h:02}s{a:02}" for h, a in SCORE_WINS]
    + ["s1sh"]
    + [f"s{i:02}s{i:02}" for i in range(4)]
    + ["s1sd"]
    + [f"s{a:02}s{h:02}" for h, a in SCORE_WINS]
    + ["s1sa"],
}
LEAGUES = {
    "EPL": "eng.1",
    "ED1": "eng.2",
    "SFL": "esp.1",
    "ISA": "ita.1",
    "GSL": "ger.1",
    "FFL": "fra.1",
    "FF2": "fra.2",
    "DFL": "ned.1",
    "PFL": "por.1",
    "JD1": "jpn.1",
    "KD1": "kor.1",
    "AD1": "aus.1",
    "MLS": "usa.1",
    "BD1": "bra.1",
    "BDC": "bra.copa_do_brazil",
    "NTL": "nor.1",
    "SAL": "swe.1",
    "FVL": "fin.1",
    "UCL": "uefa.champions",
    "ACL": "afc.champions",
    "UEL": "uefa.europa",
    "UEC": "uefa.europa",
    "WC": "fifa.world",
}
# Explicit aliases scoped by competition; never match clubs solely by a kickoff or one team.
ALIASES = {
    "eng.2": {"BRT": "BRC", "WLV": "WOL"},
    "uefa.europa": {"OLP": "OLY"},
    "afc.champions": {"NCJ":"NEW", "SSP":"SHP", "KAH":"KRE", "DJC":"DHC", "PTF":"PORT", "FBJ":"BEI", "PHS":"POH", "GAB":"GAM", "BUU":"BUR", "KAS":"KAN"},
    # Verified against both teams and kickoff in the provider feeds, 2026-11-03.
    "ita.1": {"SSL": "SAS"},
    "swe.1": {"GIS": "GAIS", "ASA": "AIK"},
    "fra.2": {"RST": "RSFC", "ASN": "ASNL"},
    "por.1": {"BEN": "SLB"},
    "bra.1": {"RBG": "BRA", "SAT": "SAN"},
    "uefa.champions": {"STU": "VFB", "BMN": "MUN", "LNZ": "LAS", "INM": "INT",
                       "MNU": "MAN", "ROM": "ROMA", "ASV": "AVL", "BOD": "BODO", "LIL": "LILL",
                       "POT":"FCP", "NPL":"NAP", "RBT":"BET", "VIK":"VFK", "SBH":"SAB",
                       "LEN":"RCL", "CMO":"COMO", "SLA":"SLP"},
    "jpn.1": {"KAH": "KRE", "NGV": "VVN", "TOK": "TYKV", "MCZ": "ZEL", "GAB": "GAM"},
    "eng.1": {
        "MNU": "MAN",
        "MNC": "MNC",
        "BRN": "BRE",
        "NOT": "NFO",
        "ASV": "AVL",
        "LEE": "LEE",
        "HUL": "HUL",
        "IPS": "IPS",
    },
}


class SourceError(RuntimeError):
    pass


def league_for(code):
    """Registered provider mappings take precedence over transport-only aliases."""
    from .catalog import catalog
    spec = next((c for c in catalog() if code and c.get('providers', {}).get('sporttery') == code), None)
    return spec.get('providers', {}).get('espn') if spec else LEAGUES.get(code)


def verify_board_identity(board, league):
    from .catalog import catalog
    expected = {str(c['providers']['espn_id']) for c in catalog()
                if c.get('providers', {}).get('espn') == league and c['providers'].get('espn_id')}
    matches = [row for row in board.get('leagues', []) if row.get('slug') == league]
    if len(matches) != 1 or (expected and str(matches[0].get('id')) not in expected):
        raise SourceError('比分来源赛事身份不一致')
    return matches[0]


def get_json(url, timeout=12):
    headers = {
        "User-Agent": "Mozilla/5.0 football-analysis/1.0",
        "Accept": "application/json",
        "Referer": (
            "https://www.sporttery.cn/"
            if "sporttery.cn" in url
            else "https://www.espn.com/"
        ),
    }
    opener = (
        request.build_opener(request.ProxyHandler({}))
        if os.environ.get("FOOTBALL_DIRECT_NETWORK", "1") == "1"
        else request.build_opener()
    )
    try:
        with opener.open(
            request.Request(url, headers=headers), timeout=timeout
        ) as response:
            payload = json.loads(response.read(8_000_000).decode("utf-8"))
        if not isinstance(payload, dict):
            raise SourceError("数据源返回结构无效")
        return payload
    except error.HTTPError as exc:
        raise SourceError(f"数据源 HTTP {exc.code}") from None
    except (error.URLError, TimeoutError, OSError, ValueError):
        raise SourceError("数据源连接失败、超时或返回格式无效") from None


def official(endpoint, **query):
    data = get_json(SPORTTERY + endpoint + "?" + parse.urlencode(query))
    if data.get("success") is not True:
        raise SourceError("体彩接口未返回成功状态")
    return data.get("value") or {}


def timestamp(item):
    date, time = item.get("updateDate"), item.get("updateTime")
    return f"{date}T{time}+08:00" if date and time else None


def number(v):
    try:
        n = float(v)
        return n if 0 < n < float("inf") else None
    except (TypeError, ValueError):
        return None


def option_label(code, key):
    if code == "hhad":
        return {"h": "让胜", "d": "让平", "a": "让负"}.get(key)
    if code == "had":
        return {"h": "主胜", "d": "平局", "a": "客胜"}.get(key)
    if code == "ttg" and re.fullmatch(r"s[0-7]", key):
        return key[1] + ("+球" if key == "s7" else "球")
    if code == "hafu" and re.fullmatch(r"[hda]{2}", key):
        return "".join({"h": "胜", "d": "平", "a": "负"}[k] for k in key)
    if code == "crs":
        if re.fullmatch(r"s\d{2}s\d{2}", key):
            return f"{int(key[1:3])}-{int(key[4:6])}"
        return {"s1sh": "胜其他", "s1sd": "平其他", "s1sa": "负其他"}.get(key)
    return None


def normalize_market(code, raw, metadata=None):
    raw, metadata = raw or {}, metadata or {}
    # The history endpoint spells the three 'other score' buckets differently.
    raw = {
        ({"s-1sh": "s1sh", "s-1sd": "s1sd", "s-1sa": "s1sa"}.get(k, k)): v
        for k, v in raw.items()
    }
    options = [
        {"key": k, "label": option_label(code, k), "odds": number(v)}
        for k, v in raw.items()
        if option_label(code, k) and number(v)
    ]
    options.sort(
        key=lambda o: (
            EXPECTED_OPTIONS[code].index(o["key"])
            if o["key"] in EXPECTED_OPTIONS[code]
            else len(EXPECTED_OPTIONS[code])
        )
    )
    line = raw.get("goalLineValue")
    if line in (None, ""):
        line = raw.get("goalLine")
    return {
        "code": code,
        "label": MARKETS[code],
        "options": options,
        "line": float(line) if line not in (None, "") else None,
        "updated_at": timestamp(raw),
        "single": metadata.get("bettingSingle", metadata.get("single"))
        in (1, "1", True),
        "sale_status": {
            "Selling": "selling",
            "Closed": "closed",
            "Stop": "closed",
            "Suspended": "suspended",
        }.get(metadata.get("poolStatus"), "unknown"),
    }


def normalize_match(raw):
    pools = {
        p["poolCode"].lower(): p for p in raw.get("poolList", []) if p.get("poolCode")
    }
    markets = {
        code: normalize_market(code, raw.get(code), pools.get(code))
        for code in MARKETS
        if raw.get(code)
    }
    kickoff = f"{raw['matchDate']}T{raw['matchTime']}+08:00"
    m = {
        "id": f"sporttery:{raw['matchId']}",
        "provider": "sporttery",
        "provider_id": str(raw["matchId"]),
        "number": raw.get("matchNumStr"),
        "home": raw.get("homeTeamAbbName") or raw.get("homeTeamAllName"),
        "away": raw.get("awayTeamAbbName") or raw.get("awayTeamAllName"),
        "home_id": str(raw.get("homeTeamId")),
        "away_id": str(raw.get("awayTeamId")),
        "home_code": raw.get("homeTeamCode"),
        "away_code": raw.get("awayTeamCode"),
        "home_rank": raw.get("homeRank"),
        "away_rank": raw.get("awayRank"),
        "league": raw.get("leagueAbbName"),
        "league_id": str(raw.get("leagueId")),
        "league_code": raw.get("leagueCode"),
        "sale_date": raw.get("businessDate") or raw["matchDate"],
        "kickoff": kickoff,
        "sale_status": (
            {
                "Selling": "selling",
                "Closed": "closed",
                "Suspended": "suspended",
                "Stop": "closed",
            }.get(raw.get("matchStatus"), "unknown")
            if raw.get("matchStatus")
            else "selling" if str(raw.get("sellStatus")) == "1" else "pending"
        ),
        "markets": markets,
        "source_url": "https://www.sporttery.cn/jc/zqszsc/",
    }
    from .catalog import classify

    m["competition"] = classify(m)
    return m


def fetch_matches():
    data = official("getMatchCalculatorV1.qry", channel="c", poolCode=",".join(MARKETS))
    try:
        return [
            normalize_match(m)
            for group in data.get("matchInfoList", [])
            for m in group.get("subMatchList", [])
        ]
    except (ValueError, KeyError, TypeError):
        raise SourceError("体彩场次格式无效，本次未覆盖已存数据") from None


def fetch_odds(match):
    value = official(
        "getFixedBonusV1.qry", clientCode="3001", matchId=match["provider_id"]
    )
    history = value.get("oddsHistory") or {}
    if str(history.get("matchId")) != match["provider_id"]:
        raise SourceError("体彩详情比赛 ID 不一致")
    if any(str(r["matchId"]) != match["provider_id"] for r in value.get("matchResultList", []) if "matchId" in r):
        raise SourceError("体彩赛果比赛 ID 不一致")
    series = {
        code: sorted(
            [normalize_market(code, p) for p in history.get(code + "List", [])],
            key=lambda p: p["updated_at"] or "",
        )
        for code in MARKETS
    }
    return {
        "history": series,
        "results": value.get("matchResultList") or [],
        "cancelled": value.get("isCancel") in (1, "1", True),
        "official_score": official_score(value),
        "captured_at": now_iso(),
    }


def official_score(value):
    """Exact 90-minute result; 'other score' market buckets are not exact scores.

    sectionsNo999 is checked against the published CRS bucket, HAD and TTG.
    Never infer a score from the payout bucket itself.
    """
    if value.get("isCancel") in (1, "1", True):
        return None
    results = {str(r.get("code", "")).lower(): str(r.get("combination", "")).lower()
               for r in value.get("matchResultList", [])}
    crs = results.get("crs", "")
    raw = value.get("sectionsNo999") or crs
    if not isinstance(raw, str) or not re.fullmatch(r"\d{1,2}[:-]\d{1,2}", raw):
        return None
    h, a = map(int, re.split("[:-]", raw))
    from .football_markets import score_key
    bucket = {"-1:-h": "s1sh", "-1:-d": "s1sd", "-1:-a": "s1sa"}.get(crs, crs)
    if re.fullmatch(r"\d{1,2}[:-]\d{1,2}", crs):
        if tuple(map(int, re.split("[:-]", crs))) != (h, a):
            raise SourceError("体彩精确比分与比分玩法结果冲突")
    elif bucket != score_key(h, a):
        return None
    actual = "h" if h > a else "a" if a > h else "d"
    if results.get("had") in ("h", "d", "a") and results["had"] != actual:
        raise SourceError("体彩精确比分与胜平负结果冲突")
    ttg = results.get("ttg", "")
    if (ttg.isdigit() and int(ttg) != min(h+a, 7)) or (ttg in ("7+", "7及以上") and h+a < 7):
        raise SourceError("体彩精确比分与总进球结果冲突")
    return f"{h}-{a}"


def parse_date(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def match_event(match, events, league):
    aliases = ALIASES.get(league, {})
    target = [
        aliases.get(match.get(f"{side}_code"), match.get(f"{side}_code"))
        for side in ("home", "away")
    ]
    if not all(target):
        return None
    candidates = []
    for event in events:
        try:
            if (
                abs(
                    (
                        parse_date(event["date"]) - parse_date(match["kickoff"])
                    ).total_seconds()
                )
                > 900
            ):
                continue
            teams = {
                p["homeAway"]: p["team"].get("abbreviation")
                for p in event["competitions"][0]["competitors"]
            }
            if [teams.get("home"), teams.get("away")] == target:
                candidates.append(event)
        except (KeyError, ValueError, TypeError):
            continue
    return candidates[0] if len(candidates) == 1 else None


def recent_rows(summary, team_id, kickoff):
    rows = []
    for group in summary.get("lastFiveGames", []):
        if str(group.get("team", {}).get("id")) != str(team_id):
            continue
        for event in group.get("events", []):
            try:
                if parse_date(event["gameDate"]) >= parse_date(kickoff):
                    continue
                home = str(event["homeTeamId"]) == str(team_id)
                gf, ga = int(event["homeTeamScore"]), int(event["awayTeamScore"])
                if not home:
                    gf, ga = ga, gf
                rows.append(
                    {
                        "id": event["id"],
                        "date": parse_date(event["gameDate"])
                        .astimezone(BEIJING)
                        .isoformat(),
                        "opponent_original": event.get("opponent", {}).get(
                            "displayName", "未知球队"
                        ),
                        "opponent": event.get("opponent", {}).get("displayName", "未知球队"),
                        "opponent_id": event.get("opponent", {}).get("id"),
                        "venue": "主" if home else "客",
                        "gf": gf,
                        "ga": ga,
                        "score": f"{gf}-{ga}",
                        **competition_label(
                            event.get("leagueName") or event.get("competitionName") or ""),
                        "source": "ESPN",
                    }
                )
            except (KeyError, ValueError, TypeError):
                continue
    from .team_labels import localize_context
    return localize_context({"recent": [sorted(rows, key=lambda r: r["date"], reverse=True)[:5]]})["recent"][0]


def fetch_context(match):
    from .fundamentals import fetch_context as enrich
    return enrich(match)


def fetch_espn_context(match):
    if match.get("league_code") == "KD1":
        return {
            "available": False,
            "reason": "韩职结构化基本面尚未接入；等待数据源授权",
            "recent": [[], []],
            "lineups": [],
            "source": "K League",
            "injuries_verified": False,
        }
    league = league_for(match["league_code"])
    if not league:
        return {
            "available": False,
            "reason": "该赛事尚未配置基本面适配器",
            "recent": [[], []],
            "lineups": [],
            "source": "ESPN",
        }
    day = parse_date(match["kickoff"]).astimezone(timezone.utc).strftime("%Y%m%d")
    board = get_json(ESPN + league + "/scoreboard?dates=" + day)
    event = match_event(match, board.get("events", []), league)
    if not event:
        return {
            "available": False,
            "reason": "未通过球队、赛事与开赛时间的一致性校验",
            "recent": [[], []],
            "lineups": [],
            "source": "ESPN",
        }
    summary = get_json(ESPN + league + "/summary?event=" + event["id"])
    verify_summary_identity(match, board, event, summary, league)
    competitors = {p["homeAway"]: p for p in event["competitions"][0]["competitors"]}
    lineups = []
    for side in ("home", "away"):
        roster = next(
            (
                r
                for r in summary.get("rosters", [])
                if r.get("homeAway") == side
                and str(r.get("team", {}).get("id")) == str(competitors[side]["id"])
            ),
            {},
        )
        players = []
        for r in roster.get("roster", []):
            if r.get("starter") is not True:
                continue
            athlete = r.get("athlete") or {}
            players.append(
                {
                    "id": athlete.get("id"),
                    "name": athlete.get("displayName"),
                    "number": r.get("jersey") or athlete.get("jersey"),
                    "position": (
                        r.get("position") or athlete.get("position") or {}
                    ).get("abbreviation"),
                }
            )
        lineups.append(
            {
                "side": side,
                "status": "confirmed" if len(players) == 11 else "unconfirmed",
                "players": players if len(players) == 11 else [],
                "formation": roster.get("formation"),
            }
        )
    recent = [
        recent_rows(summary, competitors[side]["id"], match["kickoff"])
        for side in ("home", "away")
    ]
    status = (event.get("status") or event["competitions"][0].get("status", {})).get("type", {})
    score = None
    if status.get("completed"):
        score = f"{competitors['home'].get('score')}-{competitors['away'].get('score')}"
    venue = (summary.get("gameInfo") or {}).get("venue") or event["competitions"][
        0
    ].get("venue", {})
    from .match_facts import extract, head_to_head
    captured = now_iso()
    from .readiness import fixture_key
    espn_identity = {'provider': 'espn', 'fixture': fixture_key(match), 'competition_slug': league,
        'event_id': str(event['id']), 'team_ids': {s: str(competitors[s]['id']) for s in ('home', 'away')},
        'kickoff': event.get('date'), 'season': (event.get('season') or {}).get('year'),
        'stage': (event.get('season') or {}).get('slug'),
        'neutral': (summary.get('header',{}).get('competitions') or [{}])[0].get('neutralSite',event['competitions'][0].get('neutralSite')),
        'source_url': f"https://www.espn.com/soccer/match/_/gameId/{event['id']}", 'captured_at': captured}
    return {
        "available": True,
        "espn_identity": espn_identity,
        "event_id": event["id"],
        "provider_ids": {side: competitors[side]["id"] for side in ("home", "away")},
        "recent": recent,
        "head_to_head": head_to_head(summary, match, competitors),
        "h2h_checked": any(s.get('type') == 'head-to-head' for s in summary.get('seasonseries', [])),
        "match_facts": extract(summary, match, competitors, captured),
        "lineups": lineups,
        "venue": venue.get("fullName"),
        "injuries": [],
        "injuries_verified": False,
        "state": espn_state(status),
        "score": score,
        "source": "ESPN",
        "source_url": f"https://www.espn.com/soccer/match/_/gameId/{event['id']}",
        "captured_at": captured,
    }


def espn_state(status):
    name = status.get('name', '')
    states = {'STATUS_CANCELED':'cancelled', 'STATUS_CANCELLED':'cancelled',
              'STATUS_POSTPONED':'postponed', 'STATUS_SUSPENDED':'suspended',
              'STATUS_ABANDONED':'suspended', 'STATUS_SCHEDULED':'upcoming'}
    if name in states:
        return states[name]
    if status.get('completed') is True:
        return 'completed'
    if status.get('state') == 'in':
        return 'live'
    return 'unknown'


def verify_summary_identity(match, board, event, summary, league):
    """Bind summary venue facts to both teams, source competition, season and time."""
    try:
        source = verify_board_identity(board, league)
        eid = str(event['id'])
        header = summary['header']
        comp, detail = event['competitions'][0], header['competitions'][0]
        if (len(event['competitions']) != 1 or len(header['competitions']) != 1
                or event['uid'] != f"s:600~l:{source['id']}~e:{eid}"
                or header['uid'] != event['uid'] or str(header['id']) != eid
                or str(comp['id']) != eid or str(detail['id']) != eid
                or event['season']['year'] != header['season']['year']
                or parse_date(event['date']) != parse_date(match['kickoff'])
                or parse_date(comp['date']) != parse_date(event['date'])
                or parse_date(detail['date']) != parse_date(event['date'])):
            raise ValueError('identity')
        def teams(c):
            values = c['competitors']
            if len(values) != 2 or {v['homeAway'] for v in values} != {'home','away'}:
                raise ValueError('teams')
            result = {v['homeAway']:str(v['team']['id']) for v in values}
            if len(set(result.values())) != 2:
                raise ValueError('teams')
            return result
        if teams(comp) != teams(detail):
            raise ValueError('teams')
        # A cancellation in either endpoint must not be erased by the other.
        upstream = espn_state(detail.get('status', {}).get('type', {}))
        current = espn_state((event.get('status') or comp.get('status',{})).get('type',{}))
        priority={'upcoming':0,'unknown':1,'live':2,'completed':3,'suspended':4,'postponed':5,'cancelled':6}
        if priority[upstream] > priority[current]:
            event['status'] = detail['status']
    except (KeyError, TypeError, ValueError, StopIteration, IndexError):
        raise SourceError('基本面详情赛事、球队、赛季或开球时间不一致') from None
