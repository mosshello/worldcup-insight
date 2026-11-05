"""Current official quotes for display, independent from immutable forecasts."""
import json
from copy import deepcopy
from datetime import datetime, timezone
from . import sources
from .quality import complete, age_seconds, select_markets
from .store import encode, now_iso


def identity(match):
    return {k: match.get(k) for k in ('id', 'home', 'away', 'kickoff')}


def signature(pool):
    return (pool.get('line'), sorted((o['key'], o['odds']) for o in pool['options']))


def cached(store, match_id):
    with store.connect() as db:
        row = db.execute('SELECT payload FROM official_odds_cache WHERE match_id=?', (match_id,)).fetchone()
    return json.loads(row[0]) if row else {}


def read(store, match_id, now=None):
    now = now or datetime.now(timezone.utc)
    match = store.match(match_id)
    if not match:
        raise ValueError('未找到比赛')
    cache = cached(store, match_id)
    if cache.get('fixture') != identity(match):
        cache = {}
    history = deepcopy(cache.get('history', {}))
    limit = min(now, sources.parse_date(match['kickoff']))
    # Local list snapshots retain changes even between detail-history fetches.
    # Use the provider's actual quote time, never invent earlier time points.
    with store.connect() as db:
        snapshots = db.execute('SELECT payload FROM snapshots WHERE match_id=? ORDER BY id', (match_id,)).fetchall()
    observed = {code: [] for code in sources.MARKETS}
    for row in snapshots:
        fixture = json.loads(row[0])
        if identity(fixture) == identity(match):
            for code, pool in fixture.get('markets', {}).items():
                if code in observed:
                    observed[code].append(pool)
    for code in sources.MARKETS:
        points = {(p.get('updated_at'),p.get('line')):p for p in observed[code] + history.get(code, []) if complete(p)
            and 0 <= age_seconds(p.get('updated_at'), limit) < float('inf')}
        history[code] = sorted([p for p in points.values() if complete(p)
            and 0 <= age_seconds(p.get('updated_at'), limit) < float('inf')], key=lambda p: sources.parse_date(p['updated_at']))
    markets, issues = select_markets(match.get('markets', {}), history, now)
    # Add the actually observed list quote; no synthetic/interpolated history.
    for code, pool in markets.items():
        if complete(pool) and 0 <= age_seconds(pool.get('updated_at'), limit) < float('inf'):
            if not history[code] or signature(history[code][-1]) != signature(pool):
                history[code].append(deepcopy(pool))
                history[code].sort(key=lambda p: sources.parse_date(p['updated_at']))
        changes = []
        for point in history.get(code, []):
            if not changes or signature(point) != signature(changes[-1]):
                changes.append(point)
        history[code] = changes
    checked = match.get('captured_at')
    stale = not 0 <= age_seconds(checked, now) <= 180
    closed = sources.parse_date(match['kickoff']) <= now or match.get('sale_status') == 'closed'
    return {'match_id': match_id, 'fixture': identity(match), 'markets': markets, 'history': history,
            'checked_at': checked, 'history_checked_at': cache.get('captured_at'),
            'stale': stale, 'closed': closed, 'sale_status': 'closed' if closed else match.get('sale_status'),
            'error': cache.get('error'), 'issues': issues, 'source': '体彩官方',
            'source_url': match.get('source_url'), 'refresh_seconds': 30}


def refresh(app, match_id):
    # Only official network I/O here: no model, intelligence or LLM work.
    app.sync()
    with app.lock('official-odds:' + match_id):
        match = app.store.match(match_id)
        if not match:
            raise ValueError('未找到比赛')
        old = cached(app.store, match_id)
        if old.get('fixture') != identity(match):
            old = {}
        if age_seconds(old.get('checked_at')) >= 30:
            value = {**old, 'fixture': identity(match), 'checked_at': now_iso()}
            try:
                fresh = sources.fetch_odds(match)
                history = {}
                for code in sources.MARKETS:
                    # Preserve previously fetched changes if the provider later
                    # returns a shorter window; latest valid revision wins.
                    points = {}
                    for p in old.get('history', {}).get(code, []) + fresh['history'].get(code, []):
                        if complete(p) and 0 <= age_seconds(p.get('updated_at')) < float('inf'):
                            points[(p['updated_at'],p.get('line'))] = p
                    history[code] = list(points.values())
                value.update(history=history, captured_at=fresh['captured_at'], error=None)
            except sources.SourceError as exc:
                value['error'] = str(exc)
            # Do not attach a response to a fixture changed during the request.
            current = app.store.match(match_id)
            if current and identity(current) == identity(match):
                with app.store.connect() as db:
                    db.execute('INSERT OR REPLACE INTO official_odds_cache VALUES(?,?)', (match_id, encode(value)))
        return read(app.store, match_id)
