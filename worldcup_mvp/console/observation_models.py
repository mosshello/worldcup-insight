"""League-only xG/rest research candidates with canonical evidence selection.

Historical fitting explicitly uses a seven-day xG availability assumption.
Live features require actual source capture by the forecast cutoff. No production
selection is changed and no unavailable personnel effect is invented.
"""
import json
import math
from pathlib import Path
from datetime import timedelta
from collections import defaultdict
from .observation_features import instant
from .store import fingerprint, encode
from .football_markets import market_probabilities

FIELDS = ['home','gf','ga','opp_gf','opp_ga','xgf','xga','opp_xgf','opp_xga','rest','opp_rest']
SCHEMA = 'observation-xg-rest-v1'
ROOT = Path(__file__).resolve().parents[2] / 'data' / 'research' / 'observation-models'


def history_rows(rows, cid):
    out = []
    for row in rows:
        if row['competition_id'] != cid: raise ValueError('跨赛事研究数据')
        pair, captured = None, None
        for source in ('understat_archive','understat_gallery_archive','pitchradar_understat_display'):
            fact = row.get('postmatch_xg_sources',{}).get(source,{})
            v = fact.get('xg')
            if isinstance(v,list) and len(v)==2 and all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) and x>=0 for x in v):
                pair,captured = v,fact.get('source_receipt',{}).get('observed_at')
                break
        out.append({k:row[k] for k in ('id','competition_id','kickoff','home_id','away_id','home_goals','away_goals','result_available_at')} |
                   {'xg':pair,'xg_captured_at':captured,'home_half_goals':row.get('home_half_goals'),'away_half_goals':row.get('away_half_goals')})
    if len({r['id'] for r in out}) != len(out): raise ValueError('重复历史比赛')
    return sorted(out,key=lambda r:(r['kickoff'],r['id']))


def vectors(rows, fixture, as_of, historical=False):
    at = instant(as_of)
    kickoff = instant(fixture['kickoff'])
    if at >= kickoff: raise ValueError('预测截止不在开赛前')
    teams, refs = [], []
    for team in (fixture['home_id'],fixture['away_id']):
        past = [r for r in rows if team in (r['home_id'],r['away_id']) and instant(r['kickoff'])<at and instant(r['result_available_at'])<=at][-10:]
        if len(past)<5: raise ValueError('增强模型历史样本不足5场')
        goals = lambda r: (r['home_goals'],r['away_goals']) if team==r['home_id'] else (r['away_goals'],r['home_goals'])
        advanced = [r for r in past if r['xg'] is not None and
            (instant(r['kickoff'])+timedelta(days=7)<=at if historical else r['xg_captured_at'] and instant(r['xg_captured_at'])<=at)]
        if len(advanced)<3: raise ValueError('增强模型缺少3场已可用历史xG')
        xpair = lambda r: r['xg'] if team==r['home_id'] else r['xg'][::-1]
        teams.append({'gf':sum(goals(r)[0] for r in past)/len(past), 'ga':sum(goals(r)[1] for r in past)/len(past),
                      'xgf':sum(xpair(r)[0] for r in advanced)/len(advanced),'xga':sum(xpair(r)[1] for r in advanced)/len(advanced),
                      'rest':min(30,(kickoff-instant(past[-1]['kickoff'])).total_seconds()/86400)})
        refs.append({'results':[r['id'] for r in past],'xg':[r['id'] for r in advanced], 'rest_from':past[-1]['id']})
    return [dict(teams[i],home=1-i,**{'opp_'+k:v for k,v in teams[1-i].items()}) for i in (0,1)], refs


def fit(rows, cid, cutoff):
    import numpy as np
    from sklearn.linear_model import PoissonRegressor
    from threadpoolctl import threadpool_limits
    at = instant(cutoff)
    history = [r for r in history_rows(rows,cid) if instant(r['kickoff'])<at and instant(r['result_available_at'])<=at]
    x,y,ids,excluded = [],[],[],[]
    for n,row in enumerate(history):
        try: v,_ = vectors(history[:n],row,instant(row['kickoff'])-timedelta(hours=1),historical=True)
        except ValueError as exc:
            excluded.append({'id':row['id'],'reason':str(exc)})
            continue
        x.extend([[s[k] for k in FIELDS] for s in v]);y.extend([row['home_goals'],row['away_goals']]);ids.append(row['id'])
    if len(ids)<100: raise ValueError('增强拟合有效样本不足100场')
    x=np.asarray(x); y=np.asarray(y); center=x.mean(0);scale=x.std(0);scale[scale<1e-8]=1
    with threadpool_limits(limits=1):
        reg=PoissonRegressor(alpha=.08,max_iter=500).fit((x-center)/scale,y)
    halves = [r for r in history if r.get('home_half_goals') is not None and r.get('away_half_goals') is not None]
    shares = None
    if len(halves)>=100:
        shares=[(1+sum(r[s+'_half_goals'] for r in halves))/(2+sum(r[s+'_goals'] for r in halves)) for s in ('home','away')]
    artifact={'schema':SCHEMA,'competition_id':cid,'trained_before':at.isoformat(),'fields':FIELDS,
        'mean':center.tolist(),'scale':scale.tolist(),'coef':reg.coef_.tolist(),'intercept':float(reg.intercept_),
        'history':history,'half_shares':shares,'train_count':len(ids),'train_ids':ids,'excluded':excluded,
        'history_count':len(history),'availability_policy':'historical_xg_plus_7_days_assumption; live_requires_capture',
        'trained_inputs':['recent_goals','recent_xg','league_rest'],
        'not_trained':['injuries','lineup','replacement_quality','tactics','cross_competition_load'],
        'evaluation_status':'candidate_not_selected_no_new_prospective_evidence'}
    artifact['digest']=fingerprint(artifact)
    return artifact


def save(artifact):
    directory=ROOT/artifact['competition_id'];directory.mkdir(parents=True,exist_ok=True)
    path=directory/(artifact['digest']+'.json')
    raw=encode(artifact)
    if path.exists() and path.read_text(encoding='utf-8')!=raw: raise ValueError('研究产物不可覆盖')
    if not path.exists(): path.write_text(raw,encoding='utf-8')
    return str(path)


def load(path, cid):
    path=Path(path)
    if not path.is_absolute(): path=Path(__file__).resolve().parents[2]/path
    artifact=json.loads(path.read_text(encoding='utf-8'))
    if artifact.get('competition_id')!=cid or artifact.get('schema')!=SCHEMA or fingerprint({k:v for k,v in artifact.items() if k!='digest'})!=artifact.get('digest'):
        raise ValueError('增强模型身份或摘要无效')
    return artifact


def feature_packet(artifact, fixture, as_of):
    if fixture['competition_id']!=artifact['competition_id'] or instant(artifact['trained_before'])>instant(as_of): raise ValueError('模型赛事或训练截止无效')
    vs,refs=vectors(artifact['history'],fixture,as_of)
    evidence=[]
    for side,v in enumerate(vs):
        for k in FIELDS:
            fact={'kind':'model_feature','side':('home','away')[side], 'field':k,'value':v[k],
                'source':'verified_history_aggregate','published_at':instant(as_of).isoformat(),
                'captured_at':instant(as_of).isoformat(),'history_refs':refs[side],
                'text':f'{("home","away")[side]} {k}={v[k]}'}
            fact['id']='feature-'+fingerprint(fact)[:20];evidence.append(fact)
    return {'schema':'observation-evidence-v1','fixture':fixture,'as_of':instant(as_of).isoformat(),
            'evidence':evidence,'missing':artifact['not_trained'],'excluded':[], 'artifact_digest':artifact['digest'],
            'history_latest_kickoff':max(r['kickoff'] for r in artifact['history']),
            'history_scope':'frozen_training_dataset; later_results_not_automatically_ingested'}


def forecast(artifact, fixture, as_of, extracted=None, handicap=None):
    from .football_markets import score_grid
    packet=feature_packet(artifact,fixture,as_of)
    expected={r['id']:r for r in packet['evidence']}
    if extracted is not None:
        actual={r['id']:r for r in extracted['facts']}
        if any(actual.get(k)!=v for k,v in expected.items()): raise ValueError('AI提取未完整保留受测特征，增强预测不可用')
    mu=[]
    for side in ('home','away'):
        values={r['field']:r['value'] for r in expected.values() if r['side']==side}
        score=artifact['intercept']+sum((values[k]-artifact['mean'][i])/artifact['scale'][i]*artifact['coef'][i] for i,k in enumerate(FIELDS))
        mu.append(max(.05,min(8,math.exp(score))))
    probabilities=market_probabilities(score_grid(*mu),handicap,artifact.get('half_shares'))
    return {'probabilities':probabilities,'supported_markets':list(probabilities), 'model_id':artifact['digest'],
        'input_metadata':{'actually_used_fields':FIELDS,'enhancement_kind':'ai_evidence_selected_xg_rest' if extracted else 'deterministic_xg_rest',
            'not_trained':artifact['not_trained'],'availability_policy':artifact['availability_policy'],
            'history_latest_kickoff':packet['history_latest_kickoff'],'history_scope':packet['history_scope']},
        'input_snapshot':packet,'research_only':True}
