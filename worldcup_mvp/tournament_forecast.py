"""2026 世界杯 16 强冠军、决赛与冠亚军组合推演。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

BEIJING_TZ = ZoneInfo("Asia/Shanghai")

# BetMGM 2026-07-03 23:47 ET 公开快照。十进制赔率由美式赔率换算。
OUTRIGHT_ODDS = {
    "法国": 2.88,
    "阿根廷": 5.00,
    "西班牙": 7.00,
    "英格兰": 10.00,
    "巴西": 11.00,
    "葡萄牙": 13.00,
    "哥伦比亚": 21.00,
    "美国": 21.00,
    "挪威": 26.00,
    "墨西哥": 26.00,
    "摩洛哥": 26.00,
    "比利时": 34.00,
    "瑞士": 51.00,
    "加拿大": 101.00,
    "巴拉圭": 201.00,
    "埃及": 201.00,
}

ROUND_OF_16 = [
    ("加拿大", "摩洛哥"),
    ("巴拉圭", "法国"),
    ("巴西", "挪威"),
    ("墨西哥", "英格兰"),
    ("葡萄牙", "西班牙"),
    ("美国", "比利时"),
    ("瑞士", "哥伦比亚"),
    ("阿根廷", "埃及"),
]


def _strength(team: str) -> float:
    # 用平方根压缩夺冠长赔率，再交给固定对阵树计算逐轮晋级。
    return (1.0 / OUTRIGHT_ODDS[team]) ** 0.5


def _win_probability(home: str, away: str) -> float:
    home_strength = _strength(home)
    return home_strength / (home_strength + _strength(away))


def _play(left: dict[str, float], right: dict[str, float]) -> dict[str, float]:
    winners: dict[str, float] = {}
    for team_a, path_a in left.items():
        for team_b, path_b in right.items():
            pair = path_a * path_b
            p_a = _win_probability(team_a, team_b)
            winners[team_a] = winners.get(team_a, 0.0) + pair * p_a
            winners[team_b] = winners.get(team_b, 0.0) + pair * (1.0 - p_a)
    return winners


def _round(pair: tuple[str, str]) -> dict[str, float]:
    return _play({pair[0]: 1.0}, {pair[1]: 1.0})


def build_tournament_forecast() -> dict[str, Any]:
    r16 = [_round(pair) for pair in ROUND_OF_16]
    quarterfinals = [
        _play(r16[0], r16[1]),
        _play(r16[4], r16[5]),
        _play(r16[2], r16[3]),
        _play(r16[6], r16[7]),
    ]
    left_finalist = _play(quarterfinals[0], quarterfinals[1])
    right_finalist = _play(quarterfinals[2], quarterfinals[3])

    champion = {team: 0.0 for team in OUTRIGHT_ODDS}
    final_pairs: list[dict[str, Any]] = []
    for left, p_left in left_finalist.items():
        for right, p_right in right_finalist.items():
            pair_probability = p_left * p_right
            p_left_win = _win_probability(left, right)
            champion[left] += pair_probability * p_left_win
            champion[right] += pair_probability * (1.0 - p_left_win)
            final_pairs.append(
                {
                    "pair": f"{left} vs {right}",
                    "left": left,
                    "right": right,
                    "probability": round(pair_probability, 4),
                }
            )

    finalist = {**left_finalist, **right_finalist}
    market_raw = {team: 1.0 / odds for team, odds in OUTRIGHT_ODDS.items()}
    market_total = sum(market_raw.values())
    rows = []
    for team in OUTRIGHT_ODDS:
        final_p = finalist.get(team, 0.0)
        rows.append(
            {
                "team": team,
                "champion_probability": round(champion[team], 4),
                "final_probability": round(final_p, 4),
                "runner_up_probability": round(max(final_p - champion[team], 0.0), 4),
                "market_champion_probability": round(market_raw[team] / market_total, 4),
                "outright_odds": OUTRIGHT_ODDS[team],
            }
        )
    rows.sort(key=lambda item: item["champion_probability"], reverse=True)
    final_pairs.sort(key=lambda item: item["probability"], reverse=True)
    return {
        "generated_at": datetime.now(BEIJING_TZ).replace(microsecond=0).isoformat(),
        "as_of": "2026-07-04T12:00:00+08:00",
        "stage": "round_of_16",
        "method": "公开夺冠赔率强度 + 固定淘汰赛对阵树动态规划",
        "market_source": "BetMGM 2026-07-03 23:47 ET",
        "schedule_source": "ESPN FIFA World Cup scoreboard",
        "rankings": rows,
        "final_pairs": final_pairs[:10],
        "round_of_16": [{"home": home, "away": away} for home, away in ROUND_OF_16],
        "warning": "概率为模型概览，不是保证；伤停、首发和临场价格变化会使结果失效。",
    }
