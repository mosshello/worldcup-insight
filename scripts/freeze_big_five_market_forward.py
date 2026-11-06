"""Capture available public prices for existing, immutable future research fixtures."""
import json
from datetime import datetime, timezone, timedelta
import math
import numpy as np

from big_five_challenger_sources import SPORT, fetch
from research_big_five_actual_lines import quote
from research_big_five_score_prices import quote as category_quote, football
from research_big_five_goal_challengers import OUT, ROOT, LEAGUES, receipt_data, grid, parse_date, save_immutable, sha
from worldcup_mvp.console.sources import EXPECTED_OPTIONS


def freeze():
    fetch("forward-sporttery-schedule", SPORT+"getMatchListV1.qry?clientCode=3001")
    schedule, schedule_meta=receipt_data("forward-sporttery-schedule")
    games=[m for d in schedule["value"]["matchInfoList"] for m in d["subMatchList"]]
    lines=json.loads((OUT/"line-reports.json").read_bytes())
    scores=json.loads((OUT/"score-market-reports.json").read_bytes())
    for cid,(_,lid,_) in LEAGUES.items():
        line=json.loads((ROOT/lines[cid]).read_bytes());score=json.loads((ROOT/scores[cid]).read_bytes())
        mapping=line["official_mapping"]["accepted"]
        count=0
        for f in sorted((OUT/cid/"forward").glob("*.json")):
            dest=OUT/cid/"forward-markets"/f.name
            if dest.exists():
                count+=1
                continue
            forecast=json.loads(f.read_bytes());row=forecast["fixture"];kick=parse_date(row["kickoff"])
            matches=[g for g in games if str(g["leagueId"])==str(lid)
                and mapping.get(str(g["homeTeamId"]))==row["home_id"] and mapping.get(str(g["awayTeamId"]))==row["away_id"]
                and parse_date(g["matchDate"]+"T"+g["matchTime"]+"+08:00")==kick and g.get("matchStatus")=="Selling"]
            if len(matches)!=1 or datetime.now(timezone.utc)>=kick-timedelta(hours=1):
                continue
            g=matches[0];mid=str(g["matchId"]);key="forward-price-"+mid
            fetch(key,SPORT+"getFixedBonusV1.qry?clientCode=3001&matchId="+mid)
            data,meta=receipt_data(key);now=datetime.now(timezone.utc)
            if now>=kick-timedelta(hours=1):
                continue
            h=data["value"].get("oddsHistory",{})
            if any(str(h.get(k))!=str(g[k]) for k in ("leagueId","homeTeamId","awayTeamId","matchId")):
                continue
            mat=grid(forecast["model"],forecast["inputs"])
            entries={}
            for k in ("ttg","crs"):
                q=category_quote(h.get(k+"List",[]),list(EXPECTED_OPTIONS[k]),now)
                if q:
                    w=score["metrics"][k]["football_weight"];p=football(mat,k)
                    entries[k]={"quote":q,"football_weight":w,"keys":list(EXPECTED_OPTIONS[k]),
                                "blend":(w*np.asarray(p)+(1-w)*np.asarray(q["p"])).tolist()}
            hh=quote(h.get("hhadList",[]),now)
            if hh:
                w=line["hhad"]["football_weight"];delta=np.subtract.outer(np.arange(len(mat)),np.arange(len(mat)))+hh["line"]
                p=np.array([mat[delta>0].sum(),mat[delta==0].sum(),mat[delta<0].sum()])
                entries["hhad"]={"quote":hh,"football_weight":w,"keys":["h","d","a"],
                                 "blend":(w*p+(1-w)*np.asarray(hh["p"])).tolist()}
            had=quote(h.get("hadList",[]),now)
            if had and "coef" in line["upset"]:
                p=had["p"];weak=2 if p[0]>p[2] else 0
                if p[2-weak]>=.60:
                    x=[p[weak],p[2-weak],forecast["probabilities"]["had"][weak],int(weak==0)]
                    z=np.dot(line["upset"]["coef"][0],x)+line["upset"]["intercept"][0]
                    pp=1/(1+math.exp(-z))
                    entries["upset"]={"quote":had,"underdog":weak,"probability":pp,"alarm":bool(pp>=.20)}
            if entries:
                save_immutable(dest,{"cid":cid,"official_id":mid,"forecast_sha256":sha(f.read_bytes()),
                    "schedule_sha256":schedule_meta["sha256"],"detail_sha256":meta["sha256"],"captured_at":now.isoformat(),
                    "line_report":lines[cid],"score_report":scores[cid],"markets":entries})
                count+=1
        print(cid,count,flush=True)


if __name__=="__main__":
    freeze()
