"""Versioned, offline market monitoring research. Never selects production models."""
import hashlib
import json
import math
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from .observations import stamp, iso, COMPETITIONS

VERSION='market-monitor-v1'
WINDOWS=[('24h-6h',21600,86400),('6h-3h',10800,21600),('3h-90m',5400,10800),('90m-30m',1800,5400),('30m-5m',300,1800),('5m-0',0,300)]

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def union_seconds(intervals):
    total=0; end=None
    for start,stop in sorted(intervals):
        if end is None or start>end: total+=(stop-start).total_seconds();end=stop
        elif stop>end: total+=(stop-end).total_seconds();end=stop
    return total

def analyze_timeline(fixture, quotes, asof, max_gap_seconds=180, horizon_seconds=300):
    """Coverage means bounded gaps between independent confirmations, not continuous ticks.

    A stale/suspended/invalid row is a barrier. Missing rows create a barrier once
    max_gap is exceeded. Neither repeated cached fetches nor cross-line samples
    create observation exposure. The horizon label describes price persistence,
    not match outcome, value, manipulation, or predictive accuracy.
    """
    cutoff=min(stamp(asof),stamp(fixture['kickoff'])); kickoff=stamp(fixture['kickoff'])
    groups=defaultdict(list)
    for q in quotes:
        if q.get('market')!='asian_handicap': continue
        key=(q.get('source'),q.get('provider_market_id'),q.get('period'),q.get('line'))
        try:
            captured=stamp(q['captured_at']);checked=stamp(q['source_checked_at'])
            if captured>cutoff: continue
            prices={o['key']:float(o['odds']) for o in q['options']}
            valid=(set(prices)=={'h','a'} and all(math.isfinite(p) and p>1 for p in prices.values())
                   and 0<=(captured-checked).total_seconds()<=max_gap_seconds
                   and not q.get('stale') and not q.get('suspended'))
        except (KeyError,TypeError,ValueError):
            try: captured=stamp(q['captured_at'])
            except (KeyError,TypeError,ValueError): continue
            if captured<=cutoff: groups[key].append({'at':captured,'valid':False})
            continue
        groups[key].append({'at':captured,'checked':checked,'prices':prices,'valid':valid})
    intervals=[]; events=[]; streams=[]
    for key,rows in sorted(groups.items(),key=lambda pair:str(pair[0])):
        rows.sort(key=lambda r:r['at']); segments=[]; segment=[]
        for r in rows:
            if not r['valid']:
                if segment: segments.append(segment);segment=[]
                continue
            if segment:
                prev=segment[-1]
                # Identical provider confirmation is a cached duplicate.
                if r['checked']<=prev['checked']: continue
                gap=(r['at']-prev['at']).total_seconds()
                if gap>max_gap_seconds:
                    segments.append(segment);segment=[]
                else: intervals.append((prev['at'],r['at']))
            segment.append(r)
        if segment: segments.append(segment)
        for segment in segments:
            for side in ('h','a'):
                previous_sign=0
                for i in range(1,len(segment)):
                    prev,cur=segment[i-1:i+1];delta=cur['prices'][side]-prev['prices'][side]
                    if abs(delta)<1e-10: continue
                    sign=1 if delta>0 else -1
                    after=segment[i:];last=cur; reversal=None;confirmation=None
                    for later in after[1:]:
                        if (later['prices'][side]-cur['prices'][side])*sign<0:
                            reversal=later;break
                        last=later
                    target=cur['at']+timedelta(seconds=horizon_seconds)
                    confirmation=next((r for r in after if r['at']>=target),None)
                    # Censor gaps / stream termination, rather than label as loss.
                    label=None if confirmation is None else not any((r['prices'][side]-cur['prices'][side])*sign<0 for r in after if r['at']<=confirmation['at'])
                    event={'fixture_id':fixture['id'],'competition_id':fixture['competition_id'],'kickoff':fixture['kickoff'],
                        'source':key[0],'provider_market_id':key[1],'period':key[2],'line':key[3],'side':side,
                        'observed_at':iso(cur['at']),'delta':delta,'amplitude':abs(delta),'direction':'drop' if delta<0 else 'rise',
                        'reversal_from_previous':previous_sign!=0 and sign!=previous_sign,
                        'sampled_persistence_lower_seconds':(last['at']-cur['at']).total_seconds(),
                        'reversal_interval':None if reversal is None else [iso(last['at']),iso(reversal['at'])],
                        'continuous_duration_known':False,'target_persisted':label,
                        'label_available_at':iso(confirmation['at']) if confirmation else None,'horizon_seconds':horizon_seconds}
                    events.append(event);previous_sign=sign
        streams.append({'source':key[0],'market_id':key[1],'period':key[2],'line':key[3],'segments':len(segments),'samples':sum(map(len,segments))})
    buckets=[]
    for name,lo,hi in WINDOWS:
        left=kickoff-timedelta(seconds=hi);right=min(cutoff,kickoff-timedelta(seconds=lo))
        clipped=[(max(a,left),min(b,right)) for a,b in intervals if max(a,left)<min(b,right)]
        exposure=union_seconds(clipped)
        es=[e for e in events if left<=stamp(e['observed_at'])<right]
        buckets.append({'window':name,'observed_fixture_seconds':exposure,'events':len(es),
                        'events_per_observed_hour':len(es)/(exposure/3600) if exposure else None})
    return {'schema_version':VERSION,'fixture_id':fixture['id'],'competition_id':fixture['competition_id'],'asof':iso(cutoff),
            'max_gap_seconds':max_gap_seconds,'horizon_seconds':horizon_seconds,'streams':streams,'events':events,
            'covered_seconds':union_seconds(intervals),'buckets':buckets,'continuous_coverage_claim':False}

def metrics(rows):
    n=len(rows);hits=sum(r['target_persisted'] for r in rows)
    if not n:return {'n':0,'precision':None,'wilson':None}
    p=hits/n;z=1.96;den=1+z*z/n;center=(p+z*z/(2*n))/den;radius=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return {'n':n,'precision':p,'wilson':[center-radius,center+radius]}

def fit_rule(events,competition_id,train_end,validation_end,protocol=None):
    """Train-only selection, fixture-disjoint forward validation, one event per fixture.

    Protocol minimums are safety floors. This monitors sustained prices, and does
    not establish a football forecasting improvement or permit production use.
    """
    if competition_id not in COMPETITIONS:raise ValueError('赛事无效')
    train_end=stamp(train_end);validation_end=stamp(validation_end)
    if train_end>=validation_end:raise ValueError('时间切分无效')
    p={'thresholds':[.03,.05,.1,.15,.2],'min_train':30,'min_validation':20,'min_selected':10,'minimum_precision_gain':.05,**(protocol or {})}
    if p['min_train']<30 or p['min_validation']<20 or p['min_selected']<10:raise ValueError('样本门槛不可降低')
    if not p['thresholds'] or any(not isinstance(x,(int,float)) or not math.isfinite(x) or x<=0 for x in p['thresholds']):raise ValueError('阈值无效')
    selected={};rejected=defaultdict(int)
    for e in sorted(events,key=lambda r:(r['observed_at'],r['fixture_id'],r.get('side',''))):
        if e['competition_id']!=competition_id:rejected['other_competition']+=1;continue
        if e.get('target_persisted') not in (True,False) or not e.get('label_available_at'):rejected['censored']+=1;continue
        if e.get('direction')!='drop':continue
        observed=stamp(e['observed_at']);label=stamp(e['label_available_at']);kickoff=stamp(e['kickoff'])
        if not observed<label<=kickoff or label>validation_end:rejected['invalid_or_future_label']+=1;continue
        # Entire fixture belongs to one period, ensuring no shared fixture leakage.
        phase='train' if kickoff<=train_end and label<=train_end else 'validation' if observed>train_end else None
        if phase is None:rejected['split_boundary']+=1;continue
        selected.setdefault(e['fixture_id'],(phase,e))
    train=[e for phase,e in selected.values() if phase=='train'];validation=[e for phase,e in selected.values() if phase=='validation']
    candidates=[]
    for threshold in sorted(set(p['thresholds'])):
        m=metrics([e for e in train if e['amplitude']>=threshold])
        candidates.append({'threshold':threshold,**m})
    viable=[c for c in candidates if c['n']>=p['min_selected']]
    best=max(viable,key=lambda c:(c['precision'],c['n'],-c['threshold'])) if len(train)>=p['min_train'] and viable else None
    test=metrics([e for e in validation if best and e['amplitude']>=best['threshold']]);baseline=metrics(validation)
    reason='insufficient_training' if not best else 'insufficient_validation' if len(validation)<p['min_validation'] or test['n']<p['min_selected'] else 'no_validated_gain'
    passed=bool(best and len(validation)>=p['min_validation'] and test['n']>=p['min_selected'] and test['precision']>=baseline['precision']+p['minimum_precision_gain'] and test['wilson'][0]>baseline['wilson'][1])
    report={'schema_version':VERSION,'competition_id':competition_id,'train_end':iso(train_end),'validation_end':iso(validation_end),
        'protocol':p,'dataset_hash':digest(sorted(events,key=lambda e:json.dumps(e,sort_keys=True))),
        'train':metrics(train),'train_candidates':candidates,'threshold':best['threshold'] if best else None,
        'validation':test,'validation_baseline':baseline,'rejected':dict(rejected),
        'status':'validated_monitoring_candidate' if passed else reason,'monitoring_validated':passed,'production_enabled':False,
        'target':'observed_price_persistence_not_match_accuracy','train_fixture_ids':sorted(e['fixture_id'] for e in train),
        'validation_fixture_ids':sorted(e['fixture_id'] for e in validation)}
    report['artifact_id']=digest(report)
    return report

def save_artifact(report,directory):
    """Content-addressed write, no selection pointer and no overwrite."""
    clean={k:v for k,v in report.items() if k!='artifact_id'}
    if digest(clean)!=report.get('artifact_id'):raise ValueError('报告内容校验失败')
    path=Path(directory)/report['competition_id']/f"{report['artifact_id']}.json";path.parent.mkdir(parents=True,exist_ok=True)
    content=json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)
    if path.exists():
        if path.read_text(encoding='utf8')!=content:raise ValueError('报告路径冲突')
    else:
        with path.open('x',encoding='utf8') as file:file.write(content)
    return path

def apply_rule(report,event):
    """Only validated monitoring candidates can annotate, never alter predictions."""
    if digest({k:v for k,v in report.items() if k!='artifact_id'})!=report.get('artifact_id'):raise ValueError('报告内容校验失败')
    return bool(report.get('monitoring_validated') and event.get('competition_id')==report['competition_id']
                and stamp(event['observed_at'])>stamp(report['validation_end'])
                and event.get('direction')=='drop' and event['amplitude']>=report['threshold'])
