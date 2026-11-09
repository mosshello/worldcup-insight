"""Adapt actual fixture evidence without promoting retrospective facts to pregame inputs."""
from copy import deepcopy
from .observation_features import build_packet, instant
from .store import fingerprint


def _rows(value):
    return [deepcopy(r) for r in value or [] if isinstance(r, dict)] if isinstance(value, list) else []


def _workload(fixture, summary, captured_at, as_of):
    facts, rejected = [], []
    for group in _rows(summary.get('lastFiveGames')):
        team = 'espn:' + str(group.get('team', {}).get('id', ''))
        side = next((s for s in ('home', 'away') if fixture.get(s+'_id') == team), None)
        if not side:
            rejected.append({'reason': 'workload_team_mismatch'})
            continue
        events, seen = [], set()
        for event in _rows(group.get('events')):
            ident = str(event.get('id', ''))
            sides = [str(event.get(k, '')) for k in ('homeTeamId', 'awayTeamId')]
            try:
                when = instant(event.get('gameDate'))
                # A completed result marker is needed; date alone is not completion.
                if (not ident.isdigit() or team[5:] not in sides or len(set(sides))!=2
                        or when >= instant(as_of) or when > instant(captured_at)
                        or event.get('gameResult') not in ('W','L','D','T')):
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                rejected.append({'id': ident, 'reason': 'unverified_prior_event'})
                continue
            if ident in seen:
                continue
            seen.add(ident)
            events.append({'id': 'espn:'+ident, 'kickoff': when.isoformat(),
                           'home_id': 'espn:'+sides[0], 'away_id': 'espn:'+sides[1],
                           'competition_name': event.get('competitionName') or event.get('leagueName'),
                           'competition_id': None, 'source_url': 'https://www.espn.com/soccer/match/_/gameId/'+ident,
                           'actual_minutes': None, 'extra_time': None, 'travel_distance': None})
        if not events:
            continue
        events.sort(key=lambda r: r['kickoff'])
        last = instant(events[-1]['kickoff'])
        facts.append({'kind': 'workload', 'side': side, 'team_id': team,
                      'source': 'ESPN lastFiveGames', 'source_url': fixture['source_url'],
                      'published_at': None, 'captured_at': captured_at, 'time_basis': 'observed_only',
                      'match_id': fixture['id'], 'coverage': 'partial_last_five_team_matches',
                      'history_refs': events, 'workload': {
                          'observed_match_count': len(events),
                          'matches_previous_14_days_lower_bound': sum(0 <= (instant(as_of)-instant(e['kickoff'])).total_seconds() <= 14*86400 for e in events),
                          'last_observed_kickoff': last.isoformat(),
                          'calendar_days_from_last_observed_to_target_upper_bound': (instant(fixture['kickoff'])-last).total_seconds()/86400,
                          'player_minutes': None, 'extra_time_minutes': None, 'travel_distance': None},
                      'text': '来源最近比赛列表，只证明已观察场次；未覆盖的俱乐部或国家队比赛未知，间隔不是完整休息时间。'})
    return facts, rejected


def evidence_packet(fixture, context, as_of, *, summary=None, captured_at=None, include_bulletins=True):
    """Return validated auxiliary facts; no network, training or production writes.

    Caller must pass the real summary receipt time, never now() for a cached file.
    Registration/coach relationships require explicit valid_from and identity.
    """
    context = deepcopy(context or {})
    source_facts = _rows(context.get('evidence'))
    context['evidence'] = source_facts
    for key in ('injuries', 'lineups', 'estimated_lineups'):
        context[key] = _rows(context.get(key))
    context['injuries'] += _rows(context.get('official_personnel'))
    if include_bulletins and fixture.get('provider_id'):
        from .personnel import official_bulletins
        for r in official_bulletins(fixture):
            r.update(match_id=fixture['id'])
            # The reviewed bulletin is explicitly scoped to this exact fixture.
            if r.get('scope') == '仅适用本场':
                r['competition_id'] = fixture['competition_id']
            context['injuries'].append(r)
    rejected = []
    for r in _rows(context.get('relationships')):
        if (r.get('relationship') not in ('coach','registration','club_membership')
                or not r.get('valid_from') or not r.get('team_id')
                or (r.get('relationship')!='coach' and not r.get('player_id'))
                or (r.get('relationship')=='registration' and r.get('competition_id')!=fixture['competition_id'])):
            rejected.append({'id': r.get('id'), 'reason': 'unverified_relationship_interval_or_scope'})
            continue
        context['evidence'].append(dict(r, kind='relationship'))
    if summary is not None:
        if not captured_at or instant(captured_at) > instant(as_of):
            raise ValueError('赛程资料缺少真实截止前采集时间')
        from .observation_sources import verify_summary
        verify_summary(fixture, summary, captured_at)
        workload, excluded = _workload(fixture, summary, captured_at, as_of)
        context['evidence'] += workload
        rejected += excluded
    for key in ('injuries','lineups','estimated_lineups'):
        for r in context[key]:
            side = r.get('side')
            if side in ('home','away') and not r.get('team_id'):
                r['team_id'] = fixture.get(side+'_id')
    packet = build_packet(fixture, context, as_of)
    packet['excluded'] += rejected
    # Only stable provider player IDs establish a conflict; name-only bulletins
    # remain evidence, but cannot manufacture a player identity join.
    entities = {}
    for r in packet['evidence']:
        if r.get('kind')=='personnel' and r.get('player_id') and r.get('team_id'):
            entities.setdefault((r['team_id'],r['player_id']), []).append(r)
    for rows in entities.values():
        if len({r.get('status') for r in rows}) > 1:
            for r in rows:
                r['conflicts'] = [q['id'] for q in rows if q['id']!=r['id'] and q.get('status')!=r.get('status')]
                r['uncertainty'] = 'unresolved_source_or_revision_conflict'
    packet['coverage'] = {
        'personnel': 'partial' if any(r.get('kind')=='personnel' for r in packet['evidence']) else 'unavailable',
        'confirmed_lineups': sorted({r.get('side') for r in packet['evidence'] if r.get('kind')=='lineup' and r.get('side')}),
        'workload': ('observed_club_schedules_partial_player_load' if any(r.get('source')=='ESPN all-competition team schedule' for r in packet['evidence'])
                     else 'partial_last_five_team_matches' if any(r.get('kind')=='workload' for r in packet['evidence']) else 'unavailable'),
        'full_player_load': 'unavailable', 'replacement_ability': 'unavailable',
        'expected_minutes': 'unavailable', 'complete_registration': 'unavailable'}
    packet['digest'] = fingerprint({k:v for k,v in packet.items() if k!='digest'})
    return packet


def merge_packets(model_packet, auxiliary_packet):
    """Same-fixture/same-cutoff merge. Additional facts do not become trained inputs."""
    keys = ('id','competition_id','kickoff','home_id','away_id','home','away')
    if (any(model_packet['fixture'].get(k)!=auxiliary_packet['fixture'].get(k) for k in keys)
            or instant(model_packet['as_of']) != instant(auxiliary_packet['as_of'])):
        raise ValueError('事实资料与模型快照身份或截止不一致')
    merged = deepcopy(model_packet)
    merged['evidence'] = list({r['id']:deepcopy(r) for r in model_packet['evidence']+auxiliary_packet['evidence']}.values())
    merged['excluded'] = deepcopy(auxiliary_packet.get('excluded',[]))
    merged['auxiliary_coverage'] = deepcopy(auxiliary_packet.get('coverage',{}))
    merged['auxiliary_not_trained'] = ['personnel','lineup','workload','relationship']
    merged['digest'] = fingerprint({k:v for k,v in merged.items() if k!='digest'})
    return merged
