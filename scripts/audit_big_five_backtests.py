"""Summarize the November baseline jobs and verify their individual prediction artifacts."""
import math
import json
from collections import Counter
from statistics import mean

from run_big_five_backtests import DATASETS, ROOT
from worldcup_mvp.console import model_registry as registry
from worldcup_mvp.console.model_methods import restore
from worldcup_mvp.console.football_markets import market_probabilities, outcome, score_grid
from worldcup_mvp.console.sources import parse_date
from worldcup_mvp.console.store import Store, fingerprint


def checked(value):
    if value["digest"] != fingerprint({k: v for k, v in value.items() if k != "digest"}):
        raise ValueError("Artifact checksum mismatch")


def concentration(records):
    if not records:
        return {"n": 0}
    score = "s01s01"
    groups = {"one_one": ("crs", {score}), "zero_one": ("ttg", {"s0", "s1"}),
              "two_three": ("ttg", {"s2", "s3"}), "four_plus": ("ttg", {"s4", "s5", "s6", "s7"})}
    output = {"n": len(records)}
    for group, (key, options) in groups.items():
        output[group] = {
            "mean_probability": mean(sum(r["markets"][key][option] for option in options) for r in records),
            "observed": mean(r["actual_markets"][key] in options for r in records),
            "mode_in_group": mean(max(r["markets"][key], key=r["markets"][key].get) in options for r in records),
        }
    return output


def audit(cid):
    root = registry.directory(cid)
    digest = DATASETS[cid]
    data = registry.dataset(cid, digest, admission=False)
    rows = {r["id"]: r for r in data["matches"]}
    with Store().connect() as db:
        jobs = [json.loads(r[0]) for r in db.execute(
            "SELECT payload FROM model_tasks WHERE competition_id=? AND request_key LIKE ?",
            (cid, f"big5-nov06-v1-{digest[:12]}-%"))]
    if len(jobs) != 9 or any(j["status"] != "completed" or j["dataset_digest"] != digest for j in jobs):
        raise ValueError(f"Batch incomplete: {cid}")
    result = {"dataset": digest, "dataset_matches": len(rows), "runs": {}, "evaluations": {}}
    for job in jobs:
        label = job["request_key"].split(digest[:12] + "-", 1)[1]
        artifact = job["result"]
        if job["kind"] == "fit":
            manifest, bundle, _, _ = registry.load_run(cid, artifact["run_id"])
            model = bundle["models"][cid]
            result["runs"][label] = {"id": manifest["run_id"], "task": job["id"],
                                     "cutoff": model["trained_before"], "rows": model["sample_count"]}
            continue
        rep = registry.read(root / "reports" / (artifact["report_id"] + ".json"))
        checked(rep)
        if (rep["dataset_digest"], rep["task_id"], rep["competition_id"], rep["digest"]) != (
                digest, job["id"], cid, artifact["report_digest"]):
            raise ValueError("Evaluation binding mismatch")
        records = rep["records"]
        predicted = {r["id"] for r in records}
        excluded = {r["id"] for r in rep["exclusions"]}
        if len(predicted) != len(records) or predicted & excluded or len(predicted | excluded) != rep["total_targets"]:
            raise ValueError("Coverage mismatch")
        for r in records:
            if r["competition_id"] != cid or parse_date(r["trained_before"]) > parse_date(r["asof"]) or min(r["team_samples"]) < 5:
                raise ValueError("Invalid evaluated prediction")
        item = {"id": rep["id"], "task": job["id"], "targets": rep["total_targets"], "eligible": len(records),
                "markets": {k: {m: v[m] for m in ("count", "log_loss", "brier") if m in v}
                            for k, v in rep["markets"].items()}}
        result["evaluations"][label] = item
        if label.endswith("fixed"):
            expected = result["runs"].get(label.replace("fixed", "fit"))
            if not expected:
                expected = {"id": job["request"]["run_id"]}
            if rep["run_id"] != expected["id"] or any(
                    r["artifact_ref"]["run_id"] != rep["run_id"] or r["artifact_ref"]["digest"] != rep["model_digest"]
                    for r in records):
                raise ValueError("Fixed prediction mismatch")
            continue
        item["by_season"] = {s: concentration([r for r in records if r["season"] == s])
                             for s in sorted({r["season"] for r in records})}
        item["distribution"] = concentration(records)
        folds = {}
        scenario = {-1: [], 1: []}
        baseline_losses = []
        for r in records:
            ref = r["artifact_ref"]
            if ref["competition_id"] != cid or ref["task_id"] != job["id"] or ref["dataset_digest"] != digest:
                raise ValueError("Fold binding mismatch")
            if ref["digest"] not in folds:
                fold = registry.read(root / "task_artifacts" / job["id"] / "folds" / (ref["digest"] + ".json"))
                checked(fold)
                at = parse_date(fold["cutoff"])
                eligible = [row for row in data["matches"] if max(parse_date(row["result_available_at"]),
                                                                     parse_date(row["kickoff"])) < at]
                by_id = {row["id"]: row for row in eligible}
                receipt = fold["input_receipt"]
                if (set(receipt["row_ids"]) != set(by_id) or len(receipt["row_ids"]) != len(by_id)
                        or fingerprint([by_id[i] for i in receipt["row_ids"]]) != receipt["digest"]
                        or fingerprint(fold["model"]) != ref["model_digest"] or fold["digest"] != ref["digest"]):
                    raise ValueError("Fold replay input mismatch")
                counts = Counter(outcome(row["home_goals"], row["away_goals"]) for row in eligible)
                folds[ref["digest"]] = (restore(fold["model"]), [(counts[k] + 1)/(len(eligible)+3) for k in "hda"])
            model, base = folds[ref["digest"]]
            row = rows[r["id"]]
            if parse_date(model.trained_before) != parse_date(r["trained_before"]):
                raise ValueError("Cutoff mismatch")
            grid = score_grid(*model.rates(row), model.rhos.get(cid, 0))
            for line, values in scenario.items():
                probs = market_probabilities(grid, line)["hhad"]
                actual = outcome(row["home_goals"] + line, row["away_goals"])
                values.append(-math.log(probs[actual]))
            baseline_losses.append(-math.log(base[r["outcome"]]))
        item["weekly_folds"] = len(folds)
        item["league_frequency_log_loss"] = mean(baseline_losses)
        item["scenario_handicap_log_loss"] = {str(line): mean(values) for line, values in scenario.items()}
    return result


if __name__ == "__main__":
    summary = {cid: audit(cid) for cid in DATASETS}
    path = ROOT / "data/research/big-five/2026-11-06/backtests/verified.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(path)
