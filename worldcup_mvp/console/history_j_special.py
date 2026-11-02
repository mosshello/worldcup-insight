"""Bounded official checks of J1 special-season 90-minute result semantics."""
from __future__ import annotations

import json
import re
from datetime import datetime

from .history_audit import Document, NAMES_500

# Published match links: two regional shootouts and all ten playoff second legs.
OFFICIAL_MATCHES = ("020602", "020702", "060604", "060603", "060606", "060611",
                    "060608", "060607", "060609", "060610", "060605", "060602")
ALIASES = {**NAMES_500,
           "jleague:kyoto": {"京都不死鸟"}, "jleague:kobe": {"神户胜利船"},
           "jleague:ftokyo": {"东京FC"}, "jleague:kashima": {"鹿岛鹿角"},
           "jleague:tokyov": {"东京绿茵"}, "jleague:okayama": {"冈山绿雉"},
           "jleague:shimizu": {"清水鼓动"}, "jleague:mito": {"水户蜀葵"},
           "jleague:nagasaki": {"长崎航海"}, "jleague:chiba": {"千叶市原"},
           "jleague:fukuoka": {"福冈黄蜂"}}


def dictionaries(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from dictionaries(item)
    elif isinstance(value, list):
        for item in value:
            yield from dictionaries(item)


def parse_official_periods(html):
    """Read server-rendered JSON, never execute script or infer from final totals."""
    candidates = []
    for script in Document(html).root.find("script"):
        if "playerScoreList" not in script.text:
            continue
        match = re.fullmatch(r"self\.__next_f\.push\((.*)\);?", script.text)
        if not match:
            continue
        frame = json.loads(match[1])
        if not isinstance(frame, list) or len(frame) != 2 or frame[0] != 1 or not isinstance(frame[1], str):
            continue
        for line in frame[1].splitlines():
            _, separator, body = line.partition(":")
            if not separator:
                continue
            try:
                value = json.loads(body)
            except ValueError:
                continue
            candidates.extend(d for d in dictionaries(value) if d.get("variant") == "game-details"
                              and "homeTeam" in d and "awayTeam" in d)
    if len(candidates) != 1:
        raise ValueError("官方比赛数据缺失或不唯一")
    data = candidates[0]
    if data.get("type") != "post-game" or data.get("tournamentName") != "明治安田Ｊ１百年構想リーグ":
        raise ValueError("官方赛事或完赛状态不符")
    if type(data.get("isExtended")) is not bool:
        raise ValueError("官方加时状态缺失")
    kickoff = datetime.fromisoformat(data["date"].removeprefix("$D").replace("Z", "+00:00"))
    if kickoff.tzinfo is None or kickoff.year != 2026:
        raise ValueError("官方比赛时间无效")
    result = dict(kickoff=kickoff.isoformat(), period="regular_time", extra_time=data["isExtended"],
                  shootout=data.get("penalty"), half_score_basis="official_goal_events")
    for side in ("home", "away"):
        team = data[side + "Team"]
        if not re.fullmatch(r"[a-z0-9_-]+", team.get("teamCode", "")):
            raise ValueError("官方球队标识缺失")
        total, events = team.get("score"), team.get("playerScoreList")
        if type(total) is not int or not 0 <= total <= 30 or not isinstance(events, list) or len(events) != total:
            raise ValueError("官方进球事件不完整")
        minutes = []
        for event in events:
            minute = re.fullmatch(r"(\d{1,3})(?:\+(\d{1,2}))?['’]", event.get("scoreTime", ""))
            if not minute or not 1 <= int(minute[1]) <= 120:
                raise ValueError("无法判定官方进球时段")
            # Added time belongs to the base minute's period (45+ / 90+).
            minutes.append(int(minute[1]))
        if not data["isExtended"] and any(m > 90 for m in minutes):
            raise ValueError("官方加时状态与进球时段冲突")
        result[side + "_id"] = "jleague:" + team["teamCode"]
        result[side + "_goals"] = sum(m <= 90 for m in minutes)
        result[side + "_half_goals"] = sum(m <= 45 for m in minutes)
        result[side + "_after_extra_goals"] = total
    return result


def verify_special(rows, archive):
    errors = []
    for match_id in OFFICIAL_MATCHES:
        url = f"https://www.jleague.jp/match/j1/2026/{match_id}/lineup/"
        try:
            official = parse_official_periods(archive.fetch(url))
            snapshot = archive.entries[-1]
            kickoff = datetime.fromisoformat(official["kickoff"])
            matches = [r for r in rows if r["season"] == "2026-special"
                       and r["home"] in ALIASES.get(official["home_id"], set())
                       and r["away"] in ALIASES.get(official["away_id"], set())
                       and abs((datetime.fromisoformat(r["kickoff"]) - kickoff).total_seconds()) < 12 * 3600]
            if len(matches) != 1:
                raise ValueError("官方样本无法唯一对应球队与日期")
            row = matches[0]
            same = all(row[k] == official[k] for k in ("home_goals", "away_goals", "home_half_goals", "away_half_goals"))
            row.update(independent_verification="agree" if same else "conflict", verification_url=url,
                       kickoff_review_required=abs((datetime.fromisoformat(row["kickoff"]) - kickoff).total_seconds()) > 1800,
                       period_verification={**official, "source_url": url, "source_sha256": snapshot.get("sha256"),
                                            "collected_at": snapshot["collected_at"]})
            if not same:
                errors.append(f"{row['id']}: 官方半全场比分不一致")
        except Exception as exc:
            errors.append(f"官方样本 {match_id}: {exc}")
    return errors
