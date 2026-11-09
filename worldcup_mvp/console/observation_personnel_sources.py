"""Observed club schedules and official FPL reports, with no guessed player ID joins."""
from datetime import datetime,timedelta,timezone
import json
import re
import threading
from pathlib import Path
from .sources import get_json
from .observation_features import instant
from .store import ROOT,fingerprint,encode

FPL_ALIASES={'Man City':'Manchester City','Man Utd':'Manchester United','Spurs':'Tottenham Hotspur',
    "Nott'm Forest":'Nottingham Forest','Brighton':'Brighton & Hove Albion','Leeds':'Leeds United',
    'Newcastle':'Newcastle United','Wolves':'Wolverhampton Wanderers','West Ham':'West Ham United',
    'Leicester':'Leicester City','Bournemouth':'AFC Bournemouth'}


def normalize_team_schedule(team_id,season,payload,captured_at,source_url):
    raw_id=team_id.removeprefix('espn:')
    if str(payload.get('team',{}).get('id'))!=raw_id or int(payload.get('requestedSeason',{}).get('year',-1))!=int(season):
        raise ValueError('全赛事赛程球队或赛季不符')
    if not isinstance(payload.get('events'),list):raise ValueError('全赛事赛程没有场次字段')
    rows=[];excluded=[];seen={}
    for event in payload['events']:
        ident=str(event.get('id',''));comps=event.get('competitions') or []
        if not ident.isdigit() or len(comps)!=1:excluded.append({'id':ident,'reason':'invalid_event_identity'});continue
        comp=comps[0];pair={r.get('homeAway'):r for r in comp.get('competitors',[])}
        if set(pair)!= {'home','away'} or raw_id not in [str(r.get('team',{}).get('id')) for r in pair.values()]:
            excluded.append({'id':ident,'reason':'wrong_participants'});continue
        kickoff=instant(comp['date'])
        if str(comp.get('id'))!=ident or kickoff!=instant(event['date']):raise ValueError('全赛事场次时间或ID冲突')
        state=comp.get('status',{});typ=state.get('type',{})
        if not typ.get('completed') or kickoff>=instant(captured_at):continue
        refs=[r.get('score',{}).get('$ref','') for r in pair.values()]
        slugs=[re.search(r'/leagues/([^/]+)/events/'+re.escape(ident)+r'/',s) for s in refs]
        slug=slugs[0].group(1) if all(slugs) and slugs[0].group(1)==slugs[1].group(1) else None
        period=state.get('period')
        row={'id':'espn:'+ident,'team_id':team_id,'kickoff':kickoff.isoformat(),
            'home_id':'espn:'+str(pair['home']['team']['id']),'away_id':'espn:'+str(pair['away']['team']['id']),
            'source_competition_slug':slug,'competition_name':event.get('seasonType',{}).get('name'),
            'status':typ.get('name'),'source_period':period,
            'extra_time_observed':True if isinstance(period,int) and period in (3,4) else None,
            'venue':comp.get('venue'), 'actual_player_minutes':None,'travel_distance':None,
            'captured_at':captured_at,'published_at':None,'time_basis':'observed_only',
            'source_url':source_url,'source_event_url':'https://www.espn.com/soccer/match/_/gameId/'+ident}
        if ident in seen:
            if seen[ident]!=row:raise ValueError('同场跨赛事赛程冲突')
            continue
        seen[ident]=row;rows.append(row)
    return {'schema':'observed-club-all-schedule-v1','team_id':team_id,'season':str(season),
            'captured_at':captured_at,'source_url':source_url,'rows':sorted(rows,key=lambda r:r['kickoff']),
            'excluded':excluded,'coverage':'provider_returned_completed_club_matches_not_national_team_minutes'}


def fetch_team_schedule(team_id,season,fetcher=get_json):
    if not team_id.startswith('espn:') or not team_id[5:].isdigit():raise ValueError('球队来源ID无效')
    url=f'https://site.api.espn.com/apis/site/v2/sports/soccer/all/teams/{team_id[5:]}/schedule?season={int(season)}'
    raw=fetcher(url,timeout=20);captured=datetime.now(timezone.utc).isoformat()
    return normalize_team_schedule(team_id,season,raw,captured,url),raw


def fpl_team_mapping(payload,league_teams):
    """Exact names + explicit reviewed aliases only; no fuzzy player joins."""
    by_name={name:ident for ident,name in league_teams.items()};mapping={};unmapped=[]
    for team in payload.get('teams',[]):
        name=FPL_ALIASES.get(team['name'],team['name'])
        ident=by_name.get(name)
        if ident:mapping[str(team['id'])]=ident
        else:unmapped.append({'fpl_team_id':team['id'],'name':team['name']})
    return mapping,unmapped


def fpl_evidence(fixture,payload,captured_at,team_mapping):
    if fixture['competition_id']!='eng.premier':return []
    deadlines=[r.get('deadline_time') for r in payload.get('events',[]) if r.get('deadline_time')]
    if not deadlines or str(instant(min(deadlines)).year)!=str(fixture.get('season')):return []
    captured=instant(captured_at);facts=[]
    for player in payload.get('elements',[]):
        team=team_mapping.get(str(player.get('team')))
        side=next((s for s in ('home','away') if fixture.get(s+'_id')==team),None)
        if not side or (player.get('status')=='a' and not player.get('news')):continue
        pub=player.get('news_added')
        if pub and instant(pub)>captured:continue
        facts.append({'kind':'personnel','side':side,'team_id':team,'player_id':f"fpl:{fixture.get('season','unknown')}:{player['id']}",
            'name':' '.join([player.get('first_name',''),player.get('second_name','')]).strip(),
            'status':{'i':'injury_reported','d':'doubtful','s':'suspended','u':'unavailable_reported'}.get(player.get('status'),'source_reported'),
            'text':player.get('news') or '来源当前人员状态：'+str(player.get('status')),
            'competition_id':'eng.premier','source':'Premier League Fantasy official',
            'source_url':'https://fantasy.premierleague.com/api/bootstrap-static/',
            'published_at':pub,'captured_at':captured_at,'time_basis':'source_published' if pub else 'observed_only',
            'valid_until':(captured+timedelta(hours=24)).isoformat(),
            'uncertainty':'current_FPL_report_not_confirmed_target_lineup_or_registration',
            'expected_minutes':None})
    return facts


def schedule_evidence(fixture,snapshot,as_of):
    if instant(snapshot['captured_at'])>instant(as_of):raise ValueError('全赛事赛程晚于截止')
    side=next((s for s in ('home','away') if fixture[s+'_id']==snapshot['team_id']),None)
    if not side:raise ValueError('全赛事负荷球队不匹配')
    rows=[r for r in snapshot['rows'] if instant(r['kickoff'])<instant(as_of)]
    recent=[r for r in rows if (instant(as_of)-instant(r['kickoff'])).total_seconds()<=30*86400]
    return {'kind':'workload','side':side,'team_id':snapshot['team_id'],'match_id':fixture['id'],
        'source':'ESPN all-competition team schedule','source_url':snapshot['source_url'],
        'captured_at':snapshot['captured_at'],'published_at':None,'time_basis':'observed_only',
        'coverage':snapshot['coverage'],'history_refs':recent,
        'workload':{'observed_club_matches_14d':sum((instant(as_of)-instant(r['kickoff'])).total_seconds()<=14*86400 for r in recent),
            'observed_club_matches_30d':len(recent),'player_minutes':None,'national_team_load':None,'travel_distance':None},
        'text':'来源全赛事球队赛程的已完场事实；国家队及个人分钟未覆盖，不推导完整球员负荷。'}


_CACHE={}
_CACHE_LOCK=threading.RLock()
_REFRESH_SLOTS=threading.BoundedSemaphore(3)
_INFLIGHT=set()


def _read_verified(path):
    value=json.loads((ROOT/path).read_text(encoding='utf-8'))
    if fingerprint({k:v for k,v in value.items() if k!='digest'})!=value.get('digest'):
        raise ValueError('人员源缓存摘要不符')
    return value


def _initial_cache():
    base=ROOT/'data/research/observation-personnel-sources'
    with _CACHE_LOCK:
        if _CACHE:return
        try:
            idx=json.loads((base/'latest.json').read_text(encoding='utf-8'))
            collection=json.loads((ROOT/idx['file']).read_text(encoding='utf-8'))
            for team,row in collection['teams'].items():_CACHE['schedule:'+team]=_read_verified(row['path'])
            fpl=collection.get('fpl',{})
            if fpl.get('raw_reference'):
                raw=_read_verified(fpl['raw_reference'])
                raw['team_mapping']=fpl['team_mapping'];_CACHE['fpl']=raw
        except (OSError,ValueError,KeyError):pass
        runtime=base/'runtime-cache'
        if runtime.exists():
            for path in runtime.glob('*.json'):
                try:
                    row=json.loads(path.read_text(encoding='utf-8'));key=row.pop('cache_key');digest=row.pop('digest')
                    if fingerprint(row)!=digest:continue
                    if key not in _CACHE or instant(row['captured_at'])>instant(_CACHE[key]['captured_at']):_CACHE[key]=row
                except (OSError,ValueError,KeyError):continue


def cached_fixture_evidence(fixture, *, max_age_seconds=1800, refresh=True):
    """Bounded background collector. Freeze as_of AFTER this returns.

    Max two team schedule requests plus one shared EPL/FPL request. Concurrent
    refresh capacity is three globally; busy/stale sources remain explicit.
    Page GET handlers must not call this function with refresh=True.
    """
    from concurrent.futures import ThreadPoolExecutor
    _initial_cache();ttl=max(60,min(3600,int(max_age_seconds)))
    requests=[('schedule:'+fixture[s+'_id'],fixture[s+'_id']) for s in ('home','away')]
    if fixture['competition_id']=='eng.premier':requests.append(('fpl',None))
    def get_one(pair):
        key,team=pair;now=datetime.now(timezone.utc)
        with _CACHE_LOCK:cached=_CACHE.get(key)
        if cached and key.startswith('schedule:') and str(cached.get('season'))!=str(fixture['season']):cached=None
        if cached and 0<=(now-instant(cached['captured_at'])).total_seconds()<=ttl:return key,cached,None
        if not refresh:return key,cached,'source_cache_stale_or_missing'
        if not _REFRESH_SLOTS.acquire(blocking=False):return key,cached,'source_refresh_busy'
        with _CACHE_LOCK:
            if key in _INFLIGHT:
                _REFRESH_SLOTS.release()
                return key,cached,'source_refresh_busy'
            _INFLIGHT.add(key)
        try:
            fetcher=lambda url,timeout=8:get_json(url,timeout=8)
            if team:
                value,_raw=fetch_team_schedule(team,fixture['season'],fetcher=fetcher)
            else:
                raw=fetcher('https://fantasy.premierleague.com/api/bootstrap-static/')
                # Recheck club IDs from the active league dataset, not fuzzy names.
                from . import model_registry
                matches=model_registry.dataset('eng.premier',admission=False)['matches']
                teams={r[s+'_id']:r[s+'_name'] for r in matches if str(r['season'])==str(fixture['season']) for s in ('home','away')}
                mapping,unmapped=fpl_team_mapping(raw,teams)
                value={'data':raw,'captured_at':datetime.now(timezone.utc).isoformat(),'source_url':'https://fantasy.premierleague.com/api/bootstrap-static/',
                       'team_mapping':mapping,'unmapped_teams':unmapped}
            with _CACHE_LOCK:_CACHE[key]=value
            base=ROOT/'data/research/observation-personnel-sources/runtime-cache';base.mkdir(parents=True,exist_ok=True)
            record={**value,'digest':fingerprint(value),'cache_key':key}
            # Immutable refresh files retain receipt time through restarts.
            (base/(fingerprint(record)+'.json')).write_text(encode(record),encoding='utf-8')
            return key,value,None
        except Exception:return key,cached,'source_refresh_failed'
        finally:
            with _CACHE_LOCK:_INFLIGHT.discard(key)
            _REFRESH_SLOTS.release()
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(get_one,requests))
    cutoff=datetime.now(timezone.utc).isoformat();facts=[];status=[];numeric_sources={}
    for key,value,error in results:
        status.append({'id':key,'status':'unavailable' if error else 'observed','error':error,
                       'captured_at':value.get('captured_at') if value else None,'stale':bool(error and value)})
        if value and not error:
            if key=='fpl':
                facts+=fpl_evidence(fixture,value['data'],value['captured_at'],value['team_mapping'])
                from .observation_personnel_models import immutable
                numeric_sources['fpl']=immutable({'competition_id':'eng.premier','schema':'fpl-snapshot-v1',
                    'payload':value['data'],'captured_at':value['captured_at'],
                    'team_mapping':value['team_mapping'],'source_url':value['source_url']},
                    ROOT/'data/research/epl-pregame-personnel/live-inputs')
            else:facts.append(schedule_evidence(fixture,value,cutoff))
    return {'context':{'evidence':facts},'completed_at':cutoff,'source_status':status,'numeric_sources':numeric_sources,
            'coverage':'current_partial_personnel_and_observed_club_schedule_not_complete_player_load'}
