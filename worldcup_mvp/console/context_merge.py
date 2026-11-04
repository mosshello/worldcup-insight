"""Preserve failed source fields with original timestamps; successful empties replace."""
from copy import deepcopy
from .readiness import fixture_key


def merge(match, current, previous):
    result = deepcopy(current)
    key = fixture_key(match)
    old = previous if previous.get('fixture_identity') == key else {}
    stale = result.setdefault('stale_fields', {})
    def retain(fields, error):
        for field in fields:
            if field in old:
                result[field] = deepcopy(old[field])
                stale[field] = error
    if not result.get('available') and old.get('available'):
        retain(('recent', 'recent_captured_at', 'venue_recent', 'venue_captured_at', 'h2h', 'h2h_checked', 'head_to_head',
                'lineups', 'lineup_captured_at', 'captured_at', 'source', 'source_url',
                'provider_ids', 'event_id', 'match_facts', 'coverage'), result.get('reason') or '基本面刷新失败')
        result.update(available=True, stale=True, refresh_error=result.get('reason') or '基本面刷新失败')
    if result.get('supplementary_error'):
        retain(('estimated_lineups', 'supplementary_source', 'source_identity', 'external_odds'), result['supplementary_error'])
        if old.get('injuries'):
            result['injuries'] = [deepcopy(r) for r in old['injuries'] if not r.get('official')] + [
                r for r in result.get('injuries', []) if r.get('official')]
            stale['injuries'] = result['supplementary_error']
    elif (result.get('external_odds') or {}).get('reason'):
        retain(('external_odds',), result['external_odds']['reason'])
    if result.get('venue_error'):
        retain(('venue_recent', 'venue_captured_at'), result['venue_error'])
    result['fixture_identity'] = key
    return result
