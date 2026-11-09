"""Independent research fits with past personnel usage; never production selection."""
from datetime import timedelta
import json
import math
from pathlib import Path
from .store import ROOT, fingerprint, encode
from .observation_features import instant
from .observation_models import history_rows, vectors, FIELDS
from .observation_personnel import personnel_features, h2h_features, PERSONNEL_FIELDS, COACH_FIELDS,H2H_FIELDS
from .observation_club_load import club_features,load_club_history,FIELDS as CLUB_FIELDS

OUT=ROOT/'data/research/observation-personnel-models'
SCHEMA='past-personnel-poisson-v1'
VARIANTS={'xg_base':FIELDS,'xg_personnel':FIELDS+PERSONNEL_FIELDS+['opp_'+k for k in PERSONNEL_FIELDS],
          'xg_coach':FIELDS+COACH_FIELDS+['opp_'+k for k in COACH_FIELDS],
          'xg_personnel_coach':FIELDS+PERSONNEL_FIELDS+COACH_FIELDS+['opp_'+k for k in PERSONNEL_FIELDS+COACH_FIELDS]}
VARIANTS['xg_personnel_coach_h2h']=VARIANTS['xg_personnel_coach']+H2H_FIELDS+['opp_'+k for k in H2H_FIELDS]
VARIANTS['xg_personnel_coach_h2h_clubload']=VARIANTS['xg_personnel_coach_h2h']+CLUB_FIELDS+['opp_'+k for k in CLUB_FIELDS]


def prepare_samples(rows, personnel, club_history=None):
    cid=personnel['competition_id'];history=history_rows(rows,cid);samples=[];excluded=[]
    club_history=club_history or load_club_history(cid)
    from collections import defaultdict
    club_teams=defaultdict(list);person_teams=defaultdict(list)
    for r in club_history['rows']:club_teams[r['team_id']].append(r)
    for r in personnel['rows']:person_teams[r['team_id']].append(r)
    original={r['id']:r for r in rows}
    for r in history:r['result_captured_at']=original[r['id']].get('collected_at')
    for r in rows:
        at=instant(r['kickoff'])-timedelta(hours=1)
        try:
            x,xrefs=vectors(history,r,at,historical=True)
            p,prefs=personnel_features({**personnel,'rows':person_teams[r['home_id']]+person_teams[r['away_id']]},r,at,historical=True)
            hh,hhrefs=h2h_features(history,r,at,historical=True)
            cc,ccrefs=club_features({**club_history,'rows':club_teams[r['home_id']]+club_teams[r['away_id']]},r,at,historical=True)
            samples.append({'id':r['id'],'competition_id':cid,'kickoff':r['kickoff'],'as_of':at.isoformat(),
                'available_at':max(instant(r['result_available_at']),instant(r['kickoff'])+timedelta(days=7)).isoformat(),
                'vectors':[dict(x[i],**p[i],**hh[i],**cc[i]) for i in (0,1)],'goals':[r['home_goals'],r['away_goals']],
                'half_goals':[r.get('home_half_goals'),r.get('away_half_goals')],
                'history_refs':xrefs,'personnel_refs':prefs,'h2h_refs':hhrefs,'club_load_refs':ccrefs})
        except ValueError as exc:excluded.append({'id':r['id'],'reason':str(exc)})
    return samples,excluded,history


def immutable(payload, directory):
    payload=dict(payload);payload['digest']=fingerprint(payload)
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    path=directory/(payload['digest']+'.json');raw=encode(payload)
    if path.exists() and path.read_text(encoding='utf-8')!=raw:raise ValueError('不可变研究产物冲突')
    if not path.exists():path.write_text(raw,encoding='utf-8')
    return str(path.relative_to(ROOT)).replace('\\','/')


def load(path, competition_id):
    path=Path(path);path=path if path.is_absolute() else ROOT/path
    a=json.loads(path.read_text(encoding='utf-8'))
    if a.get('competition_id')!=competition_id or fingerprint({k:v for k,v in a.items() if k!='digest'})!=a.get('digest'):
        raise ValueError('人员候选产物身份或摘要无效')
    return a


def fit(samples, competition_id, cutoff, variant, history_reference, personnel_reference, club_reference=None):
    import numpy as np
    from sklearn.linear_model import PoissonRegressor
    from threadpoolctl import threadpool_limits
    if any(r['competition_id']!=competition_id for r in samples):raise ValueError('人员候选不能跨赛事训练')
    fields=VARIANTS[variant]
    eligible=[r for r in samples if instant(r['available_at'])<=instant(cutoff)]
    if len(eligible)<100:raise ValueError('人员候选训练样本不足')
    x=np.array([[v[k] for k in fields] for r in eligible for v in r['vectors']],dtype=float)
    y=np.array([g for r in eligible for g in r['goals']],dtype=float)
    mean=x.mean(0);scale=x.std(0);scale[scale<1e-9]=1
    with threadpool_limits(limits=1):reg=PoissonRegressor(alpha=.08,max_iter=500).fit((x-mean)/scale,y)
    halves=[r for r in eligible if all(v is not None for v in r['half_goals'])]
    fractions=[(sum(r['half_goals'][i] for r in halves)+1)/(sum(r['goals'][i] for r in halves)+2) for i in (0,1)] if len(halves)>=100 else None
    return {'schema':SCHEMA,'competition_id':competition_id,'variant':variant,'fields':fields,
        'train_before':instant(cutoff).isoformat(),'train_ids':[r['id'] for r in eligible],'train_count':len(eligible),
        'mean':mean.tolist(),'scale':scale.tolist(),'coef':reg.coef_.tolist(),'intercept':float(reg.intercept_),
        'half_shares':fractions,'history_reference':history_reference,'personnel_reference':personnel_reference,'club_reference':club_reference,
        'training_availability':'retrospective_plus_7_days_assumption',
        'not_trained':['target_starting_xi','expected_minutes','injury_eligibility','replacement_quality','complete_cross_competition_load'],
        'research_only':True,'automatic_selection':False}


def probabilities(artifact, vs, handicap=None):
    from .football_markets import score_grid,market_probabilities
    mu=[max(.05,min(8,math.exp(artifact['intercept']+sum((v[k]-artifact['mean'][j])/artifact['scale'][j]*artifact['coef'][j] for j,k in enumerate(artifact['fields']))))) for v in vs]
    return market_probabilities(score_grid(*mu),handicap,artifact['half_shares'])


def forecast(artifact, fixture, as_of, handicap=None):
    cid=fixture['competition_id']
    if artifact['competition_id']!=cid or artifact['schema']!=SCHEMA or instant(artifact['train_before'])>instant(as_of):
        raise ValueError('人员模型赛事或训练截止不符')
    history=load(artifact['history_reference'],cid)
    personnel=load(artifact['personnel_reference'],cid)
    x,xrefs=vectors(history['rows'],fixture,as_of,historical=False)
    p,prefs=personnel_features(personnel,fixture,as_of,historical=False)
    hh,hhrefs=h2h_features(history['rows'],fixture,as_of,historical=False) if 'h2h_count' in artifact['fields'] else ([{},{}],[])
    cc,ccrefs=club_features(load(artifact['club_reference'],cid),fixture,as_of,historical=False) if 'club_load_missing' in artifact['fields'] else ([{},{}],[])
    probs=probabilities(artifact,[dict(x[i],**p[i],**hh[i],**cc[i]) for i in (0,1)],handicap)
    return {'probabilities':probs,'supported_markets':list(probs),'model_id':artifact['digest'],
        'input_metadata':{'actually_used_fields':artifact['fields'],'not_trained':artifact['not_trained'],
            'enhancement_kind':'research_'+artifact['variant'],'variant':artifact['variant'],'availability':'actual_capture',
            'club_load_scope':'observed_club_fixtures_not_national_team_player_load' if 'club_load_missing' in artifact['fields'] else 'not_used'},
        'input_snapshot':{'fixture':fixture,'as_of':instant(as_of).isoformat(),'history_refs':xrefs,'personnel_refs':prefs,'h2h_refs':hhrefs,'club_load_refs':ccrefs},'research_only':True}
