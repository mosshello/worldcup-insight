"""FOX Sports 世界杯赔率页面爬虫（备用数据源，无需 API Key）。"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from typing import Any

from .team_names import load_team_map, resolve_team

FOX_ODDS_URL = "https://www.foxsports.com/stories/soccer/2026-world-cup-round-32-odds"
USER_AGENT = "worldcup-insight/1.0 (+https://github.com/local/worldcup-insight)"


def american_to_decimal(american: int) -> float:
    if american > 0:
        return american / 100 + 1
    return 100 / abs(american) + 1


def _fetch_page(url: str = FOX_ODDS_URL) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法访问 FOX Sports 页面：{exc.reason}") from exc


def _abbr_to_side(abbr: str, home_abbr: str, away_abbr: str) -> str | None:
    if abbr == home_abbr:
        return "home"
    if abbr == away_abbr:
        return "away"
    return None


def parse_moneyline_blocks(page: str) -> list[dict[str, Any]]:
    """
    解析页面中的对阵与 Moneyline 行。

    示例::
        Brazil vs. Japan
        Moneyline: BRA -145, Draw +280, JPN +420
    """
    team_map = load_team_map()
    abbr_to_cn: dict[str, tuple[str, str]] = {}
    for cn_name, aliases in team_map.items():
        abbr_to_cn[aliases["abbr"].upper()] = (cn_name, aliases["en"])

    pattern = re.compile(
        r"(?P<home_en>[A-Za-z .]+?)\s+vs\.?\s+(?P<away_en>[A-Za-z .]+?)\s+"
        r"To Advance:.*?Moneyline:\s*"
        r"(?P<home_abbr>[A-Z]{2,4})\s+(?P<home_odds>[+-]\d+),\s*"
        r"Draw\s+(?P<draw_odds>[+-]\d+),\s*"
        r"(?P<away_abbr>[A-Z]{2,4})\s+(?P<away_odds>[+-]\d+)",
        re.DOTALL,
    )

    matches: list[dict[str, Any]] = []
    for block in pattern.finditer(page):
        home_abbr = block.group("home_abbr").upper()
        away_abbr = block.group("away_abbr").upper()
        home_cn = abbr_to_cn.get(home_abbr, (block.group("home_en").strip(), block.group("home_en").strip()))[0]
        away_cn = abbr_to_cn.get(away_abbr, (block.group("away_en").strip(), block.group("away_en").strip()))[0]

        matches.append(
            {
                "home": home_cn,
                "away": away_cn,
                "home_en": block.group("home_en").strip(),
                "away_en": block.group("away_en").strip(),
                "european": {
                    "home": american_to_decimal(int(block.group("home_odds"))),
                    "draw": american_to_decimal(int(block.group("draw_odds"))),
                    "away": american_to_decimal(int(block.group("away_odds"))),
                },
            }
        )
    return matches


def fetch_fox_snapshot(home: str, away: str, *, url: str = FOX_ODDS_URL) -> dict[str, Any]:
    page = _fetch_page(url)
    matches = parse_moneyline_blocks(page)
    if not matches:
        raise RuntimeError("FOX Sports 页面结构可能已变更，未能解析到 Moneyline 数据")

    for match in matches:
        if resolve_team(match["home"])["en"].casefold() == resolve_team(home)["en"].casefold() and resolve_team(
            match["away"]
        )["en"].casefold() == resolve_team(away)["en"].casefold():
            return {
                "source": "fox-sports/fanduel",
                "european": match["european"],
            }

    raise RuntimeError(f"FOX Sports 页面未找到 {home} vs {away} 的 Moneyline")
