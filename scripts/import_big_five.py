"""Explicit import only: no training and no observation selection."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console.big_five_dataset import import_snapshots

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--request-reference',required=True)
    a=p.parse_args();result=import_snapshots(a.root,a.request_reference)
    (a.root/'continuation/import-receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
