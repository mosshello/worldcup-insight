"""Offline, point-in-time club evaluation. Never trains on tips or activates a model.

Input: data/training/club_history.json, with licensed manifest + regular-time results.
All deployment remains gated on a separately collected prospective evaluation.
"""

from __future__ import annotations
import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .store import ROOT, fingerprint, now_iso
from .sources import parse_date

DATASET = ROOT / "data" / "training" / "club_history.json"
REPORT = ROOT / "data" / "training" / "club_evaluation.json"
COMPETITIONS = {"jp.j1", "kr.k1"}


def validate_dataset(data):
    manifest = data.get("manifest", {})
    if (
        manifest.get("training_permitted") is not True
        or not manifest.get("license_reference")
        or not manifest.get("source_url")
    ):
        raise ValueError("缺少数据来源、训练授权声明或授权凭据说明")
    rows = data.get("matches", [])
    if not isinstance(rows, list) or not rows:
        raise ValueError("缺少历史比赛")
    seen = set()
    for row in rows:
        required = (
            "id",
            "competition_id",
            "season",
            "stage",
            "home_id",
            "away_id",
            "kickoff",
            "result_available_at",
            "source_url",
        )
        if any(not row.get(k) for k in required):
            raise ValueError("历史比赛缺少身份、赛季、阶段、来源或可用时间")
        if row["id"] in seen:
            raise ValueError("历史比赛 ID 重复")
        seen.add(row["id"])
        if (
            row["competition_id"] not in COMPETITIONS
            or row["home_id"] == row["away_id"]
        ):
            raise ValueError("赛事或球队身份无效")
        if row.get("period") != "regular_time":
            raise ValueError("仅接收常规90分钟赛果，不能混入加时或点球")
        if any(
            type(row.get(k)) is not int or not 0 <= row[k] <= 30
            for k in ("home_goals", "away_goals")
        ):
            raise ValueError("进球数据无效")
        start, available = parse_date(row["kickoff"]), parse_date(
            row["result_available_at"]
        )
        if (
            start.tzinfo is None
            or available.tzinfo is None
            or available < start + timedelta(minutes=90)
            or available > datetime.now(timezone.utc)
        ):
            raise ValueError("赛果可用时间无效")
        if row.get("odds"):
            if set(row["odds"]) != {"h", "d", "a"} or any(
                not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 1
                for v in row["odds"].values()
            ):
                raise ValueError("历史赔率须包含完整胜平负")
            if not row.get("odds_observed_at") or not row.get("odds_source_url"):
                raise ValueError("历史赔率缺少实际记录时间或来源")
            observed = parse_date(row["odds_observed_at"])
            if observed.tzinfo is None or observed > start - timedelta(minutes=60):
                raise ValueError("赛前60分钟回测不能使用更晚的赔率")
    return sorted(rows, key=lambda r: (parse_date(r["kickoff"]), r["id"]))


def score_distribution(home, away):
    result = [0.0, 0.0, 0.0]
    for h in range(25):
        hp = math.exp(-home) * home**h / math.factorial(h)
        for a in range(25):
            p = hp * math.exp(-away) * away**a / math.factorial(a)
            result[0 if h > a else 2 if a > h else 1] += p
    total = sum(result)
    return [v / total for v in result]


class GoalModel:
    """Regularized log-linear attack/defence model with league-aware intercepts.

    separate: only target league; pooled: shared intercept; hierarchical: regularized
    league intercepts. Team identities are always scoped to their competition.
    """

    def __init__(self, mode="separate", half_life=365):
        self.mode, self.half_life = mode, half_life

    def fit(self, rows, asof, competition):
        rows = [
            r
            for r in rows
            if parse_date(r["result_available_at"]) < asof
            and (self.mode != "separate" or r["competition_id"] == competition)
        ]
        if not rows:
            raise ValueError("该时间点没有可用训练数据")
        self.params = {}
        observations = []
        for r in rows:
            league = r["competition_id"]
            weight = 2 ** (
                -max(0, (asof - parse_date(r["kickoff"])).total_seconds() / 86400)
                / self.half_life
            )
            for home in (True, False):
                own, opp = (
                    (r["home_id"], r["away_id"])
                    if home
                    else (r["away_id"], r["home_id"])
                )
                keys = ["base", f"attack:{league}:{own}", f"defence:{league}:{opp}"]
                if home:
                    keys.append("home")
                if self.mode == "hierarchical":
                    keys.append("league:" + league)
                observations.append(
                    (keys, r["home_goals" if home else "away_goals"], weight)
                )
                for key in keys:
                    self.params.setdefault(key, 0.0)
        self.params["base"] = math.log(
            max(
                0.2,
                sum(y * w for _, y, w in observations)
                / sum(w for _, _, w in observations),
            )
        )
        for _ in range(80):
            gradient = {k: -0.7 * v for k, v in self.params.items()}
            curvature = {k: 0.7 for k in self.params}
            for keys, goals, weight in observations:
                rate = math.exp(max(-3, min(2.3, sum(self.params[k] for k in keys))))
                for key in keys:
                    gradient[key] += weight * (goals - rate)
                    curvature[key] += weight * rate
            for key in self.params:
                self.params[key] += 0.15 * max(
                    -1, min(1, gradient[key] / curvature[key])
                )
        return self

    def predict(self, row):
        league = row["competition_id"]
        rates = []
        for home in (True, False):
            own, opp = (
                (row["home_id"], row["away_id"])
                if home
                else (row["away_id"], row["home_id"])
            )
            keys = ["base", f"attack:{league}:{own}", f"defence:{league}:{opp}"]
            if home:
                keys.append("home")
            if self.mode == "hierarchical":
                keys.append("league:" + league)
            rates.append(
                max(0.15, min(5.5, math.exp(sum(self.params.get(k, 0) for k in keys))))
            )
        return score_distribution(*rates)


def metrics(records, field="probabilities"):
    rows = [r for r in records if r.get(field)]
    if not rows:
        return {"count": 0}
    loss = sum(-math.log(max(1e-12, r[field][r["outcome"]])) for r in rows) / len(rows)
    brier = sum(
        sum((v - (i == r["outcome"])) ** 2 for i, v in enumerate(r[field]))
        for r in rows
    ) / len(rows)
    calibration = []
    for low in range(0, 10):
        cells = [
            (p, i == r["outcome"])
            for r in rows
            for i, p in enumerate(r[field])
            if low / 10 <= p < (low + 1) / 10 or low == 9 and p == 1
        ]
        if cells:
            calibration.append(
                {
                    "bin": low,
                    "count": len(cells),
                    "predicted": sum(x[0] for x in cells) / len(cells),
                    "observed": sum(x[1] for x in cells) / len(cells),
                }
            )
    return {
        "count": len(rows),
        "log_loss": loss,
        "brier": brier,
        "calibration": calibration,
    }


def walk_forward(all_rows, targets, mode, half_life):
    records = []
    models = {}
    for row in targets:
        # Weekly refits, with a cutoff at the start of that week, prevent same-round leakage.
        at = parse_date(row["kickoff"]) - timedelta(minutes=60)
        cutoff = (at - timedelta(days=at.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        key = (cutoff, row["competition_id"] if mode == "separate" else "shared")
        if key not in models:
            models[key] = GoalModel(mode, half_life).fit(
                all_rows, cutoff, row["competition_id"]
            )
        p = models[key].predict(row)
        odds = row.get("odds")
        market = None
        # A historical odds snapshot older than 3h at this horizon is not a comparable baseline.
        if (
            odds
            and 0 <= (at - parse_date(row["odds_observed_at"])).total_seconds() <= 10800
        ):
            total = sum(1 / v for v in odds.values())
            market = [1 / odds[k] / total for k in "hda"]
        h, a = row["home_goals"], row["away_goals"]
        records.append(
            {
                "id": row["id"],
                "competition_id": row["competition_id"],
                "season": row["season"],
                "stage": row["stage"],
                "asof": at.isoformat(),
                "trained_before": cutoff.isoformat(),
                "outcome": 0 if h > a else 2 if a > h else 1,
                "probabilities": p,
                "market": market,
            }
        )
    return records


def evaluate(data):
    rows = validate_dataset(data)
    for league in COMPETITIONS:
        selected = [r for r in rows if r["competition_id"] == league]
        if len(selected) < 300 or len({r["season"] for r in selected}) < 3:
            raise ValueError(f"{league} 至少需要3个赛季、300场完整赛果，才开放离线对比")
    dates = sorted({parse_date(r["kickoff"]).date() for r in rows})
    validation_start, test_start = (
        dates[int(len(dates) * 0.6)],
        dates[int(len(dates) * 0.8)],
    )
    validation = [
        r
        for r in rows
        if validation_start <= parse_date(r["kickoff"]).date() < test_start
    ]
    test = [r for r in rows if parse_date(r["kickoff"]).date() >= test_start]
    if any(
        sum(r["competition_id"] == c for r in part) < 30
        for c in COMPETITIONS
        for part in (validation, test)
    ):
        raise ValueError("按时间切分后，各联赛验证集和测试集均至少需要30场；请补充覆盖")
    comparison = []
    for mode in ("separate", "pooled", "hierarchical"):
        for half_life in (180, 365):
            records = walk_forward(rows, validation, mode, half_life)
            comparison.append(
                {"mode": mode, "half_life": half_life, "validation": metrics(records)}
            )
    winner = min(comparison, key=lambda c: c["validation"]["log_loss"])
    # Hyperparameters are locked before the untouched test. Weekly updates only use earlier results.
    records = walk_forward(rows, test, winner["mode"], winner["half_life"])
    paired = [r for r in records if r["market"]]
    return {
        "created_at": now_iso(),
        "dataset_digest": fingerprint(data),
        "status": "offline_only",
        "horizon_minutes": 60,
        "validation_start": str(validation_start),
        "test_start": str(test_start),
        "candidates": comparison,
        "selected": winner,
        "test": metrics(records),
        "paired_model": metrics(paired),
        "market_baseline": metrics(paired, "market"),
        "by_competition": {
            c: metrics([r for r in records if r["competition_id"] == c])
            for c in COMPETITIONS
        },
        "by_stage": {
            stage: metrics([r for r in records if r["stage"] == stage])
            for stage in sorted({r["stage"] for r in records})
        },
        "records": records,
        "production_enabled": False,
        "blockers": [
            "需独立前瞻验证；离线评估不自动启用生产模型",
            "赔率历史缺失的场次不评估市场增益或收益",
        ],
    }


def training_status():
    dataset_digest = None
    status = {
        "dataset_available": DATASET.exists(),
        "rows": 0,
        "leagues": [],
        "evaluation": None,
        "ready": False,
        "reason": "等待授权历史数据",
    }
    if DATASET.exists():
        try:
            raw = json.loads(DATASET.read_text(encoding="utf-8"))
            rows = validate_dataset(raw)
            dataset_digest = fingerprint(raw)
            status.update(
                rows=len(rows),
                leagues=sorted({r["competition_id"] for r in rows}),
                ready=True,
                reason="可执行离线评估",
            )
        except (ValueError, TypeError, KeyError, OSError) as exc:
            status["reason"] = str(exc)
    if REPORT.exists():
        try:
            report = json.loads(REPORT.read_text(encoding="utf-8"))
            if dataset_digest and report.get("dataset_digest") == dataset_digest:
                status["evaluation"] = {
                    k: v for k, v in report.items() if k != "records"
                }
            elif dataset_digest:
                status["reason"] = "数据已变更，需重新评估"
        except (ValueError, OSError):
            status["reason"] = "评估报告无法读取"
    return status


def run_evaluation():
    if not DATASET.exists():
        raise ValueError("请先配置授权历史数据 data/training/club_history.json")
    result = evaluate(json.loads(DATASET.read_text(encoding="utf-8")))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(REPORT)
    return {k: v for k, v in result.items() if k != "records"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="日职/韩职授权数据审计与时间回测")
    parser.add_argument("--evaluate", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run_evaluation() if args.evaluate else training_status(),
            ensure_ascii=False,
            indent=2,
        )
    )
