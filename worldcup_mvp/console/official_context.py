"""Identity-checked official match facts for display and post-match review."""
import json
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor
from urllib import parse, request

from .history_audit import Document
from .history_j_special import dictionaries
from .match_facts import STAT_LABELS
from .sources import SourceError, parse_date
from .store import ROOT, now_iso, fingerprint


class IdentityError(SourceError):
    """Reject the complete response when source identities contradict each other."""


def read_page(url, params=None):
    opener = request.build_opener(request.ProxyHandler({})) if os.getenv("FOOTBALL_DIRECT_NETWORK", "1") == "1" else request.build_opener()
    req = request.Request(url, data=parse.urlencode(params).encode() if params else None,
                          headers={"User-Agent": "Mozilla/5.0 football-analysis/1.0"})
    try:
        with opener.open(req, timeout=12) as response:
            body = response.read(8_000_001)
        if len(body) > 8_000_000:
            raise ValueError("oversized response")
        return body.decode("utf-8")
    except (OSError, ValueError):
        raise SourceError("官方比赛资料连接失败或结构无效") from None


def statistic(code, values):
    try:
        numbers = [float(v) for v in values if v is not None and not isinstance(v, bool)]
    except (ValueError, TypeError):
        return None
    if len(numbers) != 2 or any(not math.isfinite(v) or v < 0 for v in numbers):
        return None
    if code == "possessionPct" and any(v > 100 for v in numbers):
        return None
    suffix = "%" if code == "possessionPct" else ""
    return {"code": code, "label": STAT_LABELS[code],
            "home": f"{numbers[0]:g}{suffix}", "away": f"{numbers[1]:g}{suffix}"}


def k_lineups(html, identity):
    query = {k: v[0] for k, v in parse.parse_qs(parse.urlsplit(identity["source_url"]).query).items()}
    expected = {"MEET_YEAR": query["year"], "MEET_SEQ": query["meetSeq"], "GAME_ID": query["gameId"],
                "homeTeam": identity["source_home"], "awayTeam": identity["source_away"]}
    for key, value in expected.items():
        found = set(re.findall(r"formData\.append\('" + key + r"',\s*'([^']+)'\)", html))
        if found != {value}:
            raise IdentityError("韩职详情比赛或球队身份冲突")
    blocks = Document(html).root.find("div", **{"class": "match-lineup-player"})
    tables = blocks[0].find("table") if blocks else []
    if len(tables) != 4:
        raise SourceError("韩职首发结构待核实")
    lineups = []
    for side, table in zip(("home", "away"), (tables[0], tables[2])):
        players = []
        for row in table.find("tr"):
            cells = row.find("td")
            if len(cells) < 3 or cells[1].text not in ("GK", "DF", "MF", "FW"):
                continue
            player = re.fullmatch(r"playerDetailPop\('([^']+)',\s*'(\d+)'\)", row.attrs.get("onclick", ""))
            if player and player[1] == identity["source_" + side]:
                players.append({"id": "kleague:" + player[2], "number": cells[0].text,
                                "position": cells[1].text, "name": cells[2].text})
        confirmed = len(players) == len({p["id"] for p in players}) == 11
        lineups.append({"side": side, "status": "confirmed" if confirmed else "unconfirmed",
                        "players": players if confirmed else []})
    return lineups


K_EVENTS = {"득점": ("goal", "进球"), "골": ("goal", "进球"),
            "경고": ("yellow-card", "黄牌"), "퇴장": ("red-card", "红牌"),
            "교체": ("substitution", "换人"), "자책골": ("own-goal", "乌龙球")}


def k_events(info, identity, match):
    query = parse.parse_qs(parse.urlsplit(identity["source_url"]).query)
    result, seen = [], set()
    sides = {identity["source_home"]: "home", identity["source_away"]: "away"}
    for period, key in enumerate(("firstHalf", "secondHalf", "EfirstHalf", "EsecondHalf"), 1):
        for item in info.get(key) or []:
            if (str(item.get("year")) != query["year"][0] or str(item.get("gameId")) != query["gameId"][0]
                    or str(item.get("leagueId")) != "1" or item.get("halfType") != period
                    or item.get("meetSeq") not in (None, int(query["meetSeq"][0]))):
                raise IdentityError("韩职事件比赛身份冲突")
            kind = K_EVENTS.get(item.get("eventName"))
            side = sides.get(item.get("teamId"))
            minute = item.get("timeMin")
            if not kind or not side or type(minute) is not int or not 0 <= minute <= 65 or item.get("num") is None:
                continue
            event_id = f"{identity['provider_event_id']}:{period}:{item['num']}"
            if event_id in seen:
                continue
            seen.add(event_id)
            # The official timeline reports minutes relative to each half.
            elapsed = (0, 45, 90, 105)[period - 1] + minute
            duration = 45 if period <= 2 else 15
            end = (45, 90, 105, 120)[period - 1]
            clock = f"{end}+{minute-duration}′" if minute > duration else f"{elapsed}′"
            result.append({"id": event_id, "type": kind[0], "label": kind[1], "side": side,
                           "team": match[side], "period": period, "period_label": "常规时间" if period <= 2 else "加时",
                           "clock": clock, "seconds": elapsed * 60 + (item.get("timeSec") or 0),
                           "participants": " / ".join(p for p in (item.get("playerName"), item.get("playerName2")) if p)})
    return sorted(result, key=lambda e: (e["period"], e["seconds"], e["id"]))


def fetch_kleague(match):
    from . import live
    if match.get("league_code") != "KD1":
        raise SourceError("韩职资料仅适用韩职赛事")
    key = live.group_key(match)
    identity = live.normalize(match, live.fetch_group(key), key)
    url = identity["source_url"]
    params = {k: v[0] for k, v in parse.parse_qs(parse.urlsplit(url).query).items() if k != "leagueId"}
    captured = now_iso()
    context = {"available": True, "source": "K League 官网", "source_url": url, "captured_at": captured,
               "event_id": identity["provider_event_id"], "state": identity["state"], "score": identity["score"],
               "recent": [[], []], "lineups": [], "injuries": [], "injuries_verified": False}
    facts = {"source": context["source"], "source_url": url, "captured_at": captured,
             "provider_event_id": identity["provider_event_id"], "statistics": [], "events": [],
             "statistics_scope": "官方比赛累计统计，常规与加时口径需逐场核实",
             "events_scope": "官方时间线已列事件", "missing": ["xG尚未取得有效来源值"]}
    context["match_facts"] = facts
    errors = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        pages = {"lineups": pool.submit(read_page, url)}
        for kind in ("matchInfo", "matchRecord"):
            pages[kind] = pool.submit(read_page, f"https://www.kleague.com/api/ddf/match/{kind}.do", params)
        for kind, future in pages.items():
            try:
                text = future.result()
                if kind == "lineups":
                    context["lineups"] = k_lineups(text, identity)
                    continue
                payload = json.loads(text)
                if str(payload.get("resultCode")) != "200" or not isinstance(payload.get("data"), dict):
                    raise SourceError("韩职统计或事件响应无效")
                data = payload["data"]
                if identity["state"] not in ("live", "completed"):
                    continue  # Pre-match zero-filled statistics are not observed match facts.
                if kind == "matchInfo":
                    facts["events"] = k_events(data, identity, match)
                else:
                    codes = {"attempts": "totalShots", "onTarget": "shotsOnTarget", "possession": "possessionPct",
                             "corners": "wonCorners", "fouls": "foulsCommitted", "yellowCards": "yellowCards",
                             "redCards": "redCards", "offsides": "offsides"}
                    for field, code in codes.items():
                        row = statistic(code, [(data.get(side) or {}).get(field) for side in ("home", "away")])
                        if row:
                            facts["statistics"].append(row)
            except IdentityError:
                raise
            except (SourceError, ValueError, TypeError, KeyError) as exc:
                errors.append(f"{kind}：{exc}")
    context["source_errors"] = errors
    return context


def j_records(html):
    chunks = []
    for script in Document(html).root.find("script"):
        frame = re.fullmatch(r"self\.__next_f\.push\((.*)\);?", script.text)
        if frame:
            packet = json.loads(frame[1])
            if isinstance(packet, list) and len(packet) == 2 and packet[0] == 1 and isinstance(packet[1], str):
                chunks.append(packet[1])
    result = []
    for line in "".join(chunks).splitlines():
        try:
            result.extend(dictionaries(json.loads(line.partition(":")[2])))
        except ValueError:
            continue
    return result


def j_identity(records, match, mapping):
    headers = [d for d in records if d.get("variant") == "game-details"]
    if len(headers) != 1:
        raise SourceError("日职官方比赛详情不唯一")
    header = headers[0]
    if (header.get("tournament") != "j1" or header.get("tournamentName") != "明治安田Ｊ１リーグ"
            or parse_date(header["date"].removeprefix("$D")) != parse_date(match["kickoff"])
            or [header[s + "Team"].get("teamCode") for s in ("home", "away")] != mapping["teams"]):
        raise IdentityError("日职官方赛事、球队或开球时间冲突")
    return header


def j_events(records, match, mapping):
    periods = {'前半': 1, '後半': 2, '延長前半': 3, '延長後半': 4}
    kinds = {'yellow-card': ('yellow-card', '黄牌'), 'red-card': ('red-card', '红牌'),
             'goal': ('goal', '进球'), 'own-goal': ('own-goal', '乌龙球')}
    events, seen = [], set()
    for row in records:
        if not isinstance(row.get('period'), str):
            continue
        period = periods.get(row.get('period'))
        minute, extra = row.get('time'), row.get('extraTime', 0)
        duration = 45 if period in (1, 2) else 15
        if not period or type(minute) is not int or not 0 <= minute <= duration or type(extra) is not int or not 0 <= extra <= 30:
            continue
        elapsed = (0, 45, 90, 105)[period - 1] + minute
        clock = f'{elapsed}+{extra}′' if extra else f'{elapsed}′'
        for i, side in enumerate(('home', 'away')):
            players = row.get(side + 'Players') or []
            for p in players:
                info = p.get('legacyPlayerPhotoLookup') or {}
                if info.get('teamNameKey') != mapping['teams'][i] or not info.get('playerId'):
                    raise IdentityError('日职事件球员球队身份冲突')
            substitutions = [p for p in players if p.get('eventType') in ('sub-in', 'sub-out')]
            groups = []
            if sorted(p['eventType'] for p in substitutions) == ['sub-in', 'sub-out']:
                groups.append(('substitution', '换人', sorted(substitutions, key=lambda p: p['eventType'])))
            groups.extend((*kinds[p['eventType']], [p]) for p in players if p.get('eventType') in kinds)
            for kind, label, involved in groups:
                event_id = 'jleague:' + fingerprint([mapping['source_url'], period, minute, extra, side, kind,
                    [(p['legacyPlayerPhotoLookup']['playerId'], p['eventType']) for p in involved]])
                if event_id in seen: continue
                seen.add(event_id)
                events.append({'id': event_id, 'type': kind, 'label': label, 'side': side, 'team': match[side],
                    'period': period, 'period_label': '常规时间' if period <= 2 else '加时',
                    'clock': clock, 'seconds': (elapsed + extra) * 60,
                    'participants': ' / '.join(p.get('name') or '' for p in involved)})
    return sorted(events, key=lambda e: (e['period'], e['seconds'], e['id']))


def fetch_jleague(match):
    mappings = json.loads((ROOT / "config/football_official_matches.json").read_text(encoding="utf-8"))
    mapping = next((m for m in mappings if m["match_id"] == match["id"] and m["league_code"] == match.get("league_code")
                    and m["codes"] == [match.get("home_code"), match.get("away_code")]
                    and parse_date(m["kickoff"]) == parse_date(match["kickoff"])), None)
    if not mapping:
        return {"available": False, "reason": "暂无已核实的日职官方比赛映射", "recent": [[], []], "lineups": []}
    url = mapping["source_url"]
    text = read_page(url + "lineup/")
    records = j_records(text)
    header = j_identity(records, match, mapping)
    captured = now_iso()
    lineups = []
    pairs = [d for d in records if "isHideHeader" in d and isinstance(d.get("homeTeam"), dict)]
    if len(pairs) == 1:
        for i, side in enumerate(("home", "away")):
            players = []
            for player in pairs[0][side + "Team"].get("players", []):
                info = player.get("legacyPlayerPhotoLookup") or {}
                position = re.fullmatch(r"(GK|DF|MF|FW)\s+(\d+)", player.get("position", ""))
                if position and info.get("teamNameKey") == mapping["teams"][i] and info.get("playerId"):
                    players.append({"id": "jleague:" + str(info["playerId"]), "name": player["name"],
                                    "position": position[1], "number": position[2]})
            confirmed = len(players) == len({p["id"] for p in players}) == 11
            lineups.append({"side": side, "status": "confirmed" if confirmed else "unconfirmed", "players": players if confirmed else []})
    facts = {"source": "J.League 官网", "source_url": url + "stats/", "captured_at": captured,
             "provider_event_id": url.rstrip("/").split("/")[-1], "statistics": [], "events": j_events(records, match, mapping),
             "statistics_scope": "官方比赛累计统计；不自动用于90分钟训练", "events_scope": "官方时间线已列关键事件",
             "events_source_url": url + "lineup/"}
    errors = []
    try:
        stats_html = read_page(url + "stats/")
        j_identity(j_records(stats_html), match, mapping)
        labels = {"シュート数": "totalShots", "枠内シュート数": "shotsOnTarget", "パス数": "totalPasses",
                  "セーブ数": "saves", "警告数": "yellowCards", "退場数": "redCards", "ファウル数": "foulsCommitted"}
        for item in (Document(stats_html).root.find("div", **{"class": "o-team-comparison__stats-item"}) if header["type"] == "post-game" else []):
            fields = item.find("p")
            if len(fields) == 3 and fields[0].text in labels:
                row = statistic(labels[fields[0].text], [fields[1].text, fields[2].text])
                if row and row not in facts["statistics"]:
                    facts["statistics"].append(row)
    except IdentityError:
        raise
    except (SourceError, ValueError, TypeError, KeyError) as exc:
        errors.append(str(exc))
    return {"available": True, "source": "J.League 官网", "source_url": url, "captured_at": captured,
            "event_id": facts["provider_event_id"], "recent": [[], []], "lineups": lineups,
            "state": {"post-game": "completed", "pre-game": "upcoming"}.get(header["type"], "unknown"),
            "venue": header.get("stadium", {}).get("name"), "match_facts": facts,
            "source_errors": errors, "identity_evidence": mapping}
