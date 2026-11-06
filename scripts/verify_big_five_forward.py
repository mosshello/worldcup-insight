"""Replay frozen pregame observations, pair a pregame baseline, and score eligible final results."""
from datetime import datetime, timezone, timedelta
import json
import math
import numpy as np

from research_big_five_goal_challengers import (DATASETS, LEAGUES, OUT, PROTOCOL, distribution,
    grid, parse_date, receipt_data, registry, save_immutable, score_grid, sha, GoalModel)
from big_five_challenger_sources import SPORT, fetch
from worldcup_mvp.console.big_five_history import parse_event
from worldcup_mvp.console.football_markets import outcome, score_key


def market_results(archive, h, a, now):
    markets=archive["markets"]
    results={}
    for key, actual in (("ttg",f"s{min(h+a,7)}"),("crs",score_key(h,a))):
        if key in markets:
            v=markets[key];idx=v["keys"].index(actual)
            results[key]={"outcome":actual,"blend_loss":-math.log(v["blend"][idx]),
                          "market_loss":-math.log(v["quote"]["p"][idx])}
    if "upset" in markets:
        v=markets["upset"];hit=int("hda".index(outcome(h,a))==v["underdog"])
        results["upset"]={"occurred":hit,"alarm":v["alarm"],"brier":(v["probability"]-hit)**2}
    if "hhad" in markets:
        mid=archive["official_id"];key=f"forward-settlement-{mid}-{now:%Y%m%d}"
        try:
            fetch(key,SPORT+"getFixedBonusV1.qry?clientCode=3001&matchId="+mid)
            data,meta=receipt_data(key);value=data["value"]
            v=markets["hhad"];line=v["quote"]["line"];actual=outcome(h+line,a)
            rows=[r for r in value.get("matchResultList",[]) if r["code"]=="HHAD" and str(r["matchId"])==mid
                  and r.get("goalLine")==str(line) and r.get("refundStatus")=="0"]
            if (len(rows)==1 and rows[0]["combination"]==actual.upper() and
                    str(value.get("isCancel"))=="0" and str(value["oddsHistory"]["matchId"])==mid):
                idx=v["keys"].index(actual)
                results["hhad"]={"outcome":actual,"source":meta["sha256"],"blend_loss":-math.log(v["blend"][idx]),
                                 "market_loss":-math.log(v["quote"]["p"][idx])}
        except (KeyError,OSError,ValueError):
            pass
        if "hhad" not in results:
            results["hhad"]={"status":"official_settlement_unverified"}
    return results


def write_fixture_list():
    lines=["# 五大联赛 48 场赛前观察","",
           "北京时间。胜平负、比分和 4 球以上为足球候选模型的独立预测。让球列为采集时已核实的体彩主队盘口。","",
           "| 开球 | 联赛 | 主队 – 客队 | 主 / 平 / 客 | 首选比分 | 4 球以上 | 体彩让球 |",
           "|---|---|---|---:|---:|---:|---:|"]
    names={"eng.premier":"英超","esp.laliga":"西甲","ger.bundesliga":"德甲","ita.serie_a":"意甲","fra.ligue1":"法甲"}
    entries=[]
    for cid in LEAGUES:
        events={}
        for day in range(6,10):
            schedule,meta=receipt_data(f"schedule-{cid}-{day}")
            for event in schedule["events"]:
                events[str(event["id"])]=(event,meta["sha256"])
        for f in (OUT/cid/"forward").glob("*.json"):
            value=json.loads(f.read_bytes());evt,digest=events[f.stem];p=value["probabilities"]
            comp=evt["competitions"][0]
            teams={t["homeAway"]:t for t in comp["competitors"]}
            if (digest!=value["schedule"]["sha256"] or
                any("espn:"+str(teams[s]["id"])!=value["fixture"][s+"_id"] for s in ("home","away"))):
                raise ValueError("Forecast schedule binding")
            kickoff=parse_date(value["fixture"]["kickoff"]).astimezone(timezone(timedelta(hours=8)))
            pair=" – ".join(teams[s]["team"]["displayName"] for s in ("home","away"))
            odds="—";market=OUT/cid/"forward-markets"/f.name
            if market.exists():
                archived=json.loads(market.read_bytes())
                if archived["forecast_sha256"]!=sha(f.read_bytes()):
                    raise ValueError("Market reference mismatch")
                if "hhad" in archived["markets"]:
                    odds=f'{archived["markets"]["hhad"]["quote"]["line"]:+d}'
            link=f'[{pair}]({cid}/forward/{f.name})'
            probs=" / ".join(f"{x:.0%}" for x in p["had"])
            entries.append((kickoff,f'| {kickoff:%m-%d %H:%M} | {names[cid]} | {link} | {probs} | {p["mode"][0]}–{p["mode"][1]} | {p["goals"][2]:.1%} | {odds} |'))
    lines.extend(line for _,line in sorted(entries))
    (OUT/"forward-fixtures.md").write_text("\n".join(lines)+"\n",encoding="utf8")


def verify(cid, now):
    slug = LEAGUES[cid][0]
    root = OUT/cid
    rows = registry.dataset(cid, DATASETS[cid], admission=False)["matches"]
    base_models = {}
    counts = {"archived":0,"baseline":0,"pending":0,"scored":0,"unresolved":0}
    paired_losses=[]
    market_losses={}
    for file in sorted((root/"forward").glob("*.json")):
        data = json.loads(file.read_bytes())
        row = dict(data["fixture"], competition_id=cid)
        at, kick = parse_date(data["captured_at"]), parse_date(row["kickoff"])
        event_id = row["id"].removeprefix("espn:")
        if not (cid==data["competition_id"] and file.stem==event_id and at < kick-timedelta(hours=1)
                and parse_date(data["schedule"]["observed_at"])<=at and data["dataset"]==DATASETS[cid]
                and data["protocol"]==sha(PROTOCOL.read_bytes())):
            raise ValueError("Forward provenance")
        mat=grid(data["model"],data["inputs"])
        if distribution(mat)!=data["probabilities"]:
            raise ValueError("Frozen forecast failed replay")
        counts["archived"]+=1
        baseline_path=root/"forward-control"/file.name
        if not baseline_path.exists() and now < kick-timedelta(hours=1):
            if at not in base_models:
                model=GoalModel("separate",180,"dc").fit(rows,at,cid)
                base_models[at]=model
            model=base_models[at]
            params=model.to_dict()
            base_mat=np.array(score_grid(*model.rates(row),model.rhos.get(cid,0)))
            save_immutable(baseline_path,{"captured_at":now.isoformat(),"source":sha(file.read_bytes()),"params":params,
                                         "forecast":distribution(base_mat)})
        if baseline_path.exists():
            comp=json.loads(baseline_path.read_bytes())
            if comp["source"]!=sha(file.read_bytes()) or parse_date(comp["captured_at"])>=kick-timedelta(hours=1):
                raise ValueError("Forward control binding")
            baseline=GoalModel.from_dict(comp["params"])
            base_mat=np.array(score_grid(*baseline.rates(row),baseline.rhos.get(cid,0)))
            if distribution(base_mat)!=comp["forecast"]:
                raise ValueError("Baseline replay")
            counts["baseline"]+=1
        if now < kick+timedelta(days=7):
            counts["pending"]+=1
            continue
        day=kick.strftime("%Y%m%d")
        key=f"forward-result-{cid}-{day}-{now:%Y%m%d}"
        try:
            fetch(key,f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={day}")
            value,meta=receipt_data(key)
            event=next(e for e in value["events"] if str(e["id"])==event_id)
            final=parse_event(slug,event,meta["observed_at"])
            if (row["home_id"],row["away_id"],kick)!=(final["home_id"],final["away_id"],parse_date(final["kickoff"])):
                raise ValueError("changed fixture")
        except (StopIteration,KeyError,ValueError,OSError):
            counts["unresolved"]+=1
            continue
        h,a=final["home_goals"],final["away_goals"]
        score={"fixture":row["id"],"result":[h,a],"score_loss":-math.log(mat[h,a]),
               "control_loss":-math.log(base_mat[h,a]) if baseline_path.exists() else None,
               "forecast_sha256":sha(file.read_bytes()),"source_sha256":meta["sha256"]}
        market_path=root/"forward-markets"/file.name
        if market_path.exists():
            archive=json.loads(market_path.read_bytes())
            if archive["forecast_sha256"]!=score["forecast_sha256"] or parse_date(archive["captured_at"])>=kick-timedelta(hours=1):
                raise ValueError("Market archive binding invalid")
            score["markets"]=market_results(archive,h,a,now)
        raw=json.dumps(score,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(",",":")).encode()
        save_immutable(root/"forward-results"/file.stem/(sha(raw)+".json"),score)
        counts["scored"]+=1
        if score["control_loss"] is not None:
            paired_losses.append([score["score_loss"],score["control_loss"]])
        for key, result in score.get("markets",{}).items():
            if "blend_loss" in result:
                market_losses.setdefault(key,[]).append([result["blend_loss"],result["market_loss"]])
    if paired_losses:
        counts["paired_score_log_loss"] = dict(zip(("challenger","control"),np.mean(paired_losses,axis=0).tolist()))
    counts["market_log_loss"] = {k:dict(n=len(v),**dict(zip(("blend","market"),np.mean(v,axis=0).tolist())))
                                 for k,v in market_losses.items()}
    return counts


if __name__=="__main__":
    now=datetime.now(timezone.utc)
    print(json.dumps({cid:verify(cid,now) for cid in LEAGUES},indent=2))
    write_fixture_list()
