"""AFC history research adapter. No implicit admission, import, or model selection.

Summary identity is verified against scoreboard identity before using neutralSite.
Unknown and knockout rules stay quarantined; half-time is never inferred from goals.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import uuid

COMPETITION = 'afc.champions'
BASE = 'https://site.api.espn.com/apis/site/v2/sports/soccer/afc.champions/'
POLICY = 'acl-group-league-regular-home-away-research-v1'


def _date(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('timestamp_timezone_missing')
    return dt


def _teams(comp):
    sides = comp.get('competitors', [])
    if len(sides) != 2 or {s.get('homeAway') for s in sides} != {'home', 'away'}:
        raise ValueError('team_identity_missing')
    result = {s['homeAway']: s for s in sides}
    ids = [str(result[s].get('team', {}).get('id', '')) for s in ('home', 'away')]
    if not all(ids) or ids[0] == ids[1]:
        raise ValueError('team_identity_missing')
    return result


def parse_event(event, summary, observed_at, raw_reference=None):
    """Return (row, exclusion). Only a strict supported sample view is emitted."""
    eid = str(event.get('id', ''))
    try:
        if not eid or event.get('uid') != f's:600~l:3902~e:{eid}':
            raise ValueError('competition_identity_mismatch')
        comps = event.get('competitions', [])
        if len(comps) != 1 or str(comps[0].get('id')) != eid:
            raise ValueError('fixture_identity_mismatch')
        comp = comps[0]
        status = comp.get('status', {})
        if (status.get('type', {}).get('name') != 'STATUS_FULL_TIME'
                or status.get('type', {}).get('completed') is not True
                or status.get('period') != 2
                or comp.get('format', {}).get('regulation', {}).get('periods') != 2):
            raise ValueError('regular_time_result_unconfirmed')
        season = event.get('season', {})
        if type(season.get('year')) is not int or season.get('slug') not in ('group-stage', 'league-stage'):
            raise ValueError('stage_rules_unsupported')
        # Centralised COVID editions conflict with ESPN's false neutralSite defaults.
        # 2020 included some earlier home games; quarantine all until event-level verification.
        if season['year'] in (2020, 2021, 2022):
            raise ValueError('centralised_edition_venue_unverified')
        teams = _teams(comp)
        kickoff, observed = _date(event['date']), _date(observed_at)
        if kickoff != _date(comp['date']) or observed < kickoff + timedelta(minutes=90):
            raise ValueError('result_time_invalid')
        header = (summary or {}).get('header', {})
        detail = header.get('competitions', [])
        if header.get('uid') != event['uid'] or len(detail) != 1 or str(detail[0].get('id')) != eid:
            raise ValueError('summary_identity_unverified')
        sc = detail[0]
        st = _teams(sc)
        if (_date(sc['date']) != kickoff or header.get('season', {}).get('year') != season['year']
                or any(str(st[s]['team']['id']) != str(teams[s]['team']['id']) for s in teams)):
            raise ValueError('summary_identity_mismatch')
        if sc.get('neutralSite') is not False:
            raise ValueError('neutral_site' if sc.get('neutralSite') is True else 'neutral_site_unknown')
        ss = sc.get('status', {})
        if (ss.get('type', {}).get('name') != 'STATUS_FULL_TIME'
                or ss.get('type', {}).get('completed') is not True
                or ('period' in ss and ss['period'] != 2)):
            raise ValueError('summary_result_conflict')
        goals = {}
        for side in teams:
            score = teams[side].get('score')
            if not isinstance(score, str) or not score.isdecimal() or not 0 <= int(score) <= 30:
                raise ValueError('score_invalid')
            if str(st[side].get('score')) != score:
                raise ValueError('summary_result_conflict')
            goals[side] = int(score)
        assumed = kickoff + timedelta(days=7)
        row = {'id': 'espn:'+eid, 'competition_id': COMPETITION,
               'season': str(season['year']), 'stage': season['slug'],
               'kickoff': kickoff.isoformat(), 'period': 'regular_time', 'neutral_site': False,
               'source_url': BASE+'summary?event='+eid, 'collected_at': observed.isoformat(),
               'result_available_at': min(assumed, observed).isoformat(),
               'availability_basis': 'assumed_7_day_lag' if assumed < observed else 'observed_at_collection',
               'training_eligible': True, 'sample_policy': POLICY,
               'rules_era': 'acl-elite' if season['year'] >= 2024 else 'acl',
               'raw_reference': raw_reference,
               'extra_time': None, 'shootout': None, 'qualification': None}
        for side in teams:
            row[side+'_id'] = 'espn:'+str(teams[side]['team']['id'])
            row[side+'_name'] = teams[side]['team'].get('displayName')
            row[side+'_goals'] = goals[side]
            row[side+'_half_goals'] = None
        return row, None
    except (ValueError, KeyError, TypeError) as exc:
        return None, {'id': 'espn:'+eid, 'reason': str(exc)}


def prepare(scoreboards, summaries, observed_at, authorization_reference):
    if not authorization_reference:
        raise ValueError('local_research_authorization_required')
    rows, excluded, seen, fixtures = [], [], {}, set()
    for board in scoreboards:
        if not any(str(x.get('id')) == '3902' and x.get('slug') == COMPETITION for x in board.get('leagues', [])):
            raise ValueError('scoreboard_competition_mismatch')
        for event in board.get('events', []):
            eid = str(event.get('id', ''))
            digest = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
            if eid in seen:
                if seen[eid] != digest:
                    raise ValueError('duplicate_event_conflict:'+eid)
                continue
            seen[eid] = digest
            summary = summaries.get(eid)
            reference = {'event_sha256': digest, 'summary_header_sha256': hashlib.sha256(
                json.dumps(summary.get('header'), sort_keys=True).encode()).hexdigest() if summary else None}
            row, issue = parse_event(event, summary, observed_at, reference)
            if issue:
                excluded.append(issue)
                continue
            fixture = (row['home_id'], row['away_id'], row['kickoff'])
            if fixture in fixtures:
                raise ValueError('duplicate_fixture')
            fixtures.add(fixture)
            rows.append(row)
    rows.sort(key=lambda r: (r['kickoff'], r['id']))
    counts = Counter(r[s] for r in rows for s in ('home_id', 'away_id'))
    return {'manifest': {'usage_mode': 'local_research', 'local_training_authorized': True,
             'authorization_reference': authorization_reference, 'rights_status': 'unverified',
             'commercial_use_permitted': False, 'source_url': BASE+'scoreboard',
             'prepared_at': observed_at, 'sample_policy': POLICY,
             'availability_policy': 'historical_7_day_lag_or_observed',
             'continuity_policy': 'ACL and ACL Elite group/league stages only; era retained for separate evaluation; no ACL2',
             'quality_basis': 'single_provider_cross_endpoint_identity_and_score_check',
             'excluded': excluded, 'unique_source_events': len(seen)},
            'matches': rows, 'team_mappings': [],
            'coverage': {'seasons': dict(Counter(r['season'] for r in rows)),
                         'teams': dict(counts), 'excluded': dict(Counter(e['reason'] for e in excluded))}}


def fetch_receipt(endpoint, cache_dir, timeout=30):
    """Explicit fetch with immutable raw response and outcome receipt, including errors."""
    if not (endpoint.startswith('scoreboard?dates=') or endpoint.startswith('summary?event=')):
        raise ValueError('unsupported_endpoint')
    # No shell interpretation; only fixed ESPN host. curl handles native Windows TLS.
    url = BASE+endpoint
    root = Path(cache_dir)
    root.mkdir(parents=True, exist_ok=True)
    key = uuid.uuid4().hex
    raw = root/(key+'.raw')
    observed = datetime.now(timezone.utc).isoformat()
    receipt = {'id': key, 'url': url, 'observed_at': observed, 'raw_path': str(raw)}
    try:
        result = subprocess.run(['curl.exe', '-sS', '-L', '--max-time', str(timeout),
                                 '--write-out', '%{http_code}', '-o', str(raw), url],
                                capture_output=True, timeout=timeout+5)
        receipt.update(http_status=result.stdout.decode().strip(), transport_exit=result.returncode)
        if raw.exists():
            receipt['sha256'] = hashlib.sha256(raw.read_bytes()).hexdigest()
        if result.returncode or receipt['http_status'] != '200':
            raise ValueError('provider_request_failed')
        data = json.loads(raw.read_text(encoding='utf8'))
        receipt['status'] = 'succeeded_empty' if data.get('events') == [] else 'succeeded'
        return data, receipt
    except Exception as exc:
        receipt.update(status='failed', error=str(exc))
        raise
    finally:
        (root/(key+'.receipt.json')).write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf8')


def prepare_cached(cache_dir, output, authorization_reference):
    """Prepare a reviewable dataset without mutating the production registry."""
    root = Path(cache_dir)
    boards, summaries, receipts = [], {}, []
    for path in sorted(root.glob('*.json')):
        is_board = path.stem.isdigit() and len(path.stem) == 4
        if not is_board and not path.stem.startswith('summary-'):
            continue
        raw = path.read_bytes()
        data = json.loads(raw)
        receipts.append({'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
            'saved_at': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            'url': BASE+('scoreboard?dates='+path.stem+'&limit=1000' if is_board else
                        'summary?event='+path.stem[len('summary-'):]),
            'receipt_basis': 'local_raw_file_save_time; no historical_http_status_claim'})
        if is_board:
            boards.append(data)
        else:
            summaries[path.stem[len('summary-'):]] = {'header': data.get('header', {})}
    result = prepare(boards, summaries, datetime.now(timezone.utc).isoformat(), authorization_reference)
    result['manifest']['source_receipts'] = receipts
    Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--request-reference', required=True)
    args = parser.parse_args()
    result = prepare_cached(args.cache, args.output, args.request_reference)
    print(json.dumps({'rows': len(result['matches']), 'coverage': result['coverage']}, ensure_ascii=False))
