"""Forecast genuinely future stored fixtures with the newly fitted personnel candidates."""
from datetime import datetime,timezone
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console.store import ROOT,Store
from worldcup_mvp.console.observation_features import instant,build_packet
from worldcup_mvp.console.observation_personnel_models import load,forecast
from worldcup_mvp.console.observation_personnel_sources import schedule_evidence,fpl_evidence


def main():
    as_of=datetime.now(timezone.utc).isoformat()
    index=json.loads((ROOT/'data/research/observation-personnel-models/candidate-index.json').read_text(encoding='utf-8'))
    sourceidx=json.loads((ROOT/'data/research/observation-personnel-sources/latest.json').read_text(encoding='utf-8'))
    sources=json.loads((ROOT/sourceidx['file']).read_text(encoding='utf-8'))
    fpl=json.loads((ROOT/sources['fpl']['raw_reference']).read_text(encoding='utf-8'))
    with Store().connect() as db:fixtures=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM observation_fixtures')]
    report={'as_of':as_of,'leagues':{},'excluded':[]}
    for cid,variants in index.items():
        artifacts={v:load(r['path'],cid) for v,r in variants.items()}
        for fixture in sorted([f for f in fixtures if f['competition_id']==cid and instant(f['kickoff'])>instant(as_of)],key=lambda f:f['kickoff']):
            try:predictions={v:forecast(a,fixture,as_of) for v,a in artifacts.items()}
            except ValueError as exc:report['excluded'].append({'id':fixture['id'],'error':str(exc)});continue
            facts=[]
            for side in ('home','away'):
                snapshot=load(sources['teams'][fixture[side+'_id']]['path'],cid)
                facts.append(schedule_evidence(fixture,snapshot,as_of))
            facts+=fpl_evidence(fixture,fpl['data'],fpl['captured_at'],sources['fpl']['team_mapping'])
            packet=build_packet(fixture,{'evidence':facts},as_of)
            report['leagues'][cid]={'fixture':fixture,'predictions':predictions,'auxiliary_packet':packet}
            break
    path=ROOT/'data/research/observation-personnel-models'/('live-smoke-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'.json')
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(path.relative_to(ROOT)),
        'leagues':{cid:{'fixture':r['fixture']['id'],'candidate_count':len(r['predictions']),'evidence':len(r['auxiliary_packet']['evidence']),
          'markets':r['predictions']['xg_personnel_coach']['supported_markets']} for cid,r in report['leagues'].items()},'excluded':report['excluded']},ensure_ascii=True))


if __name__=='__main__':main()
