"""赛前情报聚合：FIFA 自动统计 + 可编辑 overlay（伤停/场地/裁判/阵容）。"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from .recent_lineups import RecentLineupError, infer_recent_lineups

from .local_match_bundle import load_local_match_bundle
from .team_names import resolve_team

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OVERLAY_PATH = PROJECT_ROOT / "data" / "intelligence_overlay.json"
OVERLAY_EXAMPLE = PROJECT_ROOT / "data" / "intelligence_overlay.example.json"

# FIFA 三字母代码 → 足联 + 典型打法标签（启发式，仅供分析文案）
CONFEDERATION_PROFILE: dict[str, dict[str, Any]] = {
    "GER": {"confederation": "UEFA", "region": "欧洲", "style_tags": ["高位压迫", "转换速度快", "身体对抗强"]},
    "FRA": {"confederation": "UEFA", "region": "欧洲", "style_tags": ["边路突破", "中场控制", "阵容深度"]},
    "NED": {"confederation": "UEFA", "region": "欧洲", "style_tags": ["控球组织", "高位线", "边路宽度"]},
    "ENG": {"confederation": "UEFA", "region": "欧洲", "style_tags": ["身体对抗", "定位球", "快速推进"]},
    "ESP": {"confederation": "UEFA", "region": "欧洲", "style_tags": ["传控", "肋部渗透", "压迫"]},
    "POR": {"confederation": "UEFA", "region": "欧洲", "style_tags": ["边路进攻", "个人能力", "反击"]},
    "BRA": {"confederation": "CONMEBOL", "region": "南美", "style_tags": ["技术流", "一对一", "边路内切"]},
    "ARG": {"confederation": "CONMEBOL", "region": "南美", "style_tags": ["中路小配合", "防守纪律", "巨星驱动"]},
    "PRY": {"confederation": "CONMEBOL", "region": "南美", "style_tags": ["低位防守", "反击", "身体对抗"]},
    "ECU": {"confederation": "CONMEBOL", "region": "南美", "style_tags": ["紧凑防守", "快速转换", "体能"]},
    "COL": {"confederation": "CONMEBOL", "region": "南美", "style_tags": ["边路速度", "压迫", "对抗"]},
    "MAR": {"confederation": "CAF", "region": "非洲", "style_tags": ["反击速度", "身体对抗", "定位球"]},
    "SEN": {"confederation": "CAF", "region": "非洲", "style_tags": ["边路冲击", "体能", "压迫"]},
    "JPN": {"confederation": "AFC", "region": "亚洲", "style_tags": ["整体跑动", "快速传切", "纪律性"]},
    "USA": {"confederation": "CONCACAF", "region": "中北美", "style_tags": ["高强度", "身体对抗", "定位球"]},
    "MEX": {"confederation": "CONCACAF", "region": "中北美", "style_tags": ["主场气势", "边路", "经验"]},
    "CIV": {"confederation": "CAF", "region": "非洲", "style_tags": ["身体对抗", "反击", "体能"]},
    "NOR": {"confederation": "UEFA", "region": "欧洲", "style_tags": ["高位压迫", "直接打法", "定位球"]},
    "SWE": {"confederation": "UEFA", "region": "欧洲", "style_tags": ["身体对抗", "定位球", "整体纪律"]},
    "BIH": {"confederation": "UEFA", "region": "欧洲", "style_tags": ["技术中场", "快速推进", "定位球"]},
    "BEL": {"confederation": "UEFA", "region": "欧洲", "style_tags": ["个人能力", "反击", "边路"]},
    "DRC": {"confederation": "CAF", "region": "非洲", "style_tags": ["反击速度", "体能", "对抗"]},
    "SUI": {"confederation": "UEFA", "region": "欧洲", "style_tags": ["组织严密", "反击", "纪律性"]},
    "ALG": {"confederation": "CAF", "region": "非洲", "style_tags": ["速度", "反击", "对抗"]},
    "CRO": {"confederation": "UEFA", "region": "欧洲", "style_tags": ["中场控制", "技术流", "大赛经验"]},
    "AUT": {"confederation": "UEFA", "region": "欧洲", "style_tags": ["高位压迫", "直接", "体能"]},
    "CPV": {"confederation": "CAF", "region": "非洲", "style_tags": ["防守反击", "体能", "对抗"]},
    "AUS": {"confederation": "AFC", "region": "亚洲", "style_tags": ["身体对抗", "定位球", "高强度"]},
    "EGY": {"confederation": "CAF", "region": "非洲", "style_tags": ["技术", "控球", "定位球"]},
    "GHA": {"confederation": "CAF", "region": "非洲", "style_tags": ["速度", "反击", "体能"]},
    "CAN": {"confederation": "CONCACAF", "region": "中北美", "style_tags": ["身体对抗", "边路", "高强度"]},
    "RSA": {"confederation": "CAF", "region": "非洲", "style_tags": ["防守组织", "反击", "体能"]},
}

STYLE_MATCHUP_NOTES: dict[tuple[str, str], str] = {
    ("UEFA", "CONMEBOL"): (
        "欧南美对阵常见节奏差异：欧洲球队往往更强调高位结构与体能输出，"
        "南美球队更擅长低位保护、个人摆脱与快速转换；若欧洲队久攻不下，平局概率会上升。"
    ),
    ("UEFA", "CAF"): "欧非对阵需关注非洲球队的反击速度与定位球效率，欧洲球队控球率未必转化为胜势。",
    ("CONMEBOL", "AFC"): "南美对亚洲球队时，技术一对一与肋部渗透通常是主要破局手段。",
}


def _empty_split() -> dict[str, Any]:
    return {
        "played": 0,
        "wins": 0,
        "draws": 0,
        "losses": 0,
        "goals_for": 0,
        "goals_against": 0,
        "points": 0,
    }


def compute_home_away_splits(
    history: list[dict[str, Any]],
    team_id: str,
) -> dict[str, dict[str, Any]]:
    """从 FIFA 已结束比赛计算球队在本届赛事的主场/客场拆分统计。"""
    home = _empty_split()
    away = _empty_split()

    for match in history:
        if not match.get("IdGroup") or match.get("MatchStatus") != 0:
            continue
        home_team = match.get("Home") or {}
        away_team = match.get("Away") or {}
        home_id = str(home_team.get("IdTeam") or "")
        away_id = str(away_team.get("IdTeam") or "")
        try:
            hg = int(match.get("HomeTeamScore"))
            ag = int(match.get("AwayTeamScore"))
        except (TypeError, ValueError):
            continue

        if home_id == team_id:
            bucket = home
            scored, conceded = hg, ag
        elif away_id == team_id:
            bucket = away
            scored, conceded = ag, hg
        else:
            continue

        bucket["played"] += 1
        bucket["goals_for"] += scored
        bucket["goals_against"] += conceded
        if scored > conceded:
            bucket["wins"] += 1
            bucket["points"] += 3
        elif scored == conceded:
            bucket["draws"] += 1
            bucket["points"] += 1
        else:
            bucket["losses"] += 1

    for bucket in (home, away):
        if bucket["played"]:
            bucket["win_rate"] = round(bucket["wins"] / bucket["played"], 3)
            bucket["goals_for_per_game"] = round(bucket["goals_for"] / bucket["played"], 2)
            bucket["goals_against_per_game"] = round(bucket["goals_against"] / bucket["played"], 2)
        else:
            bucket["win_rate"] = None
            bucket["goals_for_per_game"] = None
            bucket["goals_against_per_game"] = None

    return {"as_home": home, "as_away": away}


def _parse_venue_blob(value: str) -> dict[str, Any] | None:
    text = value.strip()
    if not text.startswith("{") and not text.startswith("["):
        return None
    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(text)
        except (ValueError, SyntaxError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _coerce_venue_input(venue: Any) -> dict[str, Any] | None:
    if venue is None or venue == "":
        return None
    if isinstance(venue, str):
        parsed = _parse_venue_blob(venue)
        return parsed if parsed is not None else None
    if isinstance(venue, dict):
        label = venue.get("label")
        if isinstance(label, str) and (label.startswith("{") or "IdStadium" in label):
            reparsed = _parse_venue_blob(label)
            if reparsed:
                return reparsed
        if venue.get("IdStadium") or venue.get("Name") or venue.get("CityName"):
            return venue
        stadium = venue.get("stadium") or venue.get("Stadium") or venue.get("Venue")
        if isinstance(stadium, (dict, str)) and (
            isinstance(stadium, dict)
            and (stadium.get("IdStadium") or stadium.get("Name"))
            or isinstance(stadium, str) and stadium.strip().startswith("{")
        ):
            coerced = dict(venue)
            if isinstance(stadium, str):
                parsed = _parse_venue_blob(stadium)
                coerced["stadium"] = parsed if parsed else stadium
            return coerced
        return venue
    return None


def _venue_text(value: Any) -> str | None:
    """从 FIFA/overlay 各种嵌套结构提取可读场地文案，禁止输出原始 JSON。"""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or text.startswith("{") or text.startswith("["):
            return None
        return text
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        for entry in value:
            text = _venue_text(entry)
            if text:
                return text
        return None
    if isinstance(value, dict):
        if isinstance(value.get("Description"), str) and value["Description"].strip():
            return value["Description"].strip()
        for key in ("Description", "Name", "ShortName", "LongName", "CityName", "name", "city", "City"):
            if key not in value:
                continue
            text = _venue_text(value[key])
            if text:
                return text
        for key in ("Stadium", "Venue", "Location", "City"):
            nested = value.get(key)
            if isinstance(nested, dict):
                text = _venue_text(nested)
                if text:
                    return text
        return None
    return None


def safe_display_text(value: Any, *, fallback: str = "") -> str:
    """供 UI 展示的安全文本，过滤 dict/list 原始结构。"""
    text = _venue_text(value)
    if text:
        return text
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and not stripped.startswith("{") and not stripped.startswith("["):
            return stripped
    return fallback


def normalize_venue(venue: dict[str, Any] | str | None) -> dict[str, Any] | None:
    coerced = _coerce_venue_input(venue)
    if not coerced or not isinstance(coerced, dict):
        return None
    venue = coerced
    stadium = _venue_text(
        venue.get("stadium")
        or venue.get("name")
        or venue.get("Stadium")
        or venue.get("Venue")
        or venue.get("Name")
        or venue.get("StadiumName")
    )
    city = _venue_text(
        venue.get("city") or venue.get("City") or venue.get("VenueCity") or venue.get("CityName")
    )
    country = _venue_text(venue.get("country") or venue.get("Country") or venue.get("IdCountry"))
    capacity = venue.get("capacity") or venue.get("Capacity")
    if isinstance(capacity, str) and capacity.isdigit():
        capacity = int(capacity)
    parts = [part for part in (stadium, city, country) if part]
    if not parts:
        return None
    return {
        "stadium": stadium,
        "city": city,
        "country": country,
        "capacity": capacity if isinstance(capacity, (int, float)) else None,
        "label": " · ".join(parts),
        "source": venue.get("source") or "fifa-public",
    }


def extract_venue_from_fifa(item: dict[str, Any]) -> dict[str, Any] | None:
    """从 FIFA 赛程对象提取场地信息（字段因 API 版本可能缺失）。"""
    stadium = item.get("StadiumName") or item.get("Stadium") or item.get("Venue")
    city = item.get("City") or item.get("VenueCity")
    if not stadium and not city:
        return None
    return normalize_venue(
        {
            "stadium": stadium,
            "city": city,
            "source": "fifa-public",
        }
    )


def _team_profile(team_name: str, abbr: str | None = None) -> dict[str, Any]:
    code = (abbr or resolve_team(team_name).get("abbr") or "").upper()
    profile = CONFEDERATION_PROFILE.get(code, {})
    return {
        "team": team_name,
        "code": code,
        "confederation": profile.get("confederation", "未知"),
        "region": profile.get("region", "未知"),
        "style_tags": profile.get("style_tags", []),
    }


def style_matchup_note(home_profile: dict[str, Any], away_profile: dict[str, Any]) -> str | None:
    home_conf = home_profile.get("confederation")
    away_conf = away_profile.get("confederation")
    if not home_conf or not away_conf or home_conf == "未知" or away_conf == "未知":
        return None
    if home_conf == away_conf:
        return f"同区域对阵（{home_profile.get('region')}），打法差异更多来自俱乐部体系与教练布置，而非地域模板。"
    note = STYLE_MATCHUP_NOTES.get((home_conf, away_conf)) or STYLE_MATCHUP_NOTES.get((away_conf, home_conf))
    if note:
        return note
    return (
        f"{home_profile.get('region')} vs {away_profile.get('region')}："
        f"关注节奏、压迫高度与转换速度差异，不宜仅用排名或积分判断走势。"
    )


def load_intelligence_overlay(path: Path | None = None) -> dict[str, Any]:
    target = path or OVERLAY_PATH
    if not target.exists():
        if OVERLAY_EXAMPLE.exists():
            target = OVERLAY_EXAMPLE
        else:
            return {"matches": {}, "teams": {}}
    try:
        with target.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"matches": {}, "teams": {}}
    if not isinstance(payload, dict):
        return {"matches": {}, "teams": {}}
    payload.setdefault("matches", {})
    payload.setdefault("teams", {})
    return payload


def _overlay_for_match(
    overlay: dict[str, Any],
    *,
    match_id: str | None = None,
    home: str | None = None,
    away: str | None = None,
) -> dict[str, Any]:
    matches = overlay.get("matches", {})
    if match_id and match_id in matches:
        return matches[match_id]
    if home and away:
        for key in (f"{home}|{away}", f"{home} vs {away}"):
            if key in matches:
                return matches[key]
    return {}


def merge_overlay_into_context(
    team_context: dict[str, Any],
    overlay_side: dict[str, Any] | None,
) -> dict[str, Any]:
    """将 overlay 中的 absences / scorers 合并进 team_context。"""
    if not overlay_side:
        return team_context
    merged = dict(team_context)
    if overlay_side.get("absences"):
        merged["absences"] = overlay_side["absences"]
    if overlay_side.get("scorers"):
        merged["scorers"] = overlay_side["scorers"]
    if overlay_side.get("predicted_lineup"):
        merged["predicted_lineup"] = overlay_side["predicted_lineup"]
    if overlay_side.get("tactics"):
        merged["tactics"] = overlay_side["tactics"]
    return merged


def _format_split(label: str, split: dict[str, Any]) -> str:
    if not split.get("played"):
        return f"{label}：本届暂无样本"
    return (
        f"{label} {split['played']} 场 "
        f"{split['wins']}胜{split['draws']}平{split['losses']}负 "
        f"进 {split['goals_for']} 失 {split['goals_against']} "
        f"（胜率 {split['win_rate']:.0%}）"
    )


def _absence_impact_summary(absences: list[dict[str, Any]]) -> dict[str, Any]:
    factors = {"out": 1.0, "suspended": 1.0, "doubtful": 0.5, "available": 0.0}
    active = [item for item in absences if item.get("status") != "available"]
    attack = sum(
        item.get("impact", 0.5) * factors.get(item.get("status", "doubtful"), 0.5)
        for item in active
        if item.get("line", "both") in ("attack", "both", None)
    )
    defense = sum(
        item.get("impact", 0.5) * factors.get(item.get("status", "doubtful"), 0.5)
        for item in active
        if item.get("line", "both") in ("defense", "both", None)
    )
    return {
        "count": len(active),
        "attack_burden": round(attack, 2),
        "defense_burden": round(defense, 2),
        "players": active,
    }


def _injuries_unavailable(match: dict[str, Any]) -> bool:
    injuries = (match.get("data_provenance") or {}).get("injuries", "")
    return isinstance(injuries, str) and "not-available" in injuries


def build_intelligence_report(
    match: dict[str, Any],
    *,
    overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成结构化赛前情报报告。"""
    overlay = overlay if overlay is not None else load_intelligence_overlay()
    home = match.get("home", "")
    away = match.get("away", "")
    match_id = str(match.get("provider_ids", {}).get("sporttery_match") or match.get("match_id") or "")

    match_overlay = _overlay_for_match(overlay, match_id=match_id or None, home=home, away=away)
    team_context = match.get("team_context") or {}
    home_ctx = merge_overlay_into_context(team_context.get("home") or {}, match_overlay.get("home"))
    away_ctx = merge_overlay_into_context(team_context.get("away") or {}, match_overlay.get("away"))

    home_profile = _team_profile(home, match_overlay.get("home_code"))
    away_profile = _team_profile(away, match_overlay.get("away_code"))

    venue = normalize_venue(match.get("venue") or match_overlay.get("venue"))
    environment = match_overlay.get("environment") or {}
    referee = match_overlay.get("referee")
    manager_quotes = match_overlay.get("manager_quotes") or []

    home_abs = _absence_impact_summary(home_ctx.get("absences") or [])
    away_abs = _absence_impact_summary(away_ctx.get("absences") or [])

    hs = home_ctx.get("group_stats") or {}
    aws = away_ctx.get("group_stats") or {}
    home_split = home_ctx.get("home_away") or {}
    away_split = away_ctx.get("home_away") or {}

    bullets: list[str] = []

    if match.get("match_num"):
        bullets.append(f"体彩场次：{match['match_num']}（match_id {match_id or '—'}）。")
    competition = match.get("competition") or match.get("league") or "世界杯"
    stage = match.get("stage") or "—"
    bullets.append(f"赛区/阶段：{competition} · {stage}。")
    if match.get("kickoff_beijing") or match.get("kickoff"):
        bullets.append(f"开球时间：{match.get('kickoff_beijing') or match.get('kickoff')}。")
    if match.get("_intelligence_source"):
        bullets.append(f"情报来源：{match['_intelligence_source']}。")

    if hs:
        bullets.append(
            f"{home} 小组赛 {hs.get('points', 0):.0f} 分、"
            f"进 {hs.get('goals_for', 0):.0f} 失 {hs.get('goals_against', 0):.0f}、"
            f"净胜球 {hs.get('goals_for', 0) - hs.get('goals_against', 0):+.0f}。"
        )
    if aws:
        bullets.append(
            f"{away} 小组赛 {aws.get('points', 0):.0f} 分、"
            f"进 {aws.get('goals_for', 0):.0f} 失 {aws.get('goals_against', 0):.0f}、"
            f"净胜球 {aws.get('goals_for', 0) - aws.get('goals_against', 0):+.0f}。"
        )

    if home_split:
        bullets.append(_format_split(f"{home} 主场", home_split.get("as_home", {})))
        bullets.append(_format_split(f"{home} 客场", home_split.get("as_away", {})))
    if away_split:
        bullets.append(_format_split(f"{away} 主场", away_split.get("as_home", {})))
        bullets.append(_format_split(f"{away} 客场", away_split.get("as_away", {})))

    style_note = style_matchup_note(home_profile, away_profile)
    if style_note:
        bullets.append(f"风格对阵：{style_note}")

    if home_abs["count"]:
        bullets.append(
            f"{home} 伤停/停赛 {home_abs['count']} 人，"
            f"进攻影响 {home_abs['attack_burden']:.1f}、防守影响 {home_abs['defense_burden']:.1f}。"
        )
    elif _injuries_unavailable(match):
        bullets.append(f"{home} 伤停：匿名公开 API 无结构化数据，需人工 overlay 补充。")
    if away_abs["count"]:
        bullets.append(
            f"{away} 伤停/停赛 {away_abs['count']} 人，"
            f"进攻影响 {away_abs['attack_burden']:.1f}、防守影响 {away_abs['defense_burden']:.1f}。"
        )
    elif _injuries_unavailable(match):
        bullets.append(f"{away} 伤停：暂无结构化数据，可通过 overlay 或 matches_*.json 补充。")

    if venue and venue.get("label"):
        bullets.append(f"场地：{venue['label']}。")
        if venue.get("capacity"):
            bullets.append(f"球场容量约 {venue['capacity']}。")
    if environment.get("temperature_c") is not None:
        bullets.append(f"预计气温约 {environment['temperature_c']}°C。")
    if referee:
        bullets.append(f"裁判：{referee}。")

    for quote in manager_quotes[:3]:
        bullets.append(f"教练表态：{quote}")

    rotation_risk = match_overlay.get("rotation_risk")
    if rotation_risk:
        bullets.append(f"轮换评估：{rotation_risk}")

    coverage = {
        "injury_api": not _injuries_unavailable(match),
        "overlay_used": bool(match_overlay),
        "venue_available": venue is not None,
        "referee_available": bool(referee),
        "home_away_splits": bool(home_split or away_split),
        "style_profiles": home_profile.get("confederation") != "未知",
        "recent_lineup_inference": bool(
            home_ctx.get("recent_availability") and away_ctx.get("recent_availability")
        ),
    }

    def _absence_lines(side: str, summary: dict[str, Any]) -> list[str]:
        lines: list[str] = []
        for item in summary.get("players") or []:
            status = {"out": "缺阵", "suspended": "停赛", "doubtful": "存疑"}.get(
                item.get("status", ""), item.get("status", "")
            )
            note = f"（{item['note']}）" if item.get("note") else ""
            lines.append(f"{item.get('player', '未知')} · {status}{note}")
        if not lines and _injuries_unavailable(match):
            lines.append("公开 API 无结构化伤停，请维护 overlay")
        return lines

    detail_sections = {
        "teams": [
            {
                "side": "home",
                "name": home,
                "profile": home_profile,
                "group_stats": hs,
                "home_away": home_split,
            },
            {
                "side": "away",
                "name": away,
                "profile": away_profile,
                "group_stats": aws,
                "home_away": away_split,
            },
        ],
        "absences": {
            "home": _absence_lines(home, home_abs),
            "away": _absence_lines(away, away_abs),
        },
        "venue": venue,
        "environment": environment or None,
        "referee": referee,
        "style_note": style_note,
        "quotes": manager_quotes,
        "rotation_risk": rotation_risk,
        "recent_lineups": {
            "home": home_ctx.get("recent_availability"),
            "away": away_ctx.get("recent_availability"),
        },
    }

    return {
        "home": home,
        "away": away,
        "match_id": match_id or None,
        "home_profile": home_profile,
        "away_profile": away_profile,
        "venue": venue,
        "environment": environment or None,
        "referee": referee,
        "home_absences": home_abs,
        "away_absences": away_abs,
        "home_predicted_lineup": home_ctx.get("predicted_lineup"),
        "away_predicted_lineup": away_ctx.get("predicted_lineup"),
        "home_lineup_detail": home_ctx.get("lineup_detail"),
        "away_lineup_detail": away_ctx.get("lineup_detail"),
        "recent_lineup_method": match.get("recent_lineup_method"),
        "home_tactics": home_ctx.get("tactics"),
        "away_tactics": away_ctx.get("tactics"),
        "manager_quotes": manager_quotes,
        "rotation_risk": rotation_risk,
        "style_matchup_note": style_note,
        "summary_bullets": bullets,
        "detail_sections": detail_sections,
        "coverage": coverage,
        "disclaimer": "情报来自 FIFA 官方统计与可编辑 overlay；不构成赛果保证。",
    }


def apply_overlay_to_match(match: dict[str, Any], overlay: dict[str, Any] | None = None) -> dict[str, Any]:
    """合并 overlay 到比赛对象，供 analyzer 重新计算上下文。"""
    overlay = overlay if overlay is not None else load_intelligence_overlay()
    enriched = dict(match)
    if enriched.get("venue"):
        normalized = normalize_venue(enriched["venue"])
        if normalized:
            enriched["venue"] = normalized
        else:
            enriched.pop("venue", None)
    team_context = dict(enriched.get("team_context") or {})
    match_id = str(enriched.get("provider_ids", {}).get("sporttery_match") or enriched.get("match_id") or "")
    match_overlay = _overlay_for_match(
        overlay,
        match_id=match_id or None,
        home=enriched.get("home"),
        away=enriched.get("away"),
    )
    team_context["home"] = merge_overlay_into_context(team_context.get("home") or {}, match_overlay.get("home"))
    team_context["away"] = merge_overlay_into_context(team_context.get("away") or {}, match_overlay.get("away"))
    enriched["team_context"] = team_context
    if match_overlay.get("venue"):
        enriched["venue"] = normalize_venue(match_overlay["venue"])
    if match_overlay.get("referee"):
        enriched["referee"] = match_overlay["referee"]
    return enriched


def build_intelligence_for_sporttery(
    sporttery_match: dict[str, Any],
    unified_match: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """体彩场次情报：优先三源 FIFA，否则本地 JSON + overlay + 风格模板。"""
    if unified_match:
        report = build_intelligence_report(apply_overlay_to_match(unified_match))
        sources = ["FIFA三源"]
        if report.get("coverage", {}).get("overlay_used"):
            sources.append("overlay")
        report["data_sources"] = sources
        report["unified_linked"] = True
        return report

    home = sporttery_match.get("home", "")
    away = sporttery_match.get("away", "")
    local = load_local_match_bundle(home, away)
    base: dict[str, Any] = {
        "home": home,
        "away": away,
        "match_id": sporttery_match.get("match_id"),
        "match_num": sporttery_match.get("match_num"),
        "league": sporttery_match.get("league"),
        "competition": sporttery_match.get("league") or "世界杯",
        "kickoff_beijing": sporttery_match.get("kickoff_beijing"),
        "data_provenance": {"injuries": "not-available-from-verified-anonymous-public-api"},
        "unified_linked": False,
    }
    if local:
        base["stage"] = local.get("stage") or "淘汰赛"
        base["team_context"] = local.get("team_context")
        base["tournament_rules"] = local.get("tournament_rules")
        if local.get("odds"):
            base["odds"] = local["odds"]
    else:
        base["stage"] = "竞彩在售（FIFA三源未匹配）"

    recent_lineups: dict[str, Any] | None = None
    home_identity = resolve_team(home)
    away_identity = resolve_team(away)
    mapped_national_teams = (
        home_identity.get("en") != home and away_identity.get("en") != away
    )
    if (
        (sporttery_match.get("league") == "世界杯" or mapped_national_teams)
        and sporttery_match.get("kickoff_beijing")
    ):
        try:
            recent_lineups = infer_recent_lineups(
                home=home,
                away=away,
                kickoff_beijing=sporttery_match["kickoff_beijing"],
                home_aliases=(
                    str(sporttery_match.get("home_en") or ""),
                    home_identity.get("en", ""),
                    home_identity.get("abbr", ""),
                ),
                away_aliases=(
                    str(sporttery_match.get("away_en") or ""),
                    away_identity.get("en", ""),
                    away_identity.get("abbr", ""),
                ),
            )
        except (RecentLineupError, ValueError):
            recent_lineups = None
    if recent_lineups and recent_lineups.get("available"):
        team_context = dict(base.get("team_context") or {})
        for side in ("home", "away"):
            side_context = dict(team_context.get(side) or {})
            side_context.update(recent_lineups[side] or {})
            team_context[side] = side_context
        base["team_context"] = team_context
        base["recent_lineup_method"] = recent_lineups.get("method")

    report = build_intelligence_report(apply_overlay_to_match(base))
    sources = ["体彩"]
    if local:
        sources.append(f"本地({local.get('_source_file', 'matches')})")
    if report.get("coverage", {}).get("overlay_used"):
        sources.append("overlay")
    if report.get("coverage", {}).get("recent_lineup_inference"):
        sources.append("ESPN近期确认首发")
        report["summary_bullets"].append(
            "预计首发沿用两队最近一场正式比赛的确认首发；"
            "作为近期可用性基线，不等同于官方伤停确认。"
        )
    report["data_sources"] = sources
    report["summary_bullets"].append(
        "FIFA 三源索引仅覆盖「赛程日当天且 Polymarket 可匹配」的场次；本场当前为体彩+本地/overlay 情报。"
    )
    return report
