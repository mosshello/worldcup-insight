"""Narrow official-evidence reconciliation, preserving every existing fact.

Offline only. Read complete receipted responses; append source-specific facts
and immutable snapshots. Missing keys, especially cards, are never zero-filled.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from copy import deepcopy
from datetime import datetime
from html import unescape
import json
import math
import re

from scripts.supplement_big_five import read, digest
from scripts.collect_big_five_pitchradar import load_verified, verify_history, SOURCE
from worldcup_mvp.console.big_five_history import SPECS, evidence_counts, has_postmatch_xg

STATS_SOURCE = 'club_official_llt'
COMPETITION_ID = '1de31796-d3cc-4875-b7be-971186f64b84'
SEASON_ID = '41e8c635-599a-4dd3-9f4e-2274b328d8da'
# Source team IDs and names were reviewed from the full official match responses.
TEAMS = {
    'UD Almería': ('883c2e18-0833-4aa9-a09c-faded8336e7f', 'espn:6832'),
    'Real Betis': ('43cb0c17-c102-43e3-b9af-4793b7db09a2', 'espn:244'),
    'Sevilla FC': ('228c36ca-7fd0-4cb4-9ee7-82b0af5e0d5d', 'espn:243'),
    'RCD Mallorca': ('14f0f48b-0a21-46c9-83e0-2431e660edfc', 'espn:84'),
    'Valencia CF': ('ec31cc3d-7db0-46e3-a886-25d0e0a2d3b8', 'espn:94'),
    'Athletic Club': ('a857abb7-942c-4828-9592-5bf0bbab9ee8', 'espn:93'),
    'RCD Espanyol de Barcelona': ('1c0dc81b-4a50-461b-9c4c-c40249f98046', 'espn:88'),
    'Real Sociedad': ('a0e5a513-ba4b-4a91-b585-af904dea4838', 'espn:89'),
    'Real Madrid': ('f96267b5-78a7-4cce-b76a-7902ce5d6faa', 'espn:86'),
    'Elche CF': ('fc9a01fc-56cd-4513-8eca-e248ce110dcd', 'espn:3751'),
}
STATS_URLS = {
    'almeria-official': 'https://www.udalmeriasad.com/partidos/temporada-2022-2023-laliga-santander-21-ud-almeria-vs-real-betis',
    'mallorca-official': 'https://www.rcdmallorca.es/partidos/temporada-2022-2023-laliga-santander-21-sevilla-fc-vs-rcd-mallorca',
    'llt-VAL': 'https://www.udalmeriasad.com/partidos/temporada-2022-2023-laliga-santander-21-valencia-cf-vs-athletic-club',
    'llt-ESP': 'https://www.udalmeriasad.com/partidos/temporada-2022-2023-laliga-santander-21-rcd-espanyol-de-barcelona-vs-real-sociedad',
    'llt-RMA': 'https://www.udalmeriasad.com/partidos/temporada-2022-2023-laliga-santander-21-real-madrid-vs-elche-cf',
}
STAT_KEYS = {
    'totalScoringAtt': 'totalShots', 'ontargetScoringAtt': 'shotsOnTarget',
    'wonCorners': 'wonCorners', 'possessionPercentage': 'possessionPct',
    'fkFoulLost': 'foulsCommitted', 'totalPass': 'totalPasses',
    'totalYelCard': 'yellowCards', 'totalRedCard': 'redCards',
    'secondYellow': 'secondYellowCards',
}
STATS_REVIEWS = [
    ('espn:643979', 'almeria-official', 'b45f3a36-d090-46c4-baf2-fd9ed6c4b8d6', 'UD Almería', 'Real Betis'),
    ('espn:643973', 'mallorca-official', 'e4f5ecdc-0fa0-42fb-8f47-fad24d47b739', 'Sevilla FC', 'RCD Mallorca'),
    ('espn:643974', 'llt-VAL', 'd389297a-4b94-48f9-8206-e509b04f3ccf', 'Valencia CF', 'Athletic Club'),
    ('espn:643976', 'llt-ESP', 'af697008-f256-41aa-bb68-2e39d7af8435', 'RCD Espanyol de Barcelona', 'Real Sociedad'),
    ('espn:643971', 'llt-RMA', '387f9084-97a1-4358-af54-f5a573daa16b', 'Real Madrid', 'Elche CF'),
]
DATE_REVIEWS = [
    {'id': 'espn:401876423', 'source_id': '32010', 'date': '2026-10-24 15:15',
     'canonical_kickoff': '2026-10-25T16:15:00+00:00', 'teams': ['Toulouse', 'Troyes'],
     'team_ids': ['espn:179', 'espn:170'], 'score': [0, 0], 'file': 'toulouse-delay',
     'url': 'https://www.toulousefc.com/play/blog/2026/10/le-programme-du-week-end-24-et-25-octobre-2026',
     'markers': ['23/10/2026', 'Troyes, initialement prévue samedi, est reportée au dimanche 25 octobre à 17h15'],
     'evidence_kind': 'official_explicit_postponement_notice'},
    {'id': 'espn:401876422', 'source_id': '32011', 'date': '2026-10-25 19:45',
     'canonical_kickoff': '2026-10-26T19:30:00+00:00', 'teams': ['Paris Saint Germain', 'Lyon'],
     'team_ids': ['espn:160', 'espn:167'], 'score': [2, 0], 'file': 'psg-lyon',
     'url': 'https://www.psg.fr/matchs/football-masculin/20262027/paris-vs-lyon-2026-10-25',
     'markers': ['Lundi 26 octobre 2026 Ligue 1', "Paris 2 S. Mayulu 72' M. Godts 84'", 'Olympique Lyonnais Lyon 0'],
     'evidence_kind': 'official_completed_match_report_date'},
]


def verified_html(work, name, url):
    receipt = read(work / (name + '.receipt.json'))
    body = (work / (name + '.html')).read_bytes()
    if (receipt.get('url') != url or receipt.get('http_status') != '200'
            or receipt.get('transport_exit') != 0 or digest(body) != receipt.get('sha256')):
        raise ValueError('official_evidence_integrity_failure')
    return body.decode('utf8'), receipt


def official_evidence(work, review):
    html, receipt = verified_html(work, review['file'], review['url'])
    # Do not accept markers present only in JavaScript, JSON or CSS.
    html = re.sub(r'<(?:script|style)\b[^>]*>.*?</(?:script|style)>', '', html, flags=re.S | re.I)
    visible = re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]+>', ' ', html))).strip()
    if not all(marker in visible for marker in review['markers']):
        raise ValueError('official_date_evidence_unconfirmed')
    return receipt


def reviewed_xg(review, source, rows, history, evidence, receipt, history_receipt, generated_at):
    if review not in DATE_REVIEWS:
        raise ValueError('unreviewed_date_exception')
    if (str(source['id']) != review['source_id'] or source['date'] != review['date']
            or [source['home'], source['away']] != review['teams']
            or [source['gh'], source['ga']] != review['score']
            or source.get('src', 'understat') != 'understat'):
        raise ValueError('reviewed_source_changed')
    candidates = [r for r in rows if r['id'] == review['id']]
    if len(candidates) != 1:
        raise ValueError('target_identity_not_unique')
    row = candidates[0]
    if (row['competition_id'] != 'fra.ligue1' or row['season'] != '2026'
            or row['period'] != 'regular_time' or row['training_eligible'] is not True
            or row['kickoff'] != review['canonical_kickoff']
            or [row['home_id'], row['away_id']] != review['team_ids']
            or [row['home_goals'], row['away_goals']] != review['score']):
        raise ValueError('reviewed_target_changed')
    if len([r for r in rows if (r['season'], r['home_id'], r['away_id']) ==
            (row['season'], row['home_id'], row['away_id'])]) != 1:
        raise ValueError('season_fixture_not_unique')
    if row.get('supplemental_postmatch_facts') or row.get('external_postmatch_xg'):
        raise ValueError('target_already_has_xg')
    if evidence.get('url') != review['url'] or evidence.get('http_status') != '200' or evidence.get('transport_exit') != 0:
        raise ValueError('official_evidence_unavailable')
    values = [source['xgh'], source['xga']]
    if not all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 30
               and abs(v - round(v, 2)) < 1e-9 for v in values):
        raise ValueError('invalid_display_xg')
    verify_history(source, history)
    return row, {
        'source_id': 'understat:' + review['source_id'], 'xg': values,
        'source_kind': 'third_party_display_attributed_to_Understat',
        'source_receipt': receipt, 'index_receipt': history_receipt,
        'precision_decimal_places': 2, 'archive_timestamp': source['date'], 'archive_timezone': None,
        'display_reported_fetched_at': generated_at, 'upstream_collection_time_verified': False,
        'identity_policy': 'explicit_official_date_review_and_both_club_history_v1',
        'identity_exception': {
            'canonical_kickoff': row['kickoff'], 'archive_date': source['date'],
            'source_date_conflict_preserved': True, 'exact_kickoff_verified': False,
            'canonical_team_ids': review['team_ids'], 'source_team_names': review['teams'],
            'club_history_verified': True, 'unique_competition_season_home_away': True,
            'evidence_kind': review['evidence_kind'], 'evidence_receipt': evidence,
        },
        'approved_as_training_feature': False,
    }


def parse_club_page(html):
    blocks = re.findall(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if len(blocks) != 1:
        raise ValueError('official_structured_data_missing')
    return json.loads(blocks[0])['props']['pageProps']['data']


def official_statistics(review, data, row, team_mapping, receipt):
    eid, _, game_id, home, away = review
    game = data['game']
    if (row['id'] != eid or row['competition_id'] != 'esp.laliga' or row['season'] != '2022'
            or row['period'] != 'regular_time' or row['training_eligible'] is not True
            or game['id'] != game_id or game['competition']['id'] != COMPETITION_ID
            or game['season']['id'] != SEASON_ID or game['gameweek']['week'] != 21
            or game['gameweek']['round']['name'] != 'Regular'
            or game['period']['type'] != 'FullTime'
            or datetime.fromisoformat(game['time']) != datetime.fromisoformat(row['kickoff'])
            or [game['homeScore'], game['awayScore']] != [row['home_goals'], row['away_goals']]):
        raise ValueError('official_fixture_identity_conflict')
    if STATS_SOURCE in row.get('additional_postmatch_statistics', {}):
        raise ValueError('official_statistics_already_present')
    statistics = {}; raw = {}; mappings = {}; comparisons = []
    for side, name in [('home', home), ('away', away)]:
        team = game[side + 'Team']
        if (team['shortName'] != name or team_mapping.get(name) != (team['id'], row[side + '_id'])):
            raise ValueError('official_team_identity_conflict')
        mappings[side] = {'source_team_id': team['id'], 'source_name': name, 'team_id': row[side + '_id']}
        stats = data['stats'][side + 'TeamStats']['stats']
        if 'firstHalfGoals' in stats and stats['firstHalfGoals'] != row[side + '_half_goals']:
            raise ValueError('official_half_time_conflict')
        if 'goals' in stats and stats['goals'] != row[side + '_goals']:
            raise ValueError('official_stat_score_conflict')
        # The source omits many zero-valued keys. Absence is not confirmation of zero.
        raw[side] = deepcopy(stats); statistics[side] = {}
        for source_key, key in STAT_KEYS.items():
            if source_key not in stats:
                continue
            value = stats[source_key]
            if (type(value) not in (int, float) or not math.isfinite(value) or value < 0
                    or (key == 'possessionPct' and value > 100)
                    or (key != 'possessionPct' and value != int(value))):
                raise ValueError('invalid_official_statistic')
            statistics[side][key] = value
            prior = row.get('supplemental_basics', {}).get('statistics', {}).get(side, {}).get(key)
            if prior is not None and prior != value:
                comparisons.append({'side': side, 'field': key, 'prior_value': prior, 'official_value': value})
    if abs(sum(statistics[s]['possessionPct'] for s in ('home', 'away')) - 100) > .11:
        raise ValueError('possession_sum_conflict')
    return {
        'source_id': 'llt:' + game_id, 'source_kind': 'official_club_match_display',
        'source_receipt': receipt, 'statistics': statistics, 'raw_team_statistics': raw,
        'identity_policy': 'explicit_source_team_ids_competition_season_exact_utc_fulltime_v1',
        'identity': {'teams': mappings, 'kickoff': game['time'], 'competition_id': COMPETITION_ID, 'season_id': SEASON_ID},
        'missing_values_policy': 'missing_key_remains_unknown; never_zero_fill',
        'card_semantics': 'source totalYelCard, totalRedCard and secondYellow kept separately; do not add them',
        'upstream_statistics_definition_verified': False,
        'comparisons': {'diagnostic_only': True, 'different_values': comparisons},
        'availability': 'postmatch collection only; historical publication time unknown',
        'approved_as_training_feature': False,
    }


def field_coverage(data, *, include_quarantined=False):
    result = {}; missing = []
    for slug, snapshot in data.items():
        rows = [r for r in snapshot['matches'] if r['training_eligible']]
        result[slug] = evidence_counts(rows, include_quarantined=include_quarantined)
        missing.extend({k: r[k] for k in ('id', 'competition_id', 'season', 'home_name', 'away_name', 'kickoff')}
                       for r in rows if not has_postmatch_xg(r))
    return result, missing


def run(root):
    work = root / 'reconciliation'
    parentpath = work / 'parents.json'
    if not parentpath.exists():
        parentpath.write_text(json.dumps({s: read(root / s / 'latest.json') for s in SPECS}))
    parents = read(parentpath); snapshots = {}
    for slug, p in parents.items():
        body = (root / slug / p['file']).read_bytes()
        if digest(body) != p['sha256']:
            raise ValueError('parent_integrity_failure')
        snapshots[slug] = json.loads(body)
    source, receipt = load_verified(root / 'depth', 'pitchradar-matches.json', 'https://pitchradar.live/matches.json')
    history, history_receipt = load_verified(root / 'depth', 'pitchradar-history.json', 'https://pitchradar.live/history.json')
    if source['meta'] != history['meta']:
        raise ValueError('source_generation_mismatch')
    generated = datetime.fromisoformat(source['meta']['generated_at'])
    if generated.tzinfo is None or generated > datetime.fromisoformat(receipt['observed_at']):
        raise ValueError('invalid_source_generation_time')
    report = {'added_xg': [], 'added_statistics': [], 'not_trained': True, 'errors': []}
    for review in DATE_REVIEWS:
        candidates = [m for m in source['ligue_1']['matches'] if str(m['id']) == review['source_id']]
        if len(candidates) != 1:
            raise ValueError('source_identity_not_unique')
        evidence = official_evidence(work, review)
        row, fact = reviewed_xg(review, candidates[0], snapshots['fra.1']['matches'], history['ligue_1']['clubs'],
                                 evidence, receipt, history_receipt, source['meta']['generated_at'])
        row.setdefault('supplemental_postmatch_facts', {})[SOURCE] = fact
        report['added_xg'].append({'id': row['id'], 'source_id': fact['source_id']})
    for review in STATS_REVIEWS:
        eid, name, *_ = review
        url = STATS_URLS[name]
        html, official_receipt = verified_html(work, name, url)
        candidates = [r for r in snapshots['esp.1']['matches'] if r['id'] == eid]
        if len(candidates) != 1:
            raise ValueError('official_target_not_unique')
        row = candidates[0]
        fact = official_statistics(review, parse_club_page(html), row, TEAMS, official_receipt)
        row.setdefault('additional_postmatch_statistics', {})[STATS_SOURCE] = fact
        report['added_statistics'].append({'id': eid, 'source_id': fact['source_id'],
            'statistics': fact['statistics'], 'comparisons': fact['comparisons']})
    coverage, missing = field_coverage(snapshots)
    report.update(coverage=coverage, remaining_xg=missing)
    # All joins and evidence checks finish before writing any latest pointer.
    for slug in ('esp.1', 'fra.1'):
        data = snapshots[slug]
        for c in data['coverage']:
            rows = [r for r in data['matches'] if r['training_eligible'] and r['season'] == c['season']]
            c['any_source_xg_both'] = sum(bool(r.get('supplemental_postmatch_facts') or r.get('external_postmatch_xg')) for r in rows)
            c['pitchradar_xg_both'] = sum(SOURCE in r.get('supplemental_postmatch_facts', {}) for r in rows)
            c['official_club_statistics_both'] = sum(STATS_SOURCE in r.get('additional_postmatch_statistics', {}) for r in rows)
        data['manifest']['official_reconciliation'] = {
            'parent_sha256': parents[slug]['sha256'], 'code_sha256': digest(Path(__file__).read_bytes()),
            'added_xg': report['added_xg'] if slug == 'fra.1' else [],
            'added_statistics_ids': [r['id'] for r in report['added_statistics']] if slug == 'esp.1' else [],
            'enhanced_features_trained': False,
        }
        body = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        sha = digest(body); path = root / slug / 'snapshots' / (sha + '.json')
        if path.exists() and path.read_bytes() != body:
            raise ValueError('snapshot_collision')
        if not path.exists():
            path.write_bytes(body)
        (root / slug / 'latest.json').write_text(json.dumps({'file': str(path.relative_to(root / slug)), 'sha256': sha, 'rows': len(data['matches'])}))
    (work / 'reconciliation-audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    (work / 'feature-coverage.json').write_text(json.dumps(coverage, indent=2))
    (work / 'missing-xg.json').write_text(json.dumps(missing, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'added_xg': report['added_xg'], 'statistics_matches': len(report['added_statistics']),
                      'remaining_xg': len(missing), 'coverage': coverage}))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    run(parser.parse_args().root)
