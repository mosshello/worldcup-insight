"""Offline, point-in-time club evaluation. Never trains on tips or activates a model.

Input: licensed or explicitly authorized local-research manifest + regular-time results.
All deployment remains gated on a separately collected prospective evaluation.
"""

from __future__ import annotations
import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .store import fingerprint, now_iso
from .sources import parse_date
from .football_markets import score_grid, market_probabilities, outcome, score_key
from .catalog import catalog

MODEL_SCHEMA = "club-goals-v2"
MIN_ROWS, MIN_SEASONS, MIN_TEST = 300, 3, 30


def validate_dataset(data):
    if not isinstance(data, dict):
        raise ValueError("历史数据须为 JSON 对象")
    manifest = data.get("manifest", {})
    local_research = (manifest.get("usage_mode") == "local_research"
                      and manifest.get("local_training_authorized") is True
                      and bool(manifest.get("authorization_reference"))
                      and manifest.get("commercial_use_permitted") is False
                      and manifest.get("rights_status") == "unverified")
    if not local_research and (
        manifest.get("training_permitted") is not True
        or not manifest.get("license_reference")
        or not manifest.get("source_url")
    ):
        raise ValueError("缺少数据来源、训练授权声明或授权凭据说明")
    if not manifest.get("source_url"):
        raise ValueError("缺少数据来源")
    rows = data.get("matches", [])
    if not isinstance(rows, list) or not rows:
        raise ValueError("缺少历史比赛")
    known_competitions = {c["id"] for c in catalog()}
    seen = set()
    fixtures = set()
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
        if any(not isinstance(row[k], str) for k in ("id", "competition_id", "home_id", "away_id", "season", "stage")):
            raise ValueError("比赛、赛季和球队标识须为字符串")
        if (
            row["competition_id"] not in known_competitions
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
        fixture_key = (row["competition_id"], row["home_id"], row["away_id"], start)
        if fixture_key in fixtures:
            raise ValueError("同一比赛被重复收录")
        fixtures.add(fixture_key)
        if any(row.get(k) is not None for k in ("home_half_goals", "away_half_goals")):
            for side in ("home", "away"):
                value = row.get(side + "_half_goals")
                if type(value) is not int or not 0 <= value <= row[side + "_goals"]:
                    raise ValueError("半场比分须完整且不超过常规时间比分")
        if row.get("handicap") is not None and (type(row["handicap"]) is not int or abs(row["handicap"]) > 20):
            raise ValueError("历史让球须为体彩整数让球")
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
    mappings = set()
    teams = {(r["competition_id"], r[s]) for r in rows for s in ("home_id", "away_id")}
    for mapping in data.get("team_mappings", []):
        key = (mapping.get("competition_id"), mapping.get("provider"), mapping.get("provider_id"))
        if key in mappings or any(not isinstance(v, str) or not v for v in key) or (key[0], mapping.get("team_id")) not in teams:
            raise ValueError("球队映射重复或无法对应历史球队")
        mappings.add(key)
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

    def __init__(self, mode="separate", half_life=365, kind="poisson"):
        if mode not in ("separate", "pooled", "hierarchical") or kind not in ("poisson", "dc"):
            raise ValueError("模型类型无效")
        if not isinstance(half_life, (int, float)) or not math.isfinite(half_life) or half_life <= 0:
            raise ValueError("时间衰减参数无效")
        self.mode, self.half_life, self.kind = mode, half_life, kind

    def fit(self, rows, asof, competition):
        rows = [
            r
            for r in rows
            if parse_date(r["result_available_at"]) < asof
            and (self.mode != "separate" or r["competition_id"] == competition)
        ]
        if not rows:
            raise ValueError("该时间点没有可用训练数据")
        self.trained_before = asof.isoformat()
        self.sample_count = len(rows)
        self.team_counts = {}
        for r in rows:
            for side in ("home_id", "away_id"):
                key = r["competition_id"] + ":" + r[side]
                self.team_counts[key] = self.team_counts.get(key, 0) + 1
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
        self.rhos, self.half_shares, self.half_counts = {}, {}, {}
        for league in sorted({r["competition_id"] for r in rows}):
            league_rows = [r for r in rows if r["competition_id"] == league]
            if self.kind == "dc":
                observations_dc = [(r, self.rates(r), 2 ** (-(asof-parse_date(r["kickoff"])).total_seconds()/86400/self.half_life)) for r in league_rows]
                # Profile the low-score correction after fitting regularised Poisson rates.
                # This is a two-stage estimator, not joint Dixon-Coles maximum likelihood.
                def objective(rho):
                    score = -2*rho*rho
                    for r, (h, a), weight in observations_dc:
                        factors = {(0, 0): 1-h*a*rho, (0, 1): 1+h*rho, (1, 0): 1+a*rho, (1, 1): 1-rho}
                        if min(factors.values()) <= 0:
                            return -math.inf
                        score += weight*math.log(factors.get((r["home_goals"], r["away_goals"]), 1))
                    return score
                self.rhos[league] = max((i/500 for i in range(-100, 101)), key=objective)
            half = [r for r in league_rows if r.get("home_half_goals") is not None]
            self.half_counts[league] = len(half)
            if len(half) >= 100 and all(sum(r[side+"_goals"] for r in half) >= 30 for side in ("home", "away")):
                weights = [2 ** (-(asof-parse_date(r["kickoff"])).total_seconds()/86400/self.half_life) for r in half]
                self.half_shares[league] = [(1+sum(r[side+"_half_goals"]*w for r, w in zip(half, weights)))/(2+sum(r[side+"_goals"]*w for r, w in zip(half, weights))) for side in ("home", "away")]
        return self

    def rates(self, row):
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
        return rates

    def forecast(self, row, handicap=None):
        rates = self.rates(row)
        grid = score_grid(*rates, self.rhos.get(row["competition_id"], 0))
        markets = market_probabilities(grid, handicap, self.half_shares.get(row["competition_id"]))
        return {"expected_goals": rates, "markets": markets,
                "half_samples": self.half_counts.get(row["competition_id"], 0),
                "half_method": "conditional-binomial" if "hafu" in markets else None}

    def predict(self, row):
        values = self.forecast(row)["markets"]["had"]
        return [values[k] for k in "hda"]

    def to_dict(self):
        return {"schema": MODEL_SCHEMA,
                "supported_markets": ["had", "hhad", "ttg", "crs"] + (["hafu"] if self.half_shares else []),
                **{k: getattr(self, k) for k in
                ("mode", "half_life", "kind", "params", "rhos", "half_shares", "half_counts", "team_counts", "trained_before", "sample_count")}}

    @classmethod
    def from_dict(cls, value):
        if value.get("schema") != MODEL_SCHEMA:
            raise ValueError("模型版本不兼容")
        model = cls(value["mode"], value["half_life"], value["kind"])
        for key in ("params", "rhos", "half_shares", "half_counts", "team_counts", "trained_before", "sample_count"):
            setattr(model, key, value[key])
        if not model.params or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in model.params.values()):
            raise ValueError("模型参数损坏")
        if parse_date(model.trained_before).tzinfo is None:
            raise ValueError("模型时间无效")
        return model


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


def walk_forward(all_rows, targets, mode, half_life, kind="poisson"):
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
            models[key] = GoalModel(mode, half_life, kind).fit(
                all_rows, cutoff, row["competition_id"]
            )
        forecast = models[key].forecast(row, row.get("handicap"))
        p = [forecast["markets"]["had"][k] for k in "hda"]
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
                "markets": forecast["markets"],
                "actual_markets": {
                    "had": outcome(h, a), "ttg": f"s{min(h+a, 7)}", "crs": score_key(h, a),
                    **({"hhad": outcome(h+row["handicap"], a)} if row.get("handicap") is not None else {}),
                    **({"hafu": outcome(row["home_half_goals"], row["away_half_goals"])+outcome(h, a)} if row.get("home_half_goals") is not None else {}),
                },
            }
        )
    return records


def split_dataset(rows):
    leagues = sorted({r["competition_id"] for r in rows})
    for league in leagues:
        selected = [r for r in rows if r["competition_id"] == league]
        if len(selected) < MIN_ROWS or len({r["season"] for r in selected}) < MIN_SEASONS:
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
        sum(r["competition_id"] == c for r in part) < MIN_TEST
        for c in leagues
        for part in (validation, test)
    ):
        raise ValueError("按时间切分后，各联赛验证集和测试集均至少需要30场；请补充覆盖")
    return leagues, validation_start, test_start, validation, test


def market_metrics(records):
    from .sources import EXPECTED_OPTIONS
    result = {}
    for code, keys in EXPECTED_OPTIONS.items():
        sample = [{"probabilities": [r["markets"][code][k] for k in keys], "outcome": keys.index(r["actual_markets"][code])}
                  for r in records if code in r.get("markets", {}) and code in r.get("actual_markets", {})]
        result[code] = metrics(sample)
    return result


def evaluate(data, progress=None, modes=("separate", "pooled", "hierarchical")):
    if modes == ("separate",):
        from .model_evaluation import evaluate as evaluate_candidates
        return evaluate_candidates(data, progress)
    rows = validate_dataset(data)
    leagues, validation_start, test_start, validation, test = split_dataset(rows)
    comparison = []
    for mode in modes:
        for half_life in (180, 365):
            for kind in ("poisson", "dc"):
                records = walk_forward(rows, validation, mode, half_life, kind)
                comparison.append({"mode": mode, "half_life": half_life, "kind": kind,
                    "validation": metrics(records), "by_competition": {c: metrics([r for r in records if r["competition_id"] == c]) for c in leagues}})
                if progress:
                    progress({"phase": "validation", "completed": len(comparison), "total": len(modes) * 4})
    winner = min(comparison, key=lambda c: c["validation"]["log_loss"])
    selected = {c: min(comparison, key=lambda candidate: candidate["by_competition"][c]["log_loss"]) for c in leagues}
    # Hyperparameters are locked before the untouched test. Weekly updates only use earlier results.
    records = []
    for league, config in selected.items():
        records.extend(walk_forward(rows, [r for r in test if r["competition_id"] == league], config["mode"], config["half_life"], config["kind"]))
        if progress:
            progress({"phase": "test", "competition": league})
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
        "selected_by_competition": selected,
        "model_schema": MODEL_SCHEMA,
        "markets": market_metrics(records),
        "test": metrics(records),
        "paired_model": metrics(paired),
        "market_baseline": metrics(paired, "market"),
        "by_competition": {
            c: metrics([r for r in records if r["competition_id"] == c])
            for c in leagues
        },
        "market_metrics_by_competition": {c: market_metrics([r for r in records if r["competition_id"] == c]) for c in leagues},
        "paired_by_competition": {c: {"model": metrics([r for r in paired if r["competition_id"] == c]),
                                      "market": metrics([r for r in paired if r["competition_id"] == c], "market")} for c in leagues},
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


def reference_checks(report):
    """A descriptive offline check, never a production-release criterion."""
    reference = {"log_loss": math.log(3), "brier": 2 / 3}
    return {league: {"label": "优于均匀基线" if metrics.get("count", 0)
                    and metrics.get("log_loss", float("inf")) < reference["log_loss"]
                    and metrics.get("brier", float("inf")) < reference["brier"]
                    else "未超过均匀基线", "reference": reference,
                    "test_count": metrics.get("count", 0)}
            for league, metrics in report.get("by_competition", {}).items()}


def atomic_json(path, value):
    from uuid import uuid4
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix("." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def fit_bundle(data, report, now=None):
    if report.get("dataset_digest") != fingerprint(data) or report.get("model_schema") != MODEL_SCHEMA:
        raise ValueError("回测报告与历史数据不匹配")
    rows = validate_dataset(data)
    at = now or datetime.now(timezone.utc)
    fit_range = report.get("training_range", {})
    if fit_range.get("date_from"):
        rows = [r for r in rows if parse_date(r["kickoff"]) >= parse_date(fit_range["date_from"])]
    models = {}
    for league, config in report["selected_by_competition"].items():
        if config['kind'] in ('poisson', 'dc'):
            model = GoalModel(config["mode"], config["half_life"], config["kind"]).fit(rows, at, league)
        else:
            from .model_methods import fit
            model = fit(config, rows, at, league)
        models[league] = model.to_dict()
    bundle = {"schema": MODEL_SCHEMA, "dataset_digest": fingerprint(data),
              "created_at": at.isoformat(), "models": models, "team_mappings": data.get("team_mappings", []),
              "purpose": "prospective_shadow", "production_enabled": False,
              "usage_mode": data.get("manifest", {}).get("usage_mode", "licensed")}
    bundle["id"] = MODEL_SCHEMA + ":" + fingerprint(bundle)[:16]
    bundle["digest"] = fingerprint(bundle)
    return bundle


def shadow_forecast(match, detail, now=None, artifacts=None, allow_network=True):
    """Prospective candidate only. Never silently replaces the market baseline."""
    from .catalog import classify
    from .football_markets import assessment
    now = now or datetime.now(timezone.utc)
    unavailable = lambda reason: {"status": "unavailable", "reason": reason, "markets": []}
    if artifacts is None:
        return unavailable("须显式指定本赛事模型及数据版本")
    try:
        bundle, report, data = artifacts
        if bundle["dataset_digest"] != fingerprint(data) or report.get("model_id") != bundle["id"]:
            return unavailable("历史数据或模型已变更，需重新训练")
        league = classify(match)["id"]
        if league not in bundle["models"]:
            return unavailable("该赛事尚未训练")
        if bundle['models'][league].get('adapter'):
            from .model_methods import restore
            model = restore(bundle['models'][league])
        else:
            model = GoalModel.from_dict(bundle["models"][league])
        trained = parse_date(model.trained_before)
        if trained > now or parse_date(match["kickoff"]) <= now:
            return unavailable("模型或比赛时间不符合赛前条件")
        if (now-trained).total_seconds() > 30*86400:
            return unavailable("模型超过30天未更新")
        mapping = {r["provider_id"]: r["team_id"] for r in bundle["team_mappings"]
                   if r["competition_id"] == league and r["provider"] == "sporttery"}
        identity_source = "training_manifest"
        identity_evidence = None
        if any(str(match.get(s)) not in mapping for s in ("home_id", "away_id")):
            from .readiness import fixture_key
            evidence = detail.get("context", {}).get("espn_identity") or {}
            spec = next((item for item in catalog() if item['id'] == league), {})
            if (evidence.get('fixture') == fixture_key(match) and evidence.get('provider') == 'espn'
                and evidence.get('competition_slug') == spec.get('providers', {}).get('espn')
                and evidence.get('event_id') and evidence.get('source_url')
                and evidence.get('kickoff') and parse_date(evidence['kickoff']) == parse_date(match['kickoff'])
                and evidence.get('captured_at') and parse_date(evidence['captured_at']) <= now):
                espn = {str(r['provider_id']): r['team_id'] for r in bundle['team_mappings']
                        if r['competition_id'] == league and r['provider'] == 'espn'}
                teams = evidence.get('team_ids', {})
                if bundle.get('cold_start_policy',{}).get('identity_policy') == 'exact-espn-id-v1':
                    for team in teams.values():
                        if isinstance(team,str) and team.isdigit():
                            espn.setdefault(team,'espn:'+team)
                if all(str(teams.get(side)) in espn for side in ('home', 'away')):
                    for side in ('home', 'away'):
                        key, team = str(match[side+'_id']), espn[str(teams[side])]
                        if key in mapping and mapping[key] != team:
                            return unavailable('球队标识映射冲突')
                        mapping[key] = team
                    identity_source, identity_evidence = evidence['source_url'], evidence
        if any(str(match.get(s)) not in mapping for s in ("home_id", "away_id")):
            # A new Sporttery listing does not require retraining model parameters.
            if not allow_network:
                return unavailable("球队标识尚未映射")
            from .league_data import load_history, match_fixture
            from .sources import SourceError
            try:
                fixture = match_fixture(match, load_history(competition_id=league))
                for side in ("home", "away"):
                    key = str(match[side+"_id"])
                    if key in mapping and mapping[key] != fixture[side+"_id"]:
                        return unavailable("球队标识映射冲突")
                    mapping[key] = fixture[side+"_id"]
                identity_source = fixture["source_url"]
            except SourceError:
                return unavailable("球队标识尚未映射")
        row = {"competition_id": league, **{s: mapping[str(match[s])] for s in ("home_id", "away_id")}}
        counts = [model.team_counts.get(league+":"+row[s], 0) for s in ("home_id", "away_id")]
        cold_policy = bundle.get('cold_start_policy')
        is_cold = any(n < 5 for n in counts)
        cold_metadata = {}
        if is_cold:
            from .cold_start import allowed, validate_binding, uncertainty
            validate_binding(bundle, report)
            if not allowed(cold_policy, counts):
                return unavailable("球队历史样本不足5场，冷启动策略未通过本组评估")
            cold_metadata = {'cold_start_policy':cold_policy,'uncertainty':uncertainty(model,row)}
        pools = detail.get("markets", {})
        eligible = detail.get("quality", {}).get("eligible_markets", list(pools))
        line = pools.get("hhad", {}).get("line") if "hhad" in eligible else None
        forecast = model.forecast(row, line)
        views = [assessment(code, p, pools.get(code) if code in eligible else None, "model", True)
                 for code, p in forecast["markets"].items()]
        views.sort(key=lambda v: ("had", "hhad", "ttg", "crs", "hafu").index(v["code"]))
        return {"status": "shadow", "reason": "前瞻观察", "model_id": bundle["id"],
                "dataset_digest": bundle["dataset_digest"], "trained_before": model.trained_before,
                "kind": model.kind, "mode": model.mode, "sample_count": model.sample_count,
                "team_samples": counts,
                **cold_metadata,
                "usage_mode": bundle.get("usage_mode", "licensed"),
                "team_identity": {**row, "source": identity_source,
                                  "evidence": identity_evidence,
                                  "evidence_digest": fingerprint(identity_evidence) if identity_evidence else None},
                "reference_check": reference_checks(report).get(league),
                "expected_goals": forecast["expected_goals"], "half_samples": forecast["half_samples"],
                "half_method": forecast["half_method"], "markets": views,
                "unavailable_markets": {code: "所选模型不支持或所需输入不足" for code in ('had','hhad','ttg','crs','hafu') if code not in forecast['markets']}}
    except (ValueError, KeyError, TypeError, OSError, OverflowError):
        return unavailable("模型或历史数据校验失败")


if __name__ == "__main__":
    from . import model_registry as registry
    parser = argparse.ArgumentParser(description="独立赛事数据导入、训练与模型选用")
    parser.add_argument("--competition", required=True)
    parser.add_argument('--target',choices=('full_time','had'),default='full_time')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--evaluate", action="store_true")
    actions.add_argument("--import-file")
    actions.add_argument("--observe", help="选用已有运行标识；训练本身不选用模型")
    args = parser.parse_args()
    result = (registry.import_competition(json.loads(Path(args.import_file).read_text(encoding="utf-8-sig")), args.competition)
              if args.import_file else registry.train(args.competition, target=args.target) if args.evaluate
              else registry.select_observation(args.competition, args.observe) if args.observe else registry.status(args.competition))
    print(json.dumps(result, ensure_ascii=False, indent=2))
