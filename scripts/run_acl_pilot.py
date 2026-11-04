"""Explicit local-research ACL import/experiment. Never selects or forecasts automatically."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console import catalog, model_registry
from worldcup_mvp.console.competition_rules import save
from worldcup_mvp.console.model_tasks import ModelTasks
from worldcup_mvp.console.store import Store, fingerprint


def prepare(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    cid = 'afc.champions'
    policy = {}
    for row in data['matches']:
        policy.setdefault(row['season'],set()).add(row['stage'])
    spec = {'id':cid,'name':'亚冠精英','country':None,'region':'AFC',
        'official_url':'https://www.the-afc.com/en/club/afc_champions_league_elite.html',
        'rule_source':'https://www.jleague.jp/en/outline/acle/',
        'providers':{'sporttery':'ACL','espn':cid},'training_enabled':True,
        'target_period':'regular_time','training_scope':'competition_only','cross_competition_features':False,
        'history_years':[2015,2016,2017,2019,2023,2024,2025,2026],
        'training_stage_policy':{y:sorted(stages) for y,stages in policy.items()},
        'prediction_stage_policy':{'2026':['league-stage']},
        'research_policy':{'sample_policy':data['manifest']['sample_policy'],
            'continuity_policy':data['manifest']['continuity_policy'], 'excluded_seasons':['2020','2021','2022'],
            'limitations':['部分球队近期样本少','不同赛制时期分组评估','无半场与历史盘口'],
            'usage_mode':'local_research'},
        'seasons':[{'id':'2026','label':'2026/27','start':'2026-07-01','end':'2027-07-01',
                    'format':'continental_league_stage','stages':['league-stage'],'shootout_separate':True}]}
    old = next((s for s in catalog.catalog() if s['id']==cid),None)
    if old is not None and old != spec:
        raise ValueError('亚冠已登记其他规则，请先核对修订')
    if old is None:
        save(spec,None)
    teams = {r[s+'_id'] for r in data['matches'] for s in ('home','away')}
    data['team_mappings'] = [{'competition_id':cid,'provider':'espn','provider_id':team.removeprefix('espn:'),
        'team_id':team,'source_url':data['manifest']['source_url']} for team in sorted(teams)]
    result = model_registry.import_competition(data,cid)
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    parser.add_argument('--experiment',action='store_true')
    args=parser.parse_args()
    result=prepare(args.input)
    if args.experiment:
        tasks=ModelTasks(Store())
        task=tasks.start(result['competition_id'],{'kind':'experiment','target':'full_time',
            'dataset_digest':result['dataset_digest'],'request_key':'acl-v2-pilot-'+result['dataset_digest']})
        previous=None
        while True:
            current=next(t for t in tasks.list(result['competition_id']) if t['id']==task['id'])
            state=(current['status'],current.get('phase'))
            if state!=previous:
                print(json.dumps({'id':task['id'],'status':state[0],'phase':state[1],'result':current.get('result'),'error':current.get('error')},ensure_ascii=False),flush=True)
                previous=state
            if current['status'] not in ('queued','running'):
                raise SystemExit(0 if current['status'] in ('completed','succeeded') else 1)
            time.sleep(2)
