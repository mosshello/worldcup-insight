"""Preseason fixed-fit paired retrospective evaluation, not new prospective proof."""
import json, math, sys
from pathlib import Path
from datetime import datetime,timedelta,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console import model_registry,training
from worldcup_mvp.console.observation_models import fit,save,vectors,history_rows,FIELDS,ROOT
from worldcup_mvp.console.observation_features import instant
from worldcup_mvp.console.football_markets import score_grid,market_probabilities,outcome,score_key
from research_big_five_context import checked_lines

def metrics(rows,source,market):
    pairs=[(r[source][market],r['actual'][market]) for r in rows if market in r[source] and market in r['actual']]
    if not pairs:return {'n':0}
    return {'n':len(pairs),'accuracy':sum(max(p,key=p.get)==a for p,a in pairs)/len(pairs),
        'log_loss':sum(-math.log(max(1e-15,p[a])) for p,a in pairs)/len(pairs),
        'brier':sum(sum((v-(k==a))**2 for k,v in p.items()) for p,a in pairs)/len(pairs)}

def main():
    cutoff=datetime(2025,7,1,tzinfo=timezone.utc)
    report={'protocol':'fixed_preseason_2025_paired_v1','cutoff':cutoff.isoformat(),
        'limitations':['previously_explored_test_period','historical_xg_plus_7_day_assumption','no_historical_ai_predictions'], 'leagues':{}}
    for cid in ('eng.premier','esp.laliga','ger.bundesliga','ita.serie_a','fra.ligue1'):
        data=model_registry.dataset(cid,admission=False);rows=training.validate_dataset(data)
        a=fit(data['matches'],cid,cutoff);save(a)
        dc=training.GoalModel('separate',180,'dc').fit(rows,cutoff,cid)
        history=history_rows(data['matches'],cid)
        official={r['id']:r for r in checked_lines(cid)[0]['official_rows']}
        records=[];exclusions=[]
        for row in rows:
            if instant(row['kickoff'])<cutoff:continue
            at=instant(row['kickoff'])-timedelta(hours=1)
            try:
                vs,refs=vectors(history,row,at,historical=True)
                if any(dc.team_counts.get(cid+':'+row[s+'_id'],0)<5 for s in ('home','away')):raise ValueError('baseline_prior_support')
                mu=[max(.05,min(8,math.exp(a['intercept']+sum((v[k]-a['mean'][i])/a['scale'][i]*a['coef'][i] for i,k in enumerate(FIELDS))))) for v in vs]
                q=official.get(row['id'],{}).get('hh');line=q['line'] if q and instant(q['time'])<=at else None
                enhanced=market_probabilities(score_grid(*mu),line,a['half_shares']);base=dc.forecast(row,line)['markets']
                h,g=row['home_goals'],row['away_goals'];actual={'had':outcome(h,g),'ttg':f's{min(h+g,7)}','crs':score_key(h,g)}
                if line is not None:actual['hhad']=outcome(h+line,g)
                if row.get('home_half_goals') is not None and row.get('away_half_goals') is not None:actual['hafu']=outcome(row['home_half_goals'],row['away_half_goals'])+outcome(h,g)
                records.append({'id':row['id'],'as_of':at.isoformat(),'actual':actual,'baseline':base,'enhanced':enhanced,'history_refs':refs,'line':line})
            except ValueError as e:exclusions.append({'id':row['id'],'reason':str(e)})
        one={'candidate':a['digest'],'training_n':a['train_count'],'common_n':len(records),'excluded':exclusions,
             'metrics':{m:{s:metrics(records,s,m) for s in ('baseline','enhanced')} for m in ('had','hhad','ttg','crs','hafu')},'records':records}
        report['leagues'][cid]=one
        print(cid,one['common_n'],one['metrics']['had'],flush=True)
    dest=ROOT/'paired-evaluation-2025.json'
    dest.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 观察增强候选：固定赛前模型同场复核','',
      '训练截止2025-07-01；测试为该截止之后仓库已有正式比赛。各组使用相同比赛及赛前一小时信息截止。历史xG使用七天可用时间假设，测试期已经被研究；这不是全新前瞻成绩，也没有AI历史预测。候选未选入生产。','',
      '| 联赛 | 玩法 | 同场数 | 基础单选准确率 | 增强单选准确率 | 基础Log loss | 增强Log loss |',
      '|---|---|---:|---:|---:|---:|---:|']
    for cid,r in report['leagues'].items():
        for m,p in r['metrics'].items():
            b,e=p['baseline'],p['enhanced']
            if b['n']:lines.append(f"| {cid} | {m} | {b['n']} | {b['accuracy']:.2%} | {e['accuracy']:.2%} | {b['log_loss']:.4f} | {e['log_loss']:.4f} |")
    lines+=['','逐场概率、排除原因、比分与真实盘口线保存在 `data/research/observation-models/paired-evaluation-2025.json`。不按本次测试选权重或改变生产选用。']
    Path('docs/reports/2026-11-09-observation-candidate-evaluation.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

if __name__=='__main__':main()
