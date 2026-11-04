"""Explicit ACL observation selection and real prospective verification."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console.service import ConsoleService
from worldcup_mvp.console.store import Store,now_iso
from worldcup_mvp.console import model_registry

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run',required=True)
parser.add_argument('--archive',action='store_true')
parser.add_argument('--output',type=Path,default=Path('.ai/tmp/acl-prospective-verification.json'))
args=parser.parse_args()
cid='afc.champions'
manifest,bundle,report,data=model_registry.load_run(cid,args.run)
selection=model_registry.selection(cid)
if selection.get('run_id') != args.run:
    selection=model_registry.select_observation(cid,args.run,expected_revision=selection['revision'])
app=ConsoleService(Store())
app.sync(force=True)
if app.last_error: raise RuntimeError(app.last_error)
from worldcup_mvp.console.catalog import classify
matches=[m for m in app.store.list_matches() if m['id'] in app.current_ids and classify(m)['id']==cid]
result={'checked_at':now_iso(),'selection':selection,'matches':[],'scope':'current official AFC champions fixtures'}
for match in matches:
    value={'id':match['id'],'home':match['home'],'away':match['away'],'kickoff':match['kickoff']}
    try:
        detail=app.detail(match['id'],force=True)
        value.update(quality=detail['quality'],model=detail.get('briefing',{}).get('football_model'))
        if args.archive and detail['quality']['can_predict']:
            prediction=app.prediction(match['id'],request_key='acl-v2-pilot-'+args.run+'-'+match['id'])['prediction']
            value.update(archive_id=prediction['id'],probabilities=prediction['probabilities'],input_receipt=prediction['input_receipt'])
    except Exception as exc:
        value['error']=str(exc)
    result['matches'].append(value)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in value.items() if k not in ('quality','model','input_receipt')},ensure_ascii=False),flush=True)
