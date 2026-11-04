import copy
import unittest
from unittest.mock import patch

from worldcup_mvp.console import public_match_facts as facts, personnel, supplemental_facts
from worldcup_mvp.console.sources import SourceError


class PublicMatchFactsTests(unittest.TestCase):
    def setUp(self):
        self.match = dict(id='sporttery:1', provider_id='1', league_code='UCL', home='莱红牛', away='曼城',
                          home_code='RBL', away_code='MNC', kickoff='2026-11-05T04:00:00+08:00')
        self.row = dict(id=100, home={'id': 178475}, away={'id': 8456},
                        status={'utcTime': '2026-11-04T20:00:00Z'})
        self.feed = {'leagues': [{'primaryId': 42, 'matches': [self.row]}]}
        self.payload = {'general': dict(matchId='100', parentLeagueId=42, homeTeam={'id': 178475},
            awayTeam={'id': 8456}, matchTimeUTCDate='2026-11-04T20:00:00Z', started=False),
            'content': {'lineup': {'lineupType': 'predicted',
                'homeTeam': {'id': 178475, 'starters': [{'id': i + 1, 'name': str(i)} for i in range(11)],
                             'unavailable': [{'id': 20, 'name': 'Player A', 'unavailability':
                                              {'type': 'injury', 'expectedReturn': 'Doubtful'}}]},
                'awayTeam': {'id': 8456, 'unavailable': []}}}}
        self.captured = '2026-11-04T10:00:00Z'

    def parse(self):
        return facts.parse(self.payload, self.match, self.row, self.captured)

    def test_exact_identity_and_day(self):
        self.assertEqual(facts.fixture(self.match, self.feed)['id'], 100)
        for field, value in [('home_code', 'ARS'), ('away_code', 'UNKNOWN'), ('league_code', 'ED1'),
                             ('kickoff', '2026-11-06T04:00:00+08:00')]:
            with self.subTest(field=field), self.assertRaises(SourceError):
                facts.fixture(dict(self.match, **{field: value}), self.feed)

    def test_duplicate_fixture_rejected(self):
        self.feed['leagues'][0]['matches'].append(copy.deepcopy(self.row))
        with self.assertRaises(SourceError): facts.fixture(self.match, self.feed)

    def test_detail_identity_conflicts_rejected(self):
        for field, value in [('matchId', '999'), ('parentLeagueId', 73), ('homeTeam', {'id': 8456}),
                             ('matchTimeUTCDate', '2026-11-05T20:00:00Z')]:
            original = copy.deepcopy(self.payload)
            self.payload['general'][field] = value
            with self.subTest(field=field), self.assertRaises(SourceError): self.parse()
            self.payload = original

    def test_partial_personnel_and_point_in_time(self):
        result = self.parse()
        self.assertEqual(result['personnel'][0]['status'], '出场存疑')
        self.assertFalse(result['personnel'][0]['official'])
        self.assertTrue(result['personnel'][0]['observed_before_kickoff'])
        self.assertEqual(result['personnel_coverage'][1]['status'], 'source_empty')
        self.assertFalse(result['personnel_coverage'][1]['complete'])
        context = personnel.annotate({'injuries': result['personnel']}, self.match)
        self.assertFalse(context['injuries_verified'])
        self.captured = '2026-11-05T10:00:00Z'
        self.assertFalse(self.parse()['personnel'][0]['observed_before_kickoff'])

    def test_absent_personnel_field_is_not_zero_absences(self):
        del self.payload['content']['lineup']['awayTeam']['unavailable']
        self.assertEqual(self.parse()['personnel_coverage'][1]['status'], 'not_provided')

    def test_previous_starting_lineup_not_predicted_or_confirmed(self):
        self.assertEqual(len(self.parse()['estimated_lineups']), 1)
        for kind in ('lastStarting11', 'standard', 'unknown'):
            self.payload['content']['lineup']['lineupType'] = kind
            self.assertEqual(self.parse()['estimated_lineups'], [])

    def test_incomplete_or_duplicate_lineup_rejected(self):
        players = self.payload['content']['lineup']['homeTeam']['starters']
        players[-1]['id'] = players[0]['id']
        self.assertEqual(self.parse()['estimated_lineups'], [])
        players[-1]['id'] = 99
        players[-1]['name'] = ''
        self.assertEqual(self.parse()['estimated_lineups'], [])

    def test_pregame_xg_never_observed_value(self):
        self.payload['content']['stats'] = {'Periods': {'All': {'stats': [
            {'stats': [{'key': 'expected_goals', 'stats': [1.1, 2.2]}]}]}}}
        self.assertIsNone(self.parse()['xg'])
        self.payload['general']['started'] = True
        self.assertIsNone(self.parse()['xg'])
        self.captured = '2026-11-04T21:00:00Z'
        self.assertEqual(self.parse()['xg']['home'], '1.1')
        self.assertTrue(self.parse()['xg']['provisional'])
        self.assertFalse(self.parse()['xg']['training_eligible'])
        self.payload['general']['finished'] = True
        self.assertFalse(self.parse()['xg']['provisional'])

    def test_invalid_xg_not_filled_with_zero(self):
        self.payload['general']['started'] = True
        self.captured = '2026-11-04T21:00:00Z'
        for value in (None, True, -1, float('nan'), 'missing'):
            self.payload['content']['stats'] = {'Periods': {'All': {'stats': [
                {'stats': [{'key': 'expected_goals', 'stats': [value, 2.2]}]}]}}}
            self.assertIsNone(self.parse()['xg'])

    def test_failure_does_not_discard_existing_official_facts(self):
        with patch.object(supplemental_facts, 'fetch_personnel', return_value={'rows': [{'name': 'Official'}], 'errors': []}), \
             patch.object(supplemental_facts, 'fetch_xg', return_value=None), \
             patch.object(facts, 'fetch', side_effect=SourceError('source timeout')):
            result = supplemental_facts.fetch(self.match)
        self.assertEqual(result['rows'], [{'name': 'Official'}])
        self.assertIn('source timeout', result['errors'])

    def test_fetch_uses_utc_fixture_day_and_exact_event(self):
        with patch.object(facts, 'source_json', side_effect=[(self.feed, self.captured), (self.payload, self.captured)]) as fetch:
            result = facts.fetch(self.match)
        self.assertTrue(fetch.call_args_list[0].args[0].endswith('date=20261104'))
        self.assertTrue(fetch.call_args_list[1].args[0].endswith('matchId=100'))
        self.assertEqual(len(result['personnel']), 1)

    def test_unmapped_team_rejected_before_network_or_cache_write(self):
        with patch.object(facts, 'source_json') as fetch:
            for side in ('home', 'away'):
                with self.subTest(side=side), self.assertRaises(SourceError):
                    facts.fetch(dict(self.match, **{side + '_code': 'UNKNOWN'}))
            fetch.assert_not_called()


if __name__ == '__main__': unittest.main()
