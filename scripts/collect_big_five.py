"""Collect five independent research datasets; bounded resumable requests."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console.big_five_history import SPECS, fetch, parse_event, enrich, coverage


def collect(slug, root, workers):
    folder = root/slug; folder.mkdir(parents=True, exist_ok=True)
    events = {}; excluded = []; receipts = []
    for year in range(2021, 2027):
        board, receipt = fetch(slug, f'scoreboard?dates={year}&limit=1000', folder/'raw')
        if not any(str(l['id'])==SPECS[slug][2] and l['slug']==slug for l in board.get('leagues',[])):
            raise ValueError('board_competition_mismatch')
        if not board.get('events') or len(board['events']) >= 1000: raise ValueError('empty_or_truncated_board')
        receipts.append(receipt)
        for event in board['events']:
            eid=str(event['id'])
            if eid in events and (events[eid]['date'] != event['date'] or events[eid]['competitions'][0]['competitors'] != event['competitions'][0]['competitors']):
                raise ValueError('conflicting_duplicate_event')
            events[eid]=event
    rows = []
    observed = datetime.now(timezone.utc).isoformat()
    for event in events.values():
        try: rows.append(parse_event(slug,event,observed))
        except (ValueError,KeyError,TypeError) as exc: excluded.append({'id':str(event['id']),'reason':str(exc)})
    rows.sort(key=lambda r:(r['kickoff'],r['id']))
    keys=[(r['home_id'],r['away_id'],r['kickoff']) for r in rows]
    if len(keys)!=len(set(keys)): raise ValueError('duplicate_fixture')
    print(json.dumps({'source':slug,'scoreboard_rows':len(rows)},ensure_ascii=False),flush=True)
    def one(row):
        for attempt in range(2):
            try:
                detail, receipt=fetch(slug,'summary?event='+row['id'].split(':')[1],folder/'raw')
                return enrich(row,detail,receipt),None
            except Exception as exc:
                error=str(exc)
                if attempt or not any(k in error for k in ('request','timeout','timed out')):break
        row['training_eligible']=False
        row['summary_verified']=False
        return row,{'id':row['id'],'reason':error}
    completed=[];errors=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i,future in enumerate(as_completed([pool.submit(one,r) for r in rows]),1):
            row,error=future.result();completed.append(row)
            if error:errors.append(error)
            if i%100==0:print(json.dumps({'source':slug,'done':i,'total':len(rows),'errors':len(errors)}),flush=True)
    completed.sort(key=lambda r:(r['kickoff'],r['id']))
    data={'manifest':{'competition_id':SPECS[slug][0],'name':SPECS[slug][1], 'source':slug,
        'created_at':datetime.now(timezone.utc).isoformat(),'requested_seasons':'2021/22 through 2026/27 played matches',
        'authorization_reference':'User requested five-league data completion 2026-11-05',
        'usage_mode':'local_research','rights_status':'unverified','commercial_use_permitted':False,
        'availability_policy':'Historical seven-day delay assumption; no contemporary pre-match archive',
        'quality_basis':'Single provider scoreboard/summary agreement, not independent source confirmation',
        'board_receipts':receipts,'errors':errors,'excluded':excluded,
        'missing':['historical pre-match injuries','timestamped historical odds','pre-match lineup publication times'],
        'not_imported':True,'not_trained':True},'coverage':coverage(slug,completed),'matches':completed}
    (folder/'research.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'source':slug,'finished':len(completed),'coverage':[{k:v for k,v in c.items() if k!='missing_pairings'} for c in data['coverage']],'errors':errors[:5]},ensure_ascii=False),flush=True)
    return data


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--sources',nargs='+',choices=SPECS,default=list(SPECS));p.add_argument('--root',type=Path,required=True);p.add_argument('--workers',type=int,default=6);a=p.parse_args()
    if not 1<=a.workers<=8:p.error('workers must be 1..8')
    for slug in a.sources:collect(slug,a.root,a.workers)
