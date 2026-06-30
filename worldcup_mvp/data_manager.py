"""世界杯公开数据的统一获取、交叉校验、规范化与溯源入口。"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from .http_client import DataSourceError, HttpJsonClient
from .match_intelligence import compute_home_away_splits, extract_venue_from_fifa
from .providers import FifaPublicProvider, PolymarketPublicProvider, SportteryPublicProvider


class ConfigurationError(ValueError):
    """公开数据配置无效。"""


FIFA_RULES = {
    "format": "knockout",
    "prediction_scope": "90_minutes",
    "extra_time_if_draw": True,
    "penalties_if_still_draw": True,
    "version": "2026-06-29",
    "source": "https://www.fifa.com/es/tournaments/mens/worldcup/canadamexicousa2026/articles/en-caso-de-empate-en-la-copa-mundial-de-la-fifa-como-se-definen-los-partidos-de-eliminatorias",
}

POLYMARKET_CODE_ALIASES = {"NED": "NLD"}
SPORTTERY_CODE_ALIASES = {"BRZ": "BRA", "PGY": "PAR", "NET": "NED", "MCO": "MAR", "NOW": "NOR"}


@dataclass(frozen=True)
class DataConfig:
    competition_id: int = 17
    tournament_start: str = "2026-06-11"
    schedule_timezone: str = "America/New_York"
    output_timezone: str = "Asia/Shanghai"
    minimum_market_liquidity: float = 10_000.0
    probability_sum_min: float = 0.90
    probability_sum_max: float = 1.10
    kickoff_tolerance_minutes: int = 15

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DataConfig":
        values = env if env is not None else os.environ
        try:
            config = cls(
                competition_id=int(values.get("FIFA_COMPETITION_ID", "17")),
                tournament_start=values.get("WORLD_CUP_TOURNAMENT_START", "2026-06-11"),
                schedule_timezone=values.get("WORLD_CUP_SCHEDULE_TIMEZONE", "America/New_York"),
                output_timezone=values.get("WORLD_CUP_OUTPUT_TIMEZONE", "Asia/Shanghai"),
                minimum_market_liquidity=float(
                    values.get("POLYMARKET_MIN_LIQUIDITY", "10000")
                ),
                probability_sum_min=float(values.get("MARKET_PROBABILITY_SUM_MIN", "0.90")),
                probability_sum_max=float(values.get("MARKET_PROBABILITY_SUM_MAX", "1.10")),
                kickoff_tolerance_minutes=int(values.get("KICKOFF_TOLERANCE_MINUTES", "15")),
            )
            date.fromisoformat(config.tournament_start)
            ZoneInfo(config.schedule_timezone)
            ZoneInfo(config.output_timezone)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError("公开数据环境变量格式无效") from exc
        if (
            config.competition_id < 1
            or config.minimum_market_liquidity < 0
            or config.kickoff_tolerance_minutes < 1
            or not 0 < config.probability_sum_min < config.probability_sum_max
        ):
            raise ConfigurationError("公开数据质量阈值无效")
        return config


def _aware_datetime(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise DataSourceError(f"{field_name} 缺少ISO时间")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataSourceError(f"{field_name} 时间格式无效") from exc
    if parsed.tzinfo is None:
        raise DataSourceError(f"{field_name} 必须包含时区")
    return parsed


def _name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text.casefold() if char.isalnum())


def _fifa_team_name(team: dict[str, Any]) -> str:
    short = team.get("ShortClubName")
    if short:
        return str(short)
    names = team.get("TeamName")
    if isinstance(names, list) and names and isinstance(names[0], dict):
        return str(names[0].get("Description") or "")
    return ""


def _parse_score(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DataSourceError(f"{field_name} 比分无效")
    return value


class UnifiedDataManager:
    """统一公开数据中心；必要数据不完整时拒绝产生分析。"""

    def __init__(
        self,
        fifa: Any,
        market: Any,
        sporttery: Any,
        config: DataConfig,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.fifa = fifa
        self.market = market
        self.sporttery = sporttery
        self.config = config
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "UnifiedDataManager":
        config = DataConfig.from_env(env)
        return cls(
            FifaPublicProvider(
                HttpJsonClient("https://api.fifa.com/api/v3", provider_name="FIFA官方接口"),
                competition_id=config.competition_id,
            ),
            PolymarketPublicProvider(
                HttpJsonClient(
                    "https://gamma-api.polymarket.com", provider_name="Polymarket公开接口"
                )
            ),
            SportteryPublicProvider(
                HttpJsonClient(
                    "https://webapi.sporttery.cn", provider_name="中国体彩网公开接口"
                )
            ),
            config,
        )

    def doctor(self) -> dict[str, Any]:
        return {
            "configuration": "ok-no-api-key-required",
            "providers": [
                self.fifa.doctor(),
                self.market.doctor(),
                self.sporttery.doctor(),
            ],
        }

    def _date_window(self, fixture_date: str) -> tuple[datetime, datetime]:
        try:
            parsed = date.fromisoformat(fixture_date)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError("赛事日期必须是 YYYY-MM-DD") from exc
        zone = ZoneInfo(self.config.schedule_timezone)
        start = datetime.combine(parsed, time.min, tzinfo=zone)
        end = datetime.combine(parsed.fromordinal(parsed.toordinal() + 1), time.min, tzinfo=zone)
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

    @staticmethod
    def _iso_utc(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _fixture_teams(item: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        home, away = item.get("Home"), item.get("Away")
        if not isinstance(home, dict) or not isinstance(away, dict):
            raise DataSourceError("FIFA赛程缺少主客队")
        for side, team in (("主队", home), ("客队", away)):
            if not team.get("IdTeam") or not _fifa_team_name(team) or not team.get("Abbreviation"):
                raise DataSourceError(f"FIFA赛程{side}标识不完整")
        return home, away

    @staticmethod
    def _group_stats(history: list[dict[str, Any]], team_id: str, team_name: str) -> dict[str, Any]:
        played = points = goals_for = goals_against = 0
        for match in history:
            if not match.get("IdGroup") or match.get("MatchStatus") != 0:
                continue
            home, away = match.get("Home", {}), match.get("Away", {})
            if str(home.get("IdTeam")) == team_id:
                scored = _parse_score(match.get("HomeTeamScore"), "FIFA主队")
                conceded = _parse_score(match.get("AwayTeamScore"), "FIFA客队")
            elif str(away.get("IdTeam")) == team_id:
                scored = _parse_score(match.get("AwayTeamScore"), "FIFA客队")
                conceded = _parse_score(match.get("HomeTeamScore"), "FIFA主队")
            else:
                continue
            played += 1
            goals_for += scored
            goals_against += conceded
            points += 3 if scored > conceded else 1 if scored == conceded else 0
        if played == 0:
            raise DataSourceError(f"FIFA官方接口缺少{team_name}已结束小组赛")
        return {
            "played": played,
            "points": points,
            "goals_for": goals_for,
            "goals_against": goals_against,
            "finish": None,
        }

    def _scorers(
        self,
        history: list[dict[str, Any]],
        team_id: str,
        official_stats: dict[str, Any],
        team_name: str,
        timeline_cache: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        counter: Counter[tuple[str, str | None]] = Counter()
        for match in history:
            if not match.get("IdGroup") or match.get("MatchStatus") != 0:
                continue
            home_id = str(match.get("Home", {}).get("IdTeam") or "")
            away_id = str(match.get("Away", {}).get("IdTeam") or "")
            if team_id not in {home_id, away_id}:
                continue
            match_id = str(match.get("IdMatch") or "")
            if match_id not in timeline_cache:
                timeline_cache[match_id] = self.fifa.timeline(match_id)
            for event in timeline_cache[match_id]:
                type_names = {
                    str(item.get("Description") or "").casefold()
                    for item in event.get("TypeLocalized", [])
                    if isinstance(item, dict)
                }
                is_goal = "goal!" in type_names or "penalty goal" in type_names
                is_own_goal = "own goal" in type_names
                if not is_goal and not is_own_goal:
                    continue
                event_team = str(event.get("IdTeam") or "")
                if is_own_goal:
                    beneficiary = away_id if event_team == home_id else home_id if event_team == away_id else ""
                else:
                    beneficiary = event_team
                if beneficiary != team_id:
                    continue
                descriptions = [
                    str(item.get("Description") or "")
                    for item in event.get("EventDescription", [])
                    if isinstance(item, dict) and item.get("Description")
                ]
                player_match = re.match(r"^(.+?)\s+\(", descriptions[0]) if descriptions else None
                if not player_match:
                    raise DataSourceError(f"FIFA时间线缺少{team_name}进球球员名称")
                player_name = player_match.group(1).strip()
                if is_own_goal:
                    player_name += "（乌龙）"
                counter[(player_name, str(event.get("IdPlayer")) if event.get("IdPlayer") else None)] += 1

        if sum(counter.values()) != official_stats["goals_for"]:
            raise DataSourceError(
                f"FIFA时间线的{team_name}球员进球数与官方球队总进球不一致"
            )
        return [
            {"player": key[0], "goals": goals, "provider_player_id": key[1]}
            for key, goals in sorted(counter.items(), key=lambda item: (-item[1], item[0][0]))
        ][:5]

    @staticmethod
    def _yes_price(market: dict[str, Any]) -> float:
        outcomes, prices = market.get("outcomes"), market.get("outcomePrices")
        try:
            outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
            prices = json.loads(prices) if isinstance(prices, str) else prices
            yes_index = [str(value).casefold() for value in outcomes].index("yes")
            price = float(prices[yes_index])
        except (TypeError, ValueError, IndexError, json.JSONDecodeError) as exc:
            raise DataSourceError("Polymarket市场缺少有效Yes价格") from exc
        if not 0 < price < 1:
            raise DataSourceError("Polymarket Yes价格超出(0,1)范围")
        return price

    def _market_odds(
        self,
        event: dict[str, Any],
        home_name: str,
        away_name: str,
        kickoff: datetime,
    ) -> tuple[dict[str, float], dict[str, Any]]:
        if event.get("closed") is True or event.get("active") is not True:
            raise DataSourceError(f"{home_name} vs {away_name} 的Polymarket事件未开放")
        event_title = str(event.get("title") or "")
        if _name(event_title) != _name(f"{home_name} vs. {away_name}"):
            raise DataSourceError("Polymarket事件标题与FIFA赛程不一致")
        end_time = _aware_datetime(event.get("endDate"), "Polymarket.endDate")
        difference = abs((end_time - kickoff).total_seconds()) / 60
        if difference > self.config.kickoff_tolerance_minutes:
            raise DataSourceError("Polymarket事件时间与FIFA开赛时间不一致")

        probabilities: dict[str, float] = {}
        market_ids: dict[str, str] = {}
        liquidities: dict[str, float] = {}
        for item in event.get("markets", []):
            if not isinstance(item, dict) or item.get("closed") is True or item.get("active") is not True:
                continue
            label = str(item.get("groupItemTitle") or "")
            if _name(label) == _name(home_name):
                outcome = "home"
            elif _name(label) == _name(away_name):
                outcome = "away"
            elif _name(label).startswith("draw"):
                outcome = "draw"
            else:
                continue
            try:
                liquidity = float(item.get("liquidity"))
            except (TypeError, ValueError) as exc:
                raise DataSourceError("Polymarket市场缺少流动性") from exc
            if liquidity < self.config.minimum_market_liquidity:
                raise DataSourceError(f"Polymarket的{outcome}市场流动性不足")
            if outcome in probabilities:
                raise DataSourceError(f"Polymarket重复出现{outcome}市场")
            probabilities[outcome] = self._yes_price(item)
            market_ids[outcome] = str(item.get("id") or "")
            liquidities[outcome] = liquidity
        if set(probabilities) != {"home", "draw", "away"}:
            raise DataSourceError("Polymarket未提供完整的主胜、平局、客胜市场")
        raw_sum = sum(probabilities.values())
        if not self.config.probability_sum_min <= raw_sum <= self.config.probability_sum_max:
            raise DataSourceError(f"Polymarket三项概率和异常：{raw_sum:.3f}")
        normalized = {outcome: value / raw_sum for outcome, value in probabilities.items()}
        odds = {outcome: round(1 / probability, 6) for outcome, probability in normalized.items()}
        return odds, {
            "provider": "polymarket-gamma-public",
            "event_id": str(event.get("id") or ""),
            "slug": event.get("slug"),
            "market_ids": market_ids,
            "raw_probabilities": probabilities,
            "normalized_probabilities": normalized,
            "raw_probability_sum": raw_sum,
            "liquidity": liquidities,
            "price_semantics": "公开二元市场Yes价格，三项归一化后转十进制赔率",
        }

    @staticmethod
    def _sporttery_decimal(value: Any, field_name: str) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise DataSourceError(f"中国体彩网{field_name}缺少有效SP") from exc
        if parsed <= 1:
            raise DataSourceError(f"中国体彩网{field_name} SP必须大于1.00")
        return parsed

    def _sporttery_timestamp(self, pool: dict[str, Any], field_name: str) -> str:
        try:
            parsed = datetime.fromisoformat(
                f"{pool['updateDate']}T{pool['updateTime']}"
            ).replace(tzinfo=ZoneInfo(self.config.output_timezone))
        except (KeyError, TypeError, ValueError) as exc:
            raise DataSourceError(f"中国体彩网{field_name}更新时间无效") from exc
        return parsed.isoformat()

    def _sporttery_offer(
        self,
        offers: list[dict[str, Any]],
        fixture_date: str,
        home_code: str,
        away_code: str,
        kickoff: datetime,
    ) -> dict[str, Any] | None:
        candidates: list[dict[str, Any]] = []
        output_zone = ZoneInfo(self.config.output_timezone)
        for item in offers:
            if item.get("businessDate") != fixture_date or item.get("matchStatus") != "Selling":
                continue
            sport_home = SPORTTERY_CODE_ALIASES.get(
                str(item.get("homeTeamCode") or item.get("homeTeamAbbEnName") or "").upper(),
                str(item.get("homeTeamCode") or item.get("homeTeamAbbEnName") or "").upper(),
            )
            sport_away = SPORTTERY_CODE_ALIASES.get(
                str(item.get("awayTeamCode") or item.get("awayTeamAbbEnName") or "").upper(),
                str(item.get("awayTeamCode") or item.get("awayTeamAbbEnName") or "").upper(),
            )
            if (sport_home, sport_away) != (home_code, away_code):
                continue
            try:
                sport_kickoff = datetime.fromisoformat(
                    f"{item.get('matchDate')}T{item.get('matchTime')}"
                ).replace(tzinfo=output_zone)
            except (TypeError, ValueError) as exc:
                raise DataSourceError("中国体彩网比赛时间格式无效") from exc
            difference = abs((sport_kickoff.astimezone(timezone.utc) - kickoff).total_seconds()) / 60
            if difference <= self.config.kickoff_tolerance_minutes:
                candidates.append(item)
        if not candidates:
            return None
        if len(candidates) != 1:
            raise DataSourceError("中国体彩网比赛无法与FIFA赛程唯一匹配")

        item = candidates[0]
        had, hhad = item.get("had"), item.get("hhad")
        if not isinstance(had, dict) or not isinstance(hhad, dict):
            raise DataSourceError("中国体彩网已开售比赛缺少HAD或HHAD")
        had_odds = {
            "home": self._sporttery_decimal(had.get("h"), "HAD主胜"),
            "draw": self._sporttery_decimal(had.get("d"), "HAD平局"),
            "away": self._sporttery_decimal(had.get("a"), "HAD客胜"),
        }
        hhad_odds = {
            "home": self._sporttery_decimal(hhad.get("h"), "HHAD让胜"),
            "draw": self._sporttery_decimal(hhad.get("d"), "HHAD让平"),
            "away": self._sporttery_decimal(hhad.get("a"), "HHAD让负"),
        }
        try:
            handicap_value = float(hhad.get("goalLineValue") or hhad.get("goalLine"))
        except (TypeError, ValueError) as exc:
            raise DataSourceError("中国体彩网HHAD缺少有效让球数") from exc
        if not handicap_value.is_integer():
            raise DataSourceError("中国体彩网HHAD让球数必须是整数")
        handicap = int(handicap_value)
        match_id = str(item.get("matchId") or "")
        if not match_id or not item.get("matchNumStr"):
            raise DataSourceError("中国体彩网比赛标识不完整")
        return {
            "provider": "sporttery-official-public",
            "match_id": match_id,
            "match_number": item.get("matchNumStr"),
            "business_date": item.get("businessDate"),
            "sale_status": item.get("matchStatus"),
            "had": {
                "odds": had_odds,
                "updated_at": self._sporttery_timestamp(had, "HAD"),
            },
            "hhad": {
                "handicap": handicap,
                "odds": hhad_odds,
                "updated_at": self._sporttery_timestamp(hhad, "HHAD"),
            },
            "price_semantics": "中国体育彩票竞彩足球官方固定奖金SP",
        }

    def collect(self, fixture_date: str) -> dict[str, Any]:
        window_start, window_end = self._date_window(fixture_date)
        current = self._clock()
        if current.tzinfo is None:
            raise ConfigurationError("统一数据管理器时钟必须包含时区")
        now = current.astimezone(timezone.utc)
        target = self.fifa.fixtures(self._iso_utc(window_start), self._iso_utc(window_end))
        history_start = datetime.combine(
            date.fromisoformat(self.config.tournament_start), time.min, tzinfo=timezone.utc
        )
        history = self.fifa.fixtures(self._iso_utc(history_start), self._iso_utc(window_end))
        if not target:
            raise DataSourceError(f"FIFA官方接口未返回{fixture_date}赛程")
        sporttery_offers = self.sporttery.matches()
        timeline_cache: dict[str, list[dict[str, Any]]] = {}

        matches = []
        errors: list[str] = []
        for item in target:
            kickoff = _aware_datetime(item.get("Date"), "FIFA.Date")
            if kickoff <= now:
                continue
            home, away = self._fixture_teams(item)
            home_name, away_name = _fifa_team_name(home), _fifa_team_name(away)
            home_code, away_code = str(home["Abbreviation"]).upper(), str(away["Abbreviation"]).upper()
            sporttery_offer = self._sporttery_offer(
                sporttery_offers, fixture_date, home_code, away_code, kickoff
            )
            if sporttery_offer is None:
                continue
            home_stats = self._group_stats(history, str(home["IdTeam"]), home_name)
            away_stats = self._group_stats(history, str(away["IdTeam"]), away_name)
            home_id = str(home["IdTeam"])
            away_id = str(away["IdTeam"])
            home_scorers = self._scorers(
                history, home_id, home_stats, home_name, timeline_cache
            )
            away_scorers = self._scorers(
                history, away_id, away_stats, away_name, timeline_cache
            )
            home_splits = compute_home_away_splits(history, home_id)
            away_splits = compute_home_away_splits(history, away_id)
            venue = extract_venue_from_fifa(item)
            market_home = POLYMARKET_CODE_ALIASES.get(home_code, home_code).lower()
            market_away = POLYMARKET_CODE_ALIASES.get(away_code, away_code).lower()
            slug = f"fifwc-{market_home}-{market_away}-{fixture_date}"
            try:
                event = self.market.event_by_slug(slug)
                odds, market_provenance = self._market_odds(
                    event, home_name, away_name, kickoff
                )
            except DataSourceError as exc:
                errors.append(f"{home_name} vs {away_name}: {exc}")
                continue
            stage_name = ""
            names = item.get("StageName")
            if isinstance(names, list) and names and isinstance(names[0], dict):
                stage_name = str(names[0].get("Description") or "")
            match_payload: dict[str, Any] = {
                    "id": f"fifa-{item.get('IdMatch')}",
                    "competition": "FIFA World Cup 2026",
                    "stage": "小组赛" if item.get("IdGroup") else "淘汰赛",
                    "fixture_date": fixture_date,
                    "kickoff_beijing": kickoff.astimezone(
                        ZoneInfo(self.config.output_timezone)
                    ).isoformat(),
                    "home": home_name,
                    "away": away_name,
                    "odds": odds,
                    "sporttery": sporttery_offer,
                    "tournament_rules": dict(FIFA_RULES),
                    "team_context": {
                        "home": {
                            "group_stats": home_stats,
                            "absences": [],
                            "scorers": home_scorers,
                            "home_away": home_splits,
                        },
                        "away": {
                            "group_stats": away_stats,
                            "absences": [],
                            "scorers": away_scorers,
                            "home_away": away_splits,
                        },
                    },
                    "provider_ids": {
                        "fifa_match": str(item.get("IdMatch")),
                        "fifa_home_team": str(home["IdTeam"]),
                        "fifa_away_team": str(away["IdTeam"]),
                        "polymarket_event": str(event.get("id") or ""),
                        "sporttery_match": sporttery_offer["match_id"],
                    },
                    "data_provenance": {
                        "fixture_results_team_stats": "fifa-public-official",
                        "player_scorers": "fifa-public-official-timelines",
                        "odds": market_provenance,
                        "sporttery": sporttery_offer,
                        "injuries": "not-available-from-verified-anonymous-public-api",
                        "rules": FIFA_RULES["source"],
                        "fifa_stage_name": stage_name,
                    },
                }
            if venue:
                match_payload["venue"] = venue
            matches.append(match_payload)
        if not matches:
            if errors:
                raise DataSourceError(errors[0].split(": ", 1)[-1])
            raise DataSourceError(f"{fixture_date}当前没有未开赛且市场开放的世界杯比赛")
        return {
            "data_as_of": now.isoformat(),
            "source": "无密钥统一公开数据中心（FIFA + Polymarket + 中国体彩网）",
            "sources": [
                "https://api.fifa.com/api/v3/calendar/matches",
                "https://api.fifa.com/api/v3/timelines/{matchId}",
                "https://gamma-api.polymarket.com/events",
                "https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry?channel=c&poolCode=hhad,had",
                FIFA_RULES["source"],
            ],
            "odds_format": "decimal",
            "quality_checks": {
                "status": "passed" if not errors else "partial",
                "api_key_required": False,
                "fixture_primary_source": "FIFA official",
                "purchasable_primary_source": "Sporttery official",
                "match_count": len(matches),
                "skipped_matches": errors,
                "minimum_market_liquidity": self.config.minimum_market_liquidity,
                "kickoff_tolerance_minutes": self.config.kickoff_tolerance_minutes,
                "injury_coverage": False,
                "injury_note": "经验证的匿名公开接口未提供结构化伤停，未作推测",
            },
            "matches": matches,
        }

    @staticmethod
    def save_snapshot(payload: dict[str, Any], path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return output.resolve()
