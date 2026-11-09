"""Fixed 2025 preseason personnel/coach ablation + independent full-data candidates."""
import json,math,sys,hashlib
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console import model_registry,training
from worldcup_mvp.console.observation_personnel import load_personnel_history,SLUGS
from worldcup_mvp.console.observation_club_load import load_club_history
from worldcup_mvp.console.observation_personnel_models import prepare_samples,fit,immutable,probabilities,OUT,VARIANTS
from worldcup_mvp.console.observation_features import instant
from worldcup_mvp.console.football_markets import outcome,score_key
from scripts.research_big_five_context import checked_lines


def metrics(records,source,market):
    pairs=[(r['predictions'][source][market],r['actual'][market]) for r in records if market in r['actual'] and market in r['predictions'][source]]
    if not pairs:return {'n':0}
    ece=[]
    for key in pairs[0][0]:
        error=0
        for b in range(10):
            selected=[(p[key],int(a==key)) for p,a in pairs if min(9,int(p[key]*10))==b]
            if selected:error+=abs(sum(v for v,y in selected)-sum(y for v,y in selected))/len(pairs)
        ece.append(error)
    return {'n':len(pairs),'accuracy':sum(max(p,key=p.get)==a for p,a in pairs)/len(pairs),
        'log_loss':sum(-math.log(max(1e-15,p[a])) for p,a in pairs)/len(pairs),
        'brier':sum(sum((v-int(k==a))**2 for k,v in p.items()) for p,a in pairs)/len(pairs),
        'macro_ece_10bins':sum(ece)/len(ece)}


def main():
    (OUT/'archive').mkdir(parents=True,exist_ok=True)
    for name in ('ablation-2025.json','candidate-index.json'):
        path=OUT/name
        if path.exists():
            raw=path.read_bytes();archive=OUT/'archive'/(path.stem+'-'+hashlib.sha256(raw).hexdigest()+'.json')
            if not archive.exists():archive.write_bytes(raw)
    cutoff=datetime(2025,7,1,tzinfo=timezone.utc);fullcut=datetime(2026,11,9,0,tzinfo=timezone.utc)
    report={'schema':'personnel-ablation-2025-v1','cutoff':cutoff.isoformat(),'full_fit_cutoff':fullcut.isoformat(),
        'limitations':['previously_explored_period','historical_plus_7day_assumption','past_usage_is_not_target_lineup','same_league_load_only'],
        'fixed_parameters':{'alpha':.08,'past_matches':5,'minimum_past_matches':3},'leagues':{}}
    index={}
    for cid in SLUGS:
        data=model_registry.dataset(cid,admission=False);rows=training.validate_dataset(data)
        person=load_personnel_history(cid)
        club=load_club_history(cid)
        samples,excluded,history=prepare_samples(data['matches'],person,club)
        history_ref=immutable({'competition_id':cid,'schema':'xg-history-v1','rows':history},OUT/cid/'inputs')
        person_ref=immutable(person,OUT/cid/'inputs')
        club_ref=immutable(club,OUT/cid/'inputs')
        fitted={};artifacts={};full={}
        for variant in VARIANTS:
            a=fit(samples,cid,cutoff,variant,history_ref,person_ref,club_ref)
            artifacts[variant]=immutable(a,OUT/cid/'models');fitted[variant]=a
            allfit=fit(samples,cid,fullcut,variant,history_ref,person_ref,club_ref)
            full[variant]={'path':immutable(allfit,OUT/cid/'models'),'train_count':allfit['train_count']}
        dc=training.GoalModel('separate',180,'dc').fit(rows,cutoff,cid)
        fixtures={r['id']:r for r in rows};official={r['id']:r for r in checked_lines(cid)[0]['official_rows']}
        records=[];evalexcluded=[]
        for sample in samples:
            if instant(sample['kickoff'])<cutoff:continue
            r=fixtures[sample['id']]
            if any(dc.team_counts.get(cid+':'+r[s+'_id'],0)<5 for s in ('home','away')):
                evalexcluded.append({'id':r['id'],'reason':'baseline_prior_support'});continue
            q=official.get(r['id'],{}).get('hh');line=q['line'] if q and instant(q['time'])<=instant(sample['as_of']) else None
            preds={v:probabilities(a,sample['vectors'],line) for v,a in fitted.items()}
            preds['dc']=dc.forecast(r,line)['markets']
            h,a=sample['goals'];actual={'had':outcome(h,a),'ttg':f's{min(h+a,7)}','crs':score_key(h,a)}
            if line is not None:actual['hhad']=outcome(h+line,a)
            if all(v is not None for v in sample['half_goals']):actual['hafu']=outcome(*sample['half_goals'])+outcome(h,a)
            records.append({'id':r['id'],'kickoff':r['kickoff'],'as_of':sample['as_of'],'actual':actual,
                'predictions':preds,'line':line,'personnel_refs':sample['personnel_refs'],'h2h_refs':sample['h2h_refs'],'club_load_refs':sample['club_load_refs']})
        sources=['dc',*VARIANTS]
        stats={m:{s:metrics(records,s,m) for s in sources} for m in ('had','hhad','ttg','crs','hafu')}
        result={'dataset_matches':len(rows),'eligible_samples':len(samples),'feature_exclusions':excluded,
            'minute_rows':sum(len(r['players']) for r in person['rows']),'personnel_side_exclusions':person['excluded'],
            'test_targets':sum(instant(r['kickoff'])>=cutoff for r in rows),'common_count':len(records),
            'evaluation_exclusions':evalexcluded,'retrospective_artifacts':artifacts,'full_candidates':full,'metrics':stats,'records':records,
            'club_load_inventory':{k:v for k,v in club.items() if k!='rows'},'club_load_team_matches':len(club['rows'])}
        report['leagues'][cid]=result;index[cid]=full
        OUT.mkdir(parents=True,exist_ok=True)
        (OUT/'progress.json').write_text(json.dumps({'completed_leagues':list(report['leagues']),'status':'running'},indent=2),encoding='utf-8')
        print(cid,len(records),json.dumps({k:round(v['log_loss'],5) for k,v in stats['had'].items()}),flush=True)
    for name,value in (('ablation-2025.json',report),('candidate-index.json',index)):
        raw=json.dumps(value,ensure_ascii=False,indent=2)
        archive=OUT/'archive'/(Path(name).stem+'-'+hashlib.sha256(raw.encode()).hexdigest()+'.json')
        if not archive.exists():archive.write_text(raw,encoding='utf-8')
        temporary=OUT/(name+'.tmp');temporary.write_text(raw,encoding='utf-8');temporary.replace(OUT/name)
    (OUT/'progress.json').write_text(json.dumps({'completed_leagues':list(report['leagues']),'status':'completed'},indent=2),encoding='utf-8')
    text=['# 历史人员使用与教练代理：五联赛独立消融','',
        '2025-07-01固定拟合，之后全部可评价目标作探索性留出；时期此前已研究，历史可用时间含七天假设。六候选均使用相同样本，固定alpha=0.08、最近五场人员使用、至少三场历史；未依据测试结果调参或选入生产。交锋版在人员与教练版外加入三年内最多五次交锋的连胜长度、平局占比、同主客胜率和样本数，检验额外信息，不以连胜或多平直接判定诱盘。负荷版进一步加入来源档案内已观察跨赛事俱乐部比赛数量，日期只有日级，历史按日结束后七天可用，未知具体开球时间及国家队个人分钟。','',
        '| 联赛 | 玩法 | 共同场数 | DC准确率 | 原xG准确率 | 人员准确率 | 教练准确率 | 两者准确率 | 加交锋准确率 | 加球队负荷准确率 | DC LL | 原xG LL | 人员 LL | 教练 LL | 两者 LL | 加交锋 LL | 加球队负荷 LL |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for cid,r in report['leagues'].items():
        for market,m in r['metrics'].items():
            if not m['dc']['n']:continue
            text.append('| '+cid+' | '+market+' | '+str(m['dc']['n'])+' | '+' | '.join(f"{m[s]['accuracy']:.2%}" for s in sources)+' | '+' | '.join(f"{m[s]['log_loss']:.4f}" for s in sources)+' |')
    text+=['','Brier、固定10桶校准、每场原始概率、真实让球线、特征引用和全部排除原因在`data/research/observation-personnel-models/ablation-2025.json`。全量候选入口为同目录`candidate-index.json`。',
        '','人员字段表示过去阵容延续、替补实际使用和轮换，教练字段只表示最近已观察教练变化；都不是本场预计首发、人员资格、替补实力或完整跨赛事负荷。实时调用必须满足真实采集时刻，不接受七天回溯假设作为实时证据。']
    Path('docs/reports/2026-11-09-observation-personnel-ablation.md').write_text('\n'.join(text)+'\n',encoding='utf-8')


if __name__=='__main__':main()
