"""Source-native FotMob historical facts; no guessed cross-provider identities."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from threading import Event
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console.big_five_history import fetch_url, date

LEAGUES={'eng.1':47,'esp.1':87,'ger.1':54,'ita.1':55,'fra.1':53}


def extract(slug, fixture, summary, receipt):
    g=summary['general'];status=fixture['status'];lid=LEAGUES[slug]
    if (str(g['matchId'])!=str(fixture['id']) or g.get('parentLeagueId',g.get('leagueId'))!=lid
            or date(g['matchTimeUTCDate'])!=date(status['utcTime'])
            or g.get('finished') is not True or g.get('gender') not in (None,'male')):
        raise ValueError('detail_identity_mismatch')
    for side in ('home','away'):
        if str(g[side+'Team']['id'])!=str(fixture[side]['id']):raise ValueError('detail_team_mismatch')
    h=summary['header'];hs=h['status']
    if hs.get('finished') is not True or hs.get('reason',{}).get('short')!='FT':raise ValueError('detail_period_unknown')
    result={str(t['id']):t.get('score') for t in h['teams']}
    actual=[int(x.strip()) for x in status['scoreStr'].split('-')]
    if [result.get(str(fixture[s]['id'])) for s in ('home','away')]!=actual:raise ValueError('score_conflict')
    if status.get('cancelled') or status.get('awarded') or status.get('reason',{}).get('short')!='FT':raise ValueError('result_not_regular')
    stats={}
    for group in summary.get('content',{}).get('stats',{}).get('Periods',{}).get('All',{}).get('stats',[]):
        for stat in group.get('stats',[]):
            values=stat.get('rawStats')
            if not isinstance(values,list) or len(values)!=2:continue
            pair=[v.get('value') if isinstance(v,dict) else None for v in values]
            if all(type(v) in (int,float) and math.isfinite(v) and v>=0 for v in pair):
                if stat['key'] in stats and stats[stat['key']]!=pair:raise ValueError('duplicate_stat_conflict')
                stats[stat['key']]=pair
    return {'id':'fotmob:'+str(fixture['id']),'source_competition':slug,'source_league_id':lid,
            'season':fixture['season'],'kickoff':status['utcTime'], 'period':'regular_time',
            'home_id':'fotmob:'+str(fixture['home']['id']),'away_id':'fotmob:'+str(fixture['away']['id']),
            'home_name':fixture['home']['name'],'away_name':fixture['away']['name'],
            'home_goals':actual[0],'away_goals':actual[1], 'postmatch_statistics':stats,
            'xg':stats.get('expected_goals'),'source_receipt':receipt,
            'observed_at':receipt['observed_at'],'historical_availability':'unknown; acquired after match',
            'training_eligible':False,'cross_provider_identity':'not_joined',
            'lineup':summary.get('content',{}).get('lineup'),
            'shotmap':summary.get('content',{}).get('shotmap'),
            'player_stats':summary.get('content',{}).get('playerStats')}


def collect(slug,root,workers,cached_only=False):
    root=root/slug;root.mkdir(parents=True,exist_ok=True);fixtures={};receipts=[]
    for year in range(2021,2027):
        season=f'{year}/{year+1}'
        feed,receipt=fetch_url(f'https://www.fotmob.com/api/data/leagues?id={LEAGUES[slug]}&ccode3=USA&season={year}%2F{year+1}',root/'raw',180,allow_network=not cached_only)
        d=feed['details']
        if d['id']!=LEAGUES[slug] or d['selectedSeason']!=season or d.get('gender')!='male':raise ValueError('league_season_mismatch')
        receipts.append(receipt)
        for f in feed['fixtures']['allMatches']:
            if f['status'].get('finished') and not f['status'].get('cancelled'):
                if str(f['id']) in fixtures:raise ValueError('duplicate_match_id')
                fixtures[str(f['id'])]={**f,'season':str(year)}
    (root/'fixtures.json').write_text(json.dumps({'receipts':receipts,'matches':list(fixtures.values())},ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'fotmob':slug,'fixtures':len(fixtures)}),flush=True)
    stopped=Event()
    def one(f):
        try:
            if stopped.is_set():raise ValueError('collection_stopped_after_access_denial')
            s,r=fetch_url('https://www.fotmob.com/api/data/matchDetails?matchId='+str(f['id']),root/'raw',allow_network=not cached_only)
            return extract(slug,f,s,r),None
        except Exception as exc:
            if 'source_access_denied' in str(exc):stopped.set()
            return None,{'id':f['id'],'reason':str(exc)}
    rows=[];errors=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i,future in enumerate(as_completed([pool.submit(one,f) for f in fixtures.values()]),1):
            row,error=future.result()
            if row:rows.append(row)
            if error:errors.append(error)
            if i%100==0:print(json.dumps({'fotmob':slug,'done':i,'total':len(fixtures),'errors':len(errors)}),flush=True)
    rows.sort(key=lambda r:(r['kickoff'],r['id']))
    data={'manifest':{'source':'FotMob','created_at':datetime.now(timezone.utc).isoformat(),'usage_mode':'local_research',
        'rights_status':'unverified','commercial_use_permitted':False,'errors':errors,'league_receipts':receipts,
        'availability_policy':'Post-match acquired facts; no pre-match snapshots claimed','not_imported':True,
        'cached_only':cached_only,'access_denied':stopped.is_set()},'matches':rows}
    (root/'research.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'fotmob':slug,'finished':len(rows),'xg':sum(bool(r['xg']) for r in rows),'errors':errors[:4]}),flush=True)
    return stopped.is_set()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--sources',nargs='+',choices=LEAGUES,default=list(LEAGUES));p.add_argument('--workers',type=int,default=4);p.add_argument('--cached-only',action='store_true');a=p.parse_args()
    if not 1<=a.workers<=6:p.error('workers must be 1..6')
    for slug in a.sources:
        if collect(slug,a.root,a.workers,a.cached_only):break
