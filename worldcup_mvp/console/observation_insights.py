"""Read projections for research and evidence; no training or provider calls."""
import json
from pathlib import Path
from .observations import COMPETITIONS, stamp, iso
from .observation_evidence import evidence_packet
from . import observation_models, model_registry

ROOT=Path(__file__).resolve().parents[2]


def evidence_summary(fixture, packet, raw=None):
    captured=packet.get('captured_at')
    result={'captured_at':captured,'source_url':packet.get('source_url'),
            'personnel':[],'workload':[],'unknown':[],'alerts':[],
            'rosters':packet.get('rosters',{}).get('facts',[]),
            'basis':'latest_collected_facts_not_archived_prediction_inputs'}
    if not captured or stamp(captured)>=stamp(fixture['kickoff']):
        result['rosters']=[]
        result['unknown']=['无可核验的赛前资料快照']
        return result
    context=packet.get('context',{})
    context={**context,'evidence':context.get('evidence',[])+packet.get('supplemental',{}).get('context',{}).get('evidence',[])}
    facts=evidence_packet(fixture,context,captured,
                          summary=raw,captured_at=packet.get('summary_captured_at',captured))
    for fact in facts['evidence']:
        kind=fact.get('kind')
        if kind in ('personnel','lineup','expected_lineup','relationship'):
            result['personnel'].append(fact)
        elif kind=='workload': result['workload'].append(fact)
        if fact.get('conflicts'):
            result['alerts'].append({'kind':'conflict','status':'unresolved','message':'人员来源存在冲突','evidence':[fact['id'],*fact['conflicts']]})
    coverage=facts.get('coverage',{})
    labels={'full_player_load':'完整球员跨赛事负荷','replacement_ability':'替补能力',
            'expected_minutes':'本场预计分钟','complete_registration':'完整注册及参赛资格','personnel':'完整伤停名单'}
    result['unknown']=[label for key,label in labels.items() if coverage.get(key) in ('unavailable','partial',None)]
    if len(coverage.get('confirmed_lineups',[]))<2: result['unknown'].append('双方确认首发')
    result['coverage']=coverage
    return result


def research_status(store):
    """Background-only file verification and aggregation; callers persist the projection."""
    path=ROOT/'data/research/observation-models/paired-evaluation-2025.json'
    evaluation=json.loads(path.read_text(encoding='utf8')) if path.exists() else {}
    personnel_root=ROOT/'data/research/observation-personnel-models'
    personnel_index=json.loads((personnel_root/'candidate-index.json').read_text(encoding='utf8')) if (personnel_root/'candidate-index.json').exists() else {}
    personnel_tests=json.loads((personnel_root/'ablation-2025.json').read_text(encoding='utf8')) if (personnel_root/'ablation-2025.json').exists() else {}
    tree_root=ROOT/'data/research/observation-personnel-trees'
    tree_index=json.loads((tree_root/'candidate-index.json').read_text(encoding='utf8')) if (tree_root/'candidate-index.json').exists() else {}
    tree_tests=json.loads((tree_root/'evaluation.json').read_text(encoding='utf8')) if (tree_root/'evaluation.json').exists() else {}
    rows=[]
    for cid in COMPETITIONS:
        with store.connect() as db:
            exp=db.execute('SELECT config FROM observation_experiments WHERE competition_id=? ORDER BY created_at DESC LIMIT 1',(cid,)).fetchone()
            total=db.execute('SELECT count(*) FROM observation_fixtures WHERE competition_id=?',(cid,)).fetchone()[0]
        if not exp: continue
        config=json.loads(exp[0]);selected=model_registry.selection(cid)
        item={'competition_id':cid,'coverage':{'targets':total},'alerts':[]}
        try:
            manifest,bundle,_,_=model_registry.load_run(cid,config['base_run_id'])
            model=bundle['models'][cid]
            item['base']={'run_id':manifest['run_id'],'model_id':manifest['model_id'],
                'status':'research_baseline','selected':selected.get('run_id')==manifest['run_id'],
                'trained_before':model.get('trained_before'),
                'input_metadata':{'actually_used_fields':['historical_goals','home_advantage','time_decay','half_time_goals']},
                'coverage':{'training_samples':model.get('sample_count')},'reports':[]}
        except (ValueError,KeyError,OSError):
            item['base']={'status':'invalid_artifact','selected':False,'reports':[]}
        digest=config.get('enhanced_digest')
        try:
            artifact=observation_models.load(observation_models.ROOT/cid/(digest+'.json'),cid)
            tested=evaluation.get('leagues',{}).get(cid,{})
            item['enhanced']={'model_id':digest,'digest':digest,'status':'candidate','selected':False,
                'trained_before':artifact.get('train_before'),
                'input_metadata':{'actually_used_fields':artifact['trained_inputs'],'not_trained':artifact['not_trained']},
                'coverage':{'training_samples':artifact['train_count'],'history_samples':artifact['history_count'],
                            'excluded':len(artifact['excluded'])},
                'reports':[{'name':'固定赛季前历史对照','status':'exploratory','source':'paired-evaluation-2025',
                            'sample_size':tested.get('common_n',0),'metrics':tested.get('metrics',{}),
                            'evaluated_model_id':tested.get('candidate'),'current_artifact_evaluation':False}]}
            item['alerts'].append({'kind':'validation','status':'not_promoted','message':'历史对照未证明稳定提升，研究候选未选入生产','evidence':['paired-evaluation-2025']})
        except (OSError,ValueError,KeyError,TypeError):
            item['enhanced']={'status':'unavailable','selected':False,'reports':[]}
        try:
            from .observation_personnel_models import load
            variants={k:load(v['path'],cid) for k,v in personnel_index[cid].items()}
            artifact=variants['xg_personnel_coach'];tested=personnel_tests['leagues'][cid]
            item['personnel']={'model_id':artifact['digest'],'status':'candidate','selected':False,
                'trained_before':artifact['train_before'],
                'input_metadata':{'actually_used_fields':artifact['fields'],'not_trained':artifact['not_trained']},
                'coverage':{'training_samples':artifact['train_count'],'history_samples':tested['dataset_matches'],
                            'player_minute_rows':tested['minute_rows'],'feature_excluded':len(tested['feature_exclusions'])},
                'variants':[{'variant':k,'model_id':a['digest'],'fields':a['fields'],'train_count':a['train_count']} for k,a in variants.items()],
                'reports':[{'name':'人员、教练与交锋消融','status':'exploratory','sample_size':tested['common_count'],
                    'metrics':tested['metrics'],'source':'observation-personnel-ablation-2025',
                    'evaluated_model_id':Path(tested['retrospective_artifacts']['xg_personnel_coach']).stem,
                    'current_artifact_evaluation':False}]}
        except (OSError,ValueError,KeyError,TypeError):
            item['personnel']={'status':'unavailable','selected':False,'reports':[]}
        item['research_candidates']=[]
        for kind,entry in tree_index.get(cid,{}).items():
            try:
                from .observation_personnel_models import load
                artifact=load(entry['path'],cid);tested=tree_tests['leagues'][cid]
                item['research_candidates'].append({'kind':'catboost' if kind=='catboost_had' else kind,
                    'model_id':artifact['digest'],'status':'candidate','selected':False,'trained_before':artifact['train_before'],
                    'supported_markets':artifact['supported_markets'],
                    'input_metadata':{'actually_used_fields':artifact['fields'],'not_trained':artifact['not_trained']},
                    'coverage':{'training_samples':artifact['train_count'],'test_targets':tested['target_count'],'common_test_samples':tested['common_count']},
                    'reports':[{'name':'固定特征算法对照','status':'exploratory','sample_size':tested['common_count'],
                        'metrics':tested['metrics'],'source':'personnel-tree-evaluation-2025',
                        'evaluated_model_id':Path(tested['retrospective_artifacts'][kind]).stem,
                        'current_artifact_evaluation':False}]})
            except (OSError,ValueError,KeyError,TypeError):
                item['research_candidates'].append({'kind':kind,'status':'unavailable','selected':False,'reports':[]})
        rows.append(item)
    return {'competitions':rows,'updated_at':iso(),'scope':'research_only'}


def read_research(store, competition=''):
    with store.connect() as db:
        row=db.execute("SELECT payload FROM observation_source_status WHERE id='research_status'").fetchone()
    result=json.loads(row[0]).get('value',{}) if row else {'competitions':[],'status':'pending'}
    if competition: result={**result,'competitions':[r for r in result.get('competitions',[]) if r['competition_id']==competition]}
    return result


def read_evidence(store, fixture):
    if not fixture: return None
    from .store import fingerprint
    from .observations import identity
    with store.connect() as db:
        row=db.execute('SELECT payload FROM observation_packets WHERE fixture_id=? AND revision=? ORDER BY captured_at DESC LIMIT 1',
                       (fixture['id'],fingerprint(identity(fixture)))).fetchone()
        if not row: return {'personnel':[],'workload':[],'unknown':['尚未采集逐场资料'],'alerts':[]}
        packet=json.loads(row[0])
        if packet.get('evidence_summary'): return packet['evidence_summary']
        raw=packet.get('raw')
        if raw is None:
            receipt=db.execute('SELECT payload FROM observation_raw_receipts WHERE digest=?',(packet.get('raw_digest'),)).fetchone()
            raw=json.loads(receipt[0]) if receipt else None
    try:
        return evidence_summary(fixture,packet,raw)
    except (ValueError,KeyError,TypeError):
        return {'personnel':[],'workload':[],'unknown':['资料身份或时间待核验'],'alerts':[]}
