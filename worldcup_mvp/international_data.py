"""公开男子国家队赛果的下载、校验与严格时间切分。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import subprocess
import time
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRAINING_DIR = PROJECT_ROOT / "data" / "training"
DATASET_FILE = TRAINING_DIR / "international_results.csv"
DATASET_META_FILE = TRAINING_DIR / "international_results.meta.json"
REGULATION_FILE = TRAINING_DIR / "world_cup_regulation_results.json"
SOURCE_URL = "https://codeload.github.com/martj42/international_results/zip/refs/heads/master"
SOURCE_PAGE = "https://github.com/martj42/international_results"
RECENT_START = date(2024, 6, 11)
FOUNDATION_END = date(2026, 6, 10)
WORLD_CUP_2022_START = date(2022, 11, 20)
WORLD_CUP_2022_END = date(2022, 12, 18)
WORLD_CUP_2026_START = date(2026, 6, 11)
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
ESPN_TEAM_ALIASES = {
    "IR Iran": "Iran",
    "Korea Republic": "South Korea",
    "USA": "United States",
    "Czechia": "Czech Republic",
    "Türkiye": "Turkey",
    "Congo DR": "DR Congo",
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
}
HOST_COUNTRIES = {"United States": "United States", "Canada": "Canada", "Mexico": "Mexico"}


@dataclass(frozen=True)
class InternationalMatch:
    match_date: date
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    tournament: str
    neutral: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["match_date"] = self.match_date.isoformat()
        return payload


def _download_zip(*, attempts: int = 3) -> bytes:
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            SOURCE_URL,
            headers={"User-Agent": "worldcup-console-mvp/1.0", "Accept": "application/zip"},
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return response.read()
        except Exception as exc:  # 网络错误需保留最后一次异常上下文
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    try:
        completed = subprocess.run(
            ["curl", "-L", "-sS", "--fail", "--max-time", "60", SOURCE_URL],
            check=True, capture_output=True,
        )
        if completed.stdout:
            return completed.stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        last_error = exc
    raise RuntimeError(f"国家队历史数据下载失败：{last_error}")


def _extract_results_csv(archive: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        candidates = [name for name in bundle.namelist() if name.endswith("/results.csv")]
        if len(candidates) != 1:
            raise RuntimeError("数据压缩包缺少唯一 results.csv")
        return bundle.read(candidates[0])


def refresh_international_dataset() -> dict[str, Any]:
    archive = _download_zip()
    csv_bytes = _extract_results_csv(archive)
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
    required = {"date", "home_team", "away_team", "home_score", "away_score", "tournament", "neutral"}
    if not rows or not required.issubset(rows[0]):
        raise RuntimeError("国家队数据字段不完整")

    TRAINING_DIR.mkdir(parents=True, exist_ok=True)
    DATASET_FILE.write_bytes(csv_bytes)
    completed = sum(1 for row in rows if row.get("home_score", "").isdigit() and row.get("away_score", "").isdigit())
    digest = hashlib.sha256(csv_bytes).hexdigest()
    meta = {
        "source_url": SOURCE_URL,
        "source_page": SOURCE_PAGE,
        "downloaded_at": datetime.now(BEIJING_TZ).replace(microsecond=0).isoformat(),
        "sha256": digest,
        "row_count": len(rows),
        "completed_count": completed,
        "license": "CC0-1.0",
    }
    DATASET_META_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta


def _fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "worldcup-console-mvp/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as primary_error:
        try:
            completed = subprocess.run(
                ["curl", "-L", "-sS", "--fail", "--max-time", "60", url],
                check=True, capture_output=True,
            )
            return json.loads(completed.stdout.decode("utf-8"))
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as fallback_error:
            raise RuntimeError(f"世界杯90分钟比分下载失败：{primary_error}; curl={fallback_error}") from fallback_error


def refresh_world_cup_regulation_results() -> dict[str, Any]:
    """用 ESPN 分节比分把加时/点球赛果还原为90分钟比分。"""
    results: list[dict[str, Any]] = []
    for period in ("20221120-20221218", "20260611-20260720"):
        payload = _fetch_json(f"{ESPN_SCOREBOARD_URL}?dates={period}&limit=200")
        for event in payload.get("events") or []:
            competition = (event.get("competitions") or [{}])[0]
            status_type = (event.get("status") or {}).get("type", {})
            if not status_type.get("completed"):
                continue
            competitors = competition.get("competitors") or []
            by_side = {item.get("homeAway"): item for item in competitors}
            if "home" not in by_side or "away" not in by_side:
                continue
            home = by_side["home"]
            away = by_side["away"]
            status_name = str(status_type.get("name") or "")
            if "AET" in status_name or "PEN" in status_name:
                summary = _fetch_json(
                    f"https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary?event={event.get('id')}"
                )
                summary_competitors = (((summary.get("header") or {}).get("competitions") or [{}])[0]).get("competitors") or []
                summary_by_side = {item.get("homeAway"): item for item in summary_competitors}
                home_periods = (summary_by_side.get("home") or {}).get("linescores") or []
                away_periods = (summary_by_side.get("away") or {}).get("linescores") or []
                if len(home_periods) < 2 or len(away_periods) < 2:
                    continue
                try:
                    home_90 = sum(int(float(item["displayValue"])) for item in home_periods[:2])
                    away_90 = sum(int(float(item["displayValue"])) for item in away_periods[:2])
                except (KeyError, TypeError, ValueError):
                    continue
            else:
                try:
                    home_90 = int(float(home["score"]))
                    away_90 = int(float(away["score"]))
                except (KeyError, TypeError, ValueError):
                    continue
            home_name = ESPN_TEAM_ALIASES.get(home["team"]["displayName"], home["team"]["displayName"])
            away_name = ESPN_TEAM_ALIASES.get(away["team"]["displayName"], away["team"]["displayName"])
            venue_country = str((((competition.get("venue") or {}).get("address") or {}).get("country") or ""))
            neutral = not (
                home_name in HOST_COUNTRIES
                and HOST_COUNTRIES[home_name].lower() in venue_country.lower()
            )
            results.append(
                {
                    "event_id": event.get("id"), "date": str(event.get("date") or "")[:10],
                    "home_team": home_name, "away_team": away_name,
                    "home_goals_90": home_90, "away_goals_90": away_90,
                    "neutral": neutral,
                    "final_detail": status_type.get("detail"),
                }
            )
    payload = {
        "source": ESPN_SCOREBOARD_URL,
        "updated_at": datetime.now(BEIJING_TZ).replace(microsecond=0).isoformat(),
        "count": len(results),
        "matches": results,
    }
    TRAINING_DIR.mkdir(parents=True, exist_ok=True)
    REGULATION_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _apply_regulation_overrides(matches: list[InternationalMatch]) -> tuple[list[InternationalMatch], int, int]:
    if not REGULATION_FILE.exists():
        return matches, 0, 0
    payload = json.loads(REGULATION_FILE.read_text(encoding="utf-8"))
    overrides = {
        (item["date"], frozenset((item["home_team"], item["away_team"]))): item
        for item in payload.get("matches") or []
    }
    output: list[InternationalMatch] = []
    changed = 0
    matched_override_keys: set[tuple[str, frozenset[str]]] = set()
    for match in matches:
        if match.tournament != "FIFA World Cup":
            output.append(match)
            continue
        candidates = [match.match_date.isoformat(), (match.match_date - date.resolution).isoformat(), (match.match_date + date.resolution).isoformat()]
        override = next((overrides.get((day, frozenset((match.home_team, match.away_team)))) for day in candidates if overrides.get((day, frozenset((match.home_team, match.away_team))))), None)
        if override is None:
            output.append(match)
            continue
        matched_override_keys.add((override["date"], frozenset((override["home_team"], override["away_team"]))))
        home_goals = override["home_goals_90"] if match.home_team == override["home_team"] else override["away_goals_90"]
        away_goals = override["away_goals_90"] if match.away_team == override["away_team"] else override["home_goals_90"]
        changed += (home_goals, away_goals) != (match.home_goals, match.away_goals)
        output.append(InternationalMatch(match.match_date, match.home_team, match.away_team, home_goals, away_goals, match.tournament, match.neutral))
    appended = 0
    for key, item in overrides.items():
        if key in matched_override_keys:
            continue
        try:
            match_date = date.fromisoformat(item["date"])
        except (KeyError, ValueError):
            continue
        output.append(
            InternationalMatch(
                match_date, item["home_team"], item["away_team"],
                int(item["home_goals_90"]), int(item["away_goals_90"]),
                "FIFA World Cup", bool(item.get("neutral", True)),
            )
        )
        appended += 1
    return output, changed, appended


def _parse_match(row: dict[str, str]) -> InternationalMatch | None:
    home_score = (row.get("home_score") or "").strip()
    away_score = (row.get("away_score") or "").strip()
    if not home_score.isdigit() or not away_score.isdigit():
        return None
    try:
        match_date = date.fromisoformat((row.get("date") or "").strip())
    except ValueError:
        return None
    home = (row.get("home_team") or "").strip()
    away = (row.get("away_team") or "").strip()
    tournament = (row.get("tournament") or "").strip()
    if not home or not away or not tournament or home == away:
        return None
    return InternationalMatch(
        match_date=match_date,
        home_team=home,
        away_team=away,
        home_goals=int(home_score),
        away_goals=int(away_score),
        tournament=tournament,
        neutral=(row.get("neutral") or "").strip().upper() == "TRUE",
    )


def load_international_matches(*, refresh: bool = False) -> tuple[list[InternationalMatch], dict[str, Any]]:
    if refresh or not DATASET_FILE.exists():
        refresh_international_dataset()
    if refresh or not REGULATION_FILE.exists():
        refresh_world_cup_regulation_results()
    matches: list[InternationalMatch] = []
    with DATASET_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            parsed = _parse_match(row)
            if parsed is not None:
                matches.append(parsed)
    matches, override_count, appended_count = _apply_regulation_overrides(matches)
    matches.sort(key=lambda item: (item.match_date, item.home_team, item.away_team))
    meta = json.loads(DATASET_META_FILE.read_text(encoding="utf-8")) if DATASET_META_FILE.exists() else {}
    meta["regulation_score_overrides"] = override_count
    meta["regulation_results_appended"] = appended_count
    meta["regulation_source"] = ESPN_SCOREBOARD_URL
    return matches, meta


def split_training_data(matches: list[InternationalMatch]) -> dict[str, list[InternationalMatch]]:
    foundation = [
        match
        for match in matches
        if (
            match.tournament == "FIFA World Cup"
            and WORLD_CUP_2022_START <= match.match_date <= WORLD_CUP_2022_END
        )
        or RECENT_START <= match.match_date <= FOUNDATION_END
    ]
    world_cup_2026 = [
        match
        for match in matches
        if match.tournament == "FIFA World Cup" and match.match_date >= WORLD_CUP_2026_START
    ]
    return {"foundation": foundation, "world_cup_2026_test": world_cup_2026}
