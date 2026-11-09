"""Explicit background/batch entry point, never called by page reads."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console.store import Store
from worldcup_mvp.console.observation_rosters import CurrentRosterStore

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',default='data/console/football.sqlite3');p.add_argument('--max-teams',type=int,default=8)
    args=p.parse_args();rows=CurrentRosterStore(Store(args.db)).sync_current_fixtures(args.max_teams)
    print(json.dumps({'teams':len(rows),'results':rows},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
