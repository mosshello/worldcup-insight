"""无需密钥的世界杯公开数据适配器。"""

from __future__ import annotations

from typing import Any

from .http_client import DataSourceError, HttpJsonClient


class FifaPublicProvider:
    """FIFA官方匿名接口：赛程、球队、比分和比赛状态。"""

    def __init__(self, client: HttpJsonClient, *, competition_id: int = 17) -> None:
        self.client = client
        self.competition_id = competition_id

    def fixtures(self, from_time: str, to_time: str) -> list[dict[str, Any]]:
        payload = self.client.get_json(
            "calendar/matches",
            query={
                "idCompetition": self.competition_id,
                "from": from_time,
                "to": to_time,
                "count": 200,
            },
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("Results"), list):
            raise DataSourceError("FIFA官方接口响应缺少 Results 数组")
        if payload.get("ContinuationToken"):
            raise DataSourceError("FIFA官方接口返回仍有续页，已停止以防遗漏比赛")
        return [item for item in payload["Results"] if isinstance(item, dict)]

    def doctor(self) -> dict[str, Any]:
        sample = self.fixtures("2026-06-11T00:00:00Z", "2026-06-12T00:00:00Z")
        if not sample:
            return {"provider": "fifa-public", "ok": False}
        events = self.timeline(str(sample[0].get("IdMatch") or ""))
        return {"provider": "fifa-public", "ok": bool(events)}

    def timeline(self, match_id: str) -> list[dict[str, Any]]:
        if not match_id:
            raise DataSourceError("FIFA时间线缺少比赛ID")
        payload = self.client.get_json(f"timelines/{match_id}")
        if not isinstance(payload, dict) or not isinstance(payload.get("Event"), list):
            raise DataSourceError(f"FIFA时间线响应无效：{match_id}")
        return [item for item in payload["Event"] if isinstance(item, dict)]


class PolymarketPublicProvider:
    """Polymarket Gamma公开接口：三个互斥的90分钟赛果市场。"""

    def __init__(self, client: HttpJsonClient) -> None:
        self.client = client

    def event_by_slug(self, slug: str) -> dict[str, Any]:
        payload = self.client.get_json("events", query={"slug": slug})
        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
            raise DataSourceError(f"Polymarket事件无法唯一定位：{slug}")
        return payload[0]

    def doctor(self) -> dict[str, Any]:
        payload = self.client.get_json("sports")
        return {"provider": "polymarket-public", "ok": isinstance(payload, list) and bool(payload)}


class SportteryPublicProvider:
    """中国体彩网竞彩足球公开接口：当前HAD与HHAD官方SP。"""

    REQUEST_HEADERS = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Origin": "https://www.sporttery.cn",
        "Referer": "https://www.sporttery.cn/jc/zqszsc/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/137.0 Safari/537.36"
        ),
        "X-Requested-With": "XMLHttpRequest",
    }

    def __init__(self, client: HttpJsonClient) -> None:
        self.client = client

    def matches(self) -> list[dict[str, Any]]:
        payload = self.client.get_json(
            "gateway/uniform/football/getMatchCalculatorV1.qry",
            query={"channel": "c", "poolCode": "hhad,had"},
            headers=self.REQUEST_HEADERS,
        )
        if not isinstance(payload, dict) or str(payload.get("errorCode")) != "0":
            raise DataSourceError("中国体彩网接口返回失败")
        value = payload.get("value")
        groups = value.get("matchInfoList") if isinstance(value, dict) else None
        if not isinstance(groups, list):
            raise DataSourceError("中国体彩网接口缺少 matchInfoList")
        matches: list[dict[str, Any]] = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("subMatchList"), list):
                continue
            matches.extend(item for item in group["subMatchList"] if isinstance(item, dict))
        return matches

    def doctor(self) -> dict[str, Any]:
        matches = self.matches()
        return {"provider": "sporttery-public", "ok": bool(matches)}
