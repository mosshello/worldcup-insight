"""League-scoped fixture identity and point-in-time basic facts from audited history."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .catalog import classify
from .history_pipeline import competition_staging
from .sources import SourceError, parse_date
from .store import fingerprint, now_iso

# Explicit bookmaker abbreviations, never fuzzy score/name matching.
ALIASES = {"jp.j1": {"町田泽维": "町田泽维亚"},
           "kr.k1": {"仁川联": "仁川联合", "仁川联队": "仁川联合", "蔚山HD": "蔚山现代"}}


def load_history(path=None, competition_id=None):
    if competition_id is not None:
        path = competition_staging(competition_id)
    if path is None:
        raise SourceError("须指定赛事或历史快照路径")
    try:
        data = json.loads(Path(path).read_text(encoding="utf8"))
        if data.get("status") != "collected" or data.get("digest") != fingerprint({"matches": data["matches"], "coverage": data["coverage"]}):
            raise ValueError()
        if data.get("fixture_digest") != fingerprint(data.get("fixtures", [])):
            raise ValueError()
        return data
    except (OSError, ValueError, KeyError, TypeError):
        raise SourceError("联赛历史快照缺失或校验失败") from None


def match_fixture(match, data):
    competition = classify(match)
    aliases = ALIASES.get(competition["id"], {})
    candidates = [r for r in data.get("fixtures", []) if r["competition_id"] == competition["id"]
                  and r["season"] == competition["season"]
                  and all(r[s] == aliases.get(match[s], match[s]) for s in ("home", "away"))
                  and abs((parse_date(r["kickoff"]) - parse_date(match["kickoff"])).total_seconds()) <= 900]
    unique = {r["id"]: r for r in candidates}
    identities = {(r["id"], r["home_id"], r["away_id"], r["kickoff"]) for r in candidates}
    if len(unique) != 1 or len(identities) != 1:
        raise SourceError("未通过联赛、赛季、双方球队与开赛时间匹配")
    return next(iter(unique.values()))


def team_mappings(matches, data):
    result = {}
    for match in matches:
        fixture = match_fixture(match, data)
        for side in ("home", "away"):
            key = (fixture["competition_id"], str(match[side+"_id"]))
            row = dict(competition_id=key[0], provider="sporttery", provider_id=key[1],
                       team_id=fixture[side+"_id"], source_url=fixture["source_url"],
                       evidence_match_id=fixture["id"], checked_at=now_iso())
            if key in result and result[key]["team_id"] != row["team_id"]:
                raise SourceError("球队标识映射冲突")
            result[key] = row
    return list(result.values())


def team_result(row, team):
    home = row["home_id"] == team
    own, opp = ("home", "away") if home else ("away", "home")
    gf, ga = row[own+"_goals"], row[opp+"_goals"]
    return dict(id=row["id"], date=row["kickoff"], opponent=row[opp], venue="主" if home else "客",
                gf=gf, ga=ga, score=f"{gf}-{ga}", source="500", source_url=row["source_url"],
                verification=row.get("independent_verification", "pending"))


def summarize(rows):
    return dict(matches=len(rows), wins=sum(r["gf"] > r["ga"] for r in rows),
                draws=sum(r["gf"] == r["ga"] for r in rows), losses=sum(r["gf"] < r["ga"] for r in rows),
                goals_for=sum(r["gf"] for r in rows), goals_against=sum(r["ga"] for r in rows))


def historical_context(match, data=None, now=None):
    if data is None:
        from .context_history import display_history
        data = display_history(match)
    fixture = match_fixture(match, data)
    now = now or datetime.now(timezone.utc)
    cutoff = min(now, parse_date(match["kickoff"]))
    if now - parse_date(fixture["collected_at"]) > timedelta(hours=48):
        raise SourceError("联赛赛程快照超过48小时未更新")
    rows = [r for r in data["matches"] if r["competition_id"] == fixture["competition_id"]
            and r["period"] == "regular_time" and not r.get("kickoff_review_required")
            and r.get("independent_verification") != "conflict"
            and parse_date(r["kickoff"]) + timedelta(minutes=90) < cutoff
            and parse_date(r["collected_at"]) <= now]
    rows.sort(key=lambda r: r["kickoff"], reverse=True)
    recent, venues, season_stats = [], [], []
    for side in ("home", "away"):
        team = fixture[side+"_id"]
        team_rows = [r for r in rows if team in (r["home_id"], r["away_id"])]
        recent.append([team_result(r, team) for r in team_rows[:5]])
        venues.append([team_result(r, team) for r in team_rows if r[side+"_id"] == team][:5])
        season_stats.append(summarize([team_result(r, team) for r in team_rows if r["season"] == fixture["season"]]))
    h2h = [team_result(r, fixture["home_id"]) for r in rows
           if {r["home_id"], r["away_id"]} == {fixture["home_id"], fixture["away_id"]}][:5]
    return dict(available=all(recent), source="500 历史赛果", recent_source="500",
                source_url=fixture["source_url"], captured_at=fixture["collected_at"], built_at=now_iso(),
                recent=recent, venue_recent=venues, season_stats=season_stats, h2h=h2h, h2h_checked=True,
                competition={k: fixture[k] for k in ("competition_id", "season", "stage")},
                provider_ids={s: fixture[s+"_id"] for s in ("home", "away")},
                event_id=fixture["id"], injuries=[], injuries_verified=False,
                lineups=[dict(side=s, status="unconfirmed", players=[]) for s in ("home", "away")],
                coverage="league_only", reason=None if all(recent) else "球队近期联赛赛果不足")
