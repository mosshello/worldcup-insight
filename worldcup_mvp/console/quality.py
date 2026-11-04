"""Fail-closed quote selection. Price change time is not a feed heartbeat."""

from copy import deepcopy
from datetime import datetime, timezone
import math
from .sources import EXPECTED_OPTIONS, parse_date

MAX_AGE_SECONDS = 180


def complete(market):
    if not market or market.get("code") not in EXPECTED_OPTIONS:
        return False
    options = market.get("options", [])
    keys = [o.get("key") for o in options]
    if len(set(keys)) != len(keys) or set(keys) != set(
        EXPECTED_OPTIONS[market["code"]]
    ):
        return False
    if any(
        not isinstance(o.get("odds"), (float, int))
        or not math.isfinite(o["odds"])
        or o["odds"] <= 1
        for o in options
    ):
        return False
    if market["code"] == "hhad":
        line = market.get("line")
        if (
            not isinstance(line, (float, int))
            or not math.isfinite(line)
            or int(line) != line
        ):
            return False
    return True


def age_seconds(value, now=None):
    try:
        return ((now or datetime.now(timezone.utc)) - parse_date(value)).total_seconds()
    except (ValueError, TypeError, AttributeError):
        return float("inf")


def select_markets(current, history, now=None):
    """Never let a partial or older history entry destroy a full current quote."""
    selected, issues = deepcopy(current), []
    for code, series in history.items():
        valid = [
            p
            for p in series
            if complete(p)
            and math.isfinite(age_seconds(p.get("updated_at"), now))
            and age_seconds(p.get("updated_at"), now) >= -30
        ]
        if not valid:
            if series:
                issues.append(
                    {
                        "market": code,
                        "code": "incomplete_history",
                        "message": "历史报价不完整",
                    }
                )
            continue
        latest = max(valid, key=lambda p: parse_date(p["updated_at"]))
        existing = current.get(code, {})
        # A complete current price is authoritative when its timestamp is missing.
        newer = existing.get("updated_at") and age_seconds(
            latest["updated_at"], now
        ) < age_seconds(existing["updated_at"], now)
        if not complete(existing) or newer:
            selected[code] = {
                **latest,
                "single": existing.get("single", False),
                "sale_status": existing.get("sale_status", "unknown"),
            }
    return selected, issues


def assess(match, detail, now=None, football=None):
    now = now or datetime.now(timezone.utc)
    reasons = []
    if not detail.get("odds_fresh"):
        reasons.append("赔率核验失败")
    for name, stamp in (
        ("场次", match.get("captured_at")),
        ("赔率", detail.get("captured_at")),
    ):
        age = age_seconds(stamp, now)
        if age > MAX_AGE_SECONDS or age < -30:
            reasons.append(name + "核验时间失效")
    if match.get("sale_status") != "selling":
        reasons.append("比赛未在售")
    eligible = []
    for code, market in detail.get("markets", {}).items():
        if (
            complete(market)
            and market.get("sale_status") == "selling"
            and (
                market.get("updated_at") is None
                or math.isfinite(age_seconds(market.get("updated_at"), now))
            )
            and age_seconds(market.get("updated_at"), now) >= -30
        ):
            eligible.append(code)
    if not {"had", "hafu"}.intersection(eligible):
        reasons.append("胜平负及半全场均缺少完整在售报价")
    context = detail.get("context", {})
    from .readiness import check
    fundamentals = check(match, context, now)
    quote_reasons = list(reasons)
    reasons.extend(fundamentals['reasons'])
    result = {
        "can_predict": not reasons,
        "quotes_ready": not quote_reasons,
        "fundamentals": fundamentals,
        "reasons": reasons,
        "eligible_markets": eligible if not quote_reasons else [],
        "verified_at": detail.get("captured_at"),
        "max_age_seconds": MAX_AGE_SECONDS,
        "upstream_latency_verified": False,
        "coverage": {
            "recent": [len(r) for r in context.get("recent", [[], []])],
            "lineups_confirmed": sum(
                r.get("status") == "confirmed" for r in context.get("lineups", [])
            ),
            "injuries": bool(context.get("injuries_verified")),
            "historical_training": False,
        },
    }
    if football is not None:
        from .model_readiness import check as model_check
        result['model_inputs'] = model_check(match,detail,football,now)
        result['can_predict'] = result['model_inputs']['can_predict']
        result['reasons'] = result['model_inputs']['reasons']
    return result
