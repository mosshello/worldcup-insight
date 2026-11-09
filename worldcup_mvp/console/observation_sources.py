"""Read-only public fixture/quote adapters for the isolated research workspace.

An observed ESPN quote is not an exchange execution or a timestamped opener.
No provider response changes a training dataset or a production selection.
"""
from copy import deepcopy
from datetime import datetime, timezone
import math

from .big_five_history import SPECS
from .sources import ESPN, SourceError, get_json, parse_date, verify_board_identity
from .team_labels import chinese_team

LEAGUES = {v[0]: {'slug': k, 'name': v[1], 'provider_id': v[2]} for k, v in SPECS.items()}


def sides(competition):
    rows = competition.get('competitors', [])
    if len(rows) != 2 or {r.get('homeAway') for r in rows} != {'home', 'away'}:
        raise ValueError('比赛双方身份不完整')
    result = {r['homeAway']: r for r in rows}
    ids = [str(result[s].get('team', {}).get('id', '')) for s in ('home', 'away')]
    if not all(i.isdigit() for i in ids) or ids[0] == ids[1]:
        raise ValueError('比赛双方来源ID无效')
    return result


def normalize_event(event, competition_id, captured_at):
    spec = LEAGUES[competition_id]
    ident = str(event.get('id', ''))
    if not ident.isdigit() or event.get('uid') != f"s:600~l:{spec['provider_id']}~e:{ident}":
        raise ValueError('赛程赛事来源不匹配')
    comps = event.get('competitions', [])
    if len(comps) != 1 or str(comps[0].get('id')) != ident:
        raise ValueError('场次来源身份不匹配')
    comp = comps[0]
    pair = sides(comp)
    kickoff = parse_date(comp['date'])
    if kickoff.tzinfo is None or parse_date(event['date']) != kickoff:
        raise ValueError('开球时间缺少时区或有冲突')
    state = comp.get('status', {}).get('type', {})
    names = {'STATUS_SCHEDULED': 'upcoming', 'STATUS_FULL_TIME': 'completed',
             'STATUS_POSTPONED': 'postponed', 'STATUS_CANCELED': 'cancelled',
             'STATUS_CANCELLED': 'cancelled', 'STATUS_SUSPENDED': 'suspended'}
    status = names.get(state.get('name'), 'live' if state.get('state') == 'in' else 'unknown')
    return {'id': 'espn:'+ident, 'competition_id': competition_id, 'league': spec['name'],
            'provider': 'espn', 'provider_id': ident, 'source_slug': spec['slug'],
            'season': str(event.get('season', {}).get('year', '')),
            'home_id': 'espn:'+str(pair['home']['team']['id']),
            'away_id': 'espn:'+str(pair['away']['team']['id']),
            'home': chinese_team(pair['home']['team'].get('displayName', '')),
            'away': chinese_team(pair['away']['team'].get('displayName', '')),
            'home_name': pair['home']['team'].get('displayName', ''),
            'away_name': pair['away']['team'].get('displayName', ''),
            'kickoff': kickoff.isoformat(), 'status': status, 'captured_at': captured_at,
            'neutral_site': comp.get('neutralSite'),
            'source_url': ESPN+spec['slug']+'/summary?event='+ident}


def fetch_fixtures(competition_id, now=None, fetcher=get_json):
    now = now or datetime.now(timezone.utc)
    spec = LEAGUES[competition_id]
    years = {now.year}
    if now.month == 12:
        years.add(now.year+1)
    rows, receipts = [], []
    for year in sorted(years):
        url = ESPN+spec['slug']+f'/scoreboard?dates={year}&limit=1000'
        board = fetcher(url, timeout=20)
        verify_board_identity(board, spec['slug'])
        if not isinstance(board.get('events'), list) or len(board['events']) >= 1000:
            raise SourceError('赛程返回缺失或可能被截断')
        captured = datetime.now(timezone.utc).isoformat()
        receipts.append({'url': url, 'captured_at': captured, 'events': len(board['events'])})
        for event in board['events']:
            row = normalize_event(event, competition_id, captured)
            delta = (parse_date(row['kickoff'])-now).total_seconds()
            if -2*86400 <= delta <= 21*86400:
                rows.append(row)
    return rows, receipts


def verify_summary(fixture, payload, captured_at):
    head = payload.get('header', {})
    spec = LEAGUES[fixture['competition_id']]
    if str(head.get('id')) != fixture['provider_id'] or head.get('uid') != f"s:600~l:{spec['provider_id']}~e:{fixture['provider_id']}":
        raise ValueError('逐场响应赛事身份不匹配')
    event = {**head, 'date': head.get('competitions', [{}])[0].get('date')}
    current = normalize_event(event, fixture['competition_id'], captured_at)
    if any(current[k] != fixture[k] for k in ('id', 'home_id', 'away_id', 'kickoff')):
        raise ValueError('赛程已变化，须重新建立观察节点')
    if str(head.get('season', {}).get('year')) != fixture['season']:
        raise ValueError('赛季身份冲突')
    return current


def decimal_odds(american):
    if isinstance(american, bool):
        raise ValueError('报价无效')
    n = float(american)
    if not math.isfinite(n) or abs(n) < 100:
        raise ValueError('美式赔率无效')
    return 1+n/100 if n > 0 else 1+100/abs(n)


def quotes_from_summary(fixture, payload, captured_at):
    """Observe current quotes only; undocumented settlement prevents spread scoring."""
    verify_summary(fixture, payload, captured_at)
    result = []
    for pool in payload.get('odds') or []:
        provider = pool.get('provider') or {}
        if not provider.get('id') or not provider.get('name'):
            continue
        h, a = pool.get('homeTeamOdds', {}), pool.get('awayTeamOdds', {})
        if ('espn:'+str(h.get('team', {}).get('id')) != fixture['home_id'] or
                'espn:'+str(a.get('team', {}).get('id')) != fixture['away_id']):
            continue
        common = {'source': 'espn:'+str(provider['id']), 'source_name': 'ESPN / '+provider['name'],
                  'source_url': fixture['source_url'], 'captured_at': captured_at,
                  'published_at': None, 'time_basis': 'observed_only', 'is_opening': False,
                  'source_freshness': 'publication_time_unknown'}
        try:
            options = [{'key': key, 'odds': decimal_odds(value)} for key, value in
                       [('h', h['moneyLine']), ('d', pool['drawOdds']['moneyLine']), ('a', a['moneyLine'])]]
            result.append({**common, 'market': 'had', 'line': None, 'options': options})
        except (KeyError, ValueError, TypeError):
            pass
        try:
            # The explicit two-sided point spread avoids inferring the sign from favorites.
            spread = pool['pointSpread']
            home, away = spread['home']['close'], spread['away']['close']
            line, other = float(home['line']), float(away['line'])
            if not math.isfinite(line) or line != -other or line*4 != int(line*4):
                raise ValueError('双边盘口不一致')
            result.append({**common, 'market': 'spread', 'line': line,
                           'settlement_policy': 'unverified_display_only',
                           'options': [{'key': k, 'odds': decimal_odds(v['odds'])}
                                       for k, v in [('h', home), ('a', away)]]})
        except (KeyError, ValueError, TypeError):
            pass
    return result


def context_from_summary(fixture, payload, captured_at):
    current = verify_summary(fixture, payload, captured_at)
    context = {'available': True, 'captured_at': captured_at, 'state': current['status'],
               'source': 'ESPN', 'source_url': fixture['source_url'],
               'lineups': [], 'injuries': [], 'injuries_verified': False,
               'evidence': [], 'neutral_site': current.get('neutral_site'),
               'provider_ids': {s: fixture[s+'_id'].split(':')[-1] for s in ('home', 'away')}}
    for roster in payload.get('rosters', []):
        side = roster.get('homeAway')
        if side not in ('home', 'away') or 'espn:'+str(roster.get('team', {}).get('id')) != fixture[side+'_id']:
            continue
        starters = [p for p in roster.get('roster', []) if p.get('starter') is True]
        players = [{'id': str(p.get('athlete', {}).get('id', '')),
                    'name': p.get('athlete', {}).get('displayName'),
                    'position': p.get('position', {}).get('abbreviation')} for p in starters]
        if current['status'] == 'upcoming' and len(players) == 11 and len({p['id'] for p in players if p['id']}) == 11:
            context['lineups'].append({'side': side, 'status': 'confirmed', 'players': players,
                                      'captured_at': captured_at, 'source_url': fixture['source_url'],
                                      'published_at': None, 'time_basis': 'observed_only'})
    # Source news may be generic or post-match; retain only time- and fixture-bound stories.
    for article in (payload.get('news') or {}).get('articles', []) if isinstance(payload.get('news'), dict) else []:
        published = article.get('published')
        try:
            when = parse_date(published)
            if when > parse_date(captured_at) or when >= parse_date(fixture['kickoff']):
                continue
            categories = article.get('categories', [])
            if not any(str(c.get('eventId')) == fixture['provider_id'] for c in categories):
                continue
        except (ValueError, TypeError, AttributeError):
            continue
        context['evidence'].append({'id': 'espn-news:'+str(article.get('id')),
                                    'text': article.get('headline', '')+' '+article.get('description', ''),
                                    'source_url': article.get('links', {}).get('web', {}).get('href'),
                                    'published_at': published, 'captured_at': captured_at,
                                    'competition_id': fixture['competition_id'], 'match_id': fixture['id']})
    return context


def fetch_summary(fixture, fetcher=get_json):
    payload = fetcher(fixture['source_url'], timeout=15)
    captured = datetime.now(timezone.utc).isoformat()
    current = verify_summary(fixture, payload, captured)
    return current, context_from_summary(current, payload, captured), quotes_from_summary(current, payload, captured), payload
