"""Daily, immutable first observations; models and market references never mix."""
import json
import math
import re
from copy import deepcopy
from datetime import datetime, timezone, date

from . import legacy_predictions
from .football_markets import assessment, settle_markets, score_key, outcome, official_outcomes
from .sources import BEIJING, EXPECTED_OPTIONS, parse_date
from .store import encode, fingerprint, now_iso
from .catalog import classify

KINDS = {'model': '足球模型', 'market': '市场参考', 'legacy': '旧日志参考'}
CODES = ('had', 'hhad', 'hafu', 'crs', 'ttg')


def reference(match, now=None):
    now = now or datetime.now(timezone.utc)
    markets = []
    for code in CODES:
        pool = match.get('markets', {}).get(code, {})
        try:
            if pool.get('updated_at') and parse_date(pool['updated_at']) > min(now, parse_date(match['kickoff'])):
                continue
        except (ValueError, TypeError):
            continue
        options = pool.get('options', [])
        if (pool.get('sale_status') != 'selling' or
                {o.get('key') for o in options} != set(EXPECTED_OPTIONS[code]) or
                len(options) != len(EXPECTED_OPTIONS[code]) or
                any(type(o.get('odds')) not in (int, float) or not math.isfinite(o['odds']) or o['odds'] <= 1 for o in options)):
            continue
        line = pool.get('line')
        if code == 'hhad' and (type(line) not in (int, float) or not math.isfinite(line) or int(line) != line):
            continue
        weights = {o['key']: 1/o['odds'] for o in options}
        total = sum(weights.values())
        markets.append(assessment(code, {k: v/total for k, v in weights.items()},
                                  {**pool, 'line': int(line) if code == 'hhad' else line}))
    return {'market_assessments': markets, 'policy': 'observation-top-v1'}


def reconcile(store, match, forecast):
    detail = (store.detail(match['id']) or {}).get('data', {})
    results = list(detail.get('results', []))
    score = str(detail.get('official_score') or '')
    actuals = official_outcomes(results)
    if not re.fullmatch(r'\d+[:-]\d+', score):
        score = next((str(r.get('combination', '')) for r in results if r.get('code', '').lower() == 'crs'), '')
    conflict = False
    if re.fullmatch(r'\d+[:-]\d+', score):
        h, a = map(int, re.split('[:-]', score))
        derived = {'had': outcome(h, a), 'crs': score_key(h, a), 'ttg': f's{min(h+a, 7)}'}
        conflict = any(k in actuals and actuals[k] != v for k, v in derived.items())
        results = [r for r in results if r.get('code', '').lower() != 'crs']
        results.append({'code': 'crs', 'combination': score})
        results.extend({'code': k, 'combination': v} for k, v in derived.items() if k not in actuals and k != 'crs')
    else:
        score = None
    rows = settle_markets(forecast, results, detail.get('cancelled', False))
    for row in rows:
        if (forecast.get('kind') == 'legacy' and row['code'] == 'crs' and score and row['hit'] is not None
                and re.fullmatch(r'\d+[:-]\d+', row['selection'])):
            row['hit'] = tuple(map(int,re.split('[:-]',row['selection']))) == tuple(map(int,re.split('[:-]',score)))
            row['actual'] = score.replace(':','-')
        # A handicap observation is tied to its frozen line, never today's line.
        if row['code'] == 'hhad' and not score and row['hit'] is not None:
            row.update(hit=None, status='待精确比分核对让球')
        if conflict:
            row.update(hit=None, status='赛果冲突')
        if row['hit'] is None:
            row.update(unit_return=None, unit_profit=None)
        if row['hit'] is not None:
            row['status'] = '命中' if row['hit'] else '未命中'
    value = {'markets': rows, 'score': score, 'conflict': conflict,
             'cancelled': bool(detail.get('cancelled')), 'source': '体彩官方' if results else None}
    if forecast.get('explorations'):
        extra = reconcile(store, match, {'market_assessments':forecast['explorations']})
        codes = {m['code'] for m in forecast['explorations']}
        value['exploration_results'] = [m for m in extra['markets'] if m['code'] in codes]
    return value


class DailySweep:
    def __init__(self, app):
        self.app, self.store = app, app.store
        self.attempts = {}

    def frozen(self, row):
        payload = json.loads(row['payload'])
        with self.store.connect() as db:
            additions = db.execute('SELECT payload,captured_at FROM daily_market_additions WHERE forecast_id=? ORDER BY code', (row['id'],)).fetchall()
        payload['market_assessments'] = [
            {**m, 'captured_at': payload['captured_at']} for m in payload.get('market_assessments', [])
        ] + [{**json.loads(r['payload']), 'captured_at': r['captured_at']} for r in additions]
        with self.store.connect() as db:
            extras = db.execute('SELECT * FROM daily_explorations WHERE forecast_id=? ORDER BY code', (row['id'],)).fetchall()
        if extras:
            payload['explorations'] = [{**json.loads(r['payload']),'captured_at':r['captured_at']} for r in extras]
        return payload

    def observe_explorations(self, row, match, now):
        if row['kind'] not in ('market','model') or parse_date(match['kickoff']) <= now:
            return
        frozen = self.frozen(row)
        if any(frozen['fixture'].get(k) != match.get(k) for k in ('home','away','kickoff')):
            return
        from .exploration import candidates
        choices = candidates(frozen.get('market_assessments',[]))
        if not choices:
            return
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM matches WHERE id=?',(match['id'],)).fetchone()
            if not current:
                return
            latest = json.loads(current['payload'])
            stamp = datetime.now(timezone.utc)
            if (any(latest.get(k) != match.get(k) for k in ('home','away','kickoff')) or
                parse_date(match['kickoff']) <= stamp or latest.get('sale_status') != 'selling' or
                not 0 <= (stamp-parse_date(current['updated_at'])).total_seconds() <= 180):
                return
            detail = db.execute('SELECT payload FROM detail_cache WHERE match_id=?',(match['id'],)).fetchone()
            live = db.execute('SELECT payload FROM live_scores WHERE match_id=?',(match['id'],)).fetchone()
            facts, live = json.loads(detail[0]) if detail else {}, json.loads(live[0]) if live else {}
            if (facts.get('results') or facts.get('official_score') or facts.get('cancelled') or
                live.get('state') in ('live','completed','cancelled','postponed','suspended')):
                return
            for choice in choices:
                db.execute('INSERT OR IGNORE INTO daily_explorations VALUES(?,?,?,?)',
                           (row['id'],choice['code'],stamp.isoformat(),encode(choice)))

    def unresolved_matches(self):
        with self.store.connect() as db:
            rows = db.execute('''SELECT f.match_id,o.payload FROM daily_forecasts f LEFT JOIN daily_outcomes o
                ON o.id=(SELECT MAX(id) FROM daily_outcomes WHERE forecast_id=f.id)''').fetchall()
        return {r['match_id'] for r in rows if not r['payload'] or any(
            m.get('selection_count',0) > 0 and (m['status'].startswith('待') or m['status']=='赛果冲突')
            for m in json.loads(r['payload'])['markets'])}

    def save(self, match, kind, prediction, captured):
        payload = {**deepcopy(prediction), 'kind': kind, 'source': KINDS[kind], 'captured_at': captured,
                   'fixture': {k: match.get(k) for k in ('id', 'home', 'away', 'kickoff', 'league', 'number')}}
        for item in payload.get('market_assessments', []):
            line = item.get('line')
            if item.get('code') == 'hhad' and type(line) in (int, float) and math.isfinite(line) and int(line) == line:
                item['line'] = int(line)
        with self.store.connect() as db:
            if kind == 'market':
                db.execute('BEGIN IMMEDIATE')
                current = db.execute('SELECT payload,updated_at FROM matches WHERE id=?', (match['id'],)).fetchone()
                if not current:
                    return
                fixture = json.loads(current['payload'])
                stamp = datetime.now(timezone.utc)
                if (parse_date(match['kickoff']) <= stamp or parse_date(captured) >= parse_date(match['kickoff']) or
                    any(fixture.get(k) != match.get(k) for k in ('home', 'away', 'kickoff')) or
                    fixture.get('sale_status') != 'selling' or not 0 <= (stamp-parse_date(current['updated_at'])).total_seconds() <= 180):
                    return
                detail = db.execute('SELECT payload FROM detail_cache WHERE match_id=?', (match['id'],)).fetchone()
                live = db.execute('SELECT payload FROM live_scores WHERE match_id=?', (match['id'],)).fetchone()
                facts = json.loads(detail[0]) if detail else {}
                live = json.loads(live[0]) if live else {}
                if (facts.get('results') or facts.get('official_score') or facts.get('cancelled') or
                    live.get('state') in ('live', 'completed', 'cancelled', 'postponed', 'suspended')):
                    return
            db.execute('INSERT OR IGNORE INTO daily_forecasts(match_id,kind,captured_at,payload) VALUES(?,?,?,?)',
                       (match['id'], kind, captured, encode(payload)))
            if kind == 'market':
                saved = db.execute('SELECT id,payload FROM daily_forecasts WHERE match_id=? AND kind=?', (match['id'],kind)).fetchone()
                original = json.loads(saved['payload'])
                if any(original['fixture'].get(k) != match.get(k) for k in ('home','away','kickoff')):
                    return
                codes = {m['code'] for m in original.get('market_assessments', [])}
                for item in payload.get('market_assessments', []):
                    if item['code'] not in codes:
                        db.execute('INSERT OR IGNORE INTO daily_market_additions VALUES(?,?,?,?)',
                                   (saved['id'],item['code'],captured,encode(item)))

    def tick(self, now=None, generate=True):
        now = now or datetime.now(timezone.utc)
        with self.app.lock('daily-sweep'):
            legacy = self.app.legacy_entries()
            pending = []
            for match in self.store.list_matches():
                try:
                    kickoff = parse_date(match['kickoff'])
                    day = match.get('sale_date') or kickoff.astimezone(BEIJING).date().isoformat()
                    date.fromisoformat(day)
                except (ValueError, TypeError, KeyError):
                    continue
                with self.store.connect() as db:
                    db.execute('INSERT OR IGNORE INTO daily_fixtures VALUES(?,?,?)', (match['id'], day, encode(match)))
                    existing = {r[0] for r in db.execute('SELECT kind FROM daily_forecasts WHERE match_id=?', (match['id'],))}
                    archives = db.execute('SELECT created_at,input_snapshot,payload FROM analyses WHERE match_id=? ORDER BY created_at,version', (match['id'],)).fetchall()
                if 'model' not in existing:
                    for archive in archives:
                        p, inputs = json.loads(archive['payload']), json.loads(archive['input_snapshot'])
                        frozen = inputs.get('match', {})
                        try:
                            if (p.get('schema_version') != 2 or p.get('prediction_type') != 'football' or
                                any(frozen.get(k) != match.get(k) for k in ('id', 'home', 'away', 'kickoff')) or
                                parse_date(archive['created_at']) >= kickoff or parse_date(p['created']) >= kickoff):
                                continue
                            self.save(match, 'model', p, p['created'])
                            existing.add('model')
                            break
                        except (KeyError, ValueError, TypeError):
                            continue
                if 'legacy' not in existing:
                    old = legacy_predictions.for_match(match, legacy)
                    if old and old['recorded_before_kickoff']:
                        markets = []
                        key = {'主胜': 'h', '平局': 'd', '客胜': 'a'}.get(old['direction'])
                        if key:
                            markets.append({'code': 'had', 'selected_keys': [key], 'selection': old['direction']})
                        if re.fullmatch(r'\d+[:-]\d+', str(old.get('score', ''))):
                            key = score_key(*map(int, re.split('[:-]', old['score'])))
                            markets.append({'code': 'crs', 'selected_keys': [key], 'selection': old['score']})
                        self.save(match, 'legacy', {'market_assessments': markets, 'notice': old['notice'],
                                  'imported_at': now_iso()}, old['recorded_at'])
                if kickoff > now and (kickoff-now).total_seconds() <= 7*86400:
                    detail = (self.store.detail(match['id']) or {}).get('data', {})
                    live = detail.get('live_score') or self.store.live_scores().get(match['id'], {})
                    safe = (match.get('sale_status') == 'selling' and not detail.get('results') and not detail.get('official_score') and not detail.get('cancelled') and
                            live.get('state') not in ('live', 'completed', 'cancelled', 'postponed', 'suspended'))
                    try:
                        fresh = 0 <= (now-parse_date(match['captured_at'])).total_seconds() <= 180
                    except (ValueError, KeyError, TypeError):
                        fresh = False
                    if safe and fresh:
                        p = reference(match, now)
                        if p['market_assessments']:
                            p['input_snapshot'] = match
                            self.save(match, 'market', p, now.isoformat())
                    if safe and 'model' not in existing:
                        pending.append(match)
            # Fair, bounded retries. The existing prediction path enforces v2 eligibility twice.
            for match in sorted(pending, key=lambda m: self.attempts.get(m['id'], 0))[:6] if generate else []:
                if now.timestamp()-self.attempts.get(match['id'], 0) < 300:
                    continue
                self.attempts[match['id']] = now.timestamp()
                from . import model_registry
                cid = classify(match)['id']
                try:
                    if not cid or not model_registry.selection(cid).get('run_id'):
                        continue
                    saved = self.app.prediction(match['id'], request_key='daily-sweep:'+match['id'])['prediction']
                    self.save(match, 'model', saved, saved['created'])
                except (ValueError, OSError, KeyError):
                    continue
            with self.store.connect() as db:
                forecasts = db.execute('SELECT * FROM daily_forecasts').fetchall()
            for row in forecasts:
                match = self.store.match(row['match_id'])
                if not match:
                    continue
                self.observe_explorations(row, match, now)
                p = self.frozen(row)
                result = reconcile(self.store, match, p)
                if any(p['fixture'].get(k) != match.get(k) for k in ('home', 'away', 'kickoff')):
                    for item in result['markets'] + result.get('exploration_results',[]):
                        item.update(hit=None, unit_return=None, unit_profit=None,
                                    status='比赛信息变更待核实')
                    result['conflict'] = True
                digest = fingerprint(result)
                with self.store.connect() as db:
                    last = db.execute('SELECT digest FROM daily_outcomes WHERE forecast_id=? ORDER BY id DESC LIMIT 1', (row['id'],)).fetchone()
                    if not last or last[0] != digest:
                        db.execute('INSERT INTO daily_outcomes(forecast_id,digest,created_at,payload) VALUES(?,?,?,?)',
                                   (row['id'], digest, now_iso(), encode(result)))
            self.store.status('daily-sweep', {'status': 'available', 'last_success': now_iso(), 'interval_seconds': 60})

    def read(self, day=None, kind='auto', competition=None):
        if kind not in (*KINDS, 'auto'):
            raise ValueError('扫盘来源无效')
        if day:
            date.fromisoformat(day)
        today = datetime.now(BEIJING).date().isoformat()
        with self.store.connect() as db:
            days = [r[0] for r in db.execute('SELECT DISTINCT day FROM daily_fixtures ORDER BY day DESC')]
            day = day or (today if today in days else next(iter(days), today))
            rows = []
            competitions = {}
            for fixture in db.execute('SELECT * FROM daily_fixtures WHERE day=? ORDER BY match_id', (day,)).fetchall():
                m = json.loads(fixture['payload'])
                cid = classify(m)['id'] or 'unknown'
                competitions[cid] = m.get('league') or cid
                if competition and cid != competition:
                    continue
                forecasts = db.execute('SELECT * FROM daily_forecasts WHERE match_id=?', (m['id'],)).fetchall()
                by_kind = {f['kind']: f for f in forecasts}
                observations = {}
                for source, archive in by_kind.items():
                    last = db.execute('SELECT payload FROM daily_outcomes WHERE forecast_id=? ORDER BY id DESC LIMIT 1', (archive['id'],)).fetchone()
                    observations[source] = {'forecast': self.frozen(archive),
                                            **(json.loads(last['payload']) if last else {})}
                f = by_kind.get(kind) if kind != 'auto' else next((by_kind[k] for k in KINDS if k in by_kind), None)
                p = observations[f['kind']]['forecast'] if f else None
                versions = db.execute('SELECT id,created_at,payload FROM daily_outcomes WHERE forecast_id=? ORDER BY id DESC', (f['id'],)).fetchall() if f else []
                result = json.loads(versions[0]['payload']) if versions else reconcile(self.store, m, {})
                details = (self.store.detail(m['id']) or {}).get('data', {})
                reasons = details.get('quality', {}).get('reasons', [])
                from .official_quotes import read as read_quotes
                current_quotes = read_quotes(self.store, m['id']) if self.store.match(m['id']) else None
                if current_quotes and any(current_quotes['fixture'].get(k) != m.get(k) for k in ('id','home','away','kickoff')):
                    current_quotes = None
                rows.append({**{k: m.get(k) for k in ('id', 'home', 'away', 'league', 'number', 'kickoff')},
                             'current_quotes': current_quotes,
                             'observations': observations,
                             'day': day, 'forecast': p, 'available_sources': list(by_kind), **result,
                             'model_status': '；'.join(reasons) or ('已有模型归档' if 'model' in by_kind else '尚无通过赛前核验的赛事模型预测'),
                             'settlement_versions': [{'id': v['id'], 'created_at': v['created_at'], **json.loads(v['payload'])} for v in versions]})
        summary = {}
        for source in KINDS:
            summary[source] = {}
            for code in CODES:
                items = [v for r in rows if (r.get('forecast') or {}).get('kind') == source for v in r['markets'] if v['code'] == code and v['hit'] is not None]
                summary[source][code] = {'settled': len(items), 'hits': sum(v['hit'] for v in items)}
        from .daily_audit import count, paired
        audit = {'sources': {k: count(rows, k) for k in KINDS}, 'paired': paired(rows)}
        return {'day': day, 'days': days, 'rows': rows, 'summary': summary, 'audit': audit,
                'competition': competition, 'competitions': [{'value': k, 'label': v} for k, v in competitions.items()],
                'collector': next((s for s in self.store.statuses() if s['id'] == 'daily-sweep'), None), 'sources': KINDS}
