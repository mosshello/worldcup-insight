"""基于连续快照分析欧赔与亚盘指数变动。"""

from __future__ import annotations

from typing import Any

from .analyzer import OUTCOME_LABELS, analyze_match


DIRECTION_LABELS = {
    "down": "走低（受热）",
    "up": "走高（受冷）",
    "flat": "持平",
}


def _pct_change(old: float, new: float) -> float:
    if old == 0:
        return 0.0
    return (new - old) / old


def _direction(delta: float, *, threshold: float = 0.005) -> str:
    if delta <= -threshold:
        return "down"
    if delta >= threshold:
        return "up"
    return "flat"


def _movement_item(
    label: str,
    old: float,
    new: float,
    *,
    threshold: float = 0.005,
) -> dict[str, Any]:
    delta = new - old
    pct = _pct_change(old, new)
    direction = _direction(delta, threshold=threshold)
    return {
        "label": label,
        "from": old,
        "to": new,
        "delta": delta,
        "pct_change": pct,
        "direction": direction,
        "direction_label": DIRECTION_LABELS[direction],
    }


def _consistent_trend(moves: list[dict[str, Any]], key: str) -> str | None:
    directions = [item["direction"] for item in moves if item["direction"] != "flat"]
    if len(directions) < 2:
        return None
    if all(direction == "down" for direction in directions):
        return f"{key}连续走低"
    if all(direction == "up" for direction in directions):
        return f"{key}连续走高"
    return None


def analyze_european_movement(
    previous: dict[str, float],
    current: dict[str, float],
) -> list[dict[str, Any]]:
    return [
        _movement_item(OUTCOME_LABELS[key], previous[key], current[key])
        for key in ("home", "draw", "away")
    ]


def analyze_asian_movement(
    previous: dict[str, float],
    current: dict[str, float],
) -> list[dict[str, Any]]:
    moves = [
        _movement_item("让球线", previous["line"], current["line"], threshold=0.01),
        _movement_item("主队水位", previous["home"], current["home"]),
        _movement_item("客队水位", previous["away"], current["away"]),
    ]
    return moves


def _cross_market_signal(
    european_moves: list[dict[str, Any]] | None,
    asian_moves: list[dict[str, Any]] | None,
) -> str | None:
    if not european_moves or not asian_moves:
        return None

    home_euro = next(item for item in european_moves if item["label"] == "主胜")
    home_water = next(item for item in asian_moves if item["label"] == "主队水位")
    line_move = next(item for item in asian_moves if item["label"] == "让球线")

    home_strengthen = home_euro["direction"] == "down" or home_water["direction"] == "down"
    home_weaken = home_euro["direction"] == "up" or home_water["direction"] == "up"
    line_to_home = line_move["delta"] < 0
    line_to_away = line_move["delta"] > 0

    if home_strengthen and (line_to_home or line_move["direction"] == "flat"):
        return "欧赔与亚盘同步指向主队受热，市场倾向主队。"
    if home_weaken and (line_to_away or line_move["direction"] == "flat"):
        return "欧赔与亚盘同步指向主队受冷，市场倾向客队或平局。"
    if home_euro["direction"] != "flat" and home_water["direction"] != "flat":
        if home_euro["direction"] != home_water["direction"]:
            return "欧赔与亚盘方向分歧，可能存在套利或不同资金池定价差异。"
    return None


def _build_movement_analysis(
    match_meta: dict[str, Any],
    european_moves: list[dict[str, Any]] | None,
    asian_moves: list[dict[str, Any]] | None,
    window_size: int,
    latest_analysis: dict[str, Any] | None,
) -> list[str]:
    lines: list[str] = []
    first_at = match_meta["snapshots"][0]["recorded_at"]
    last_at = match_meta["snapshots"][-1]["recorded_at"]
    lines.append(f"观察窗口共 {window_size} 条快照，从 {first_at} 至 {last_at}。")

    if european_moves:
        changed = [item for item in european_moves if item["direction"] != "flat"]
        if not changed:
            lines.append("欧赔三项均未出现明显变动。")
        else:
            for item in changed:
                lines.append(
                    f"欧赔{item['label']}由 {item['from']:.2f} 变为 {item['to']:.2f}，"
                    f"变动 {item['pct_change']:+.1%}，{item['direction_label']}。"
                )
            trend = _consistent_trend(changed, "欧赔")
            if trend:
                lines.append(f"近期{trend}，资金方向较一致。")

    if asian_moves:
        changed = [item for item in asian_moves if item["direction"] != "flat"]
        if not changed:
            lines.append("亚盘让球线与水位均未出现明显变动。")
        else:
            for item in changed:
                if item["label"] == "让球线":
                    lines.append(
                        f"亚盘{item['label']}由 {item['from']:+.2f} 调整为 {item['to']:+.2f}。"
                    )
                else:
                    lines.append(
                        f"亚盘{item['label']}由 {item['from']:.2f} 变为 {item['to']:.2f}，"
                        f"{item['direction_label']}。"
                    )

    signal = _cross_market_signal(european_moves, asian_moves)
    if signal:
        lines.append(signal)

    if latest_analysis:
        pick = OUTCOME_LABELS[latest_analysis["pick"]]
        lines.append(
            f"最新欧赔去水后首选仍为{pick}，信心 {latest_analysis['confidence']}。"
        )

    lines.append("盘口变动反映市场定价漂移，不等于赛果预测；需结合临场信息与数据源延迟。")
    return lines


def analyze_movement(
    history: dict[str, Any],
    *,
    lookback: int | None = None,
) -> dict[str, Any]:
    """分析盘口历史，默认对比最近两条快照，也可指定 lookback 条窗口。"""
    snapshots = history["snapshots"]
    if len(snapshots) < 2:
        raise ValueError("至少需要 2 条快照才能分析变动")

    window = snapshots[-lookback:] if lookback and lookback >= 2 else snapshots[-2:]
    previous = window[0]
    current = window[-1]

    european_moves = None
    asian_moves = None
    latest_analysis = None

    if "european" in previous and "european" in current:
        european_moves = analyze_european_movement(previous["european"], current["european"])
        latest_analysis = analyze_match(
            {
                "home": history["home"],
                "away": history["away"],
                "odds": current["european"],
            }
        )

    if "asian_handicap" in previous and "asian_handicap" in current:
        asian_moves = analyze_asian_movement(
            previous["asian_handicap"],
            current["asian_handicap"],
        )

    analysis = _build_movement_analysis(
        {"snapshots": window},
        european_moves,
        asian_moves,
        len(window),
        latest_analysis,
    )

    return {
        "match_id": history["match_id"],
        "home": history["home"],
        "away": history["away"],
        "window_size": len(window),
        "from_snapshot": previous["recorded_at"],
        "to_snapshot": current["recorded_at"],
        "european_movement": european_moves,
        "asian_movement": asian_moves,
        "latest_european_analysis": latest_analysis,
        "analysis": analysis,
    }
