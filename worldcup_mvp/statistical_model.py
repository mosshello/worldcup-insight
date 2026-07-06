"""可复现的国家队 Elo、滚动攻防与双 Poisson 统计模型。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .international_data import InternationalMatch, PROJECT_ROOT, load_international_matches, split_training_data

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
MODEL_FILE = PROJECT_ROOT / "data" / "training" / "statistical_model.json"
MODEL_VERSION = "elo-poisson-1.0"
VALIDATION_START = date(2025, 7, 1)
MAX_GOALS = 10

TEAM_ALIASES = {
    "阿根廷": "Argentina", "法国": "France", "西班牙": "Spain", "英格兰": "England",
    "巴西": "Brazil", "葡萄牙": "Portugal", "哥伦比亚": "Colombia", "美国": "United States",
    "挪威": "Norway", "墨西哥": "Mexico", "摩洛哥": "Morocco", "比利时": "Belgium",
    "瑞士": "Switzerland", "加拿大": "Canada", "巴拉圭": "Paraguay", "埃及": "Egypt",
    "佛得角": "Cape Verde", "澳大利亚": "Australia", "德国": "Germany", "日本": "Japan",
    "韩国": "South Korea", "塞内加尔": "Senegal", "厄瓜多尔": "Ecuador",
}


@dataclass
class TeamState:
    ratings: dict[str, float] = field(default_factory=dict)
    attack: dict[str, float] = field(default_factory=dict)
    defence: dict[str, float] = field(default_factory=dict)
    games: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ratings": self.ratings, "attack": self.attack, "defence": self.defence, "games": self.games}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TeamState":
        return cls(
            ratings={key: float(value) for key, value in (payload.get("ratings") or {}).items()},
            attack={key: float(value) for key, value in (payload.get("attack") or {}).items()},
            defence={key: float(value) for key, value in (payload.get("defence") or {}).items()},
            games={key: int(value) for key, value in (payload.get("games") or {}).items()},
        )


def normalize_team(team: str) -> str:
    return TEAM_ALIASES.get(team.strip(), team.strip())


def _tournament_weight(tournament: str) -> float:
    label = tournament.lower()
    if "friendly" in label:
        return 0.6
    if "world cup" in label or "euro" in label or "copa américa" in label or "african cup" in label:
        return 1.25
    if "qualification" in label or "nations league" in label:
        return 1.0
    return 0.85


def _expected_elo(home_rating: float, away_rating: float, *, neutral: bool) -> float:
    home_bonus = 0.0 if neutral else 55.0
    return 1.0 / (1.0 + 10 ** (-(home_rating + home_bonus - away_rating) / 400.0))


def _result_value(home_goals: int, away_goals: int) -> float:
    return 1.0 if home_goals > away_goals else 0.0 if home_goals < away_goals else 0.5


def update_state(state: TeamState, match: InternationalMatch, *, elo_k: float, base_goals: float) -> None:
    home = match.home_team
    away = match.away_team
    home_rating = state.ratings.get(home, 1500.0)
    away_rating = state.ratings.get(away, 1500.0)
    expected = _expected_elo(home_rating, away_rating, neutral=match.neutral)
    margin = min(abs(match.home_goals - match.away_goals), 4)
    margin_factor = 1.0 + 0.12 * max(margin - 1, 0)
    change = elo_k * _tournament_weight(match.tournament) * margin_factor * (
        _result_value(match.home_goals, match.away_goals) - expected
    )
    state.ratings[home] = home_rating + change
    state.ratings[away] = away_rating - change

    alpha = 0.14
    home_attack_target = min(max(match.home_goals / base_goals, 0.35), 2.5)
    away_attack_target = min(max(match.away_goals / base_goals, 0.35), 2.5)
    state.attack[home] = (1 - alpha) * state.attack.get(home, 1.0) + alpha * home_attack_target
    state.attack[away] = (1 - alpha) * state.attack.get(away, 1.0) + alpha * away_attack_target
    state.defence[home] = (1 - alpha) * state.defence.get(home, 1.0) + alpha * away_attack_target
    state.defence[away] = (1 - alpha) * state.defence.get(away, 1.0) + alpha * home_attack_target
    state.games[home] = state.games.get(home, 0) + 1
    state.games[away] = state.games.get(away, 0) + 1


def expected_goals(
    state: TeamState,
    home: str,
    away: str,
    *,
    neutral: bool,
    base_goals: float,
    elo_goal_coefficient: float,
    home_goal_advantage: float,
) -> tuple[float, float]:
    home = normalize_team(home)
    away = normalize_team(away)
    rating_diff = (state.ratings.get(home, 1500.0) - state.ratings.get(away, 1500.0)) / 400.0
    venue = 0.0 if neutral else home_goal_advantage
    home_form = math.sqrt(state.attack.get(home, 1.0) * state.defence.get(away, 1.0))
    away_form = math.sqrt(state.attack.get(away, 1.0) * state.defence.get(home, 1.0))
    home_lambda = base_goals * home_form * math.exp(venue + elo_goal_coefficient * rating_diff)
    away_lambda = base_goals * away_form * math.exp(-venue - elo_goal_coefficient * rating_diff)
    return min(max(home_lambda, 0.2), 4.5), min(max(away_lambda, 0.2), 4.5)


def _poisson(lam: float, goals: int) -> float:
    return math.exp(-lam) * lam**goals / math.factorial(goals)


def score_matrix(home_lambda: float, away_lambda: float) -> list[list[float]]:
    matrix = [[_poisson(home_lambda, h) * _poisson(away_lambda, a) for a in range(MAX_GOALS + 1)] for h in range(MAX_GOALS + 1)]
    total = sum(sum(row) for row in matrix)
    return [[value / total for value in row] for row in matrix]


def probabilities_from_matrix(matrix: list[list[float]]) -> dict[str, Any]:
    had = {"home": 0.0, "draw": 0.0, "away": 0.0}
    total_goals: dict[str, float] = {str(value): 0.0 for value in range(8)}
    total_goals["7+"] = 0.0
    btts = 0.0
    scores: list[dict[str, Any]] = []
    for home_goals, row in enumerate(matrix):
        for away_goals, probability in enumerate(row):
            key = "home" if home_goals > away_goals else "away" if home_goals < away_goals else "draw"
            had[key] += probability
            total = home_goals + away_goals
            total_goals[str(total) if total <= 7 else "7+"] += probability
            if home_goals > 0 and away_goals > 0:
                btts += probability
            scores.append({"score": f"{home_goals}-{away_goals}", "probability": probability})
    scores.sort(key=lambda item: item["probability"], reverse=True)
    over_25 = sum(value for key, value in total_goals.items() if key == "7+" or int(key) >= 3)
    return {
        "had": {key: round(value, 6) for key, value in had.items()},
        "total_goals": {key: round(value, 6) for key, value in total_goals.items()},
        "over_2_5": round(over_25, 6),
        "under_2_5": round(1.0 - over_25, 6),
        "btts_yes": round(btts, 6),
        "btts_no": round(1.0 - btts, 6),
        "top_scores": [{**item, "probability": round(item["probability"], 6)} for item in scores[:5]],
    }


def predict_with_state(state: TeamState, home: str, away: str, *, neutral: bool, params: dict[str, float]) -> dict[str, Any]:
    home_lambda, away_lambda = expected_goals(
        state, home, away, neutral=neutral, base_goals=params["base_goals"],
        elo_goal_coefficient=params["elo_goal_coefficient"], home_goal_advantage=params["home_goal_advantage"],
    )
    derived = probabilities_from_matrix(score_matrix(home_lambda, away_lambda))
    return {
        "home": home, "away": away, "neutral": neutral,
        "expected_goals": {"home": round(home_lambda, 3), "away": round(away_lambda, 3)},
        **derived,
    }


def _evaluate(matches: list[InternationalMatch], state: TeamState, params: dict[str, float], *, update: bool) -> dict[str, Any]:
    brier = log_loss = goal_mae = poisson_deviance = 0.0
    correct = 0
    for match in matches:
        prediction = predict_with_state(state, match.home_team, match.away_team, neutral=match.neutral, params=params)
        actual = "home" if match.home_goals > match.away_goals else "away" if match.home_goals < match.away_goals else "draw"
        probs = prediction["had"]
        brier += sum((probs[key] - (1.0 if key == actual else 0.0)) ** 2 for key in probs)
        log_loss -= math.log(max(probs[actual], 1e-12))
        correct += max(probs, key=probs.get) == actual
        lambdas = prediction["expected_goals"]
        goal_mae += (abs(lambdas["home"] - match.home_goals) + abs(lambdas["away"] - match.away_goals)) / 2
        for observed, lam in ((match.home_goals, lambdas["home"]), (match.away_goals, lambdas["away"])):
            poisson_deviance += 2 * (lam - observed + (observed * math.log(observed / lam) if observed else 0.0))
        if update:
            update_state(state, match, elo_k=params["elo_k"], base_goals=params["base_goals"])
    count = len(matches)
    return {
        "count": count,
        "brier_score": round(brier / count, 4) if count else None,
        "log_loss": round(log_loss / count, 4) if count else None,
        "direction_accuracy": round(correct / count, 4) if count else None,
        "goal_mae": round(goal_mae / count, 4) if count else None,
        "poisson_deviance_per_team": round(poisson_deviance / (2 * count), 4) if count else None,
    }


def _fit_candidate(train: list[InternationalMatch], validation: list[InternationalMatch], params: dict[str, float]) -> tuple[float, TeamState, dict[str, Any]]:
    state = TeamState()
    for match in train:
        update_state(state, match, elo_k=params["elo_k"], base_goals=params["base_goals"])
    metrics = _evaluate(validation, state, params, update=True)
    return float(metrics["log_loss"] or 99.0), state, metrics


def train_statistical_model(*, refresh: bool = False) -> dict[str, Any]:
    matches, source_meta = load_international_matches(refresh=refresh)
    split = split_training_data(matches)
    foundation = split["foundation"]
    test_2026 = split["world_cup_2026_test"]
    train = [match for match in foundation if match.match_date < VALIDATION_START]
    validation = [match for match in foundation if match.match_date >= VALIDATION_START]
    if len(train) < 500 or len(validation) < 100:
        raise RuntimeError(f"训练数据不足：训练 {len(train)}，验证 {len(validation)}")
    base_goals = sum(match.home_goals + match.away_goals for match in train) / (2 * len(train))

    best: tuple[float, dict[str, float], TeamState, dict[str, Any]] | None = None
    for elo_k in (16.0, 22.0, 28.0):
        for elo_coefficient in (0.25, 0.4, 0.55, 0.7):
            for home_advantage in (0.05, 0.12, 0.19):
                params = {
                    "elo_k": elo_k, "base_goals": base_goals,
                    "elo_goal_coefficient": elo_coefficient, "home_goal_advantage": home_advantage,
                }
                loss, state, metrics = _fit_candidate(train, validation, params)
                if best is None or loss < best[0]:
                    best = (loss, params, state, metrics)
    assert best is not None
    _, params, foundation_state, validation_metrics = best

    # 参数已固定；2026 世界杯逐场先预测再更新，只允许过去场次影响未来场次。
    test_metrics = _evaluate(test_2026, foundation_state, params, update=True)
    naive_home = sum(match.home_goals > match.away_goals for match in train) / len(train)
    naive_draw = sum(match.home_goals == match.away_goals for match in train) / len(train)
    naive_away = 1.0 - naive_home - naive_draw
    naive_probs = {"home": naive_home, "draw": naive_draw, "away": naive_away}
    naive_log_loss = 0.0
    for match in test_2026:
        actual = "home" if match.home_goals > match.away_goals else "away" if match.home_goals < match.away_goals else "draw"
        naive_log_loss -= math.log(max(naive_probs[actual], 1e-12))

    artifact = {
        "schema_version": 1,
        "model_version": MODEL_VERSION,
        "trained_at": datetime.now(BEIJING_TZ).replace(microsecond=0).isoformat(),
        "status": "shadow",
        "data_source": source_meta,
        "boundaries": {
            "foundation_definition": "2022 World Cup + 2024-06-11..2026-06-10 internationals",
            "validation_start": VALIDATION_START.isoformat(),
            "world_cup_2026_role": "sealed prequential test; predict then update state",
        },
        "counts": {"foundation": len(foundation), "train": len(train), "validation": len(validation), "world_cup_2026_test": len(test_2026)},
        "parameters": {key: round(value, 8) for key, value in params.items()},
        "metrics": {
            "validation": validation_metrics,
            "world_cup_2026": test_metrics,
            "world_cup_2026_naive_log_loss": round(naive_log_loss / len(test_2026), 4) if test_2026 else None,
        },
        "deployment_state": foundation_state.to_dict(),
        "activation": {
            "active": False,
            "reason": "影子模型尚未与同时间戳市场概率基线完成比较，不覆盖生产方向。",
        },
    }
    MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODEL_FILE.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return artifact


def load_statistical_model() -> dict[str, Any] | None:
    if not MODEL_FILE.exists():
        return None
    try:
        return json.loads(MODEL_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def predict_statistical_match(home: str, away: str, *, neutral: bool = True) -> dict[str, Any] | None:
    artifact = load_statistical_model()
    if artifact is None:
        return None
    state = TeamState.from_dict(artifact["deployment_state"])
    prediction = predict_with_state(state, home, away, neutral=neutral, params=artifact["parameters"])
    return {
        **prediction,
        "model_version": artifact["model_version"],
        "model_status": artifact["status"],
        "trained_at": artifact["trained_at"],
        "data_quality": "B",
        "note": "历史赛果统计影子模型；未包含当前伤停与临场价格，不单独构成投注建议。",
    }
