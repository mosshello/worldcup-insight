"""Bounded explicit research collection, no registry mutations.
Example: python scripts/collect_espn_competitions.py --source uefa.champions --years 2021 2022 2023 2024 2025 2026
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console.espn_competition_history import SOURCE_SPECS,fetch,prepare,verify_receipt_time


def collect(source,years,root,workers=4,allow_qualifying=False):
    folder=Path(root)/source;folder.mkdir(parents=True,exist_ok=True)
    boards=[];receipts=[];errors=[];summaries={}
    for year in years:
        # Existing probes retain their actual save-time receipt, never invent HTTP status.
        probe=folder/(str(year)+'.json')
        if probe.exists():
            import hashlib
            raw=probe.read_bytes();board=json.loads(raw)
            receipt={'url':f'https://site.api.espn.com/apis/site/v2/sports/soccer/{source}/scoreboard?dates={year}&limit=1000',
                     'raw_path':str(probe),'sha256':hashlib.sha256(raw).hexdigest(),
                     'observed_at':datetime.fromtimestamp(probe.stat().st_mtime,timezone.utc).isoformat(),'receipt_basis':'probe_file_save_time'}
        else:board,receipt=fetch(source,f'scoreboard?dates={year}&limit=1000',folder/'raw')
        verify_receipt_time(receipt)
        if len(board.get('events',[]))>=1000:raise ValueError('source_response_possibly_truncated')
        boards.append(board);receipts.append(receipt)
    spec=SOURCE_SPECS[source]
    events={str(e['id']):e for b in boards for e in b.get('events',[])}
    wanted=[eid for eid,e in events.items() if e.get('season',{}).get('slug') in spec['stages']
            and e.get('competitions',[{}])[0].get('status',{}).get('type',{}).get('name')=='STATUS_FULL_TIME']
    print(json.dumps({'source':source,'events':len(events),'summary_requests':len(wanted)}),flush=True)
    def one(eid):
        try:
            summary,receipt=fetch(source,'summary?event='+eid,folder/'raw')
            verify_receipt_time(receipt)
            return eid,{'header':summary.get('header',{})},receipt,None
        except Exception as exc:return eid,None,None,str(exc)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for count,future in enumerate(as_completed([pool.submit(one,eid) for eid in wanted]),1):
            eid,summary,receipt,error=future.result()
            if error:errors.append({'id':eid,'error':error})
            else:summaries[eid]=summary;receipts.append(receipt)
            if count%100==0:print(json.dumps({'source':source,'summaries_done':count,'failed':len(errors)}),flush=True)
    result=prepare(source,boards,summaries,datetime.now(timezone.utc).isoformat(),
                   'User-approved all-competition data completion 2026-11-04',allow_qualifying)
    result['manifest']['source_receipts']=receipts
    result['manifest']['collection_errors']=errors
    result['manifest']['collection_status']='partial' if errors else 'completed_requested_scope'
    (folder/'research.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'source':source,'rows':len(result['matches']),'coverage':result['coverage'],'errors':len(errors)}),flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',required=True,choices=SOURCE_SPECS)
    parser.add_argument('--years',nargs='+',required=True,type=int)
    parser.add_argument('--root',default='.ai/tmp/remaining-coverage');parser.add_argument('--workers',default=4,type=int)
    parser.add_argument('--allow-qualifying',action='store_true');args=parser.parse_args()
    if not 1<=args.workers<=8:parser.error('workers must be 1..8')
    collect(args.source,args.years,args.root,args.workers,args.allow_qualifying)
