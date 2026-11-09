"""Time-bounded evidence for research forecasts, never inferred availability."""
from copy import deepcopy
from datetime import datetime, timezone
from .sources import parse_date
from .store import fingerprint


def instant(value):
    at = value if isinstance(value, datetime) else parse_date(value)
    if at.tzinfo is None:
        raise ValueError('证据时间缺少时区')
    return at.astimezone(timezone.utc)


def build_packet(fixture, context, as_of, quotes=None):
    at, kickoff = instant(as_of), instant(fixture['kickoff'])
    if at >= kickoff:
        raise ValueError('比赛已开赛，不能生成赛前资料')
    cid = fixture['competition_id']
    identity = {k: fixture.get(k) for k in ('id', 'competition_id', 'kickoff', 'home_id', 'away_id', 'home', 'away')}
    facts, excluded, seen = [], [], set()
    candidates = list(context.get('evidence') or [])
    for kind, key in [('personnel','injuries'), ('lineup','lineups'), ('expected_lineup','estimated_lineups')]:
        candidates += [dict(r, kind=kind) for r in context.get(key, [])]
    for item in candidates:
        reason = None
        published = item.get('published_at')
        captured = item.get('captured_at') or item.get('recorded_at') or item.get('verified_at')
        try:
            observed_only = item.get('time_basis') == 'observed_only' and bool(item.get('source_url'))
            if not captured or (not published and not observed_only) or (published and instant(published) > at) or (captured and instant(captured) > at):
                reason = 'missing_or_future_time'
            if item.get('valid_from') and instant(item['valid_from']) > at:
                reason = 'not_yet_effective'
            if item.get('valid_until') and instant(item['valid_until']) <= at:
                reason = 'expired'
        except (ValueError, TypeError):
            reason = 'invalid_time'
        if item.get('match_id') and item['match_id'] != fixture['id']:
            reason = 'fixture_mismatch'
        if item.get('competition_id') and item['competition_id'] != cid:
            reason = 'competition_mismatch'
        if item.get('status') in ('suspended','ineligible','unregistered','停赛','官方确认停赛','无参赛资格') and item.get('competition_id') != cid:
            reason = 'unknown_competition_eligibility'
        if item.get('side') not in (None, 'home', 'away'):
            reason = 'side_mismatch'
        if item.get('team_id') and item['team_id'] not in (fixture.get('home_id'), fixture.get('away_id')):
            reason = 'team_mismatch'
        if not item.get('source') and not item.get('source_url'):
            reason = 'missing_source'
        if reason:
            excluded.append({'id': item.get('id'), 'reason': reason})
            continue
        # Raw source text remains evidence, never executable instructions.
        clean = {k: deepcopy(item[k]) for k in (
            'kind','side','team_id','player_id','name','text','summary','status','players',
            'formation','published_at','captured_at','recorded_at','verified_at','valid_from','valid_until',
            'source','source_url','competition_id','match_id','expected_minutes','conflicts',
            'time_basis','history_refs','coverage','workload','position','actual_minutes',
            'relationship','related_competition_id','uncertainty','revision_of'
        ) if k in item}
        semantic = fingerprint({k:v for k,v in clean.items() if k not in ('source','source_url','captured_at','recorded_at','verified_at')})
        if semantic in seen:
            continue
        seen.add(semantic)
        clean['id'] = 'e-' + fingerprint(clean)[:20]
        facts.append(clean)
    packet = {'schema':'observation-evidence-v1','fixture':identity,'as_of':at.isoformat(),
              'evidence':facts,'excluded':excluded,'missing':[]}
    for kind in ('personnel','lineup'):
        if not any(v.get('kind') == kind for v in facts):
            packet['missing'].append(kind)
    # Only a runner-provided, already time/identity validated market snapshot may
    # be used; validate the common identity/time again at this boundary.
    if quotes is not None:
        qtime = quotes.get('captured_at') or quotes.get('checked_at')
        qfixture = quotes.get('fixture') or {}
        if not qtime or instant(qtime) > at or (at-instant(qtime)).total_seconds() > 180:
            raise ValueError('市场报价缺少同期采集证据')
        if any(qfixture.get(k) != fixture.get(k) for k in ('id','home','away','kickoff')):
            raise ValueError('市场报价比赛身份不匹配')
        packet['market'] = deepcopy(quotes)
    packet['digest'] = fingerprint(packet)
    return packet
