"""Identity-bound public personnel and observed xG; never model training inputs."""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from datetime import timezone

from .sources import SourceError, get_json, parse_date
from .store import ROOT, now_iso

BASE = 'https://www.fotmob.com'
LEAGUES = {'UCL': 42, 'UEC': 73, 'UEL': 73, 'ED1': 48, 'ACL': 525}
# Reviewed against the provider's dated fixture feeds, not inferred from kickoff alone.
TEAMS = {
    'UCL': {'GAL': 8637, 'STU': 10269, 'ATM': 9906, 'BMN': 9823, 'LNZ': 9977,
            'SLB': 6019, 'FEY': 10235, 'INM': 8636, 'MNU': 10260, 'ROM': 8686,
            'BAR': 8634, 'ASV': 10252, 'VIL': 10205, 'PSG': 9847, 'BOD': 8402,
            'LIL': 8639, 'FEN': 8695, 'LIV': 8650, 'RBL': 178475, 'MNC': 8456,
            'POT': 9773, 'NPL': 9875, 'PSV': 8640, 'BRU': 8342, 'DOR': 9789,
            'RBT': 8603, 'VIK': 8478, 'SBH': 951893, 'LEN': 8588, 'CMO': 10171,
            'SLA': 7787, 'ARS': 9825},
    'ACL': {'PHS': 109373, 'GAB': 6582, 'BUU': 165243, 'KAS': 4397},
    'UEC': {'REN': 9851, 'OLP': 8638}, 'UEL': {'REN': 9851, 'OLP': 8638},
    'ED1': {'BRT': 8427, 'WLV': 8602},
}
_cache, _lock = {}, threading.Lock()


def source_json(url):
    with _lock:
        cached = _cache.get(url)
        if cached and time.monotonic() - cached[0] < 180:
            return cached[1], cached[2]
    payload = get_json(url, timeout=10)
    captured = now_iso()
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')
    digest = hashlib.sha256(body).hexdigest()
    directory = ROOT / 'data/console/public_match_facts'
    directory.mkdir(parents=True, exist_ok=True)
    body_path = directory / (digest + '.json')
    if not body_path.exists():
        body_path.write_bytes(body)
    meta = {'source_url': url, 'captured_at': captured, 'sha256': digest}
    (directory / (hashlib.sha256((url + captured).encode()).hexdigest() + '.meta.json')).write_text(
        json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    with _lock:
        if len(_cache) > 128:
            _cache.clear()
        _cache[url] = time.monotonic(), payload, captured
    return payload, captured


def fixture(match, feed):
    code = match.get('league_code')
    teams = TEAMS.get(code, {})
    pair = [teams.get(match.get(s + '_code')) for s in ('home', 'away')]
    if not all(pair):
        raise SourceError('FotMob 球队映射未核实')
    found = []
    for league in feed.get('leagues', []):
        if league.get('primaryId', league.get('parentLeagueId')) != LEAGUES.get(code):
            continue
        for row in league.get('matches', []):
            if [row.get(s, {}).get('id') for s in ('home', 'away')] != pair:
                continue
            if parse_date(row.get('status', {}).get('utcTime')) == parse_date(match['kickoff']):
                found.append(row)
    if len(found) != 1:
        raise SourceError('FotMob 比赛身份或时间未匹配')
    return found[0]


def parse(payload, match, matched, captured):
    general = payload.get('general') or {}
    pair = [matched[s]['id'] for s in ('home', 'away')]
    if (str(general.get('matchId')) != str(matched['id'])
            or general.get('parentLeagueId', general.get('leagueId')) != LEAGUES.get(match.get('league_code'))
            or [general.get(s + 'Team', {}).get('id') for s in ('home', 'away')] != pair
            or parse_date(general.get('matchTimeUTCDate')) != parse_date(match['kickoff'])):
        raise SourceError('FotMob 详情身份或时间冲突')
    url = BASE + '/match/' + str(matched['id'])
    evidence = {'source': 'FotMob', 'source_url': url, 'captured_at': captured,
                'provider_event_id': str(matched['id']), 'official': False,
                'observed_before_kickoff': parse_date(captured) < parse_date(match['kickoff']),
                'training_eligible': False, 'usage_policy': 'display_only'}
    result = {'personnel': [], 'estimated_lineups': [], 'xg': None,
              'personnel_coverage': [], 'source': evidence}
    content = payload.get('content') or {}
    lineup = content.get('lineup') or {}
    result['source_lineup_type'] = lineup.get('lineupType')
    for index, side in enumerate(('home', 'away')):
        team = lineup.get(side + 'Team') or {}
        if team and team.get('id') != pair[index]:
            raise SourceError('FotMob 阵容球队冲突')
        unavailable = team.get('unavailable')
        result['personnel_coverage'].append({'side': side, 'status': 'reported' if unavailable else
                                            'source_empty' if unavailable == [] else 'not_provided',
                                            'complete': False, **evidence})
        for player in unavailable or []:
            info = player.get('unavailability') or {}
            kind = info.get('type')
            if not player.get('name') or kind not in ('injury', 'suspension'):
                continue
            doubtful = str(info.get('expectedReturn', '')).lower() == 'doubtful'
            result['personnel'].append({**evidence, 'side': side, 'name': player['name'],
                'provider_player_id': str(player.get('id', '')), 'kind': kind,
                'status': '出场存疑' if doubtful else '来源报告停赛' if kind == 'suspension' else '来源报告伤缺',
                'reason': ('伤病，出场存疑' if doubtful else '伤病记录') if kind == 'injury' else '本场停赛记录',
                'source_injury_id': info.get('injuryId'), 'expected_return': info.get('expectedReturn'),
                'scope': '仅适用本场', 'eligibility_verified': False})
        players = team.get('starters') or []
        # lastStarting11 is historical, not a forecast or an announced lineup.
        if (lineup.get('lineupType') == 'predicted' and len(players) == 11
                and all(p.get('id') and p.get('name') for p in players)
                and len({p['id'] for p in players}) == 11):
            result['estimated_lineups'].append({**evidence, 'side': side, 'team': match[side],
                'formation': team.get('formation'), 'confirmed': False, 'status': '预计首发',
                'players': [{'name': p['name'], 'number': p.get('shirtNumber'),
                             'provider_player_id': str(p['id'])} for p in players if p.get('name')]})
    # Only current-match observed xG after kickoff, never pre-game team aggregates.
    if general.get('started') is True and not evidence['observed_before_kickoff']:
        stats = ((content.get('stats') or {}).get('Periods') or {}).get('All') or {}
        for group in stats.get('stats', []):
            for item in group.get('stats', []):
                if item.get('key') != 'expected_goals':
                    continue
                values = item.get('stats') or []
                if len(values) != 2 or any(v is None or isinstance(v, bool) for v in values):
                    continue
                try:
                    values = [float(v) for v in values]
                except (ValueError, TypeError):
                    continue
                if all(math.isfinite(v) and 0 <= v <= 30 for v in values):
                    result['xg'] = {**evidence, 'code': 'expectedGoals', 'label': 'xG',
                                    'home': f'{values[0]:g}', 'away': f'{values[1]:g}', 'period': 'source_all_match',
                                    'provisional': not bool(general.get('finished'))}
    return result


def fetch(match):
    if match.get('league_code') not in LEAGUES:
        return None
    teams = TEAMS.get(match['league_code'], {})
    if not all(match.get(side + '_code') in teams for side in ('home', 'away')):
        raise SourceError('FotMob 球队映射未核实')
    date = parse_date(match['kickoff']).astimezone(timezone.utc).strftime('%Y%m%d')
    feed, _ = source_json(BASE + '/api/data/matches?date=' + date)
    matched = fixture(match, feed)
    data, captured = source_json(BASE + '/api/data/matchDetails?matchId=' + str(matched['id']))
    return parse(data, match, matched, captured)
