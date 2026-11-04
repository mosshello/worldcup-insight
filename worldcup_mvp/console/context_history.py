"""Refresh display history independently of immutable training datasets and models."""
import json
import threading
import time
from datetime import datetime, timedelta, timezone

from .catalog import classify
from .history_pipeline import collect, SEASONS
from .sources import SourceError, parse_date
from .store import ROOT, fingerprint
from .training import atomic_json

DIRECTORY = ROOT / 'data/console/context_history'
_locks = {cid: threading.Lock() for cid in ('jp.j1', 'kr.k1')}
_attempts = {}


def merge(seed, current, competition_id):
    seasons = {r['season'] for r in current['coverage']}
    if (current.get('status') != 'collected' or not seasons or not current.get('fixtures')
            or any(r['competition_id'] != competition_id or r.get('issues') for r in current['coverage'])
            or any(r['competition_id'] != competition_id for field in ('fixtures', 'matches') for r in current[field])):
        raise SourceError('当前赛季采集不完整，保留上次历史快照')
    data = {**seed, 'competition_id': competition_id, 'collected_at': current['collected_at'],
            'purpose': 'display_only', 'training_eligible': False}
    for field in ('matches', 'coverage', 'fixtures', 'sources'):
        data[field] = [r for r in seed[field] if r['season'] not in seasons] + current[field]
    if any(r['competition_id'] != competition_id for r in data['matches'] + data['fixtures']):
        raise SourceError('历史资料混入其他赛事')
    if len({r['id'] for r in data['matches']}) != len(data['matches']):
        raise SourceError('历史资料含重复比赛')
    data['digest'] = fingerprint({k: data[k] for k in ('matches', 'coverage')})
    data['fixture_digest'] = fingerprint(data['fixtures'])
    return data


def display_history(match):
    from .league_data import load_history, match_fixture
    cid = classify(match)['id']
    seed = load_history(competition_id=cid)
    path = DIRECTORY / (cid + '.json')
    with _locks[cid]:
        try:
            data = load_history(path=path)
            if data.get('competition_id') != cid:
                raise SourceError('赛事快照身份不符')
        except SourceError:
            data = seed
        now = datetime.now(timezone.utc)
        try:
            fixture = match_fixture(match, data)
            if now - parse_date(fixture['collected_at']) < timedelta(hours=24):
                return data
        except SourceError:
            pass
        if time.monotonic() - _attempts.get(cid, -1e10) < 600:
            return data
        _attempts[cid] = time.monotonic()
        try:
            specs = [s for s in SEASONS if s[0] == cid and s[3] is None]
            current = collect(DIRECTORY / (cid + '.pending.json'), specs=specs,
                              raw_root=ROOT / 'data/training/history_raw')
            updated = merge(seed, current, cid)
            # Validate the requested fixture before replacing any display snapshot.
            match_fixture(match, updated)
            atomic_json(path, updated)
            return updated
        except (OSError, ValueError, KeyError, TypeError, SourceError):
            return data  # Its original timestamp remains subject to the stale-data guard.
