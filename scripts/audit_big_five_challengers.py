"""Recompute paired goal diagnostics, actual market coverage and rolling upset alarms."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date
import json
import numpy as np

from research_big_five_goal_challengers import OUT, ROOT, LEAGUES, sha, save_immutable
from research_big_five_market_features import SEASONS, build_sample, upset_monitor


def diagnostics(records):
    if not records:
        return {"n":0}
    goals=np.array([r["actual"] for r in records])
    total=goals.sum(axis=1)
    return {"n":len(records),"score_loss":float(np.mean([r["loss"] for r in records])),
        "first11":float(np.mean([r["mode"]==[1,1] for r in records])),
        "p11":float(np.mean([r["p11"] for r in records])),"actual11":float(np.mean((goals==1).all(axis=1))),
        "predicted_goals":np.mean([r["goals"] for r in records],axis=0).tolist(),
        "actual_goals":[float((total<=1).mean()),float(((total==2)|(total==3)).mean()),float((total>=4).mean())]}


def rolling_alarms(source, code):
    previous=next((OUT.parent/"backtests/market"/code).glob("*.json"))
    if sha(previous.read_bytes())!=previous.stem:
        raise ValueError("Invalid market source reference")
    receipt=json.loads(previous.read_bytes())["source_sha256"]
    new=json.loads((OUT/"asian-source"/(code+".json")).read_bytes())["sha256"]
    with TemporaryDirectory() as directory:
        scratch=Path(directory)
        for year, checksum in {**receipt,"2627":new}.items():
            path=source/f"{year}-{code}.csv" if year!="2627" else OUT/"asian-source"/(new+".csv")
            raw=path.read_bytes()
            if sha(raw)!=checksum:
                raise ValueError(path)
            (scratch/f"{year}-{code}.csv").write_bytes(raw)
        samples=build_sample(scratch,code,(*SEASONS,"2627"))
    samples=samples[samples.fixture.str[:10]<="2026-10-29"]
    out={}
    for year in ("2324","2425","2526","2627"):
        model=upset_monitor(samples,test_season=year)
        for method,field in (("model","predictions"),("market","market_probability")):
            pred=np.array(model[field])>=.20
            actual=np.array(model["outcomes"])
            model[method+"_alarms"]={"n":int(pred.sum()),"hits":int(actual[pred].sum()),"misses":int(actual[~pred].sum())}
        out[year]=model
    return out


def audit(directory):
    goals=json.loads((OUT/"goal-reports.json").read_bytes())
    lines=json.loads((OUT/"line-reports.json").read_bytes())
    out={}
    for cid,(_,_,code) in LEAGUES.items():
        gf,lf=ROOT/goals[cid],ROOT/lines[cid]
        if sha(gf.read_bytes())!=gf.stem or sha(lf.read_bytes())!=lf.stem.removeprefix("lines-"):
            raise ValueError("Research artifact changed")
        g,m=json.loads(gf.read_bytes()),json.loads(lf.read_bytes())
        if g["league"]!=cid or m["competition_id"]!=cid or m["goal_report"]!=gf.stem:
            raise ValueError("Research scope mismatch")
        result={"goal_report":goals[cid],"line_report":lines[cid],"validation":g["validation"],
            "model":g["selected"],"seasons":{},"forward":g["forward_archived"],"hhad":m["hhad"],"asian":m["asian"],
            "official_upset":{k:v for k,v in m["upset"].items() if k not in ("predictions","coef","intercept")}}
        for year,s in g["seasons"].items():
            selected={r["id"]:r for r in s["predictions"]}
            paired={}
            for group in ("all","balanced","strong"):
                base=[r for r in s["dc"] if group=="all" or
                      group=="balanced" and abs(r["had"][0]-r["had"][2])<.15 or
                      group=="strong" and abs(r["had"][0]-r["had"][2])>=.40]
                paired[group]={"control":diagnostics(base),"challenger":diagnostics([selected[r["id"]] for r in base])}
            result["seasons"][year]={"pairs":paired,
                "ablation":{k:v["mean_loss"] for k,v in s["variants"].items()},
                "xg_refs":sum(len(i["xg"]) for r in selected.values() for i in r["inputs"]),
                "verified_xg_refs":sum(i["xg_observed_by_asof"] for r in selected.values() for i in r["inputs"])}
        alarms=rolling_alarms(directory,code)
        payload=json.dumps(alarms,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(",",":")).encode()
        path=OUT/cid/("upsets-"+sha(payload)+".json")
        save_immutable(path,alarms)
        result["upset_rolling"]={k:{f:v[f] for f in ("test","observed","model_alarms","market_alarms","brier_model","brier_market")} for k,v in alarms.items()}
        out[cid]=result
    (OUT/"audit.json").write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=False),encoding="utf8")


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("csv_directory",type=Path)
    audit(parser.parse_args().csv_directory)
