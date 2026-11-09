"""Recompute final parameters from every frozen admitted row, without publishing models."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from worldcup_mvp.console import model_registry as registry, model_methods, training
from worldcup_mvp.console.sources import parse_date
from worldcup_mvp.console.store import fingerprint, now_iso
from scripts.train_big_five_full import check_receipt


def audit(folder):
    summary=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
    if summary['digest'] != fingerprint({k:v for k,v in summary.items() if k!='digest'}):
        raise ValueError('Batch summary changed')
    checked=[]
    for competition in summary['competitions']:
        cid=competition['competition_id']
        candidate=registry.read(registry.directory(cid)/'candidate.json')
        if candidate['run_id'] != competition['fits']['dc']['run_id']:
            raise ValueError('Final candidate pointer differs from declared DC run')
        for kind,item in competition['fits'].items():
            manifest,bundle,report,data=registry.load_run(cid,item['run_id'])
            value=bundle['models'][cid]
            cutoff=parse_date(summary['protocol']['cutoff'])
            if parse_date(value['trained_before']) != cutoff:
                raise ValueError('Training cutoff differs from frozen batch')
            rows=training.validate_dataset(data)
            rebuilt=model_methods.fit({'kind':kind,'mode':'separate','half_life':180},rows,cutoff,cid)
            if rebuilt.to_dict()!=value:
                raise ValueError('Full-input parameter replay differs: '+cid+' '+kind)
            receipt=check_receipt(data,rebuilt,cutoff)
            if receipt!=item['input_receipt']:
                raise ValueError('Training input receipt differs')
            if (manifest['dataset_digest']!=summary['protocol']['datasets'][cid]
                or report['evaluation_type']!='unassessed_artifact' or report['test']['count']!=0):
                raise ValueError('New final fit mislabelled as tested or wrong dataset')
            for row in rows[-3:]:
                for line in (-1,1):
                    markets=rebuilt.forecast(row,line)['markets']
                    if any(abs(sum(p.values())-1)>1e-8 or any(not 0<=v<=1 for v in p.values()) for p in markets.values()):
                        raise ValueError('Invalid forecast probability mass')
            checked.append({'competition_id':cid,'kind':kind,'run_id':item['run_id'],
                            'rows':receipt['rows'],'half_time_rows':sum(r.get('home_half_goals') is not None for r in rows),
                            'season_counts':dict(Counter(r['season'] for r in rows)),
                            'input_digest':receipt['digest'],'model_digest':manifest['model_digest'],
                            'parameter_replay_identical':True,'omitted_ids':[]})
            print(cid,kind,receipt['rows'],'full parameter replay identical',flush=True)
    for path,digest in summary['protocol']['protected_files'].items():
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=digest:
            raise ValueError('Protected prior artifact changed: '+path)
    result={'created_at':now_iso(),'batch_digest':summary['digest'],'models':checked,
            'protected_unchanged':True,'unique_training_matches':summary['unique_training_matches']}
    result['digest']=fingerprint(result)
    target=folder/'final-audit.json'
    if target.exists():
        raise ValueError('Audit output exists; do not replace prior evidence')
    training.atomic_json(target,result)
    print('VERIFIED',len(checked),'models',result['unique_training_matches'],'unique matches',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder',type=Path)
    audit(parser.parse_args().folder.resolve())
