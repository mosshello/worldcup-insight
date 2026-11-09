"""League-isolated, retrospective context ablations; never selects a production model."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

import numpy as np
from scipy.stats import binom, nbinom, poisson
from sklearn.linear_model import LogisticRegression, PoissonRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from big_five_challenger_sources import LEAGUES, OUT as OLD, sha
from research_big_five_goal_challengers import (
    BASE, EXTRA, minutes, observations, receipt_data, save_immutable,
)
from research_big_five_actual_lines import quote, asian_class
from run_big_five_backtests import DATASETS
from replay_big_five_backtests import protected_hashes
from worldcup_mvp.console import model_registry as registry, training
from worldcup_mvp.console.football_markets import score_grid, score_key, outcome
from worldcup_mvp.console.sources import EXPECTED_OPTIONS, parse_date

PROTOCOL = ROOT / 'docs/reports/2026-11-09-context-backtest-protocol.md'
H2H = ['h2h_gf', 'h2h_ga', 'h2h_draw', 'h2h_points_residual', 'h2h_n', 'same_venue_points', 'same_venue_n']
FORM = ['points_residual', 'goals_minus_xg', 'points_n', 'goals_xg_n']
CONTEXT = EXTRA['rest'] + EXTRA['minutes']
def both(fields):
    return fields + ['opp_' + k for k in fields]
VARIANTS = {
    'recent_nb': BASE,
    'xg': BASE + EXTRA['xg'],
    'xg_h2h': BASE + EXTRA['xg'] + both(H2H),
    'xg_surprise': BASE + EXTRA['xg'] + both(FORM),
    'xg_context': BASE + EXTRA['xg'] + CONTEXT,
    'all': BASE + EXTRA['xg'] + both(H2H + FORM) + CONTEXT,
}
N = 31
GH, GA = np.indices((N, N))
DIFF = GH - GA
OPTIONS = {k: list(EXPECTED_OPTIONS[k]) for k in ('had', 'hhad', 'ttg', 'crs', 'hafu')}
END = datetime(2026, 11, 9, tzinfo=timezone.utc)


def read(path):
    return json.loads(Path(path).read_bytes())


def year_start(year):
    return datetime(year, 7, 1, tzinfo=timezone.utc)


def checked_lines(cid):
    path = ROOT / read(OLD / 'line-reports.json')[cid]
    raw = path.read_bytes()
    if path.stem != 'lines-' + sha(raw):
        raise ValueError('Line archive checksum mismatch')
    data = json.loads(raw)
    if data['competition_id'] != cid:
        raise ValueError('Cross-competition line archive')
    return data, sha(raw)


def prepare(cid, rows, load, official):
    """All predictors are produced before appending the current match's labels."""
    if any(r['competition_id'] != cid for r in rows):
        raise ValueError('Cross-competition data')
    if len({r['id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate fixture')
    samples, _ = observations(rows, load)
    lookup = {s['id']: s for s in samples}
    histories = defaultdict(list)
    pairs = defaultdict(list)
    for row in sorted(rows, key=lambda r: (r['kickoff'], r['id'])):
        item = lookup.get(row['id'])
        at = parse_date(row['kickoff']) - timedelta(hours=1)
        home, away = row['home_id'], row['away_id']
        pair_key = tuple(sorted((home, away)))
        pair = [v for v in pairs[pair_key] if v['available'] < at and at-v['time'] < timedelta(days=1095)]
        extras, evidence = [], []
        for side, team in enumerate((home, away)):
            past = [v for v in histories[team] if v['available'] < at and v['time'] < at][-10:]
            own = lambda v: 0 if v['home'] == team else 1
            goals = lambda v: (v['goals'][own(v)], v['goals'][1-own(v)])
            points = lambda v: 3 if goals(v)[0] > goals(v)[1] else 1 if goals(v)[0] == goals(v)[1] else 0
            weights = [2 ** (-(at-v['time']).total_seconds()/86400/365) for v in pair]
            denom = 5 + sum(weights)
            residual = lambda v: points(v) - v['expected'][own(v)]
            same = [(v,w) for v,w in zip(pair,weights) if (own(v) == side)]
            available_expected = [v for v in past if v['expected'] is not None]
            available_xg = [v for v in past if v['xg'] is not None]
            available_market = [v for v in past if v['market_expected'] is not None]
            x = {
                'h2h_gf': (6.75 + sum(w*goals(v)[0] for v,w in zip(pair,weights)))/denom,
                'h2h_ga': (6.75 + sum(w*goals(v)[1] for v,w in zip(pair,weights)))/denom,
                'h2h_draw': (1.25 + sum(w*(v['goals'][0] == v['goals'][1]) for v,w in zip(pair,weights)))/denom,
                'h2h_points_residual': sum(w*residual(v) for v,w in zip(pair,weights) if v['expected'] is not None)/denom,
                'h2h_n': sum(weights),
                'same_venue_points': (6.25 + sum(w*points(v) for v,w in same))/(5+sum(w for _,w in same)),
                'same_venue_n': sum(w for _,w in same),
                'points_residual': sum(residual(v) for v in available_expected)/(5+len(available_expected)),
                'points_n': len(available_expected),
                'goals_minus_xg': sum(goals(v)[0]-v['xg'][own(v)] for v in available_xg)/(5+len(available_xg)),
                'goals_xg_n': len(available_xg),
                'market_points_residual': sum(points(v)-v['market_expected'][own(v)] for v in available_market)/(5+len(available_market)),
                'market_points_n': len(available_market),
            }
            extras.append(x)
            evidence.append({'h2h': [v['id'] for v in pair], 'recent': [v['id'] for v in past],
                             'market': [v['id'] for v in available_market]})
        expected = None
        if item:
            mu = [(v['gf'] + v['opp_ga']) / 2 for v in item['vectors']]
            g = np.outer(poisson.pmf(np.arange(N), mu[0]), poisson.pmf(np.arange(N), mu[1]))
            ph, pd, pa = g[DIFF > 0].sum(), g.trace(), g[DIFF < 0].sum()
            expected = [3*ph+pd, 3*pa+pd]
            for side in (0, 1):
                item['vectors'][side].update(extras[side])
                item['vectors'][side].update({'opp_'+k: v for k,v in extras[1-side].items()})
            item['context_inputs'] = evidence
            item['half_goals'] = [row.get('home_half_goals'), row.get('away_half_goals')]
        from research_big_five_goal_challengers import xg_fact
        xg, _ = xg_fact(row)
        market = official.get(row['id'], {}).get('had')
        if market and parse_date(market['time']) > at:
            raise ValueError('Future historical market expectation')
        mp = market['p'] if market else None
        record = {'id': row['id'], 'home': home, 'goals': [row['home_goals'],row['away_goals']],
                  'time': parse_date(row['kickoff']),
                  'available': max(parse_date(row['result_available_at']), parse_date(row['kickoff'])+timedelta(days=7)),
                  'expected': expected, 'xg': xg,
                  'market_expected': [3*mp[0]+mp[1],3*mp[2]+mp[1]] if mp else None}
        histories[home].append(record)
        histories[away].append(record)
        pairs[pair_key].append(record)
    return samples


def fit(samples, version, at):
    eligible = [r for r in samples if parse_date(r['time']) < at and parse_date(r['available']) < at]
    cols = VARIANTS[version]
    x = np.array([[v[k] for k in cols] for r in eligible for v in r['vectors']])
    y = np.array([g for r in eligible for g in r['goals']])
    center, scale = x.mean(0), x.std(0)
    scale[scale < 1e-9] = 1
    reg = PoissonRegressor(alpha=.08, max_iter=500).fit((x-center)/scale, y)
    mu = reg.predict((x-center)/scale)
    dispersion = float(np.clip(np.sum((y-mu)**2-y)/np.sum(mu**2), 0, 1))
    return {'version': version, 'fields': cols, 'mean': center.tolist(), 'scale': scale.tolist(),
            'coef': reg.coef_.tolist(), 'intercept': float(reg.intercept_), 'dispersion': dispersion,
            'cutoff': at.isoformat(), 'train_ids': [r['id'] for r in eligible], 'train_count': len(eligible)}


def grid(model, item):
    z = (np.array([[v[k] for k in model['fields']] for v in item['vectors']])-model['mean'])/model['scale']
    mu = np.clip(np.exp(z @ model['coef'] + model['intercept']), .05, 8)
    a = model['dispersion']
    pn = poisson.pmf(np.arange(N)[:,None], mu) if a < 1e-7 else nbinom.pmf(np.arange(N)[:,None], 1/a, 1/(1+a*mu))
    mat = np.outer(pn[:,0], pn[:,1])
    if mat.sum() < .999 or not np.isfinite(mat).all():
        raise ValueError('Score tail exceeds fixed tolerance')
    return mat / mat.sum()


@lru_cache(maxsize=24)
def projections(half_shares):
    labels = {
        'had': np.where(DIFF>0,'h',np.where(DIFF<0,'a','d')),
        'ttg': np.array([f's{min(int(n),7)}' for n in (GH+GA).ravel()]).reshape(N,N),
        'crs': np.array([score_key(int(h),int(a)) for h,a in zip(GH.ravel(),GA.ravel())]).reshape(N,N),
    }
    result = {k: np.array([(v==o).ravel() for o in OPTIONS[k]],float) for k,v in labels.items()}
    if half_shares:
        bh, ba = [binom.pmf(np.arange(N)[None,:],np.arange(N)[:,None],p) for p in half_shares]
        ht = {'h': bh@(DIFF>0)@ba.T, 'd': bh@(DIFF==0)@ba.T, 'a': bh@(DIFF<0)@ba.T}
        result['hafu'] = np.array([(ht[o[0]]*(labels['had']==o[1])).ravel() for o in OPTIONS['hafu']])
    return result


def markets(mat, half_shares, line=None):
    out = {k: (p @ mat.ravel()).tolist() for k,p in projections(tuple(half_shares) if half_shares else ()).items()}
    if line is not None:
        if not float(line).is_integer():
            raise ValueError('Integer handicap required')
        labels = np.where(DIFF+line>0,'h',np.where(DIFF+line<0,'a','d'))
        out['hhad'] = [float(mat[labels==o].sum()) for o in OPTIONS['hhad']]
    for p in out.values():
        if min(p)<-1e-12 or not np.isfinite(p).all() or abs(sum(p)-1)>1e-8:
            raise ValueError('Invalid market distribution')
    return out


def actuals(item, line=None):
    h,a = item['goals']
    labels = {'had':outcome(h,a), 'ttg':f's{min(h+a,7)}', 'crs':score_key(h,a)}
    hh,ha = item['half_goals']
    if hh is not None and ha is not None and 0<=hh<=h and 0<=ha<=a:
        labels['hafu'] = outcome(hh,ha)+outcome(h,a)
    if line is not None:
        labels['hhad'] = outcome(h+line,a)
    return {k: OPTIONS[k].index(v) for k,v in labels.items()}


def metric(p, y):
    if not len(y):
        return {'n':0,'accuracy':None,'log_loss':None,'brier':None,'ece':None,'top_k_coverage':None}
    p,y = np.asarray(p),np.asarray(y,int)
    order = np.argsort(-p,axis=1,kind='stable')
    hit = order[:,0]==y
    confidence = p.max(axis=1)
    ece = 0.
    for i in range(10):
        mask = (confidence >= i/10) & (confidence < (i+1)/10 if i<9 else confidence<=1)
        if mask.any():
            ece += mask.mean()*abs(confidence[mask].mean()-hit[mask].mean())
    return {'n':len(y),'hits':int(hit.sum()),'accuracy':float(hit.mean()),
            'log_loss':float(-np.log(np.maximum(1e-15,p[np.arange(len(y)),y])).mean()),
            'brier':float(np.mean(np.sum((p-np.eye(p.shape[1])[y])**2,axis=1))), 'ece':float(ece)}


def summarize(records, field='probabilities'):
    summary = {}
    for code in OPTIONS:
        rows = [r for r in records if code in r[field] and code in r['actual']]
        p,y = [r[field][code] for r in rows],[r['actual'][code] for r in rows]
        value = metric(p,y)
        k = 2 if code=='ttg' else 3 if code=='crs' else 1
        value['top_k'] = k
        if rows:
            order = np.argsort(-np.array(p),axis=1,kind='stable')[:,:k]
            value['top_k_coverage'] = float((order==np.array(y)[:,None]).any(axis=1).mean())
        summary[code] = value
    return summary


def paired_interval(records, candidate, baseline):
    """Weekly block bootstrap; difference candidate minus baseline, no selection here."""
    blocks = defaultdict(list)
    for r in records:
        p,q,y = np.array(r[candidate]),np.array(r[baseline]),r['y']
        delta = [float(p.argmax()==y)-float(q.argmax()==y),
                 -math.log(max(1e-15,p[y]))+math.log(max(1e-15,q[y]))]
        t = parse_date(r['time'])
        blocks[(t.isocalendar().year,t.isocalendar().week)].append(delta)
    if len(blocks)<2:
        return {'blocks':len(blocks),'accuracy_delta95':None,'log_loss_delta95':None}
    sums = np.array([np.sum(v,axis=0) for v in blocks.values()])
    counts = np.array([len(v) for v in blocks.values()])
    rng = np.random.default_rng(20261109)
    idx = rng.integers(0,len(sums),(2000,len(sums)))
    draws = sums[idx].sum(axis=1)/counts[idx].sum(axis=1)[:,None]
    ci = np.quantile(draws,[.025,.975],axis=0)
    return {'blocks':len(blocks), 'accuracy_delta95':ci[:,0].tolist(),'log_loss_delta95':ci[:,1].tolist()}


def movement(history, asof, latest, handicap=False):
    """Only compare probabilities for the same contract, never across changed lines."""
    valid = []
    for row in history:
        q = quote([row],asof)
        if q:
            valid.append(q)
    if not latest or not valid:
        return [0.,0.,0.,0.,0.,0.]
    valid.sort(key=lambda q:q['time'])
    line_changed = handicap and any(q['line']!=latest['line'] for q in valid)
    # After a line change only the latest uninterrupted line segment is comparable.
    if handicap:
        last_other = max((i for i,q in enumerate(valid) if q['line']!=latest['line']),default=-1)
        valid = valid[last_other+1:]
    times = sorted(set(q['time'] for q in valid))
    first = quote([r for r in history if r.get('updateDate','')+'T'+r.get('updateTime','')+'+08:00' == times[0]],asof) if times else None
    # Datetime isoformat is identical to source +08:00 representation.
    distinct = first is not None and len(times)>1
    delta = np.array(latest['p'])-first['p'] if distinct else np.zeros(3)
    age = max(0,(asof-parse_date(latest['time'])).total_seconds()/3600)
    return [float(delta[0]),float(delta[2]),float(distinct),float(line_changed),min(age,168),len(times)]


def market_vector(item, record, code, histories):
    current = record['had'] if code=='had' else record['hh']
    if not current:
        return None
    at = parse_date(item['time'])-timedelta(hours=1)
    if parse_date(current['time']) > at:
        raise ValueError('Future quote')
    h = histories['hadList' if code=='had' else 'hhadList']
    p = np.asarray(current['p'])
    v = item['vectors'][0]
    # Market reference and observed match features only, not settlement labels.
    keys = ['h2h_points_residual','h2h_draw','h2h_n','same_venue_points','same_venue_n',
            'points_residual','opp_points_residual','goals_minus_xg','opp_goals_minus_xg',
            'market_points_residual','opp_market_points_residual','market_points_n','opp_market_points_n']
    return [float(np.log(p[0]/p[1])),float(np.log(p[2]/p[1])),
            *[float(v[k]) for k in keys], *movement(h,at,current,code=='hhad'), float(current['line'])]


def market_experiment(samples, forecasts, official):
    result = {}
    by_id = {s['id']:s for s in samples}
    histories = {}
    for fid,r in official.items():
        if fid not in forecasts:
            continue
        raw,_ = receipt_data('detail-'+r['official_id'])
        histories[fid] = raw['value']['oddsHistory']
    for code in ('had','hhad'):
        rows=[]
        for fid, prediction in forecasts.items():
            record=official.get(fid)
            if not record or fid not in histories:
                continue
            s=by_id[fid]
            x=market_vector(s,record,code,histories[fid])
            if x is None or code not in prediction['probabilities']:
                continue
            p=record['had']['p'] if code=='had' else record['hh']['p']
            # Official probabilities have fixed H,D,A order; convert to shared option order.
            market=[p['hda'.index(k)] for k in OPTIONS[code]]
            football=prediction['probabilities'][code]
            x += [math.log(max(1e-9,football[0])), math.log(max(1e-9,football[2]))]
            rows.append({'id':fid,'time':s['time'],'available':s['available'],'season':s['season'],
                         'x':x,'market':market,'football':football,'y':prediction['actual'][code]})
        rows.sort(key=lambda r:(r['time'],r['id']))
        prior=[r for r in rows if r['season']==2025]
        test=[r for r in rows if r['season']==2026]
        if len(prior)<60 or not test:
            result[code]={'status':'insufficient_samples','train_n':len(prior),'test_n':len(test)}
            continue
        boundary=parse_date(prior[int(len(prior)*.75)]['time'])-timedelta(hours=1)
        tr=[r for r in prior if parse_date(r['time'])<boundary and parse_date(r['available'])<boundary]
        val=[r for r in prior if parse_date(r['time'])-timedelta(hours=1)>=boundary]
        candidates=[]
        for c in (.01,.1,1.):
            model=make_pipeline(StandardScaler(),LogisticRegression(C=c,max_iter=1500))
            model.fit([r['x'] for r in tr],[r['y'] for r in tr])
            if list(model[-1].classes_)!=[0,1,2]:
                raise ValueError('Missing market training class')
            p=model.predict_proba([r['x'] for r in val])
            for w in (0.,.25,.5,1.):
                mix=w*p+(1-w)*np.array([r['market'] for r in val])
                candidates.append({'c':c,'weight':w,'validation':metric(mix,[r['y'] for r in val])})
        choice=min(candidates,key=lambda d:(d['validation']['log_loss'],d['weight'],d['c']))
        at=year_start(2026)
        train=[r for r in prior if parse_date(r['available'])<at]
        model=make_pipeline(StandardScaler(),LogisticRegression(C=choice['c'],max_iter=1500))
        model.fit([r['x'] for r in train],[r['y'] for r in train])
        predicted=model.predict_proba([r['x'] for r in test])
        for r,p in zip(test,predicted):
            r['candidate']=p.tolist()
            r['selected']=(choice['weight']*p+(1-choice['weight'])*np.array(r['market'])).tolist()
        scaler,reg=model[0],model[1]
        result[code]={'status':'completed','selection':choice,'validation_candidates':candidates,
                      'train_n':len(train),'validation_n':len(val),'selection_train_n':len(tr),
                      'split_gap_excluded':len(prior)-len(tr)-len(val),'test_n':len(test),
                      'train_ids':[r['id'] for r in train], 'validation_ids':[r['id'] for r in val],
                      'selection_train_ids':[r['id'] for r in tr], 'validation_boundary':boundary.isoformat(),
                      'metrics':{k:metric([r[k] for r in test],[r['y'] for r in test]) for k in ('market','football','candidate','selected')},
                      'paired_selected_market':paired_interval(test,'selected','market'),
                      'parameters':{'mean':scaler.mean_.tolist(),'scale':scaler.scale_.tolist(),
                                    'coef':reg.coef_.tolist(),'intercept':reg.intercept_.tolist(),'classes':reg.classes_.tolist()},
                      'records':test}
    return result


def run_league(cid):
    print(cid,'loading',flush=True)
    data=registry.dataset(cid,DATASETS[cid],admission=False)
    rows=training.validate_dataset(data)
    lines,line_hash=checked_lines(cid)
    official={r['id']:r for r in lines['official_rows']}
    load,minute_hash=minutes(cid,LEAGUES[cid][0])
    samples=prepare(cid,rows,load,official)
    by_id={r['id']:r for r in rows}
    val=[r for r in samples if r['season']==2024 and parse_date(r['available'])<year_start(2025)]
    ranking={}
    for name in VARIANTS:
        model=fit(samples,name,year_start(2024))
        p=[markets(grid(model,r),None)['had'] for r in val]
        ranking[name]=metric(p,[actuals(r)['had'] for r in val])
    winner=min(ranking,key=lambda k:(ranking[k]['log_loss'],k))
    report={'competition_id':cid,'dataset':DATASETS[cid],'dataset_n':len(rows),
            'latest_kickoff':max(r['kickoff'] for r in rows),'line_archive_sha256':line_hash,
            'minutes_sha256':minute_hash,'validation':ranking,'selected':winner,'seasons':{}}
    print(cid,'validation selected',winner,flush=True)
    frozen=read(OLD.parent/'backtests/verified.json')[cid]
    selected_records={}
    for year in (2025,2026):
        at=year_start(year)
        target=[r for r in rows if int(r['season'])==year and parse_date(r['result_available_at'])<END]
        eligible=[s for s in samples if s['season']==year and parse_date(s['available'])<END]
        dc_id=frozen['runs'][f'{year}-dc-fit']['id']
        _,bundle,_,_=registry.load_run(cid,dc_id)
        dc=training.GoalModel.from_dict(bundle['models'][cid])
        half=dc.half_shares.get(cid)
        ids={s['id'] for s in eligible}
        excluded=[{'id':r['id'],'reason':'fewer_than_five_prior_available_matches'} for r in target if r['id'] not in ids]
        season={'targets':len(target),'predicted':len(eligible),'excluded':excluded,'variants':{},'dc_run_id':dc_id}
        for name in VARIANTS:
            model=fit(samples,name,at)
            records=[]
            for s in eligible:
                line=official.get(s['id'],{}).get('hh',{}).get('line')
                p=markets(grid(model,s),half,line)
                records.append({'id':s['id'],'time':s['time'],'actual':actuals(s,line),'probabilities':p})
            season['variants'][name]={'model':model,'metrics':summarize(records),'records':records}
            if name==winner:
                selected_records.update({r['id']:r for r in records})
        baseline=[]
        common=[]
        for s in eligible:
            row=by_id[s['id']]
            if min(dc.team_counts.get(cid+':'+row[k],0) for k in ('home_id','away_id'))<5:
                continue
            line=official.get(s['id'],{}).get('hh',{}).get('line')
            p=markets(np.asarray(score_grid(*dc.rates(row),dc.rhos.get(cid,0))),half,line)
            rec={'id':s['id'],'time':s['time'],'actual':actuals(s,line),'probabilities':p}
            baseline.append(rec)
            common.append(selected_records[s['id']])
        season['dc']={'metrics':summarize(baseline),'records':baseline,'additional_excluded':len(eligible)-len(baseline)}
        season['selected_common']={'metrics':summarize(common),'n':len(common)}
        season['paired']={}
        for code in OPTIONS:
            pairs=[{'time':a['time'],'y':a['actual'][code],'candidate':a['probabilities'][code],'baseline':b['probabilities'][code]}
                   for a,b in zip(common,baseline) if code in a['actual'] and code in a['probabilities']]
            season['paired'][code]=paired_interval(pairs,'candidate','baseline')
        report['seasons'][str(year)]=season
        print(cid,year,'targets',len(target),'predicted',len(eligible),'common',len(common),
              'HDA',season['dc']['metrics']['had']['accuracy'],season['selected_common']['metrics']['had']['accuracy'],flush=True)
    report['market_experiment']=market_experiment(samples,selected_records,official)
    # Persist feature evidence once per fixture, including absence indicators.
    report['samples']=samples
    return report


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    args.output=args.output.resolve()
    args.output.mkdir(parents=True,exist_ok=False)
    before=protected_hashes()
    run={'protocol_sha256':sha(PROTOCOL.read_bytes()),'code_sha256':sha(Path(__file__).read_bytes()),
         'cutoff':END.isoformat(),'usage':'retrospective_exploratory_assumed_historical_availability',
         'started_at':datetime.now(timezone.utc).isoformat(),'reports':{},'not_evaluated':[
             'historical timestamped injury and lineup intelligence', 'LLM forecasts of unresolved matches',
             'real betting-volume sentiment', 'production selection or profitability']}
    save_immutable(args.output/'protocol.json',run)
    with threadpool_limits(limits=1):
        for cid in LEAGUES:
            report=run_league(cid)
            encoded=json.dumps(report,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
            path=args.output/cid/(sha(encoded)+'.json')
            save_immutable(path,report)
            run['reports'][cid]=str(path.relative_to(ROOT))
            save_immutable(args.output/(cid+'.receipt.json'),{'report':run['reports'][cid]})
    after=protected_hashes()
    if before!=after:
        raise ValueError('Protected artifacts changed during research')
    run['protected_artifacts_unchanged']=len(before)
    run['finished_at']=datetime.now(timezone.utc).isoformat()
    save_immutable(args.output/'index.json',run)
    print('COMPLETED',args.output,flush=True)


if __name__=='__main__':
    main()
