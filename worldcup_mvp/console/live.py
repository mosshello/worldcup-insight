"""Independent public score feeds. Live scores are never settlement results."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib import request

from .sources import ESPN, league_for, verify_board_identity, SourceError, get_json, match_event, parse_date

KOREA = timezone(timedelta(hours=9))
K_CODES = {
    "GWJ": "K22",
    "GJS": "K35",
    "USH": "K01",
    "GGW": "K21",
    "SEO": "K09",
    "ICN": "K18",
}
# Exact official-club identities; no fuzzy matching or single-team matching.
K_NAMES = {
    "光州FC": "K22",
    "金泉尚武": "K35",
    "蔚山现代": "K01",
    "蔚山HD": "K01",
    "江原FC": "K21",
    "首尔FC": "K09",
    "仁川联": "K18",
    "全北现代": "K05",
    "大田市民": "K10",
    "富川FC": "K26",
    "富川1995": "K26",
    "安养FC": "K27",
    "济州SK": "K04",
    "浦项制铁": "K03",
}


def group_key(match):
    code = match.get("league_code")
    if code == "KD1":
        return (
            "kleague",
            parse_date(match["kickoff"]).astimezone(KOREA).strftime("%Y-%m"),
        )
    league = league_for(code)
    if league:
        return (
            league,
            parse_date(match["kickoff"]).astimezone(timezone.utc).strftime("%Y%m%d"),
        )
    return None


def fetch_group(key):
    provider, date = key
    if provider != "kleague":
        data = get_json(ESPN + provider + "/scoreboard?dates=" + date)
        if not isinstance(data.get("events"), list):
            raise SourceError("比分接口结构无效")
        source = verify_board_identity(data, provider)
        if any(e.get('uid') != f"s:600~l:{source['id']}~e:{e.get('id')}" for e in data['events']):
            raise SourceError('比分比赛来源赛事身份不一致')
        return data["events"]
    year, month = date.split("-")
    body = {
        "leagueId": "1",
        "teamId": "",
        "ticketStatus": "",
        "year": year,
        "month": month,
        "ticketYn": "",
    }
    opener = (
        request.build_opener(request.ProxyHandler({}))
        if os.environ.get("FOOTBALL_DIRECT_NETWORK", "1") == "1"
        else request.build_opener()
    )
    try:
        req = request.Request(
            "https://www.kleague.com/getScheduleList.do",
            data=json.dumps(body).encode(),
            headers={
                "User-Agent": "Mozilla/5.0 football-analysis/1.0",
                "Content-Type": "application/json",
                "Referer": "https://www.kleague.com/schedule.do?leagueId=1",
            },
        )
        with opener.open(req, timeout=12) as response:
            data = json.loads(response.read(8_000_000).decode("utf8"))
        rows = data["data"]["scheduleList"]
        if str(data["resultCode"]) != "200" or not isinstance(rows, list):
            raise ValueError()
        return rows
    except (OSError, ValueError, KeyError, TypeError):
        raise SourceError("K League 比分接口连接失败或结构无效") from None


def goals(value):
    if isinstance(value, bool) or not re.fullmatch(r"\d{1,2}", str(value)):
        raise SourceError("比分字段无效")
    return int(value)


def normalize(match, rows, key):
    if key[0] == "kleague":
        target = [
            K_CODES.get(match.get(s + "_code")) or K_NAMES.get(match.get(s))
            for s in ("home", "away")
        ]
        candidates = []
        for row in rows:
            try:
                kickoff = parse_date(
                    row["gameDate"].replace(".", "-")
                    + "T"
                    + row["gameTime"]
                    + ":00+09:00"
                )
                if (
                    all(target)
                    and str(row["leagueId"]) == "1"
                    and [row["homeTeam"], row["awayTeam"]] == target
                    and abs((kickoff - parse_date(match["kickoff"])).total_seconds())
                    <= 900
                ):
                    candidates.append(row)
            except (KeyError, ValueError, TypeError):
                continue
        if len(candidates) != 1:
            raise SourceError("未通过联赛、主客队及开球时间匹配")
        row = candidates[0]
        status = str(row.get("gameStatus") or "").strip()
        state, label, clock = "upcoming", "未开赛", None
        if status == "FE" or row.get("endYn") == "Y":
            state, label = "completed", "全场结束"
        elif status.startswith("1E"):
            state, label = "live", "中场休息"
        elif status.startswith(("2E", "3E", "4E")):
            state, label = (
                "live",
                {"2E": "下半场结束", "3E": "加时中场", "4E": "加时结束"}[status[:2]],
            )
        elif re.match(r"^[1-4]S", status):
            state = "live"
            label = {
                "1S": "上半场",
                "2S": "下半场",
                "3S": "加时上半场",
                "4S": "加时下半场",
            }[status[:2]]
            minute = status[2:].strip()
            if re.fullmatch(r"\d{1,3}(?:\s*\+\s*\d{1,2})?", minute):
                clock = minute + "′"
        elif status:
            state, label = "unknown", "状态待核实"
        score = (
            None
            if state not in ("live", "completed")
            else f"{goals(row.get('homeGoal'))}-{goals(row.get('awayGoal'))}"
        )
        return {
            "source": "K League 官网",
            "source_url": f"https://www.kleague.com/match.do?year={row['year']}&leagueId=1&gameId={row['gameId']}&meetSeq={row['meetSeq']}",
            "provider_event_id": f"{row['year']}:1:{row['gameId']}",
            "state": state,
            "label": label,
            "clock": clock,
            "score": score,
            # K League 1 has no extra-time/shoot-out match outcome.
            "score_period": "regular_time" if state == "completed" and status == "FE" else "unknown",
            "source_status": status,
            "source_home": row["homeTeam"],
            "source_away": row["awayTeam"],
            "source_kickoff": (
                row["gameDate"].replace(".", "-") + "T" + row["gameTime"] + ":00+09:00"
            ),
        }
    event = match_event(match, rows, key[0])
    if not event:
        raise SourceError("未通过联赛、主客队及开球时间匹配")
    status = event.get("status") or event["competitions"][0].get("status", {})
    kind = status.get("type", {})
    name = kind.get("name", "")
    state = {"pre": "upcoming", "in": "live", "post": "completed"}.get(
        kind.get("state"), "unknown"
    )
    label = {
        "upcoming": "未开赛",
        "live": "进行中",
        "completed": "全场结束",
        "unknown": "状态待核实",
    }[state]
    if "POSTPONED" in name:
        state, label = "postponed", "延期"
    elif "CANCELED" in name or "CANCELLED" in name:
        state, label = "cancelled", "取消"
    elif "SUSPENDED" in name or "DELAYED" in name or "ABANDONED" in name:
        state, label = "suspended", "比赛暂停"
    elif name == "STATUS_HALFTIME":
        label = "中场休息"
    elif state == "live":
        label = {
            1: "上半场",
            2: "下半场",
            3: "加时上半场",
            4: "加时下半场",
            5: "点球大战",
        }.get(status.get("period"), "进行中")
    teams = {p["homeAway"]: p for p in event["competitions"][0]["competitors"]}
    score = (
        f"{goals(teams['home'].get('score'))}-{goals(teams['away'].get('score'))}"
        if state in ("live", "completed", "suspended")
        else None
    )
    clock = (
        status.get("displayClock")
        if state == "live" and name != "STATUS_HALFTIME"
        else None
    )
    return {
        "source": "ESPN",
        "source_url": f"https://www.espn.com/soccer/match/_/gameId/{event['id']}",
        "provider_event_id": str(event["id"]),
        "state": state,
        "label": label,
        "clock": clock,
        "score": score,
        "score_period": (
            "regular_time"
            if state == "completed" and name == "STATUS_FULL_TIME"
            and status.get("period") == 2 and kind.get("completed") is True
            else "unknown"
        ),
        "source_status": name,
        "source_home": teams["home"]["team"]["id"],
        "source_away": teams["away"]["team"]["id"],
        "source_kickoff": event["date"],
    }


def display(record, now=None):
    if not record:
        return {"score": None, "state": "unknown", "label": "待更新", "stale": True}
    now = now or datetime.now(timezone.utc)
    try:
        age = (now - parse_date(record["observed_at"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        age = float("inf")
    stale = (
        bool(record.get("error"))
        or age < -30
        or (record.get("state") != "completed" and age > 90)
    )
    return {**record, "stale": stale}
