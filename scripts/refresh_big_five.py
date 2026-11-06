"""Check new completed matches; do not import, train, select or schedule jobs."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console.big_five_refresh import refresh

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    p.add_argument('--season',type=int,default=datetime.now(timezone.utc).year)
    a=p.parse_args();output,reports=refresh(a.root,a.season)
    print(json.dumps({'audit':str(output/'audit.json'),'competitions':[{k:r[k] for k in ('competition_id','season','source_observed_at','existing_checked','added','retained')} for r in reports]},ensure_ascii=False,indent=2))
