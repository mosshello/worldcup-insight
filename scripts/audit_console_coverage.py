"""Audit every local fixture without creating predictions or injecting product data."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from urllib.request import build_opener, ProxyHandler


def main():
    parser = argparse.ArgumentParser(description='核查全部场次的真实数据覆盖与缺失项')
    parser.add_argument('--base-url', default='http://127.0.0.1:8891')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--output', default='.ai/tmp/coverage-audit.json')
    args = parser.parse_args()
    def get(path):
        with build_opener(ProxyHandler({})).open(args.base_url.rstrip('/')+path, timeout=120) as response:
            return json.load(response)
    listing = get('/api/v1/matches')
    def inspect(match):
        row = {'id':match['id'], 'fixture':match['home']+'—'+match['away'], 'league':match['league']}
        try:
            detail = get('/api/v1/matches/'+match['id']+('?refresh=1' if args.refresh else ''))['detail']
            context = detail.get('context') or {}
            brief = detail.get('briefing') or {}
            row.update(recent=[len(x) for x in context.get('recent',[[],[]])],
                       estimated=len(context.get('estimated_lineups',[])),
                       confirmed=sum(r.get('status')=='confirmed' for r in context.get('lineups',[])),
                       injuries=len(context.get('injuries',[])), future=[len(x) for x in context.get('upcoming',[[],[]])],
                       external_quotes=len(context.get('external_odds',{}).get('rows',[])),
                       statistics=len(context.get('match_facts',{}).get('statistics',[])),
                       events=len(context.get('match_facts',{}).get('events',[])), briefing=bool(brief),
                       phase=brief.get('phase'), coverage=brief.get('coverage',[]), errors=detail.get('errors',[]))
            row['untranslated'] = sorted({r.get('opponent_original','') for field in ('recent','upcoming')
                for group in context.get(field,[]) for r in group if r.get('opponent')=='球队译名待补充'})
            row['unknown_competitions'] = sorted({r.get('league_original') or r.get('league','') for field in ('recent','upcoming')
                for group in context.get(field,[]) for r in group if r.get('competition_type')=='类型待核实'})
            print(row['id'],row['fixture'],'近期',row['recent'],'首发',row['confirmed'],'预计',row['estimated'],
                  '欧赔',row['external_quotes'],'过程',row['statistics'],row['events'],flush=True)
        except Exception as exc:
            row['error'] = str(exc); print(row['id'],str(exc),flush=True)
        return row
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(inspect,listing['matches']))
    summary = {'matches':len(rows), 'request_errors':sum('error' in r for r in rows),
               'briefings':sum(bool(r.get('briefing')) for r in rows),
               'both_recent':sum(all(r.get('recent',[0,0])) for r in rows),
               'both_five_recent':sum(min(r.get('recent',[0,0]))>=5 for r in rows),
               'with_events':sum(r.get('events',0)>0 for r in rows),
               'with_external_quotes':sum(r.get('external_quotes',0)>0 for r in rows),
               'unknown_names':sorted({x for r in rows for x in r.get('untranslated',[])}),
               'unknown_competitions':sorted({x for r in rows for x in r.get('unknown_competitions',[])})}
    output=Path(args.output); output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps({'checked_at':listing['server_time'],'summary':summary,'matches':rows},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    return 1 if summary['request_errors'] else 0


if __name__=='__main__': raise SystemExit(main())
