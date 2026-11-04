"""Audit every local fixture without creating predictions or injecting product data."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from urllib.request import build_opener, ProxyHandler

BEIJING = timezone(timedelta(hours=8))


def in_scope(match, selected_date, date_field):
    if not selected_date:
        return True
    kickoff_day = datetime.fromisoformat(match['kickoff']).astimezone(BEIJING).date().isoformat()
    return ((date_field in ('kickoff', 'either') and kickoff_day == selected_date)
            or (date_field in ('sale', 'either') and match.get('sale_date') == selected_date))


def summarize(rows):
    return {'matches': len(rows), 'request_errors': sum('error' in r for r in rows),
            'source_errors': sum(bool(r.get('errors') or r.get('source_errors') or r.get('stale')) for r in rows),
            'both_five_recent': sum(min(r.get('recent') or [0, 0]) >= 5 for r in rows),
            'both_venue': sum(len(r.get('venue', [])) == 2 and all(r['venue']) for r in rows),
            'both_estimated': sum(r.get('estimated_players') == [11, 11] for r in rows),
            'both_confirmed': sum(r.get('confirmed') == 2 for r in rows),
            'with_injuries': sum(r.get('injuries', 0) > 0 for r in rows),
            'with_statistics': sum(r.get('statistics', 0) > 0 for r in rows),
            'with_events': sum(r.get('events', 0) > 0 for r in rows),
            'with_xg': sum(bool(r.get('xg')) for r in rows),
            'with_external_quotes': sum(r.get('external_quotes', 0) > 0 for r in rows)}


def main():
    parser = argparse.ArgumentParser(description='核查全部场次的真实数据覆盖与缺失项')
    parser.add_argument('--base-url', default='http://127.0.0.1:8891')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--date', type=date.fromisoformat, help='北京时间日期 YYYY-MM-DD')
    parser.add_argument('--date-field', choices=('kickoff', 'sale', 'either'), default='either',
                        help='开赛日、销售日，或两者并集去重（默认）')
    parser.add_argument('--output', default='.ai/tmp/coverage-audit.json')
    args = parser.parse_args()
    def get(path):
        with build_opener(ProxyHandler({})).open(args.base_url.rstrip('/')+path, timeout=120) as response:
            return json.load(response)
    listing = get('/api/v1/matches')
    selected_date = args.date.isoformat() if args.date else None
    matches = [m for m in listing['matches'] if in_scope(m, selected_date, args.date_field)]
    def inspect(match):
        row = {'id':match['id'], 'fixture':match['home']+'—'+match['away'], 'league':match['league'],
               'kickoff': match['kickoff'], 'sale_date': match.get('sale_date'),
               'lifecycle': match.get('lifecycle')}
        try:
            detail = get('/api/v1/matches/'+match['id']+('?refresh=1' if args.refresh else ''))['detail']
            context = detail.get('context') or {}
            brief = detail.get('briefing') or {}
            model = brief.get('football_model') or {}
            row.update(can_predict=detail.get('quality',{}).get('can_predict') is True,
                       blocking_reasons=detail.get('quality',{}).get('reasons',[]),
                       model_status=model.get('status'),model_reason=model.get('reason'),
                       competition_id=model.get('competition_id'),run_id=model.get('run_id'),
                       model_inputs=detail.get('quality',{}).get('model_inputs'),
                       supported_markets=model.get('supported_markets'),
                       archived_versions=len(detail.get('versions',[])))
            row.update(recent=[len(x) for x in context.get('recent',[[],[]])],
                       estimated=len(context.get('estimated_lineups',[])),
                       confirmed=sum(r.get('status')=='confirmed' for r in context.get('lineups',[])),
                       injuries=len(context.get('injuries',[])), future=[len(x) for x in context.get('upcoming',[[],[]])],
                       external_quotes=len(context.get('external_odds',{}).get('rows',[])),
                       statistics=len(context.get('match_facts',{}).get('statistics',[])),
                       events=len(context.get('match_facts',{}).get('events',[])), briefing=bool(brief),
                       phase=brief.get('phase'), coverage=brief.get('coverage',[]), errors=detail.get('errors',[]))
            facts = context.get('match_facts') or {}
            row.update(venue=[len(x) for x in context.get('venue_recent') or [[], []]],
                       can_predict=(detail.get('quality') or {}).get('can_predict'),
                       readiness=(detail.get('quality') or {}).get('fundamentals'),
                       prediction_blockers=(detail.get('quality') or {}).get('reasons', []),
                       stale_fields=context.get('stale_fields', {}),
                       estimated_players=[len(x.get('players', [])) for x in context.get('estimated_lineups', [])],
                       head_to_head=len(context.get('head_to_head') or []),
                       xg=[r for r in facts.get('statistics', []) if r.get('code') == 'expectedGoals'],
                       source_errors={k: context[k] for k in ('reason', 'source_errors', 'supplementary_error',
                           'supplemental_fact_errors', 'refresh_error', 'venue_error') if context.get(k)},
                       stale=bool(context.get('stale')), context_checked_at=detail.get('context_checked_at'),
                       enriched_at=context.get('enriched_at'), source_captured_at=context.get('captured_at'),
                       official_score=detail.get('official_score'), live_score=detail.get('live_score'))
            if (context.get('external_odds') or {}).get('reason'):
                row['source_errors']['external_odds'] = context['external_odds']['reason']
            row['untranslated'] = sorted({r.get('opponent_original','') for field in ('recent','upcoming')
                for group in context.get(field,[]) for r in group if r.get('opponent')=='球队译名待补充'})
            row['unknown_competitions'] = sorted({r.get('league_original') or r.get('league','') for field in ('recent','upcoming')
                for group in context.get(field,[]) for r in group if r.get('competition_type')=='类型待核实'})
            print(row['id'],row['fixture'],'近期',row['recent'],'首发',row['confirmed'],'预计',row['estimated'],
                  '欧赔',row['external_quotes'],'过程',row['statistics'],row['events'],flush=True)
        except Exception as exc:
            row['error'] = str(exc); print(row['id'],str(exc),flush=True)
        return row
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(inspect,matches))
    summary = {'matches':len(rows), 'request_errors':sum('error' in r for r in rows),
               'briefings':sum(bool(r.get('briefing')) for r in rows),
               'both_recent':sum(all(r.get('recent',[0,0])) for r in rows),
               'both_five_recent':sum(min(r.get('recent',[0,0]))>=5 for r in rows),
               'with_events':sum(r.get('events',0)>0 for r in rows),
               'with_external_quotes':sum(r.get('external_quotes',0)>0 for r in rows),
               'unknown_names':sorted({x for r in rows for x in r.get('untranslated',[])}),
               'unknown_competitions':sorted({x for r in rows for x in r.get('unknown_competitions',[])})}
    summary.update(summarize(rows))
    summary['prediction_ready']=sum(r.get('can_predict',False) for r in rows)
    summary['model_available']=sum(r.get('model_status')=='shadow' for r in rows)
    by_phase = {phase: summarize([r for r in rows if r.get('lifecycle') == phase])
                for phase in sorted({r.get('lifecycle') or 'unknown' for r in rows})}
    output=Path(args.output); output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps({'started_at':listing['server_time'], 'checked_at':datetime.now(timezone.utc).isoformat(),
        'date':selected_date, 'date_field':args.date_field, 'timezone':'Asia/Shanghai',
        'listing_cached':listing.get('cached'), 'listing_warning':listing.get('warning'),
        'summary':summary,'by_phase':by_phase,'matches':rows},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    return 1 if summary['request_errors'] else 0


if __name__=='__main__': raise SystemExit(main())
