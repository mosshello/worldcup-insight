from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.reconcile_big_five import (
    DATE_REVIEWS, STATS_REVIEWS, TEAMS, COMPETITION_ID, SEASON_ID, STATS_SOURCE,
    official_evidence, reviewed_xg, official_statistics, field_coverage,
)
from scripts.supplement_big_five import digest
from tests.test_big_five_dataset import canonical
from worldcup_mvp.console.big_five_dataset import prepare


def date_fixture():
    review = DATE_REVIEWS[0]
    row = canonical()
    row.update(id=review['id'], competition_id='fra.ligue1', season='2026',
               home_id=review['team_ids'][0], away_id=review['team_ids'][1],
               home_name='Toulouse', away_name='Troyes', kickoff=review['canonical_kickoff'],
               home_goals=0, away_goals=0)
    source = {'id': review['source_id'], 'date': review['date'], 'home': 'Toulouse', 'away': 'Troyes',
              'gh': 0, 'ga': 0, 'xgh': .52, 'xga': .34}
    history = {'Toulouse': [{'date': review['date'][:10], 'home': True, 'gf': 0, 'ga': 0, 'xg': .52, 'xga': .34}],
               'Troyes': [{'date': review['date'][:10], 'home': False, 'gf': 0, 'ga': 0, 'xg': .34, 'xga': .52}]}
    evidence = {'url': review['url'], 'http_status': '200', 'transport_exit': 0}
    return review, source, row, history, evidence


def stat_fixture():
    review = STATS_REVIEWS[0]
    row = canonical()
    row.update(id=review[0], competition_id='esp.laliga', season='2022', kickoff='2023-02-11T15:15:00+00:00',
               home_id=TEAMS[review[3]][1], away_id=TEAMS[review[4]][1], home_goals=2, away_goals=3,
               home_half_goals=1, away_half_goals=2, postmatch_statistics={})
    game = {'id': review[2], 'competition': {'id': COMPETITION_ID}, 'season': {'id': SEASON_ID},
            'gameweek': {'week': 21, 'round': {'name': 'Regular'}}, 'period': {'type': 'FullTime'},
            'time': '2023-02-11T15:15:00Z', 'homeScore': 2, 'awayScore': 3,
            'homeTeam': {'id': TEAMS[review[3]][0], 'shortName': review[3]},
            'awayTeam': {'id': TEAMS[review[4]][0], 'shortName': review[4]}}
    stats = {'homeTeamStats': {'stats': {'wonCorners': 8, 'totalPass': 411, 'totalYelCard': 2, 'possessionPercentage': 51.5}},
             'awayTeamStats': {'stats': {'wonCorners': 2, 'totalPass': 404, 'totalYelCard': 3, 'possessionPercentage': 48.5}}}
    return review, {'game': game, 'stats': stats}, row


class ReconciliationTests(unittest.TestCase):
    def test_date_exception_preserves_source_date_unknown_timezone_and_target(self):
        review, source, row, history, evidence = date_fixture()
        original = deepcopy(row)
        target, fact = reviewed_xg(review, source, [row], history, evidence, {}, {}, '2026-11-04T14:25:58Z')
        self.assertIs(target, row)
        self.assertEqual(row, original)
        self.assertEqual(fact['archive_timestamp'], source['date'])
        self.assertIsNone(fact['archive_timezone'])
        self.assertFalse(fact['identity_exception']['exact_kickoff_verified'])
        self.assertFalse(fact['approved_as_training_feature'])

    def test_exception_cannot_accept_other_sources_fixtures_or_changed_scores(self):
        review, source, row, history, evidence = date_fixture()
        for change in [{'id': 'bsd32010'}, {'src': 'bsd'}, {'date': '2026-10-23 15:15'},
                       {'home': 'Troyes', 'away': 'Toulouse'}, {'gh': 1}, {'xga': float('nan')}, {'xga': -1}]:
            with self.assertRaises(ValueError):
                reviewed_xg(review, {**source, **change}, [row], history, evidence, {}, {}, 'time')
        for change in [{'competition_id': 'eng.premier'}, {'kickoff': '2026-10-24T16:15:00Z'},
                       {'home_id': row['away_id']}, {'training_eligible': False}, {'season': '2025'}, {'away_goals': 1}]:
            with self.assertRaises(ValueError):
                reviewed_xg(review, source, [{**row, **change}], history, evidence, {}, {}, 'time')
        with self.assertRaises(ValueError):
            reviewed_xg({**review, 'id': 'espn:other'}, source, [row], history, evidence, {}, {}, 'time')

    def test_exception_requires_unique_fixture_and_both_club_histories(self):
        review, source, row, history, evidence = date_fixture()
        with self.assertRaises(ValueError):
            reviewed_xg(review, source, [row, {**row, 'id': 'another'}], history, evidence, {}, {}, 'time')
        history['Troyes'][0]['xg'] = .99
        with self.assertRaises(ValueError):
            reviewed_xg(review, source, [row], history, evidence, {}, {}, 'time')

    def test_official_evidence_rejects_truncation_tampering_and_script_only_markers(self):
        review = DATE_REVIEWS[0]
        with TemporaryDirectory() as tmp:
            work = Path(tmp)
            body = ('<main>' + ' '.join(review['markers']) + '</main>').encode()
            path = work / (review['file'] + '.html'); path.write_bytes(body)
            meta = work / (review['file'] + '.receipt.json')
            receipt = {'url': review['url'], 'http_status': '200', 'transport_exit': 0, 'sha256': digest(body)}
            meta.write_text(json.dumps(receipt))
            self.assertEqual(official_evidence(work, review), receipt)
            meta.write_text(json.dumps({**receipt, 'transport_exit': 28}))
            with self.assertRaises(ValueError): official_evidence(work, review)
            meta.write_text(json.dumps(receipt)); path.write_bytes(body + b'changed')
            with self.assertRaises(ValueError): official_evidence(work, review)
            body = ('<script>' + ' '.join(review['markers']) + '</script>').encode()
            path.write_bytes(body); meta.write_text(json.dumps({**receipt, 'sha256': digest(body)}))
            with self.assertRaises(ValueError): official_evidence(work, review)

    def test_official_statistics_require_exact_identity_scope_time_and_scores(self):
        review, data, row = stat_fixture()
        for change in [{'competition_id': 'fra.ligue1'}, {'season': '2023'}, {'home_id': row['away_id']},
                       {'kickoff': '2023-02-11T16:15:00Z'}, {'home_goals': 3}, {'period': 'extra_time'}]:
            with self.assertRaises(ValueError): official_statistics(review, data, {**row, **change}, TEAMS, {})
        d = deepcopy(data); d['game']['competition']['id'] = 'other'
        with self.assertRaises(ValueError): official_statistics(review, d, row, TEAMS, {})
        d = deepcopy(data); d['game']['period']['type'] = 'FirstHalf'
        with self.assertRaises(ValueError): official_statistics(review, d, row, TEAMS, {})

    def test_missing_cards_not_zero_and_second_yellow_not_added_to_total_red(self):
        review, data, row = stat_fixture()
        fact = official_statistics(review, data, row, TEAMS, {})
        self.assertNotIn('redCards', fact['statistics']['home'])
        data['stats']['homeTeamStats']['stats'].update(totalRedCard=1, secondYellow=1)
        fact = official_statistics(review, data, row, TEAMS, {})
        self.assertEqual(fact['statistics']['home']['redCards'], 1)
        self.assertEqual(fact['statistics']['home']['secondYellowCards'], 1)
        self.assertNotIn('redCards', fact['statistics']['away'])
        for value in [None, float('inf'), -1, True, 1.5]:
            data['stats']['homeTeamStats']['stats']['totalPass'] = value
            with self.assertRaises(ValueError): official_statistics(review, data, row, TEAMS, {})

    def test_import_does_not_overwrite_sources_or_activate_postmatch_features(self):
        review, data, row = stat_fixture()
        row['supplemental_basics'] = {'statistics': {'home': {'totalShots': 17}, 'away': {'totalShots': 12}}}
        fact = official_statistics(review, data, row, TEAMS, {})
        fact['approved_as_training_feature'] = True
        row['additional_postmatch_statistics'] = {STATS_SOURCE: fact}
        source = {'manifest': {'competition_id': 'esp.laliga', 'source': 'esp.1'}, 'matches': [row], 'coverage': []}
        prepared = prepare(source, 'a' * 64, 'user')['matches'][0]
        self.assertEqual(prepared['postmatch_statistics'], {})
        self.assertEqual(prepared['supplemental_basics'], row['supplemental_basics'])
        self.assertFalse(prepared['additional_postmatch_statistics'][STATS_SOURCE]['approved_as_training_feature'])
        self.assertTrue(row['additional_postmatch_statistics'][STATS_SOURCE]['approved_as_training_feature'])

    def test_coverage_requires_both_sides_in_one_source_and_keeps_explicit_zero(self):
        _, _, row = stat_fixture()
        row['postmatch_statistics'] = {'home': {'redCards': 0}}
        row['additional_postmatch_statistics'] = {STATS_SOURCE: {'statistics': {'away': {'redCards': 0}}}}
        count, _ = field_coverage({'esp.1': {'matches': [row]}})
        self.assertEqual(count['esp.1']['redCards_both'], 0)
        row['postmatch_statistics']['away'] = {'redCards': 0}
        count, _ = field_coverage({'esp.1': {'matches': [row]}})
        self.assertEqual(count['esp.1']['redCards_both'], 1)
        row['postmatch_statistics']['away']['redCards'] = None
        count, _ = field_coverage({'esp.1': {'matches': [row]}})
        self.assertEqual(count['esp.1']['redCards_both'], 0)


if __name__ == '__main__':
    unittest.main()
