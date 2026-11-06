"""Join verified official HHAD and Asian handicap archives to frozen league forecasts."""
import argparse
from collections import Counter, defaultdict
from datetime import timedelta
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from big_five_challenger_sources import LEAGUES, OUT, ROOT, sha
from research_big_five_goal_challengers import (grid, minutes, observations, parse_date,
                                               receipt_data, registry, save_immutable)


def identity(source, target):
    def signatures(rows):
        signs = defaultdict(set)
        for row in rows:
            for side in ("home","away"):
                signs[row[side]].add((row["date"], side))
        return signs
    a, b = signatures(source), signatures(target)
    mapping, evidence = {}, {}
    for name, days in a.items():
        scores = sorted(((len(days & others), team) for team, others in b.items()),reverse=True)
        (best, team), (second, _) = scores[:2]
        if best >= 5 and best >= 1.4*max(second,1) and best >= .45*len(days):
            mapping[name] = team
            evidence[name] = [team, best, second, len(days)]
    duplicates = {v for v,n in Counter(mapping.values()).items() if n>1}
    return {k:v for k,v in mapping.items() if v not in duplicates}, evidence


def matched(source, rows, offset_hours=0):
    def source_date(row):
        return (parse_date(row["kickoff"]) + timedelta(hours=offset_hours)).date()
    target = [dict(home=r["home_id"],away=r["away_id"],date=source_date(r)) for r in rows]
    mapping, evidence = identity(source,target)
    pairs = defaultdict(list)
    for r in rows:
        pairs[r["home_id"],r["away_id"]].append(r)
    found, problems = {}, Counter()
    for item in source:
        teams = [mapping.get(item[k]) for k in ("home","away")]
        choices = [r for r in pairs[tuple(teams)] if source_date(r)==item["date"]]
        if len(choices) != 1:
            problems["identity_or_date"] += 1
            continue
        r = choices[0]
        if item["goals"] != [r["home_goals"],r["away_goals"]]:
            problems["score_conflict"] += 1
            continue
        if r["id"] in found:
            raise ValueError("Duplicate matched fixture")
        found[r["id"]] = item
    return found, {"clubs":evidence,"accepted":mapping,"excluded":problems,"source_count":len(source),"matched":len(found)}


def quote(items, asof):
    valid = defaultdict(set)
    for row in items:
        try:
            time = parse_date(row["updateDate"]+"T"+row["updateTime"]+"+08:00")
            prices = tuple(float(row[k]) for k in ("h","d","a"))
            line = float(row.get("goalLine",0) or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if time <= asof and all(math.isfinite(p) and p>1 for p in prices) and line.is_integer():
            valid[time].add((int(line), *prices))
    if not valid or len(valid[max(valid)])!=1:
        return None
    line,*odds = next(iter(valid[max(valid)]))
    prob = 1/np.asarray(odds)
    return {"time":max(valid).isoformat(),"line":line,"odds":odds,"p":(prob/prob.sum()).tolist()}


def official(mid, match, row):
    data, meta = receipt_data("detail-"+str(mid))
    v = data["value"]
    h = v["oddsHistory"]
    if (str(v.get("isCancel"))!="0" or any(str(h.get(k))!=str(match.get(k)) for k in
        ("leagueId","matchId","homeTeamId","awayTeamId"))):
        return None
    cutoff = parse_date(row["kickoff"])-timedelta(hours=1)
    q = quote(h.get("hhadList",[]),cutoff)
    if not q:
        return None
    result = [e for e in v.get("matchResultList",[]) if e["code"]=="HHAD" and str(e.get("refundStatus"))=="0"
              and str(e.get("matchId"))==str(mid) and e.get("goalLine")==str(q["line"])]
    diff = row["home_goals"]-row["away_goals"]+q["line"]
    label = 0 if diff>0 else 1 if diff==0 else 2
    if len(result)!=1 or result[0]["combination"]!="HDA"[label]:
        return None
    return {"hh":q,"had":quote(h.get("hadList",[]),cutoff),"y":label,"receipt":meta["sha256"]}


def asian_class(diff, line):
    # +1 / 0 / -1 for each of two equal stake legs, ordered loss through win.
    if not math.isfinite(line) or line*4!=round(line*4):
        raise ValueError("Invalid Asian line")
    half = line*2
    return (np.sign(diff+math.floor(half)/2)+np.sign(diff+math.ceil(half)/2)+2).astype(int)


def csv_sources(directory, code):
    # Bind the 2025/26 file to the previous public market experiment.
    archive = next((OUT.parent/"backtests/market"/code).glob("*.json"))
    previous = json.loads(archive.read_bytes())
    if sha(archive.read_bytes())!=archive.stem:
        raise ValueError("Market manifest digest")
    result = []
    files = {}
    for season in ("2425","2526","2627"):
        if season == "2627":
            meta = json.loads((OUT/"asian-source"/(code+".json")).read_bytes())
            path = OUT/"asian-source"/(meta["sha256"]+".csv")
            expected = meta["sha256"]
        else:
            path = directory/f"{season}-{code}.csv"
            expected = previous["source_sha256"][season]
        if sha(path.read_bytes()) != expected:
            raise ValueError("Asian archive source mismatch")
        files[season] = expected
        df = pd.read_csv(path, encoding="cp1252")
        for r in df[df.FTR.isin(["H","D","A"])].itertuples():
            result.append({"home":r.HomeTeam,"away":r.AwayTeam,"date":pd.to_datetime(r.Date,dayfirst=True).date(),
                           "goals":[r.FTHG,r.FTAG],"line":r.AHh,"prices":[r.AvgAHH,r.AvgAHA],"source":expected})
    return result,files


def nll(rows, field):
    return float(np.mean([-math.log(max(1e-15,r[field][r["y"]])) for r in rows])) if rows else None


def upset_scores(records):
    sets = {}
    for year in (2025,2026):
        eligible = []
        for row in records:
            if row["season"] != year or not row["had"]:
                continue
            p = row["had"]["p"]
            weak = 2 if p[0]>p[2] else 0
            if p[2-weak] < .60:
                continue
            eligible.append((row["id"], [p[weak],p[2-weak],row["football_had"][weak],int(weak==0)],
                              int(row["actual_had"]==weak)))
        sets[year] = eligible
    train,test = sets.values()
    if not test or len({y for _,_,y in train})<2:
        return {"train":len(train),"test":len(test)}
    reg=LogisticRegression(C=1.,max_iter=500).fit([x for _,x,_ in train],[y for _,_,y in train])
    p=reg.predict_proba([x for _,x,_ in test])[:,1]
    y=np.asarray([y for _,_,y in test]); market=np.asarray([x[0] for _,x,_ in test])
    def score(values):
        alarms=values>=.20
        return {"brier":float(np.mean((values-y)**2)),"alarms":int(alarms.sum()),
                "hits":int(y[alarms].sum()),"misses":int(y[~alarms].sum())}
    return {"train":len(train),"test":len(test),"observed":int(y.sum()),"model":score(p),"market":score(market),
            "coef":reg.coef_.tolist(),"intercept":reg.intercept_.tolist(),
            "predictions":[[game,float(pp),int(yy)] for (game,_,_),pp,yy in zip(test,p,y)]}


def run(cid, report_path, index, directory):
    slug, sport_id, code = LEAGUES[cid]
    report = json.loads(report_path.read_bytes())
    if sha(report_path.read_bytes())!=report_path.stem:
        raise ValueError("Goal report digest")
    rows = registry.dataset(cid,report["dataset"],admission=False)["matches"]
    league = [r for r in index["matches"].values() if r["leagueId"]==sport_id]
    official_rows=[]
    for m in league:
        ft=str(m.get("sectionsNo999","")).split(":")
        if len(ft)==2 and all(x.isdigit() for x in ft):
            official_rows.append({"home":str(m["homeTeamId"]),"away":str(m["awayTeamId"]),
                                  "date":pd.to_datetime(m["matchDate"]).date(),"goals":list(map(int,ft)),"match":m})
    hh, h_info = matched(official_rows,rows,offset_hours=8)
    asian, files = csv_sources(directory,code)
    ah, a_info = matched(asian,rows)
    samples,_=observations(rows,minutes(cid,slug)[0])
    lookup={r["id"]:r for r in rows}
    sport, asia=[],[]
    for s in samples:
        year=str(s["season"])
        season=report["seasons"].get(year)
        if not season or parse_date(s["available"])>=parse_date("2026-11-06T00:00:00+00:00"):
            continue
        model=season["variants"][report["selected"]]["model"]
        mat=grid(model,s)
        diff=np.subtract.outer(np.arange(len(mat)),np.arange(len(mat)))
        h,a=s["goals"]
        fid=s["id"]
        if fid in hh:
            m=hh[fid]["match"];mid=str(m["matchId"])
            q=official(mid,m,lookup[fid]) if index["detail_status"].get(mid)=="ok" else None
            if q:
                ds=diff+q["hh"]["line"]
                sport.append({"id":fid,"official_id":mid,"season":s["season"],"asof":(parse_date(s["time"])-timedelta(hours=1)).isoformat(),
                    "football":[float(mat[ds>0].sum()),float(mat[ds==0].sum()),float(mat[ds<0].sum())],
                    "market":q["hh"]["p"],"actual_had":0 if h>a else 1 if h==a else 2,
                    "football_had":[float(mat[diff>0].sum()),float(mat[diff==0].sum()),float(mat[diff<0].sum())],**q})
        if fid in ah:
            m=ah[fid];line=m["line"]
            if not np.isfinite(line) or not all(np.isfinite(p) and p>1 for p in m["prices"]):
                continue
            cats=asian_class(diff,line)
            probs=np.bincount(cats.ravel(),weights=mat.ravel(),minlength=5).tolist()
            asia.append({"id":fid,"season":s["season"],"line":float(line),"odds":m["prices"],"source":m["source"],
                         "y":int(asian_class(np.array(h-a),line)),"football":probs})
    tr=[r for r in sport if r["season"]==2025];test=[r for r in sport if r["season"]==2026]
    if not tr or not test:
        raise ValueError(f"{cid}: insufficient paired official market")
    def mix(weight,r):
        return [weight*f+(1-weight)*m for f,m in zip(r["football"],r["market"])]
    weights=[i/10 for i in range(11)]
    weight=min(weights,key=lambda w: np.mean([-math.log(mix(w,r)[r["y"]]) for r in tr]))
    for row in test:
        row["blend"]=mix(weight,row)
    return {"competition_id":cid,"goal_report":report_path.stem,"official_mapping":h_info,"asian_mapping":a_info,
        "asian_sources":files,"official_rows":sport,"asian_rows":asia,"upset":upset_scores(sport),
        "hhad":{"train":len(tr),"test":len(test),"football_weight":weight,**{k:nll(test,k) for k in ("market","football","blend")}},
        "asian":{"n":len(asia),"test_n":sum(r["season"]==2026 for r in asia),"log_loss":nll(asia,"football")}}


if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("csv_directory",type=Path);directory=ap.parse_args().csv_directory
    paths=json.loads((OUT/"goal-reports.json").read_bytes())
    index=json.loads((OUT/"official-index.json").read_bytes())
    refs={}
    for cid,path in paths.items():
        value=run(cid,ROOT/path,index,directory)
        raw=json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(",",":")).encode()
        dest=OUT/cid/("lines-"+sha(raw)+".json")
        save_immutable(dest,value);refs[cid]=str(dest.relative_to(ROOT))
        print(cid,value["hhad"],value["asian"],flush=True)
    (OUT/"line-reports.json").write_text(json.dumps(refs,indent=2),encoding="utf8")
