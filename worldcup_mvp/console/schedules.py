"""Identity-checked public schedules, for display only and never cross-league training."""
import time
from copy import deepcopy
from datetime import datetime, timezone
from .sources import get_json, ESPN, SourceError, parse_date, BEIJING
from .competition_labels import label
from .team_labels import chinese_team

_cache = {}


def venue_history(data, team_id, side, match, now=None):
    """Same-competition regulation results only, bounded by observation and kickoff."""
    from .sources import league_for
    if str(data.get('team', {}).get('id')) != str(team_id):
        raise SourceError('历史赛程球队ID不匹配')
    cutoff = min(now or datetime.now(timezone.utc), parse_date(match['kickoff']))
    league = league_for(match.get('league_code'))
    result, seen = [], set()
    for event in data.get('events', []):
        try:
            start = parse_date(event['date'])
            comp = event['competitions'][0]
            status = comp.get('status', {}).get('type', {})
            if (not league or event.get('league', {}).get('slug') != league or start >= cutoff or
                not status.get('completed') or status.get('name') != 'STATUS_FULL_TIME' or comp.get('neutralSite')):
                continue
            own = [p for p in comp['competitors'] if str(p['team']['id']) == str(team_id)]
            other = [p for p in comp['competitors'] if str(p['team']['id']) != str(team_id)]
            if len(own) != 1 or len(other) != 1 or own[0]['homeAway'] != side:
                continue
            scores = [p.get('score', {}).get('displayValue') for p in (own[0], other[0])]
            if not all(isinstance(s, str) and s.isdigit() and len(s) <= 2 for s in scores):
                continue
            ident = str(event['id'])
            if ident in seen:
                continue
            seen.add(ident)
            gf, ga = map(int, scores)
            result.append({'id': ident, 'date': start.astimezone(BEIJING).isoformat(),
                           'opponent': chinese_team(other[0]['team'].get('displayName')),
                           'venue': '主' if side == 'home' else '客', 'gf': gf, 'ga': ga, 'score': f'{gf}-{ga}',
                           **label(event.get('league', {}).get('name', match.get('league', ''))),
                           'league_slug': league, 'score_period': 'regular_time', 'source': 'ESPN',
                           'source_url': f'https://www.espn.com/soccer/match/_/gameId/{ident}'})
        except (KeyError, ValueError, TypeError, AttributeError, IndexError):
            continue
    return sorted(result, key=lambda r: r['date'], reverse=True)[:5]


def fetch_venue(team_id, side, match):
    from .store import now_iso
    if not str(team_id).isdigit():
        raise SourceError('历史赛程球队ID无效')
    season = parse_date(match['kickoff']).year
    key = f'history:{team_id}:{season}'
    cache = _cache.get(key)
    if cache and time.monotonic()-cache[0] < 300:
        data, captured = deepcopy(cache[1]), cache[2]
    else:
        data = get_json(ESPN+f'all/teams/{team_id}/schedule?season={season}', timeout=8)
        captured = now_iso()
        if len(_cache) > 128:
            _cache.clear()
        _cache[key] = (time.monotonic(), deepcopy(data), captured)
    return venue_history(data, team_id, side, match), captured


def normalize(data, team_id, cutoff):
    if str(data.get('team',{}).get('id')) != str(team_id):
        raise SourceError('赛程球队ID不匹配')
    result = []
    for event in data.get('events',[]):
        try:
            start = parse_date(event['date'])
            competition = event['competitions'][0]
            players = competition['competitors']
            own = [p for p in players if str(p.get('team',{}).get('id'))==str(team_id)]
            other = [p for p in players if str(p.get('team',{}).get('id'))!=str(team_id)]
            if len(own)!=1 or len(other)!=1 or start<=cutoff: continue
            status = competition.get('status',{}).get('type',{})
            if status.get('completed') or status.get('state')=='in': continue
            source_url = next((l['href'] for l in event.get('links',[]) if l.get('href','').startswith('https://www.espn.com/')),None)
            result.append({'id':str(event['id']), 'date':start.astimezone(BEIJING).isoformat(),
                'date_precision':'time' if event.get('timeValid',True) else 'date',
                'opponent':chinese_team(other[0]['team'].get('displayName')),
                'opponent_original':other[0]['team'].get('displayName'),
                'venue':'主' if own[0]['homeAway']=='home' else '客',
                **label(event.get('league',{}).get('name','')), 'source':'ESPN', 'source_url':source_url,
                'stage_original':event.get('seasonType',{}).get('name')})
        except (KeyError,ValueError,TypeError,IndexError):
            continue
    return sorted(result,key=lambda r:r['date'])[:5]


def fetch(team_id, kickoff):
    if not str(team_id).isdigit(): raise SourceError('赛程球队ID无效')
    cache = _cache.get(str(team_id))
    if cache and time.monotonic()-cache[0]<300:
        data=deepcopy(cache[1])
    else:
        data=get_json(ESPN+f'all/teams/{team_id}/schedule?fixture=true',timeout=8)
        if len(_cache)>128: _cache.clear()
        _cache[str(team_id)]=(time.monotonic(),deepcopy(data))
    return normalize(data,team_id,max(datetime.now(timezone.utc),parse_date(kickoff)))
