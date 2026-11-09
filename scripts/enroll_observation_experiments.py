"""Explicit research enrollment; defaults to a read-only plan."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console.store import Store
from worldcup_mvp.console.observations import ObservationWorkspace,stamp

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',default='data/console/football.sqlite3')
    p.add_argument('--manifest',required=True,help='JSON map: competition_id -> explicit experiment_id')
    scope=p.add_mutually_exclusive_group(required=True);scope.add_argument('--fixture',action='append');scope.add_argument('--all-upcoming',action='store_true')
    p.add_argument('--reason',required=True);p.add_argument('--apply',action='store_true');a=p.parse_args()
    mapping=json.loads(Path(a.manifest).read_text(encoding='utf8'));store=Store(a.db);w=ObservationWorkspace(store);now=stamp()
    if not isinstance(mapping,dict) or not all(isinstance(x,str) for x in mapping.values()):raise ValueError('实验清单格式错误')
    with store.connect() as db:fixtures=[json.loads(r[0]) for r in db.execute('SELECT payload FROM observation_fixtures')]
    selected=[f for f in fixtures if f['competition_id'] in mapping and (f['id'] in a.fixture if a.fixture else f.get('status') in ('scheduled','upcoming','not_started','pre','未开赛') and stamp(f['kickoff'])>now)]
    results=[]
    for f in selected:
        exp=mapping[f['competition_id']]
        if not a.apply:results.append({'fixture_id':f['id'],'competition_id':f['competition_id'],'experiment_id':exp,'status':'planned'});continue
        try:
            record=w.enroll_experiment(f['id'],exp,now,a.reason)
            results.append({'fixture_id':f['id'],'experiment_id':exp,'status':'enrolled','enrollment_id':record['id'],'retired_jobs':len(record['retired_job_ids'])})
        except ValueError as exc:results.append({'fixture_id':f['id'],'status':'rejected','reason':str(exc)})
    if a.apply:w.schedule()
    print(json.dumps({'applied':a.apply,'results':results},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
