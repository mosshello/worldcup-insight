"""Isolated research ingestion. Never imports, trains or selects production models.

Post-match facts are retained separately; historical availability is an assumption,
not a fabricated pre-match observation. Sources and every exclusion remain auditable.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import subprocess
import uuid

SPECS = {
    'eng.1': ('eng.premier', '英超', '700', 'english-premier-league'),
    'esp.1': ('esp.laliga', '西甲', '740', 'laliga'),
    'ger.1': ('ger.bundesliga', '德甲', '720', 'german-bundesliga'),
    'ita.1': ('ita.serie_a', '意甲', '730', 'italian-serie-a'),
    'fra.1': ('fra.ligue1', '法甲', '710', 'ligue-1'),
}
BASE = 'https://site.api.espn.com/apis/site/v2/sports/soccer/'


def date(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timezone_missing')
    return parsed


def fetch(slug, endpoint, folder):
    if slug not in SPECS or not endpoint.startswith(('scoreboard?dates=', 'summary?event=')):
        raise ValueError('unsupported_endpoint')
    url = BASE + slug + '/' + endpoint
    return fetch_url(url, folder, 180 if 'scoreboard' in endpoint else None)


def fetch_url(url, folder, ttl=None, allow_network=True):
    if not url.startswith((BASE, 'https://www.fotmob.com/api/data/leagues?', 'https://www.fotmob.com/api/data/matchDetails?')):
        raise ValueError('unsupported_source_url')
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()
    for p in sorted(folder.glob(key + '*.receipt.json'), reverse=True):
        receipt = json.loads(p.read_text(encoding='utf8'))
        raw = folder / receipt['file']
        if receipt.get('status') != 'succeeded' or not raw.is_file():
            continue
        age = (datetime.now(timezone.utc)-date(receipt['observed_at'])).total_seconds()
        if age < 0 or (allow_network and ttl is not None and age > ttl):
            continue
        body = gzip.decompress(raw.read_bytes())
        if hashlib.sha256(body).hexdigest() == receipt['sha256']:
            return json.loads(body), receipt
    if not allow_network:
        raise ValueError('not_cached')
    name = key + '.' + uuid.uuid4().hex
    raw = folder / (name + '.raw')
    receipt = {'url': url, 'observed_at': datetime.now(timezone.utc).isoformat(), 'file': name+'.json.gz'}
    try:
        r = subprocess.run(['curl.exe', '-sS', '-L', '--max-time', '35', '--write-out', '%{http_code}', url, '-o', str(raw)], capture_output=True, timeout=40)
        receipt.update(http_status=r.stdout.decode().strip(), transport_exit=r.returncode)
        if raw.exists():
            body = raw.read_bytes(); receipt['sha256'] = hashlib.sha256(body).hexdigest()
            (folder / receipt['file']).write_bytes(gzip.compress(body))
            raw.unlink()
        if receipt['http_status'] in ('403','429'):
            raise ValueError('source_access_denied:'+receipt['http_status'])
        if r.returncode or receipt['http_status'] != '200':
            raise ValueError('source_request_failed')
        value = json.loads(body)
        receipt['status'] = 'succeeded'
        return value, receipt
    except Exception as exc:
        receipt.update(status='failed', error=str(exc)); raise
    finally:
        (folder / (name+'.receipt.json')).write_text(json.dumps(receipt, indent=2), encoding='utf8')


def teams(comp):
    rows = comp.get('competitors', [])
    result = {t['homeAway']: t for t in rows}
    if len(rows) != 2 or set(result) != {'home', 'away'}:
        raise ValueError('team_sides_invalid')
    if result['home']['team']['id'] == result['away']['team']['id']:
        raise ValueError('same_team')
    return result


def parse_event(slug, event, observed_at, first_season=2021):
    cid, _, lid, suffix = SPECS[slug]
    eid = str(event['id']); year = event['season']['year']
    if type(year) is not int or year < first_season:
        raise ValueError('outside_requested_seasons')
    # ESPN names 2022/23 Serie A regular season explicitly; relegation-playoff
    # is a separate source stage and is deliberately not admitted here.
    known_stage = (slug,year,event['season'].get('slug')) == ('ita.1',2022,'regular-season')
    if not known_stage and event['season'].get('slug') != f'{year}-{str(year+1)[-2:]}-{suffix}':
        raise ValueError('season_or_stage_unverified')
    if event.get('uid') != f's:600~l:{lid}~e:{eid}' or len(event['competitions']) != 1:
        raise ValueError('competition_identity_mismatch')
    comp = event['competitions'][0]; status = comp['status']
    if (str(comp['id']) != eid or date(event['date']) != date(comp['date'])):
        raise ValueError('fixture_identity_mismatch')
    if (status['type'].get('name') != 'STATUS_FULL_TIME' or status['type'].get('completed') is not True
            or status.get('period') != 2 or comp.get('format', {}).get('regulation', {}).get('periods') != 2):
        raise ValueError('regular_time_unconfirmed')
    kickoff = date(event['date']); observed = date(observed_at)
    if observed < kickoff + timedelta(minutes=90):
        raise ValueError('future_or_unfinished')
    row = {'id': 'espn:'+eid, 'competition_id': cid, 'season': str(year), 'stage': 'regular-season',
           'kickoff': kickoff.isoformat(), 'period': 'regular_time', 'source_url': BASE+slug+'/summary?event='+eid,
           'collected_at': observed_at, 'result_available_at': min(observed, kickoff+timedelta(days=7)).isoformat(),
           'availability_basis': 'historical_7_day_lag_or_observed_assumption', 'source_competition': slug,
           'neutral_site': None, 'training_eligible': False, 'extra_time': None, 'shootout': None,
           'qualification': None, 'postmatch_statistics': {}, 'postmatch_lineups': {},
           'feature_availability': 'Post-match facts; target-match fields forbidden as pre-match inputs'}
    for side, team in teams(comp).items():
        score = str(team['score'])
        if not score.isdecimal() or not 0 <= int(score) <= 30:
            raise ValueError('score_invalid')
        row.update({side+'_id': 'espn:'+str(team['team']['id']), side+'_name': team['team']['displayName'],
                    side+'_goals': int(score), side+'_half_goals': None})
    return row


def empty_boxscore_sides(stats):
    return [side for side in ('home','away') if all(
        type(stats.get(side,{}).get(key)) in (int,float) and stats[side][key]==0
        for key in ('possessionPct','totalShots','totalPasses'))]


def usable_boxscore(row):
    stats=row.get('postmatch_statistics',{})
    quarantined=row.get('supplemental_quality',{}).get('espn_boxscore',{}).get('usable') is False
    return {} if quarantined or empty_boxscore_sides(stats) else stats


def has_postmatch_xg(row):
    sources=[row.get('external_postmatch_xg'),*row.get('supplemental_postmatch_facts',{}).values()]
    return any(f and isinstance(f.get('xg'),(list,tuple)) and len(f['xg'])==2 and
               all(type(v) in (int,float) and math.isfinite(v) and v>=0 for v in f['xg']) for f in sources)


def evidence_counts(rows, *, include_quarantined=False):
    """Count a complete pair from one source after applying source-level quality flags."""
    fields=('totalShots','shotsOnTarget','wonCorners','possessionPct','foulsCommitted','totalPasses',
            'yellowCards','redCards','secondYellowCards')
    counts={'matches':0,'starting_eleven_both':0,'xg_both':0,**{key+'_both':0 for key in fields}}
    for row in rows:
        if not row['training_eligible']:continue
        counts['matches']+=1
        stats=[row.get('postmatch_statistics',{}) if include_quarantined else usable_boxscore(row),
               row.get('supplemental_basics',{}).get('statistics',{}),
               *(f['statistics'] for f in row.get('additional_postmatch_statistics',{}).values())]
        for key in fields:
            counts[key+'_both']+=any(all(type(s.get(side,{}).get(key)) in (int,float)
                and math.isfinite(s[side][key]) and s[side][key]>=0 for side in ('home','away')) for s in stats)
        starters=row.get('supplemental_basics',{}).get('lineups') or row.get('postmatch_lineups',{})
        counts['starting_eleven_both']+=all(len(starters.get(side,[]))==11 and
            len({p.get('id') for p in starters[side] if p.get('id')})==11 for side in ('home','away'))
        counts['xg_both']+=has_postmatch_xg(row)
    return counts


def event_half_score(row):
    """Return a half score only when distinct regulation goals account for the entire result."""
    goals=[e for e in row.get('postmatch_events',[]) if e['type'] in ('own-goal','penalty---scored','goal')
           or (e['type'] or '').startswith('goal---')]
    if (any(e['side'] not in ('home','away') or e['period'] not in (1,2) or not e.get('id') for e in goals)
            or len({e['id'] for e in goals})!=len(goals)
            or any(sum(e['side']==s for e in goals)!=row[s+'_goals'] for s in ('home','away'))):
        return None
    return [sum(e['side']==s and e['period']==1 for e in goals) for s in ('home','away')]


def enrich(row, summary, receipt):
    header = summary['header']; eid = row['id'].split(':')[1]
    lid = SPECS[row['source_competition']][2]
    if header.get('uid') != f's:600~l:{lid}~e:{eid}' or len(header.get('competitions', [])) != 1:
        raise ValueError('summary_identity_mismatch')
    comp = header['competitions'][0]
    if str(comp['id']) != eid or date(comp['date']) != date(row['kickoff']) or str(header['season']['year']) != row['season']:
        raise ValueError('summary_fixture_mismatch')
    status = comp['status']['type']
    if status.get('name') != 'STATUS_FULL_TIME' or status.get('completed') is not True:
        raise ValueError('summary_not_full_time')
    if comp.get('status', {}).get('period', 2) != 2:
        raise ValueError('summary_extra_time')
    for side, team in teams(comp).items():
        if 'espn:'+str(team['team']['id']) != row[side+'_id'] or str(team['score']) != str(row[side+'_goals']):
            raise ValueError('summary_score_or_team_conflict')
    row['neutral_site'] = comp.get('neutralSite')
    row['summary_verified'] = True
    row['summary_receipt'] = receipt
    row['training_eligible'] = comp.get('neutralSite') is False
    row['postmatch_statistics'] = {}
    row['postmatch_lineups'] = {}
    for side, team in teams(comp).items():
        row[side+'_half_goals'] = None
        lines = team.get('linescores', [])
        values = [str(v.get('displayValue', '')) for v in lines]
        if len(values) == 2 and all(v.isdecimal() for v in values) and sum(map(int, values)) == row[side+'_goals']:
            row[side+'_half_goals'] = int(values[0])
        elif len(values) == 2 and all(v.isdecimal() for v in values):
            row['training_eligible'] = False
            row.setdefault('quality_issues', []).append(side+'_period_total_conflict')
    ids = {row[s+'_id'].split(':')[1]: s for s in ('home', 'away')}
    for entry in (summary.get('boxscore') or {}).get('teams', []):
        side = ids.get(str(entry['team']['id']))
        if side is None:
            raise ValueError('statistics_team_mismatch')
        stats = {}
        for stat in entry.get('statistics', []):
            try:
                value = float(str(stat['displayValue']).rstrip('%'))
                if math.isfinite(value) and value >= 0: stats[stat['name']] = value
            except (KeyError, ValueError, TypeError): pass
        row['postmatch_statistics'][side] = stats
    empty=empty_boxscore_sides(row['postmatch_statistics'])
    if empty:
        row.setdefault('supplemental_quality',{})['espn_boxscore']={
            'usable':False,'scope':'postmatch_statistics','review_required':True,
            'reason':'empty_side_in_team_statistics','empty_sides':empty}
    row['postmatch_players'] = {}
    for entry in summary.get('rosters', []):
        side = ids.get(str(entry['team']['id']))
        if side is None or entry.get('homeAway') != side:
            raise ValueError('lineup_team_mismatch')
        starters = [{'id': str(p['athlete']['id']), 'name': p['athlete'].get('displayName')}
                    for p in entry.get('roster', []) if p.get('starter') is True]
        if len(starters) == 11 and len({p['id'] for p in starters}) == 11:
            row['postmatch_lineups'][side] = starters
        players = []
        for player in entry.get('roster', []):
            values = {}
            for stat in player.get('stats', []):
                value = stat.get('value')
                if type(value) in (int, float) and math.isfinite(value): values[stat['name']] = value
            players.append({'id':str(player['athlete']['id']), 'name':player['athlete'].get('displayName'),
                            'starter':player.get('starter'), 'subbed_in':player.get('subbedIn'),
                            'subbed_out':player.get('subbedOut'), 'position':player.get('position'), 'statistics':values})
        row['postmatch_players'][side] = players
    row['postmatch_events'] = []
    seen = set()
    for event in summary.get('keyEvents', []):
        event_id = str(event.get('id', '')); side = ids.get(str(event.get('team', {}).get('id')))
        period = event.get('period', {}).get('number')
        if not event_id or event_id in seen or side is None or period not in (1,2) or event.get('shootout'): continue
        seen.add(event_id)
        row['postmatch_events'].append({'id':event_id,'side':side,'period':period,'type':event.get('type',{}).get('type'),
             'clock':event.get('clock'), 'participants':[str(p['athlete']['id']) for p in event.get('participants',[]) if p.get('athlete',{}).get('id')]})
    periods=[row['home_half_goals'],row['away_half_goals']]
    trace=event_half_score(row)
    if None not in periods and trace is not None and trace!=periods:
        row['half_time_review']={'line_scores':periods,'event_half_score':trace,'reason':'period_label_conflict'}
        row['home_half_goals']=row['away_half_goals']=None
    # Keep source odds in raw response only: no exact historical quote timestamp verified.
    if row['source_competition'] == 'ger.1' and eid == '711556':
        if (row['home_goals'],row['away_goals']) != (0,2):
            raise ValueError('known_administrative_result_changed_requires_review')
        row['training_eligible'] = False
        row['period'] = 'administrative_award'
        row['administrative_outcome'] = {'home': 0, 'away': 2,
            'source': 'https://www.dfb.de/news/dfb-sportgericht-wertet-bundesligaspiel-in-berlin-fuer-bochum'}
        row['on_field_outcome'] = {'home': 1, 'away': 1,
            'source': 'https://www.fc-union-berlin.de/de/meldungen/union-trennt-sich-mit-11-vom-vfl-bochum-nWqphR'}
        row['exclusion_reason'] = 'administrative_award_not_regulation_training_label'
    return row


def coverage(slug, rows):
    result = []
    for season in sorted({r['season'] for r in rows}):
        group = [r for r in rows if r['season'] == season]
        expected_teams = 18 if slug == 'ger.1' or (slug == 'fra.1' and int(season) >= 2023) else 20
        fixtures = Counter((r['home_id'], r['away_id']) for r in group)
        clubs = sorted({r[s+'_id'] for r in group for s in ('home', 'away')})
        expected = expected_teams*(expected_teams-1)
        result.append({'season': season, 'rows': len(group), 'expected_full_season': expected,
                       'teams': len(clubs), 'duplicate_pairings': sum(v-1 for v in fixtures.values()),
                       'missing_pairings': [[h,a] for h in clubs for a in clubs if h != a and (h,a) not in fixtures],
                       'round_robin_complete': len(clubs)==expected_teams and len(fixtures)==expected and all(v==1 for v in fixtures.values()),
                       'summary_verified': sum(r.get('summary_verified', False) for r in group),
                       'eligible': sum(r['training_eligible'] for r in group),
                       'half_time': sum(all(r[s+'_half_goals'] is not None for s in ('home','away')) for r in group),
                       'lineups_both': sum(len(r['postmatch_lineups'])==2 for r in group),
                       'statistics_both': sum(all(usable_boxscore(r).get(s) for s in ('home','away')) for r in group),
                       'xg_both': sum(all('expectedGoals' in usable_boxscore(r).get(s,{}) for s in ('home','away')) for r in group)})
    return result
