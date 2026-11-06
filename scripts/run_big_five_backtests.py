"""Run the frozen November 2026 competition-only baseline protocol."""
from concurrent.futures import ProcessPoolExecutor
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from worldcup_mvp.console.model_tasks import ModelTasks
from worldcup_mvp.console.store import Store

DATASETS = {
    "eng.premier": "538e5e7364ff9880feee6d26723497cc93c082cf65be778f3db3428f87f0560a",
    "esp.laliga": "495b7add1aebe324225d0080546e70e1c95695166d63c15891eec0b28d87a956",
    "ger.bundesliga": "30531d7687a27b4b8947f82f1e4dde489e68d778ba16986552506889369e3e67",
    "ita.serie_a": "9da92be852806774e1c0fc111cf4537596acaa81db6563390d735566e977eed8",
    "fra.ligue1": "b5ee843ba576c4a20cc9047598b657b0e3578caaa55188d4d5c27c1fc1baefd4",
}
WINDOWS = (("2025-06-30", "2025-07-02", "2026-06-30"),
           ("2026-06-30", "2026-07-02", "2026-10-28"))


def run_competition(cid):
    tasks = ModelTasks(Store())
    tasks.reconcile(cid)
    digest = DATASETS[cid]
    def run(label, **body):
        body.update(dataset_digest=digest, request_key=f"big5-nov06-v1-{digest[:12]}-{label}")
        job = tasks.start(cid, body)
        while job["status"] in ("running", "queued"):
            time.sleep(1)
            with tasks.store.connect() as db:
                row = db.execute("SELECT payload FROM model_tasks WHERE id=?", (job["id"],)).fetchone()
            job = json.loads(row["payload"])
        if job["status"] != "completed":
            raise RuntimeError(f"{cid} {label} {job['id']}: {job.get('error')}")
        print(cid, label, job["id"], flush=True)
        return job["result"]

    for cutoff, start, end in WINDOWS:
        for algorithm, target in (("elo_logistic", "had"), ("dc", "full_time")):
            label = cutoff[:4] + "-" + algorithm
            params = dict(algorithm=algorithm, target=target, half_life=180)
            fit = run(label + "-fit", kind="fit", date_to=cutoff, **params)
            run(label + "-fixed", kind="evaluate", evaluation_mode="fixed", run_id=fit["run_id"],
                date_from=start, date_to=end, **params)
    run("dc-rolling", kind="evaluate", evaluation_mode="rolling", algorithm="dc",
        target="full_time", half_life=180, date_from=WINDOWS[0][1], date_to=WINDOWS[-1][2])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=range(1, 6), default=3)
    args = parser.parse_args()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(run_competition, DATASETS))
