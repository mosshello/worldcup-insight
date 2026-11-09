"""Fit reproducible league-isolated observation candidates; never select production."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console import model_registry
from worldcup_mvp.console.observation_models import fit, save, ROOT

LEAGUES=('eng.premier','esp.laliga','ger.bundesliga','ita.serie_a','fra.ligue1')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cutoff',default=datetime.now(timezone.utc).isoformat())
    parser.add_argument('--competition',choices=LEAGUES,action='append')
    args=parser.parse_args()
    result={}
    for cid in args.competition or LEAGUES:
        data=model_registry.dataset(cid,admission=False)
        artifact=fit(data['matches'],cid,args.cutoff)
        path=save(artifact)
        result[cid]={'path':Path(path).relative_to(Path(__file__).resolve().parents[1]).as_posix(),'digest':artifact['digest'],'train_count':artifact['train_count'],
            'excluded':len(artifact['excluded']),'history_count':artifact['history_count']}
        print(cid,result[cid]['train_count'],flush=True)
    index=ROOT/('candidate-index-'+args.cutoff.replace(':','').replace('+','_')+'.json')
    if index.exists():
        if json.loads(index.read_text(encoding='utf-8'))!=result:raise ValueError('Frozen fit index differs')
    else:index.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(index)

if __name__=='__main__':main()
