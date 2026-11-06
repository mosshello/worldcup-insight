"""Compare timed official TTG/CRS prices and football probabilities on matched fixtures."""
from collections import defaultdict
from functools import lru_cache
import json
import math
import numpy as np

from research_big_five_goal_challengers import (LEAGUES, OUT, ROOT, grid, minutes, observations,
    parse_date, receipt_data, registry, save_immutable, sha)
from worldcup_mvp.console.sources import EXPECTED_OPTIONS
from worldcup_mvp.console.football_markets import score_key


def quote(history, keys, at):
    by_time=defaultdict(set)
    for row in history:
        try:
            ts=parse_date(row["updateDate"]+"T"+row["updateTime"]+"+08:00")
            odds=tuple(float(row[k.replace("s1s","s-1s") if k.startswith("s1s") else k]) for k in keys)
        except (KeyError,ValueError,TypeError):
            continue
        if ts<=at and all(math.isfinite(v) and v>1 for v in odds):
            by_time[ts].add(odds)
    if not by_time or len(by_time[max(by_time)])!=1:
        return None
    odds=next(iter(by_time[max(by_time)]))
    p=1/np.asarray(odds);p/=p.sum()
    return {"time":max(by_time).isoformat(),"odds":odds,"p":p.tolist()}


@lru_cache(maxsize=2)
def cells(kind, n):
    keys=list(EXPECTED_OPTIONS[kind])
    return np.array([keys.index(score_key(h,a) if kind=="crs" else f"s{min(h+a,7)}")
                     for h in range(n) for a in range(n)])


def football(mat,kind):
    return np.bincount(cells(kind,len(mat)),weights=mat.ravel(),minlength=len(EXPECTED_OPTIONS[kind])).tolist()


def fit_score(rows,kind):
    train=[r for r in rows if r["season"]==2025];test=[r for r in rows if r["season"]==2026]
    if not train or not test:
        return {"train":len(train),"test":len(test)}
    def loss(items,w):
        return float(np.mean([-math.log(w*r["football"][r["y"]]+(1-w)*r["q"]["p"][r["y"]]) for r in items]))
    weight=min([i/10 for i in range(11)], key=lambda w:loss(train,w))
    summary={"train":len(train),"test":len(test),"football_weight":weight,"football":loss(test,1),
             "market":loss(test,0),"blend":loss(test,weight)}
    keys=list(EXPECTED_OPTIONS[kind])
    for label,w in [("football",1),("market",0),("blend",weight)]:
        p=np.array([w*np.asarray(r["football"])+(1-w)*np.asarray(r["q"]["p"]) for r in test])
        top=p.argmax(axis=1)
        indices=[keys.index("s01s01")] if kind=="crs" else [keys.index("s2"),keys.index("s3")]
        summary[label+"_mode_share"]=float(np.isin(top,indices).mean())
    return summary


def run(cid,goals,lines):
    slug=LEAGUES[cid][0]
    rows=registry.dataset(cid,goals["dataset"],admission=False)["matches"]
    samples={r["id"]:r for r in observations(rows,minutes(cid,slug)[0])[0]}
    pairs={"ttg":[],"crs":[]}
    for r in lines["official_rows"]:
        item=samples[r["id"]]
        s=goals["seasons"][str(r["season"])]
        model=s["variants"][goals["selected"]]["model"]
        mat=grid(model,item)
        detail,meta=receipt_data("detail-"+r["official_id"])
        if meta["sha256"]!=r["receipt"]:
            raise ValueError("Official source revision")
        history=detail["value"]["oddsHistory"]
        h,a=item["goals"]
        for k in pairs:
            keys=list(EXPECTED_OPTIONS[k])
            q=quote(history.get(k+"List",[]),keys,parse_date(r["asof"]))
            if not q:
                continue
            y=keys.index(score_key(h,a) if k=="crs" else f"s{min(h+a,7)}")
            pairs[k].append({"id":r["id"],"season":r["season"],"asof":r["asof"],"source_sha256":r["receipt"],
                             "y":y,"football":football(mat,k),"q":q})
    return {"league":cid,"protocol":sha((ROOT/"docs/reports/2026-11-06-five-league-score-market-protocol.md").read_bytes()),
            "goal_version":goals["dataset"],"metrics":{k:fit_score(v,k) for k,v in pairs.items()},"records":pairs}


if __name__=="__main__":
    goals=json.loads((OUT/"goal-reports.json").read_bytes());lines=json.loads((OUT/"line-reports.json").read_bytes());refs={}
    for cid in LEAGUES:
        result=run(cid,json.loads((ROOT/goals[cid]).read_bytes()),json.loads((ROOT/lines[cid]).read_bytes()))
        raw=json.dumps(result,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(",",":")).encode()
        path=OUT/cid/("score-market-"+sha(raw)+".json");save_immutable(path,result);refs[cid]=str(path.relative_to(ROOT))
        print(cid,result["metrics"],flush=True)
    (OUT/"score-market-reports.json").write_text(json.dumps(refs,indent=2),encoding="utf8")
