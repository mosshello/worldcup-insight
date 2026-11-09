"""Read-only quote replay; save immutable research artifacts, never enable rules."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console.observation_market_research import analyze_timeline,fit_rule,save_artifact,WINDOWS
from worldcup_mvp.console.observations import COMPETITIONS

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--train-end',required=True)
    p.add_argument('--validation-end',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    db=sqlite3.connect(Path(a.db).resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    summaries=[]
    for cid in COMPETITIONS:
        events=[];timelines=[]
        for row in db.execute("SELECT * FROM observation_fixture_versions WHERE json_extract(payload,'$.competition_id')=?",(cid,)):
            fixture=json.loads(row['payload'])
            quotes=[json.loads(q[0]) for q in db.execute('SELECT payload FROM observation_quotes WHERE fixture_id=? AND revision=? ORDER BY captured_at',(fixture['id'],row['revision']))]
            timeline=analyze_timeline(fixture,quotes,a.validation_end);events.extend(timeline['events']);timelines.append(timeline)
        report=fit_rule(events,cid,a.train_end,a.validation_end);path=save_artifact(report,a.output)
        coverage={'competition_id':cid,'fixtures':len(timelines),'covered_fixture_seconds':sum(t['covered_seconds'] for t in timelines),'events':len(events),
            'buckets':[{ 'window':name,'observed_fixture_seconds':sum(t['buckets'][i]['observed_fixture_seconds'] for t in timelines),'events':sum(t['buckets'][i]['events'] for t in timelines)} for i,(name,_,_) in enumerate(WINDOWS)]}
        for bucket in coverage['buckets']:bucket['events_per_observed_hour']=bucket['events']/(bucket['observed_fixture_seconds']/3600) if bucket['observed_fixture_seconds'] else None
        summaries.append({'coverage':coverage,'status':report['status'],'artifact':str(path)})
    print(json.dumps(summaries,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
