"""Identity-checked public schedules, for display only and never cross-league training."""
import time
from copy import deepcopy
from datetime import datetime, timezone
from .sources import get_json, ESPN, SourceError, parse_date, BEIJING
from .competition_labels import label
from .team_labels import chinese_team

_cache = {}


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
