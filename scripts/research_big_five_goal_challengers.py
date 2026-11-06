"""Frozen, local-research score distributions and pregame observations for five leagues."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import json
import math
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from scipy.stats import nbinom, poisson
from sklearn.linear_model import PoissonRegressor
from threadpoolctl import threadpool_limits

from big_five_challenger_sources import LEAGUES, OUT, sha
from run_big_five_backtests import DATASETS, ROOT
from worldcup_mvp.console import model_registry as registry
from worldcup_mvp.console.sources import parse_date
from worldcup_mvp.console.training import GoalModel
from worldcup_mvp.console.football_markets import score_grid

BASE = ["home", "gf", "ga", "opp_gf", "opp_ga"]
EXTRA = {"xg": ["xgf", "xga", "opp_xgf", "opp_xga", "xg_n", "opp_xg_n"],
         "rest": ["rest", "opp_rest"], "minutes": ["minute_share", "opp_minute_share"]}
VERSIONS = {"poisson": [], "nb": [], **{k: v for k, v in EXTRA.items()},
            "all": sum(EXTRA.values(), [])}
PROTOCOL = ROOT / "docs/reports/2026-11-06-five-league-challenger-protocol.md"


def save_immutable(path, value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError(f"Immutable research record changed: {path}")
    else:
        with path.open("xb") as f:
            f.write(raw)
    return sha(raw)


def receipt_data(key):
    directory = OUT / "sources"
    meta = json.loads((directory / (key + ".json")).read_bytes())
    raw = gzip.decompress((directory / (meta["sha256"] + ".gz")).read_bytes())
    if sha(raw) != meta["sha256"]:
        raise ValueError(key)
    return json.loads(raw), meta


def minutes(cid, slug):
    directory = OUT.parent / "club-personnel"
    bind = json.loads((directory / "catalog-bindings.json").read_bytes())[slug]
    raw = (ROOT / bind["audit_file"]).read_bytes()
    if sha(raw) != bind["audit_sha256"] or bind["competition_id"] != cid:
        raise ValueError("Personnel binding mismatch")
    info = json.loads(raw)["minutes_file"]
    path = directory / slug / info["file"]
    if sha(path.read_bytes()) != info["sha256"]:
        raise ValueError("Minute file mismatch")
    grouped = defaultdict(list)
    for r in pq.read_table(path, columns=["competition_id", "fixture_id", "espn_team_id", "minutes"]).to_pylist():
        if r["competition_id"] != cid:
            raise ValueError(cid)
        grouped[r["fixture_id"], r["espn_team_id"]].append(r["minutes"])
    return {k: sum(m*m for m in v)/sum(v)**2 for k, v in grouped.items()}, info["sha256"]


def xg_fact(row):
    for src in ("understat_archive", "understat_gallery_archive", "pitchradar_understat_display"):
        fact = row.get("postmatch_xg_sources", {}).get(src, {})
        pair = fact.get("xg")
        if pair and len(pair) == 2 and all(math.isfinite(x) and x >= 0 for x in pair):
            return pair, fact["source_receipt"].get("observed_at")
    return None, None


def team_features(history, kickoff, captured_at=None):
    # All past games that actually took place count toward league rest. Scores and
    # player data enter separately and only once their availability cutoff has passed.
    at = kickoff - timedelta(hours=1)
    if captured_at is not None:
        at = min(at, captured_at)
    prior = [r for r in history if r["time"] < at]
    recent = [r for r in prior if r["available"] < at][-10:]
    if len(recent) < 5:
        return None
    advanced = [r for r in recent if r["advanced_at"] < at]
    xg = [r for r in advanced if r["xg"] is not None]
    load = [r["load"] for r in advanced[-3:] if r["load"] is not None]
    vals = {"gf": (sum(r["gf"] for r in recent)+2.7)/(len(recent)+2),
            "ga": (sum(r["ga"] for r in recent)+2.7)/(len(recent)+2),
            "xgf": (sum(r["xg"][0] for r in xg)+2.7)/(len(xg)+2),
            "xga": (sum(r["xg"][1] for r in xg)+2.7)/(len(xg)+2), "xg_n": len(xg)/10,
            "rest": min(30., (kickoff-prior[-1]["time"]).total_seconds()/86400),
            "minute_share": 100 * (sum(load)/len(load)) if load else 0.}
    return vals, {"results": [r["id"] for r in recent], "xg": [r["id"] for r in xg],
                  "xg_observed_by_asof": sum(bool(r["observed"]) and parse_date(r["observed"]) < at for r in xg),
                  "minutes": [r["id"] for r in advanced[-3:] if r["load"] is not None],
                  "rest_from": prior[-1]["id"]}


def features(row, histories, captured_at=None):
    kickoff = parse_date(row["kickoff"])
    teams = [team_features(histories[row[s+"_id"]], kickoff, captured_at) for s in ("home", "away")]
    if any(t is None for t in teams):
        return None
    vectors = []
    for side in (0, 1):
        vectors.append(dict(teams[side][0], home=1-side, **{"opp_"+k: v for k,v in teams[1-side][0].items()}))
    return {"id": row["id"], "time": row["kickoff"], "vectors": vectors, "inputs": [t[1] for t in teams]}


def observations(rows, load):
    history, samples = defaultdict(list), []
    for r in sorted(rows, key=lambda row: (row["kickoff"], row["id"])):
        item = features(r, history)
        if item:
            item.update(goals=[r["home_goals"], r["away_goals"]], available=r["result_available_at"],
                        season=int(r["season"]))
            samples.append(item)
        pair, observed = xg_fact(r)
        at, ready = parse_date(r["kickoff"]), parse_date(r["result_available_at"])
        for i, side in enumerate(("home", "away")):
            team = r[side+"_id"]
            history[team].append({"id": r["id"], "time": at, "available": ready,
                "advanced_at": max(ready, at+timedelta(days=7)), "gf": r[side+"_goals"],
                "ga": r[("away" if i == 0 else "home")+"_goals"],
                "xg": [pair[i], pair[1-i]] if pair else None,
                "observed": observed, "load": load.get((r["id"], team))})
    return samples, history


def fit(samples, version, at):
    rows = [r for r in samples if parse_date(r["available"]) < at and parse_date(r["time"]) < at]
    cols = BASE + VERSIONS[version]
    x = np.array([[v[k] for k in cols] for r in rows for v in r["vectors"]])
    y = np.array([g for r in rows for g in r["goals"]])
    center, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-9] = 1.
    z = (x-center)/scale
    reg = PoissonRegressor(alpha=.08, max_iter=150).fit(z, y)
    mu = reg.predict(z)
    alpha = 0. if version == "poisson" else float(np.clip(np.sum((y-mu)**2-y)/np.sum(mu**2), 0., 1.))
    return {"version": version, "fields": cols, "mean": center.tolist(), "scale": scale.tolist(),
            "coef": reg.coef_.tolist(), "intercept": float(reg.intercept_), "dispersion": alpha,
            "cutoff": at.isoformat(), "train_count": len(rows), "train_ids_sha256": sha(json.dumps([r["id"] for r in rows]).encode())}


def grid(model, item):
    z = (np.array([[v[k] for k in model["fields"]] for v in item["vectors"]])-model["mean"])/model["scale"]
    mu = np.clip(np.exp(z @ model["coef"] + model["intercept"]), .05, 8.)
    a = model["dispersion"]
    goals = np.arange(64)[:, None]
    pn = poisson.pmf(goals, mu) if a < 1e-7 else nbinom.pmf(goals, 1/a, 1/(1+a*mu))
    mat = np.outer(pn[:, 0], pn[:, 1])
    if mat.sum() < .999 or not np.isfinite(mat).all():
        raise ValueError("Score tail exceeds protocol bound")
    return mat / mat.sum()


def distribution(mat):
    ix = np.indices(mat.shape)
    total = np.bincount((ix[0]+ix[1]).ravel(), weights=mat.ravel())
    return {"had": [float(np.tril(mat,-1).sum()), float(mat.trace()), float(np.triu(mat,1).sum())],
            "p11": float(mat[1,1]), "mode": [int(v) for v in np.unravel_index(mat.argmax(), mat.shape)],
            "goals": [float(total[:2].sum()), float(total[2:4].sum()), float(total[4:].sum())]}


def scored(model, rows):
    out = []
    for r in rows:
        mat = grid(model, r)
        h, a = r["goals"]
        out.append(dict(distribution(mat), id=r["id"], actual=[h,a], loss=-math.log(float(mat[h,a])),
                        inputs=r["inputs"]))
    return out


def utc(year):
    return datetime(year, 7, 1, tzinfo=timezone.utc)


def avg(rows):
    return float(np.mean([r["loss"] for r in rows]))


def run(cid):
    slug = LEAGUES[cid][0]
    rows = registry.dataset(cid, DATASETS[cid], admission=False)["matches"]
    load, receipt = minutes(cid, slug)
    samples, history = observations(rows, load)
    now = datetime.now(timezone.utc)
    history_end = datetime(2026,11,6,tzinfo=timezone.utc)
    val = [r for r in samples if r["season"] == 2024 and parse_date(r["available"]) < utc(2025)]
    ranking = {k: avg(scored(fit(samples,k,utc(2024)), val)) for k in VERSIONS}
    winner = min(ranking, key=ranking.get)
    report = {"dataset": DATASETS[cid], "league": cid, "minutes_sha256": receipt,
              "protocol_sha256": sha(PROTOCOL.read_bytes()), "validation": ranking, "validation_n": len(val),
              "selected": winner, "seasons": {}}
    fixed = json.loads((OUT.parent/"backtests/verified.json").read_bytes())[cid]
    root = OUT / cid
    for year in (2025, 2026):
        target = [r for r in samples if r["season"] == year and parse_date(r["available"]) < history_end]
        rid = fixed["runs"][f"{year}-dc-fit"]["id"]
        _, bundle, _, _ = registry.load_run(cid,rid)
        dc = GoalModel.from_dict(bundle["models"][cid])
        source = {r["id"]:r for r in rows}
        records = {}
        for k in VERSIONS:
            model = fit(samples,k,utc(year))
            pred = scored(model,target)
            records[k] = {"model": model, "mean_loss": avg(pred)}
            if k == winner:
                selected = pred
        compare = []
        for item in selected:
            row = source[item["id"]]
            if min(dc.team_counts.get(cid+":"+row[s+"_id"],0) for s in ("home","away")) < 5:
                continue
            mat = np.asarray(score_grid(*dc.rates(row),dc.rhos.get(cid,0)))
            h,a = item["actual"]
            compare.append(dict(distribution(mat),id=row["id"],actual=[h,a],loss=-math.log(float(mat[h,a]))))
        report["seasons"][str(year)] = {"variants": records, "predictions": selected, "dc": compare}
    # Real pregame observations use features frozen at capture time, including the
    # league calendar through the latest archived completed match. No future results.
    model = fit(samples,winner,now)
    observed = 0
    for day in range(6,10):
        schedule, meta = receipt_data(f"schedule-{cid}-{day}")
        for game in schedule.get("events",[]):
            path = root/"forward"/(game["id"]+".json")
            if path.exists():
                observed += 1
                continue
            kick = parse_date(game["date"])
            if game["status"]["type"]["state"] != "pre" or kick-timedelta(hours=1) <= now:
                continue
            comp = game["competitions"][0]
            teams = {t["homeAway"]:"espn:"+str(t["id"]) for t in comp["competitors"]}
            row = {"id":"espn:"+game["id"], "kickoff":game["date"], "home_id":teams["home"],"away_id":teams["away"]}
            # Do not expose a result with a simulated future release between capture and kickoff.
            item = features(row,history,now)
            if item is None:
                continue
            receipt_row = {"competition_id":cid,"dataset":DATASETS[cid],"fixture":row,"captured_at":now.isoformat(),
                "schedule":meta,"protocol":report["protocol_sha256"],"model":model,"inputs":item,
                "probabilities":distribution(grid(model,item)), "usage":"research_forward"}
            save_immutable(path,receipt_row)
            observed += 1
    report["forward_archived"] = observed
    encoded = json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",",":")).encode()
    path = root/(sha(encoded)+".json")
    save_immutable(path, report)
    print(cid,winner,observed,path,flush=True)
    return str(path.relative_to(ROOT))


if __name__ == "__main__":
    with threadpool_limits(limits=1):
        index = {cid: run(cid) for cid in LEAGUES}
    (OUT/"goal-reports.json").write_text(json.dumps(index,indent=2),encoding="utf8")
