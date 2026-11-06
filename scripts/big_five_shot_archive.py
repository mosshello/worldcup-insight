"""Verify each match in the pinned Understat shot archive independently."""
from collections import Counter, defaultdict
from math import fsum, isfinite
from pathlib import Path
import re
import unicodedata

import pyarrow.parquet as pq

from scripts.supplement_big_five import digest, read, LEAGUES
from scripts.resolve_big_five_dates import REVIEWS, SEGMENTS

FIELDS = ['id', 'understat_id', 'match_id', 'league_code', 'season', 'h_team', 'a_team',
          'h_goals', 'a_goals', 'date', 'h_a', 'is_home', 'minute', 'result', 'x_g',
          'x', 'y', 'player_id', 'player', 'situation', 'shot_type']
EXCEPTIONS = {review[1]: review for review in REVIEWS if review[1] in
              {'610409', '674166', '711539', '679159', '609395', '639932', '677197'}}


def verified_parquet(path, releases):
    receipt = read(path.with_suffix('.parquet.receipt.json'))
    assets = [a for release in releases for a in release['assets'] if a['id'] == receipt['asset']['id']]
    if len(assets) != 1:
        raise ValueError('unrecognized_release_asset')
    asset = assets[0]
    sha = digest(path.read_bytes())
    if (asset['name'] != path.name or receipt['url'] != asset['browser_download_url']
            or receipt['http_status'] != '200' or receipt.get('transport_exit', 0) != 0
            or path.stat().st_size != asset['size'] or sha != receipt['sha256']
            or asset['digest'] != 'sha256:' + sha):
        raise ValueError('release_asset_integrity_failure:' + path.name)
    return receipt


def index_shots(path, wanted):
    groups = defaultdict(dict)
    conflicts = set()
    for batch in pq.ParquetFile(path).iter_batches(batch_size=8192, columns=FIELDS):
        for entry in batch.to_pylist():
            key = (entry['league_code'], str(entry['understat_id']))
            if key not in wanted:
                continue
            sid = entry['id']
            previous = groups[key].get(sid)
            if previous is not None and previous != entry:
                conflicts.add(key)
            groups[key][sid] = entry
    return groups, conflicts


def shot_evidence(meta, entries):
    if not entries:
        raise ValueError('missing_shot_group')
    sides = ('home', 'away')
    values = {s: [] for s in sides}
    goals = []; own = Counter()
    for shot in entries.values():
        if (not str(shot['id']).isdecimal() or shot['match_id'] != str(meta['understat_id'])
                or shot['understat_id'] != shot['match_id'] or shot['league_code'] != meta['league_code']
                or shot['season'] != meta['season'] or shot['date'] != meta['match_date']
                or [shot['h_team'], shot['a_team']] != [meta['home_team'], meta['away_team']]
                or [int(shot['h_goals']), int(shot['a_goals'])] != [meta['home_score'], meta['away_score']]):
            raise ValueError('shot_source_identity_conflict')
        if shot['h_a'] not in ('h', 'a') or shot['is_home'] is not (shot['h_a'] == 'h'):
            raise ValueError('shot_side_conflict')
        side = 'home' if shot['is_home'] else 'away'
        value = float(shot['x_g'])
        if not all(isfinite(v) and 0 <= v <= 1 for v in (value, float(shot['x']), float(shot['y']))):
            raise ValueError('invalid_shot_value')
        minute = int(shot['minute'])
        if not 0 <= minute <= 130 or shot['result'] not in {'Goal','OwnGoal','SavedShot','BlockedShot','MissedShots','ShotOnPost'}:
            raise ValueError('unsupported_shot_event')
        values[side].append(value)
        if shot['result'] in ('Goal', 'OwnGoal'):
            beneficiary = side
            if shot['result'] == 'OwnGoal':
                own[side] += 1
                beneficiary = 'away' if side == 'home' else 'home'
            goals.append({'player': shot['player'], 'side': beneficiary, 'minute': minute + 1,
                          'own_goal': shot['result'] == 'OwnGoal'})
    if [sum(g['side'] == s for g in goals) for s in sides] != [meta['home_score'], meta['away_score']]:
        raise ValueError('incomplete_score_reconstruction')
    for side in sides:
        n = len(values[side])
        if meta[side + '_shots'] not in {n, n - own[side]}:
            raise ValueError('shot_count_conflict')
    return {'source_id': 'understat:' + str(meta['understat_id']),
            'shot_ids': sorted(entries, key=int), 'event_count': [len(values[s]) for s in sides],
            'own_goal_events': [own[s] for s in sides], 'shot_xg_sum': [fsum(values[s]) for s in sides],
            'published_team_xg': [meta[s + '_xg'] for s in sides], 'goals': goals,
            'archive_timestamp': meta['match_date'], 'archive_timezone': None,
            'historical_availability': 'retrospective_only', 'approved_as_training_feature': False}


def name_key(name):
    return ''.join(c for c in unicodedata.normalize('NFKD', name.casefold()) if c.isalnum())


def goal_keys(goals):
    return sorted((g['side'], g['minute'], name_key(g['player']), g['own_goal']) for g in goals)


def espn_goals(row):
    players = {p['id']: p['name'] for group in row['postmatch_players'].values() for p in group}
    result = []
    for event in row['postmatch_events']:
        if 'goal' not in event['type']:
            continue
        clock = re.fullmatch(r"(\d+)'(?:\+(\d+)')?", event['clock']['displayValue'])
        if not clock or event['period'] not in (1, 2):
            raise ValueError('unverified_goal_clock')
        result.append({'side': event['side'], 'player': players[event['participants'][0]],
            'minute': int(clock[1]) + int(clock[2] or 0), 'own_goal': event['type'] == 'own-goal'})
    return result


def reviewed_archive_fact(row, data, meta, shots, meta_receipt, shot_receipt):
    slug, eid, uid, canonical_date, archive_date, *_ = EXCEPTIONS[row['id'].removeprefix('espn:')]
    if (data['manifest']['source'] != slug or row['competition_id'] != data['manifest']['competition_id']
            or row['season'] != meta['season'] or row['kickoff'][:10] != canonical_date
            or meta['match_date'] != archive_date or str(meta['understat_id']) != uid
            or (meta['league_code'], meta['league']) != LEAGUES[slug]
            or row['period'] != 'regular_time' or row['training_eligible'] is not True
            or not row.get('summary_verified') or row['summary_receipt']['http_status'] != '200'
            or row.get('supplemental_postmatch_facts') or row.get('external_postmatch_xg')):
        raise ValueError('reviewed_identity_conflict')
    for side in ('home', 'away'):
        mapping = data['understat_mapping']['understat:' + str(meta[side + '_team_id'])]
        if mapping['espn_id'] != row[side + '_id'] or row[side + '_goals'] != meta[side + '_score']:
            raise ValueError('reviewed_team_or_score_conflict')
    if sum((r['season'], r['home_id'], r['away_id']) == (row['season'], row['home_id'], row['away_id'])
           for r in data['matches']) != 1:
        raise ValueError('season_fixture_not_unique')
    if not shots['goals'] or goal_keys(shots['goals']) != goal_keys(espn_goals(row)):
        raise ValueError('independent_goal_log_conflict')
    xg = shots['published_team_xg']
    if not all(type(v) in (int, float) and isfinite(v) and 0 <= v <= 30 for v in xg):
        raise ValueError('invalid_team_xg')
    return {'source_id': 'understat:' + uid, 'source_kind': 'third_party_archive_of_understat',
            'source_receipt': meta_receipt, 'archive_timestamp': archive_date, 'archive_timezone': None,
            'identity_policy': 'reviewed_date_exception_complete_independent_goal_log_v1',
            'identity_exception': {'canonical_date': canonical_date, 'archive_date': archive_date,
                'source_date_conflict_preserved': True, 'exact_kickoff_verified': False,
                'summary_receipt': row['summary_receipt'], 'shot_archive_sha256': shot_receipt['sha256'],
                'confirmed_goals': shots['goals'], 'played_segments': SEGMENTS.get(row['id']),
                'aggregation_definitions_verified': False},
            'facts': meta, 'xg': xg, 'upstream_collection_time_verified': False,
            'approved_as_training_feature': False}
