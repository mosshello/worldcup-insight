"""Frozen multi-season rolling evaluation followed by full-data league-only fits."""
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from worldcup_mvp.console import model_registry as registry, training
from worldcup_mvp.console.model_methods import restore
from worldcup_mvp.console.model_tasks import ModelTasks
from worldcup_mvp.console.sources import parse_date
from worldcup_mvp.console.store import Store, fingerprint, now_iso
from scripts.replay_big_five_backtests import LEAGUES, summarize


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def immutable_hashes():
    paths = []
    for cid in LEAGUES:
        base = registry.directory(cid)
        for part in ('runs','datasets','reports','selections'):
            paths += list((base/part).rglob('*.json'))
        paths += [base/name for name in ('dataset.json','observation.json') if (base/name).exists()]
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def check_receipt(data, model, cutoff):
    rows = training.validate_dataset(data)
    eligible = [r for r in rows if parse_date(r['kickoff']) < cutoff and parse_date(r['result_available_at']) < cutoff]
    if len(eligible) != len(rows):
        raise ValueError('Full-data batch contains unavailable matches')
    cid = data['manifest']['competition_id']
    counts = Counter(cid+':'+r[side] for r in eligible for side in ('home_id','away_id'))
    if model.sample_count != len(eligible) or model.team_counts != dict(counts):
        raise ValueError('Fitted sample/team counts do not match all admitted rows')
    if getattr(model, 'history', None) is not None:
        if {r['id'] for r in model.history} != {r['id'] for r in eligible}:
            raise ValueError('Serialized training history omitted matches')
    return {'row_ids':[r['id'] for r in eligible], 'rows':len(eligible), 'digest':fingerprint(eligible),
            'season_counts':dict(Counter(r['season'] for r in eligible)), 'team_counts':dict(counts),
            'omitted_ids':[], 'cutoff':cutoff.isoformat()}


def verify_rolling(cid, data, report, receipts):
    if report['digest'] != fingerprint({k:v for k,v in report.items() if k!='digest'}):
        raise ValueError('Report checksum mismatch')
    rows = training.validate_dataset(data)
    start, end = parse_date(report['date_from']), parse_date(report['date_to'])
    targets = {r['id'] for r in rows if start <= parse_date(r['kickoff']) < end}
    predicted = {r['id'] for r in report['records']}
    excluded = {r['id'] for r in report['exclusions']}
    if len(predicted)!=len(report['records']) or predicted & excluded or predicted|excluded != targets:
        raise ValueError('Evaluation accounting mismatch')
    folds = {}
    dataset_digest = fingerprint(data)
    for record in report['records']:
        ref = record['artifact_ref']
        if ref['competition_id'] != cid or ref['dataset_digest'] != dataset_digest:
            raise ValueError('Cross-competition or dataset mismatch')
        digest = ref['digest']
        if digest not in folds:
            path = registry.directory(cid)/'task_artifacts'/ref['task_id']/'folds'/(digest+'.json')
            value = read(path)
            if (value['digest'] != fingerprint({k:v for k,v in value.items() if k!='digest'})
                or fingerprint(value['model']) != ref['model_digest']):
                raise ValueError('Fold checksum mismatch')
            at = parse_date(value['cutoff'])
            if value['cutoff'] not in receipts:
                eligible = [r for r in rows if parse_date(r['kickoff']) < at and parse_date(r['result_available_at']) < at]
                receipts[value['cutoff']] = {'row_ids':[r['id'] for r in eligible], 'rows':len(eligible), 'digest':fingerprint(eligible)}
            if value['input_receipt'] != receipts[value['cutoff']] | {'availability':'result_available_at < cutoff'}:
                raise ValueError('Fold omitted or leaked input rows')
            model = restore(value['model'])
            if model.sample_count != receipts[value['cutoff']]['rows']:
                raise ValueError('Fold model count mismatch')
            folds[digest] = at
        if (record['competition_id'] != cid or parse_date(record['asof']) < folds[digest]
            or parse_date(record['trained_before']) != folds[digest] or min(record['team_samples']) < 5):
            raise ValueError('Invalid prediction cutoff or team eligibility')
    return {'targets':len(targets),'predicted':len(predicted),'excluded':len(excluded),
            'exclusion_reasons':dict(Counter(r['reason'] for r in report['exclusions'])),
            'folds':len(folds),'accuracy':summarize(report['records']), 'probability_metrics':report['test'],
            'by_season':{season:{'accuracy':summarize([r for r in report['records'] if r['season']==season]),
                                 'probability_metrics':training.metrics([r for r in report['records'] if r['season']==season])}
                         for season in sorted({r['season'] for r in report['records']})}}


def run_league(args):
    cid, out = args
    out = Path(out)
    protocol = read(out/'protocol.json')
    tasks = ModelTasks(Store())
    data = registry.dataset(cid, protocol['datasets'][cid])
    state_path = out/(cid+'.json')
    state = read(state_path) if state_path.exists() else {'competition_id':cid,'jobs':{},'evaluations':{},'fits':{}}
    def run(label, **body):
        body.update(dataset_digest=protocol['datasets'][cid], request_key=protocol['batch_id']+'-'+label)
        job = tasks.start(cid,body)
        state['jobs'][label] = {'id':job['id'],'status':job['status']}
        training.atomic_json(state_path,state)
        last = 0
        while job['status'] in ('running','queued'):
            if time.monotonic()-last >= 25:
                folds = len(list((registry.directory(cid)/'task_artifacts'/job['id']/'folds').glob('*.json')))
                print(cid,label,job['status'],'folds',folds,flush=True)
                last = time.monotonic()
            time.sleep(1)
            with tasks.store.connect() as db:
                job = json.loads(db.execute('SELECT payload FROM model_tasks WHERE id=?',(job['id'],)).fetchone()[0])
        state['jobs'][label] = {'id':job['id'],'status':job['status'],'result':job.get('result'),'error':job.get('error')}
        training.atomic_json(state_path,state)
        if job['status'] != 'completed':
            raise RuntimeError(f'{cid} {label}: {job.get("error")}')
        print(cid,label,'completed',flush=True)
        return job['result']
    receipts = {}
    for kind,target in (('elo_logistic','had'),('dc','full_time')):
        result = run(kind+'-rolling',kind='evaluate',evaluation_mode='rolling',algorithm=kind,target=target,
                     half_life=180,date_from=protocol['evaluation_start'],date_to=protocol['cutoff'])
        report = read(registry.directory(cid)/'reports'/(result['report_id']+'.json'))
        state['evaluations'][kind] = {**result,**verify_rolling(cid,data,report,receipts)}
        training.atomic_json(state_path,state)
    ids = []
    for kind in ('elo_logistic','dc'):
        report = read(registry.directory(cid)/'reports'/(state['evaluations'][kind]['report_id']+'.json'))
        ids.append({r['id'] for r in report['records']})
    if ids[0] != ids[1]:
        raise ValueError('Comparison methods have different evaluated fixtures')
    # DC is fitted last to retain the declared full-time candidate, not a test-selected winner.
    for kind,target in (('elo_logistic','had'),('dc','full_time')):
        result = run(kind+'-full-fit',kind='fit',algorithm=kind,target=target,half_life=180,date_to=protocol['cutoff'])
        manifest,bundle,_,frozen = registry.load_run(cid,result['run_id'])
        model = restore(bundle['models'][cid])
        receipt = check_receipt(frozen,model,parse_date(protocol['cutoff']))
        state['fits'][kind] = {**result,'input_receipt':receipt,'supported_markets':registry.supported_markets(bundle['models'][cid],cid),
                              'strategy_report_id':state['evaluations'][kind]['report_id'],
                              'effectiveness_status':'new_full_fit_not_prospectively_evaluated'}
        training.atomic_json(state_path,state)
    state['status'] = 'completed'
    state['digest'] = fingerprint({k:v for k,v in state.items() if k!='digest'})
    training.atomic_json(state_path,state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--workers',type=int,choices=(1,2,3),default=3)
    args = parser.parse_args()
    out = args.output.resolve()
    if not (out/'protocol.json').exists():
        out.mkdir(parents=True,exist_ok=False)
        protocol = {'batch_id':'big5-full-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
                    'cutoff':now_iso(),'evaluation_start':'2024-07-02T00:00:00+08:00',
                    'datasets':{cid:fingerprint(registry.dataset(cid)) for cid in LEAGUES},
                    'algorithms':['elo_logistic','dc'],'half_life':180,'selection_policy':'no_observation_change',
                    'protected_files':immutable_hashes()}
        for cid in LEAGUES:
            rows=registry.dataset(cid,protocol['datasets'][cid])['matches']
            if any(parse_date(r['result_available_at']) >= parse_date(protocol['cutoff']) for r in rows):
                raise ValueError('Unavailable rows; do not silently omit from full training')
        training.atomic_json(out/'protocol.json',protocol)
    protocol=read(out/'protocol.json')
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results=list(pool.map(run_league,[(cid,str(out)) for cid in LEAGUES]))
    for path,digest in protocol['protected_files'].items():
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=digest:
            raise ValueError('Existing immutable artifact or selection was changed: '+path)
    summary={'created_at':now_iso(),'protocol':protocol,'competitions':results,'protected_unchanged':True,
             'unique_training_matches':sum(r['fits']['dc']['input_receipt']['rows'] for r in results)}
    summary['digest']=fingerprint(summary)
    training.atomic_json(out/'summary.json',summary)
    print('COMPLETE',summary['unique_training_matches'],out/'summary.json',flush=True)


if __name__=='__main__':
    main()
