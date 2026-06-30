"""盘口数据采集：官方 API、FOX 爬虫或本地 feed 文件。"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .env_config import get_odds_api_key
from .fox_scraper import fetch_fox_snapshot
from .odds_snapshot import validate_snapshot
from .sporttery_api import SportteryApiError, find_match, match_to_snapshot
from .the_odds_api import find_snapshot


class OddsCollector(ABC):
    """盘口采集器接口。"""

    @abstractmethod
    def fetch(self, match_id: str) -> dict[str, Any] | None:
        """返回一条待写入的快照；无新数据时返回 None。"""


class FileFeedCollector(OddsCollector):
    """从本地 JSON feed 读取最新盘口。"""

    def __init__(self, feed_path: str | Path) -> None:
        self.feed_path = Path(feed_path)

    def fetch(self, match_id: str) -> dict[str, Any] | None:
        if not self.feed_path.exists():
            return None

        with self.feed_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if payload.get("match_id") and payload["match_id"] != match_id:
            return None

        snapshot = {key: payload[key] for key in ("european", "asian_handicap", "source") if key in payload}
        if not snapshot:
            return None
        return validate_snapshot(snapshot)


class TheOddsApiCollector(OddsCollector):
    """
    The Odds API 官方采集器。

    需在 .env 或环境变量中设置 ODDS_API_KEY。
    免费额度约 500 次/月，注册：https://the-odds-api.com
    """

    def __init__(
        self,
        *,
        home: str,
        away: str,
        sport: str = "soccer_fifa_world_cup",
        event_id: str | None = None,
        regions: str = "uk,eu,us",
        api_key: str | None = None,
    ) -> None:
        self.home = home
        self.away = away
        self.sport = sport
        self.event_id = event_id
        self.regions = regions
        self.api_key = api_key or get_odds_api_key()
        self.last_meta: dict[str, Any] | None = None
        self.last_usage: dict[str, str | None] | None = None

        if not self.api_key:
            raise ValueError("缺少 ODDS_API_KEY，请在 .env 中配置或访问 https://the-odds-api.com 申请")

    def fetch(self, match_id: str) -> dict[str, Any] | None:
        snapshot, meta, usage = find_snapshot(
            self.api_key,
            sport=self.sport,
            home=self.home,
            away=self.away,
            event_id=self.event_id,
            regions=self.regions,
        )
        self.last_meta = meta
        self.last_usage = usage
        return validate_snapshot(snapshot)


class SportteryCollector(OddsCollector):
    """中国体育彩票竞彩足球官方 API（主盘）。"""

    def __init__(
        self,
        *,
        home: str,
        away: str,
        match_id: str | None = None,
    ) -> None:
        self.home = home
        self.away = away
        self.match_id = match_id
        self.last_match: dict[str, Any] | None = None

    def fetch(self, match_id: str) -> dict[str, Any] | None:
        match = find_match(
            home=self.home,
            away=self.away,
            match_id=self.match_id or match_id,
        )
        self.last_match = match
        return validate_snapshot(match_to_snapshot(match))


class FoxScraperCollector(OddsCollector):
    """FOX Sports 页面爬虫（仅欧赔 Moneyline，无需 API Key）。"""

    def __init__(self, *, home: str, away: str, url: str | None = None) -> None:
        self.home = home
        self.away = away
        self.url = url

    def fetch(self, match_id: str) -> dict[str, Any] | None:
        snapshot = fetch_fox_snapshot(self.home, self.away, url=self.url) if self.url else fetch_fox_snapshot(
            self.home, self.away
        )
        return validate_snapshot(snapshot)


class StaticCollector(OddsCollector):
    """用于测试或手动注入单条快照。"""

    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = validate_snapshot(snapshot)

    def fetch(self, match_id: str) -> dict[str, Any] | None:
        return self.snapshot


def create_collector(
    source: str,
    *,
    home: str,
    away: str,
    feed_path: str | Path | None = None,
    sport: str = "soccer_fifa_world_cup",
    event_id: str | None = None,
    regions: str = "uk,eu,us",
    api_key: str | None = None,
    fox_url: str | None = None,
    sporttery_match_id: str | None = None,
) -> OddsCollector:
    if source == "file":
        if feed_path is None:
            raise ValueError("file 数据源需要 feed_path")
        return FileFeedCollector(feed_path)
    if source == "sporttery":
        return SportteryCollector(
            home=home,
            away=away,
            match_id=sporttery_match_id or event_id,
        )
    if source == "api":
        return TheOddsApiCollector(
            home=home,
            away=away,
            sport=sport,
            event_id=event_id,
            regions=regions,
            api_key=api_key,
        )
    if source == "fox":
        return FoxScraperCollector(home=home, away=away, url=fox_url)
    raise ValueError(f"未知数据源：{source}，可选 file / sporttery / api / fox")
