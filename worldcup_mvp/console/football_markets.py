"""90-minute score distributions and Chinese Sporttery market semantics.

No network, LLM, prices-as-labels or retrospective selection in this module.
"""
from __future__ import annotations

import math
import re
from .sources import EXPECTED_OPTIONS, MARKETS, option_label


def outcome(home, away):
    return "h" if home > away else "a" if away > home else "d"


def score_key(home, away):
    key = f"s{home:02}s{away:02}"
    return key if key in EXPECTED_OPTIONS["crs"] else "s1s" + outcome(home, away)


def score_grid(home, away, rho=0.0, limit=30):
    if any(not math.isfinite(x) or not 0 < x <= 10 for x in (home, away)):
        raise ValueError("进球强度必须在 (0, 10] 内")
    if not math.isfinite(rho):
        raise ValueError("低比分校正参数无效")
    # Restrict the correction to the positive-probability region for this fixture.
    rho = min(0.999 / max(home * away, 1), max(-0.999 / max(home, away), rho))
    hp, ap = [math.exp(-home)], [math.exp(-away)]
    for n in range(1, limit + 1):
        hp.append(hp[-1] * home / n)
        ap.append(ap[-1] * away / n)
    adjustments = {(0, 0): 1-home*away*rho, (0, 1): 1+home*rho,
                   (1, 0): 1+away*rho, (1, 1): 1-rho}
    grid = [[x*y*adjustments.get((h, a), 1) for a, y in enumerate(ap)]
            for h, x in enumerate(hp)]
    mass = sum(map(sum, grid))
    return [[p/mass for p in row] for row in grid]


def market_probabilities(grid, handicap=None, half_shares=None):
    result = {code: dict.fromkeys(EXPECTED_OPTIONS[code], 0.0)
              for code in ("had", "ttg", "crs")}
    if handicap is not None:
        if isinstance(handicap, bool) or not isinstance(handicap, (int, float)) or not math.isfinite(handicap) or handicap != int(handicap):
            raise ValueError("体彩让球必须是整数")
        result["hhad"] = dict.fromkeys(EXPECTED_OPTIONS["hhad"], 0.0)
    if half_shares is not None:
        if len(half_shares) != 2 or any(not math.isfinite(p) or not 0 < p < 1 for p in half_shares):
            raise ValueError("半场进球分配参数无效")
        result["hafu"] = dict.fromkeys(EXPECTED_OPTIONS["hafu"], 0.0)
        # Conditional allocation given the full-time score keeps HT/FT coherent.
        # This baseline assumes independent goal timing; it is not a match-state simulator.
        bins = [[[math.comb(n, k)*p**k*(1-p)**(n-k) for k in range(n+1)]
                 for n in range(len(grid))] for p in half_shares]
        cumulative = []
        for values in bins[1]:
            total, cdf = 0.0, []
            for value in values:
                total += value
                cdf.append(total)
            cumulative.append(cdf)
    for h, row in enumerate(grid):
        for a, p in enumerate(row):
            ft = outcome(h, a)
            result["had"][ft] += p
            result["ttg"][f"s{min(h+a, 7)}"] += p
            result["crs"][score_key(h, a)] += p
            if "hhad" in result:
                result["hhad"][outcome(h+handicap, a)] += p
            if "hafu" in result and p > 0:
                draw = sum(ph*bins[1][a][hh] for hh, ph in enumerate(bins[0][h]) if hh <= a)
                win = sum(ph*(cumulative[a][min(hh-1, a)] if hh else 0) for hh, ph in enumerate(bins[0][h]))
                for key, value in (("h", win), ("d", draw), ("a", max(0, 1-win-draw))):
                    result["hafu"][key+ft] += p*value
    return result


def assessment(code, probabilities, pool=None, origin="market", eligible=True):
    """Freeze a deterministic observation policy, rather than inventing picks in review."""
    pool = pool or {}
    quotes = {o["key"]: o for o in pool.get("options", [])}
    ranked = sorted(probabilities, key=probabilities.get, reverse=True)
    close = code in ("had", "hhad", "hafu") and len(ranked) > 1 and probabilities[ranked[0]] - probabilities[ranked[1]] < 0.03
    count = 2 if code == "ttg" else 3 if code == "crs" else 1
    selected = ranked[:count] if eligible and not close else []
    options = [{"key": k, "label": quotes.get(k, {}).get("label") or option_label(code, k),
                "odds": quotes.get(k, {}).get("odds"), "probability": round(probabilities[k]*100, 4)} for k in ranked]
    if origin == "model" and set(quotes) == set(EXPECTED_OPTIONS[code]) and all(isinstance(o.get("odds"), (int, float)) and math.isfinite(o["odds"]) and o["odds"] > 1 for o in quotes.values()):
        total = sum(1/o["odds"] for o in quotes.values())
        for option in options:
            market = 100/quotes[option["key"]]["odds"]/total
            option["market_probability"] = round(market, 4)
            option["difference"] = round(option["probability"]-market, 4)
    return {"code": code, "label": MARKETS[code], "line": pool.get("line"),
            "origin": origin, "options": options, "selected_keys": selected,
            "selection": " / ".join(o["label"] for o in options if o["key"] in selected) or "观望",
            "coverage": round(sum(probabilities[k] for k in selected)*100, 4),
            "selection_count": len(selected), "policy": "observation-top-v1",
            "status": "观察" if selected else "观望",
            "reason": "玩法未开售或数据失效" if not eligible else "前两项接近" if close else "按概率排序；非收益优势判断"}


def official_outcomes(results):
    resolved = {}
    for item in results:
        code = str(item.get("code", "")).lower()
        raw = str(item.get("combination", "")).lower().strip()
        if code not in EXPECTED_OPTIONS:
            continue
        if code == "crs" and re.fullmatch(r"\d+[:-]\d+", raw):
            h, a = map(int, re.split("[:-]", raw))
            raw = score_key(h, a)
        elif code == "ttg":
            if raw in ("7+", "7及以上"):
                raw = "s7"
            elif raw.isdigit():
                raw = f"s{min(int(raw), 7)}"
        elif code == "hafu":
            raw = raw.replace(":", "").replace("-", "")
        if raw in EXPECTED_OPTIONS[code]:
            resolved[code] = raw
    return resolved


def settle_markets(prediction, results, cancelled=False):
    actuals = official_outcomes(results)
    rows = []
    assessments = {a["code"]: a for a in prediction.get("market_assessments", [])}
    # Old versions explicitly stored 1X2, goal and exact-score observations, but
    # not handicap/HTFT choices. Recover only those frozen explicit fields.
    legacy = {}
    if prediction.get("direction_key") in ("h", "d", "a"):
        legacy["had"] = [prediction["direction_key"]]
    if re.fullmatch(r"[0-7]\+?(?:/[0-7]\+?)*球", prediction.get("goals", "").replace(" ", "")):
        legacy["ttg"] = ["s"+k for k in re.findall(r"[0-7]", prediction["goals"])]
    scores = prediction.get("score_probabilities", [])
    if scores and all(re.fullmatch(r"\d+-\d+", s.get("score", "")) for s in scores):
        legacy["crs"] = [score_key(*map(int, s["score"].split("-"))) for s in scores]
    for code in MARKETS:
        view = dict(assessments.get(code, {}))
        if "selected_keys" not in view and code in legacy:
            view["selected_keys"] = list(dict.fromkeys(legacy[code]))
            view["selection"] = " / ".join(option_label(code, k) for k in view["selected_keys"])
        keys = view.get("selected_keys")
        actual = actuals.get(code)
        if code == "hhad" and type(view.get("line")) is int:
            score = next((str(r.get("combination", "")) for r in results if str(r.get("code", "")).lower() == "crs"), "")
            if re.fullmatch(r"\d+[:-]\d+", score):
                home, away = map(int, re.split("[:-]", score))
                actual = outcome(home+view["line"], away)
        hit = None
        if cancelled:
            status = "已取消"
        elif keys is None:
            status = "未归档选择"
        elif not keys:
            status = "观望"
        elif actual is None:
            status = "待赛果"
        else:
            hit = actual in keys
            status = "覆盖" if hit else "未覆盖"
        options = {o["key"]: o for o in view.get("options", [])}
        # Fixed, equal 1-unit stakes per archived selection; no optimised stakes after result.
        cost = len(keys) if keys else None
        payout = None
        if hit is not None and all(isinstance(options.get(k, {}).get("odds"), (int, float)) and options[k]["odds"] > 1 for k in keys):
            payout = options[actual]["odds"] if hit else 0.0
        rows.append({"code": code, "label": MARKETS[code], "line": view.get("line"),
                     "selection": view.get("selection", "未归档"), "selection_count": len(keys or []),
                     "actual": option_label(code, actual) if actual else "—", "actual_key": actual,
                     "hit": hit, "status": status, "unit_cost": cost,
                     "unit_return": payout, "unit_profit": round(payout-cost, 4) if payout is not None else None})
    return rows
