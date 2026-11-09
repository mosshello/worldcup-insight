"""Separate CatBoost HDA and LightGBM goal candidates on frozen personnel features."""
import json,tempfile
from pathlib import Path
import numpy as np
from .observation_features import instant
from .observation_personnel_models import VARIANTS,load
from .observation_personnel import personnel_features,h2h_features
from .observation_club_load import club_features
from .observation_models import vectors
from .football_markets import market_probabilities,score_grid

SCHEMA='personnel-tree-research-v1'
FEATURE_VERSION='xg-personnel-coach-h2h-clubload-v1'
FIELDS=VARIANTS['xg_personnel_coach_h2h_clubload']
CONFIGS={'catboost_had':{'iterations':200,'depth':4,'learning_rate':.03,'loss_function':'MultiClass',
                       'random_seed':20261109,'thread_count':1,'allow_writing_files':False,'verbose':False},
         'lightgbm_goals':{'objective':'poisson','n_estimators':200,'max_depth':4,'num_leaves':15,
                          'learning_rate':.03,'min_child_samples':40,'reg_lambda':5,
                          'random_state':20261109,'n_jobs':1,'verbosity':-1,'deterministic':True,'force_col_wise':True}}
_MODELS={}


def fit(samples,competition_id,cutoff,kind,references):
    if kind not in CONFIGS:raise ValueError('未知人员树模型')
    if any(s['competition_id']!=competition_id for s in samples):raise ValueError('树模型不可跨赛事训练')
    eligible=[s for s in samples if instant(s['available_at'])<=instant(cutoff)]
    if len(eligible)<100:raise ValueError('人员树模型样本不足')
    if kind=='catboost_had':
        import catboost
        x=np.asarray([[s['vectors'][0][k] for k in FIELDS] for s in eligible])
        y=np.asarray([0 if s['goals'][0]>s['goals'][1] else 2 if s['goals'][0]<s['goals'][1] else 1 for s in eligible])
        if set(y)!={0,1,2}:raise ValueError('胜平负训练类别不完整')
        model=catboost.CatBoostClassifier(**CONFIGS[kind]).fit(x,y)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'candidate.json';model.save_model(str(path),format='json');native=json.loads(path.read_text(encoding='utf-8'))
        library={'name':'catboost','version':catboost.__version__};half_shares=None
    else:
        import lightgbm
        x=np.asarray([[v[k] for k in FIELDS] for s in eligible for v in s['vectors']])
        y=np.asarray([g for s in eligible for g in s['goals']])
        model=lightgbm.LGBMRegressor(**CONFIGS[kind]).fit(x,y)
        native=model.booster_.model_to_string();library={'name':'lightgbm','version':lightgbm.__version__}
        halves=[s for s in eligible if all(v is not None for v in s['half_goals'])]
        half_shares=[(1+sum(s['half_goals'][i] for s in halves))/(2+sum(s['goals'][i] for s in halves)) for i in (0,1)] if len(halves)>=100 else None
    return {'schema':SCHEMA,'competition_id':competition_id,'kind':kind,'feature_version':FEATURE_VERSION,'fields':FIELDS,
        'train_before':instant(cutoff).isoformat(),'train_ids':[s['id'] for s in eligible],'train_count':len(eligible),
        'config':CONFIGS[kind],'library':library,'native_model':native,'half_shares':half_shares,
        'references':references,'supported_markets':['had'] if kind=='catboost_had' else ['had','hhad','ttg','crs']+(['hafu'] if half_shares else []),
        'availability_policy':{'historical_personnel_xg':'plus_7_days_assumption','historical_club_load':'day_end_plus_7_days_assumption','live':'actual_capture'},
        'not_trained':['target_starting_xi','expected_minutes','injury_eligibility','replacement_quality','national_team_player_load'],
        'automatic_selection':False,'research_only':True}


def restore(artifact):
    key=id(artifact)
    if key in _MODELS and _MODELS[key][0] is artifact:return _MODELS[key][1]
    if artifact.get('schema')!=SCHEMA:raise ValueError('人员树模型版本不符')
    if artifact['kind']=='catboost_had':
        from catboost import CatBoostClassifier
        model=CatBoostClassifier()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'candidate.json';path.write_text(json.dumps(artifact['native_model']),encoding='utf-8');model.load_model(str(path),format='json')
    else:
        from lightgbm import Booster
        model=Booster(model_str=artifact['native_model'])
    if len(_MODELS)>32:_MODELS.clear()
    _MODELS[key]=(artifact,model)
    return model


def probabilities(artifact,vs,handicap=None):
    model=restore(artifact)
    if artifact['kind']=='catboost_had':
        raw=model.predict_proba(np.asarray([[vs[0][k] for k in artifact['fields']]]))[0]
        p={('h','d','a')[int(label)]:float(value) for label,value in zip(model.classes_,raw)}
        if set(p)!={'h','d','a'}:raise ValueError('分类器输出类别不完整')
        return {'had':p}
    mu=np.clip(model.predict(np.asarray([[v[k] for k in artifact['fields']] for v in vs]),num_threads=1),.05,8)
    return market_probabilities(score_grid(*[float(v) for v in mu]),handicap,artifact['half_shares'])


def forecast(artifact,fixture,as_of,handicap=None):
    cid=fixture['competition_id']
    if artifact['competition_id']!=cid or instant(artifact['train_before'])>instant(as_of):raise ValueError('人员树模型赛事或训练截止错误')
    refs=artifact['references'];history=load(refs['history'],cid);personnel=load(refs['personnel'],cid);club=load(refs['club'],cid)
    x,xr=vectors(history['rows'],fixture,as_of);p,pr=personnel_features(personnel,fixture,as_of)
    hh,hr=h2h_features(history['rows'],fixture,as_of);cc,cr=club_features(club,fixture,as_of)
    probs=probabilities(artifact,[dict(x[i],**p[i],**hh[i],**cc[i]) for i in (0,1)],handicap)
    return {'probabilities':probs,'supported_markets':list(probs),'model_id':artifact.get('digest'),
        'input_metadata':{'actually_used_fields':artifact['fields'],'feature_version':artifact['feature_version'],
            'kind':artifact['kind'],'availability':'actual_capture','not_trained':artifact['not_trained']},
        'input_snapshot':{'fixture':fixture,'as_of':instant(as_of).isoformat(),'history_refs':xr,'personnel_refs':pr,'h2h_refs':hr,'club_load_refs':cr},'research_only':True}
