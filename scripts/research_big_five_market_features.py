"""Offline public-archive experiment; supply 25 files named e.g. 2122-E0.csv."""
import argparse
from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


SEASONS = ("2122", "2223", "2324", "2425", "2526")
LEAGUES = ("E0", "SP1", "D1", "I1", "F1")
PRICE_COLUMNS = ["AvgH", "AvgD", "AvgA"]
FOOTBALL = ["elo_gap", "home_goals_for", "home_goals_against",
            "away_goals_for", "away_goals_against", "home_games", "away_games"]
MARKET = ["log_home_draw", "log_away_draw"]


def build_sample(path, league, seasons_to_read=SEASONS):
    seasons = []
    for season in seasons_to_read:
        columns = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR", *PRICE_COLUMNS]
        frame = pd.read_csv(path / f"{season}-{league}.csv", encoding="cp1252", usecols=columns)
        seasons.append(frame.assign(season=season))
    matches = pd.concat(seasons, ignore_index=True)
    matches = matches[matches.FTR.isin(["H", "D", "A"])].copy()
    matches["date"] = pd.to_datetime(matches.Date, format="%d/%m/%Y")
    if matches.duplicated(["date", "HomeTeam", "AwayTeam"]).any():
        raise ValueError(f"Duplicate fixture in {league}")
    ratings = defaultdict(lambda: 1500.)
    history = defaultdict(lambda: deque(maxlen=5))
    rows = []
    for _, day in matches.groupby("date", sort=True):
        updates = []
        for game in day.itertuples():
            home, away = game.HomeTeam, game.AwayTeam
            gap = ratings[home] - ratings[away]
            result = ["H", "D", "A"].index(game.FTR)
            features = [gap]
            for team in (home, away):
                features.extend(np.mean(history[team], axis=0) if history[team] else (0., 0.))
            features.extend((len(history[home]), len(history[away])))
            prices = np.array([getattr(game, col) for col in PRICE_COLUMNS], dtype=float)
            if np.isfinite(prices).all() and (prices > 1).all():
                prob = 1 / prices
                prob /= prob.sum()
                rows.append(dict(zip(FOOTBALL, features), season=game.season, y=result,
                                 fixture=f"{game.date:%Y-%m-%d}|{home}|{away}",
                                 log_home_draw=np.log(prob[0] / prob[1]),
                                 log_away_draw=np.log(prob[2] / prob[1]), prob=prob))
            expected = 1 / (1 + 10 ** (-(gap + 60) / 400))
            updates.append((home, away, game.FTHG, game.FTAG, 20 * (1 - result / 2 - expected)))
        for home, away, hg, ag, change in updates:
            ratings[home] += change
            ratings[away] -= change
            history[home].append((hg, ag))
            history[away].append((ag, hg))
    return pd.DataFrame(rows)


def parameters(model, columns):
    scaler, reg = model.steps[0][1], model.steps[1][1]
    return {"features": columns, "center": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(),
            "coef": reg.coef_.tolist(), "intercept": reg.intercept_.tolist(), "classes": reg.classes_.tolist()}


def evaluate(sample, evidence=None):
    train = sample[sample.season != SEASONS[-1]]
    test = sample[sample.season == SEASONS[-1]]
    scores = {}
    for name, columns in [("football", FOOTBALL), ("combined", FOOTBALL + MARKET)]:
        model = make_pipeline(StandardScaler(), LogisticRegression(C=1., max_iter=1000))
        model.fit(train[columns], train.y)
        prob = model.predict_proba(test[columns])
        scores[name] = log_loss(test.y, prob, labels=[0, 1, 2])
        if evidence is not None:
            evidence[name] = {"parameters": parameters(model, columns), "predictions": prob.tolist()}
    scores["market"] = log_loss(test.y, np.stack(test.prob), labels=[0, 1, 2])
    if evidence is not None:
        evidence["fixtures"] = test.fixture.tolist()
        evidence["outcomes"] = test.y.tolist()
        evidence["market"] = np.stack(test.prob).tolist()
    return dict(train=len(train), test=len(test), **scores)


def upset_monitor(sample, test_season=SEASONS[-1]):
    # An upset is a win by the opposing team when one team's market probability is at least 60%.
    prob = np.stack(sample.prob)
    favorite_home = prob[:, 0] >= prob[:, 2]
    away_or_home = np.where(favorite_home, 2, 0)
    favorite_prob = np.maximum(prob[:, 0], prob[:, 2])
    underdog_prob = prob[np.arange(len(prob)), away_or_home]
    direction = np.where(favorite_home, -1, 1)
    frame = pd.DataFrame({"underdog_prob": underdog_prob, "favorite_prob": favorite_prob,
                          "underdog_home": (~favorite_home).astype(int), "underdog_elo_gap": direction * sample.elo_gap,
                          "upset": (sample.y == away_or_home).astype(int), "fixture": sample.fixture, "season": sample.season})
    frame = frame[favorite_prob >= 0.60]
    columns = ["underdog_prob", "favorite_prob", "underdog_home", "underdog_elo_gap"]
    train, test = frame[frame.season < test_season], frame[frame.season == test_season]
    model = make_pipeline(StandardScaler(), LogisticRegression(C=1., max_iter=1000))
    model.fit(train[columns], train.upset)
    values = model.predict_proba(test[columns])[:, 1]
    return {"definition": "favorite_1x2_p>=0.60/opposing_team_wins", "train": len(train),
            "test": len(test), "observed": int(test.upset.sum()), "parameters": parameters(model, columns),
            "brier_model": float(np.mean((values - test.upset)**2)),
            "brier_market": float(np.mean((test.underdog_prob - test.upset)**2)),
            "fixtures": test.fixture.tolist(), "outcomes": test.upset.tolist(),
            "market_probability": test.underdog_prob.tolist(), "predictions": values.tolist()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--archive", type=Path, help="Write content-addressed league research reports")
    args = parser.parse_args()
    directory = args.directory
    for league in LEAGUES:
        receipt = {s: hashlib.sha256((directory / f"{s}-{league}.csv").read_bytes()).hexdigest() for s in SEASONS}
        sample = build_sample(directory, league)
        evidence = {} if args.archive else None
        scores = evaluate(sample, evidence)
        print(league, scores)
        if args.archive:
            report = {"schema": "market-challenger-archive-v1", "league": league, "source_sha256": receipt,
                      "usage": "retrospective_research_nonclosing_no_quote_timestamp",
                      "test_season": SEASONS[-1], "scores": scores, "evidence": evidence,
                      "upset_monitor": upset_monitor(sample)}
            payload = json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
            target = args.archive / league / (hashlib.sha256(payload).hexdigest() + ".json")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.read_bytes() != payload:
                    raise ValueError("Archive integrity mismatch")
            else:
                with target.open("xb") as dest:
                    dest.write(payload)
