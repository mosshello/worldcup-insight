"""Registered prospective checks. A report can request review, never select a model."""
import json
import math
from collections import defaultdict
from .observations import stamp, iso, COMPETITIONS, NODES
from .sources import EXPECTED_OPTIONS
from .football_markets import outcome, score_key

REGISTERED_AT='2026-11-09T11:30:00+00:00'
PROTOCOL='observation-forward-v1'


def actual_result(score,market,line=None):
    if score.get('cancelled') or score.get('period')!='regular_time': return None
    h,a=score.get('home_goals'),score.get('away_goals')
    if any(type(v) is not int or v<0 for v in (h,a)):return None
    if market=='had': return outcome(h,a)
    if market=='hhad': return outcome(h+line,a) if type(line) is int else None
    if market=='ttg': return 's'+str(min(7,h+a))
    if market=='crs': return score_key(h,a)
    if market=='hafu' and all(type(score.get(k)) is int for k in ('home_half_goals','away_half_goals')):
        if not 0<=score['home_half_goals']<=h or not 0<=score['away_half_goals']<=a:return None
        return outcome(score['home_half_goals'],score['away_half_goals'])+outcome(h,a)


def losses(p,y):
    return -math.log(max(p[y],1e-15)),sum((v-(k==y))**2 for k,v in p.items())


def valid_probability(p,market):
    return (isinstance(p,dict) and set(p)==set(EXPECTED_OPTIONS[market])
            and all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=1 for v in p.values())
            and abs(sum(p.values())-1)<1e-6)


def ece(rows,key):
    if not rows: return None
    errors=[]
    for option in rows[0][key]:
        total=0
        for i in range(10):
            bucket=[r for r in rows if min(9,int(r[key][option]*10))==i]
            if bucket:
                total+=abs(sum(r[key][option] for r in bucket)-sum(r['actual']==option for r in bucket))
        errors.append(total/len(rows))
    return sum(errors)/len(errors)


def assess_pair(rows,targets,*,resamples=10000):
    """First 200 distinct fixtures, never extend a failed cohort until significant."""
    unique={}
    for row in sorted(rows,key=lambda r:(r['kickoff'],r['fixture_id'])):
        unique.setdefault(row['fixture_id'],row)
    rows=list(unique.values())[:200]
    if targets<len(rows):raise ValueError('目标分母小于有效样本数')
    report={'protocol':PROTOCOL,'n':len(rows),'targets':targets,'coverage':len(rows)/targets if targets else 0,
            'status':'observing','selected':False,'reasons':[],'p_value':None}
    if len(rows)<200:
        report['reasons']=['共同有效赛前样本不足200场'];return report
    if resamples<10000: raise ValueError('正式检查至少10000次重采样')
    report['fixture_ids']=[r['fixture_id'] for r in rows]
    delta=[]; brier=[];weeks=defaultdict(list)
    for row in rows:
        left=losses(row['base'],row['actual']);right=losses(row['candidate'],row['actual'])
        delta.append(right[0]-left[0]);brier.append(right[1]-left[1])
        dt=stamp(row['kickoff']);year,week,_=dt.isocalendar();weeks[(year,week)].append(delta[-1])
    report.update(mean_log_loss_difference=sum(delta)/200,mean_brier_difference=sum(brier)/200,
                  first_half_difference=sum(delta[:100])/100,second_half_difference=sum(delta[100:])/100,
                  base_ece=ece(rows,'base'),candidate_ece=ece(rows,'candidate'),weeks=len(weeks))
    duration=(stamp(rows[-1]['kickoff'])-stamp(rows[0]['kickoff'])).total_seconds()/86400
    months={(stamp(r['kickoff']).year,stamp(r['kickoff']).month) for r in rows}
    if len(weeks)<8 or len(months)<2 or duration<28:
        report['status']='not_promoted';report['reasons']=['固定200场的赛历周或跨月覆盖不足'];return report
    import numpy as np
    rng=np.random.default_rng(20261109)
    sums=np.asarray([sum(v) for v in weeks.values()]);counts=np.asarray([len(v) for v in weeks.values()])
    indices=rng.integers(0,len(sums),(resamples,len(sums)))
    boot=sums[indices].sum(axis=1)/counts[indices].sum(axis=1)
    # Null-centered, one-sided cluster bootstrap; p is adjusted across the fixed family below.
    centered=sums-counts*report['mean_log_loss_difference']
    null=centered[indices].sum(axis=1)/counts[indices].sum(axis=1)
    report['p_value']=float((1+(null<=report['mean_log_loss_difference']).sum())/(resamples+1))
    report['interval_95']=[float(x) for x in np.quantile(boot,[.025,.975])]
    conditions={'coverage':report['coverage']>=.9,'log_loss':report['mean_log_loss_difference']<=-.01,
                'brier':report['mean_brier_difference']<=0,'stability':max(report['first_half_difference'],report['second_half_difference'])<0,
                'calibration':report['candidate_ece']<=report['base_ece']+.02,
                'uncertainty':report['interval_95'][1]<0 and float(np.ptp(boot))>1e-12}
    report.update(status='awaiting_family_check',conditions=conditions)
    return report


def holm_family(reports,family_size=75):
    """Unobserved predefined cells count as p=1, not a posthoc reduced family."""
    if family_size<75:raise ValueError('不得缩小预登记检验家族')
    tested=sorted([r for r in reports if r.get('p_value') is not None],key=lambda r:r['p_value'])
    if any(not math.isfinite(r['p_value']) or not 0<=r['p_value']<=1 for r in tested):raise ValueError('检验概率无效')
    if len(tested)>family_size: raise ValueError('超过预登记检验家族')
    maximum=0
    for rank,r in enumerate(tested):
        maximum=max(maximum,min(1,r['p_value']*(family_size-rank)))
        r['adjusted_p_value']=maximum
        r['status']='eligible_for_manual_review' if maximum<.05 and all(r['conditions'].values()) else 'not_promoted'
    return reports


def evaluate(store,now=None):
    """Background check only; first complete 200-fixture reports are append-only."""
    now=stamp(now);jobs={};scores={};targets=defaultdict(dict);families_by_exp={};excluded=defaultdict(int);all_targets={}
    with store.connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS observation_evaluation_checks(
            id TEXT PRIMARY KEY,created_at TEXT NOT NULL,payload TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS observation_evaluation_evidence(
            id TEXT PRIMARY KEY,digest TEXT NOT NULL,payload TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS observation_evaluation_families(
            id TEXT PRIMARY KEY,created_at TEXT NOT NULL,payload TEXT NOT NULL)''')
        experiments={r['id']:dict(r) for r in db.execute('SELECT * FROM observation_experiments WHERE created_at<=?',(iso(now),))}
        for r in db.execute('SELECT * FROM observation_outcomes WHERE created_at<=? ORDER BY created_at,rowid',(iso(now),)):
            scores[(r['fixture_id'],r['revision'])]={'score':json.loads(r['payload']),'recorded_at':r['created_at']}
        records=db.execute('SELECT * FROM observation_jobs WHERE created_at<=? ORDER BY created_at,id',(iso(now),)).fetchall()
    for r in records:
        job=json.loads(r['payload']);result=json.loads(r['result']) if r['result'] else {}
        experiment=experiments.get(r['experiment_id'])
        if (not experiment or job.get('experiment')!=json.loads(experiment['config'])
            or job['fixture']['competition_id']!=experiment['competition_id']
            or job['fixture']['id']!=r['fixture_id'] or job.get('revision',r['revision'])!=r['revision']):
            excluded['experiment_mismatch']+=1;continue
        config=json.loads(experiment['config'])
        family=config.get('evaluation_family')
        if config.get('evaluation_protocol')!=PROTOCOL or not family:
            excluded['unregistered']+=1;continue
        registered=max(stamp(REGISTERED_AT),stamp(experiment['created_at']))
        if stamp(r['created_at'])<registered or stamp(r['due_at'])<registered:
            excluded['before_registration']+=1;continue
        # Future targets are not failures; only completed fixtures enter the coverage window.
        if stamp(job['fixture']['kickoff'])>now: continue
        cid=job['fixture']['competition_id'];group=(cid,r['node'],r['source'],r['experiment_id'])
        families_by_exp[r['experiment_id']]=family
        all_targets[(cid,r['node'],r['experiment_id'],r['fixture_id'])]=job['fixture']['kickoff']
        targets[group][r['fixture_id']]={'kickoff':job['fixture']['kickoff'],'state':r['status']}
        tolerance=180 if r['node']=='opening' else min(180,max(0,float(config.get('node_tolerance_seconds',60))))
        if (r['status']!='completed' or not result.get('strict_node') or not result.get('cutoff')
            or not result.get('created_at') or not r['finished_at']
            or stamp(result['cutoff'])<registered or stamp(result['cutoff'])<stamp(r['due_at'])
            or (stamp(result['cutoff'])-stamp(r['due_at'])).total_seconds()>tolerance
            or stamp(result['cutoff'])>stamp(result['created_at'])
            or stamp(result['created_at'])>=stamp(job['fixture']['kickoff'])
            or stamp(r['finished_at'])>=stamp(job['fixture']['kickoff'])
            or stamp(r['finished_at'])>stamp(r['deadline']) or stamp(result['created_at'])>stamp(r['deadline'])
            or stamp(result['created_at'])>now or stamp(r['finished_at'])>now): continue
        key=(r['fixture_id'],r['revision'],r['experiment_id'],r['node'])
        jobs.setdefault(key,{})[r['source']]={'job':job,'result':result}
    for (cid,node,experiment,fid),kickoff in all_targets.items():
        for src in ('market','enhanced','ai'):
            targets[(cid,node,src,experiment)].setdefault(fid,{'kickoff':kickoff,'state':'missing'})
    samples=defaultdict(list)
    for key,groups in jobs.items():
        if 'base' not in groups or key[:2] not in scores: continue
        base=groups['base'];fixture=base['job']['fixture'];b=base['result']
        settlement=scores[key[:2]]
        if stamp(settlement['recorded_at'])<stamp(fixture['kickoff']):continue
        for src in ('market','enhanced','ai'):
            candidate=groups.get(src)
            if not candidate: continue
            c=candidate['result']
            if b['cutoff']!=c['cutoff']: continue
            for market in EXPECTED_OPTIONS:
                if market=='hhad' and b.get('line')!=c.get('line'): continue
                y=actual_result(settlement['score'],market,b.get('line'))
                bp=b.get('probabilities',{}).get(market,{});cp=c.get('probabilities',{}).get(market,{})
                if not valid_probability(bp,market) or not valid_probability(cp,market) or y not in bp: continue
                group=(fixture['competition_id'],key[3],src,key[2],market)
                samples[group].append({'fixture_id':key[0],'revision':key[1],'kickoff':fixture['kickoff'],'cutoff':b['cutoff'],
                    'line':b.get('line'),'actual':y,'base':bp,'candidate':cp,'settlement':settlement,
                    'base_model_id':b.get('model_id'),'candidate_model_id':c.get('model_id')})
    result=[]
    for (cid,node,src,experiment),target_rows in targets.items():
        if src=='base': continue
        for market in (['had'] if src=='ai' else EXPECTED_OPTIONS):
            rows=samples.get((cid,node,src,experiment,market),[])
            ordered=sorted({r['fixture_id']:r for r in rows}.values(),key=lambda r:(r['kickoff'],r['fixture_id']))
            end=(ordered[199]['kickoff'],ordered[199]['fixture_id']) if len(ordered)>=200 else None
            window=[t for fid,t in target_rows.items() if end is None or (t['kickoff'],fid)<=end]
            states=defaultdict(int)
            for t in window: states[t['state']]+=1
            from .store import fingerprint,encode
            check_id=fingerprint([PROTOCOL,experiment,cid,node,src,market])
            with store.connect() as db:
                existing=db.execute('SELECT payload,created_at FROM observation_evaluation_checks WHERE id=?',(check_id,)).fetchone()
                future_check=bool(existing and stamp(existing['created_at'])>now)
                stored=existing if existing and not future_check else None
            if stored:
                report=json.loads(stored[0])
            else:
                report=assess_pair(rows,len(window))
                report.update(competition_id=cid,node=node,source=src,experiment_id=experiment,market=market,states=dict(states))
                report['evaluation_family']=families_by_exp[experiment]
                if report['n']==200 and future_check:
                    report.update(historical_replay=True,frozen=False)
                elif report['n']==200:
                    evidence={'samples':ordered[:200],'targets':[{**t,'fixture_id':fid} for fid,t in sorted(target_rows.items()) if end is None or (t['kickoff'],fid)<=end]}
                    report['evidence_digest']=fingerprint(evidence)
                    report.update(check_id=check_id,checked_at=iso(now),frozen=True)
                    with store.connect() as db:
                        db.execute('INSERT OR IGNORE INTO observation_evaluation_evidence VALUES(?,?,?)',(check_id,report['evidence_digest'],encode(evidence)))
                        db.execute('INSERT OR IGNORE INTO observation_evaluation_checks VALUES(?,?,?)',(check_id,iso(now),encode(report)))
                        report=json.loads(db.execute('SELECT payload FROM observation_evaluation_checks WHERE id=?',(check_id,)).fetchone()[0])
            result.append(report)
    families=defaultdict(list)
    for r in result: families[(r['source'],r['evaluation_family'])].append(r)
    family_decisions=[]
    for family_key,rows in families.items():
        cell_ids=[(r['competition_id'],r['node'],r['market']) for r in rows]
        if len(set(cell_ids))!=len(cell_ids):
            for r in rows:
                r['status']='invalid_family';r['reasons']=['同一检验家族包含重复实验，需另行预登记']
        else: holm_family(rows)
        frozen=[r for r in rows if r.get('frozen')]
        if frozen:
            # Preserve each multiple-testing decision as an append-only snapshot.
            # A later completed cell creates another version, never rewrites one.
            from .store import fingerprint,encode
            decision={'protocol':PROTOCOL,'source':family_key[0],'evaluation_family':family_key[1],'family_size':75,
                'cells':sorted([{'check_id':r['check_id'],'evidence_digest':r.get('evidence_digest'),
                    'p_value':r.get('p_value'),'adjusted_p_value':r.get('adjusted_p_value'),'status':r['status']}
                    for r in frozen],key=lambda r:r['check_id']),'production_changes':False}
            decision_id=fingerprint(decision)
            with store.connect() as db:db.execute('INSERT OR IGNORE INTO observation_evaluation_families VALUES(?,?,?)',(decision_id,iso(now),encode(decision)))
            family_decisions.append({'id':decision_id,'source':family_key[0],'evaluation_family':family_key[1]})
            for r in frozen:r['family_decision_id']=decision_id
    return {'protocol':PROTOCOL,'registered_at':REGISTERED_AT,'evaluated_at':iso(now),'cells':result,
            'family_decisions':family_decisions,'excluded':dict(excluded),'production_changes':False}
