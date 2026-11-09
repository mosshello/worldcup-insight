import copy
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from datetime import datetime, timezone

from worldcup_mvp.console.observation_epl_personnel import (
    CID, SCHEMA, ALL_FIELDS, fpl_vectors, fit, probabilities, blend, forecast_packet, runtime_packet)
from worldcup_mvp.console.observation_ai import validate
from worldcup_mvp.console.store import fingerprint


class EPLPersonnelTests(unittest.TestCase):
    def setUp(self):
        self.fixture = {'id': 'm1', 'competition_id': CID, 'season': '2025', 'home_id': 'h',
                        'away_id': 'a', 'kickoff': '2025-09-10T18:00:00Z'}
        self.at = '2025-09-10T17:00:00Z'
        self.capture = '2025-09-10T16:00:00Z'
        self.payload = {'events': [{'deadline_time': '2025-08-15T17:30:00Z'}],
                        'teams': [{'id': i} for i in range(1, 21)], 'elements': []}
        for team in (1, 2):
            for i in range(11):
                self.payload['elements'].append({'id': team*100+i, 'team': team, 'minutes': 180,
                    'status': 'a', 'news': '', 'news_added': None})
        self.mapping = {'1': 'h', '2': 'a'}
        self.payload['elements'][0].update(status='i', news='Knee injury', news_added=self.capture)

    def run_features(self, **kwargs):
        values = dict(payload=self.payload, fixture=self.fixture, as_of=self.at,
                      captured_at=self.capture, team_mapping=self.mapping)
        values.update(kwargs)
        return fpl_vectors(**values)

    def test_real_reports_become_numeric_features(self):
        vs, refs = self.run_features()
        self.assertEqual(vs[0]['reported_injured'], 1)
        self.assertAlmostEqual(vs[0]['flagged_minutes_share'], 1/11)
        self.assertEqual(vs[1]['opp_reported_injured'], 1)
        self.assertEqual(refs[0]['reported_players'][0]['status'], 'i')

    def test_late_stale_foreign_season_and_live_cutoff_rejected(self):
        for kw in ({'captured_at': '2025-09-10T17:00:01Z'}, {'captured_at': '2025-09-07T16:59:59Z'},
                   {'fixture': dict(self.fixture, competition_id='esp.laliga')},
                   {'fixture': dict(self.fixture, season='2024')}, {'as_of': self.fixture['kickoff']}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.run_features(**kw)

    def test_season_opening_carryover_minutes_are_not_used(self):
        f = dict(self.fixture, kickoff='2025-08-15T19:00:00Z')
        vs, refs = self.run_features(fixture=f, as_of='2025-08-15T18:00:00Z', captured_at='2025-08-15T16:00:00Z')
        self.assertEqual(vs[0]['minutes_unknown'], 1)
        self.assertEqual(vs[0]['flagged_minutes_share'], 0)

    def test_future_news_is_unknown_not_confirmed_absence(self):
        self.payload['elements'][0]['news_added'] = '2025-09-10T16:01:00Z'
        vs, refs = self.run_features()
        self.assertEqual(vs[0]['reported_injured'], 0)
        self.assertLess(vs[0]['news_status_coverage'], 1)
        self.assertEqual(refs[0]['unknown_status_ids'], [100])

    def test_missing_minutes_and_status_not_invented(self):
        self.payload['elements'][1].update(minutes=None, status='u', news='Unavailable')
        vs, refs = self.run_features()
        self.assertEqual(vs[0]['minutes_unknown'], 1)
        self.assertIn(101, refs[0]['unknown_status_ids'])

    def test_identity_collisions_rejected(self):
        self.payload['elements'].append(copy.deepcopy(self.payload['elements'][0]))
        with self.assertRaises(ValueError):
            self.run_features()
        self.payload['elements'].pop()
        with self.assertRaises(ValueError):
            self.run_features(team_mapping={'1': 'h', '2': 'h'})

    def test_fit_uses_only_available_labels_and_preserves_isolation(self):
        vs, _ = self.run_features()
        vectors = [dict.fromkeys(ALL_FIELDS, 0.) for _ in (0, 1)]
        for i in (0, 1):
            vectors[i].update(vs[i]); vectors[i]['home'] = 1-i
        samples = [{'id': str(i), 'competition_id': CID, 'vectors': vectors, 'goals': [1, 2],
            'half_goals': [0, 1], 'as_of': '2025-08-01T17:00:00Z', 'kickoff': '2025-08-01T18:00:00Z',
            'available_at': '2025-08-08T18:00:00Z'} for i in range(101)]
        samples[-1]['available_at'] = '2025-10-01T00:00:00Z'
        model = fit(samples, '2025-09-01T00:00:00Z', .08, True, 'test-input')
        self.assertEqual(model['train_count'], 100)
        self.assertNotIn('100', model['train_ids'])
        samples[-1]['competition_id'] = 'esp.laliga'
        with self.assertRaises(ValueError):
            fit(samples, '2025-09-01T00:00:00Z', .08, True, 'test-input')

    def model_packet(self):
        model = {'schema': SCHEMA, 'competition_id': CID, 'train_before': '2025-09-01T00:00:00Z',
            'fields': ['reported_injured'], 'mean': [0.], 'scale': [1.], 'coef': [-.2],
            'intercept': .5, 'half_shares': [.45, .45], 'not_trained': ['expected_minutes']}
        model['digest'] = fingerprint(model)
        facts = [{'id': s, 'side': s, 'kind': 'model_feature', 'value': {'reported_injured': i}}
                 for i, s in enumerate(('home', 'away'))]
        packet = {'fixture': self.fixture, 'as_of': self.at, 'artifact_digest': model['digest'], 'evidence': facts}
        return model, packet

    def test_news_changes_prediction_and_ai_cannot_rewrite_it(self):
        model, packet = self.model_packet()
        extraction = validate({'facts': [{'evidence_id': s, 'value': 999} for s in ('home', 'away')]}, packet, 'extract')
        result = forecast_packet(model, packet, extraction, handicap=-1)
        self.assertEqual(set(result['probabilities']), {'had', 'hhad', 'ttg', 'crs', 'hafu'})
        for probs in result['probabilities'].values():
            self.assertAlmostEqual(sum(probs.values()), 1., places=6)
        original = result['probabilities']['had']
        changed = copy.deepcopy(packet); changed['evidence'][0]['value']['reported_injured'] = 4
        self.assertNotEqual(original, forecast_packet(model, changed)['probabilities']['had'])
        with self.assertRaises(ValueError):
            forecast_packet(model, changed, extraction)
        with self.assertRaises(ValueError):
            forecast_packet(model, packet, {'facts': extraction['facts'][:1]})

    def test_blend_preserves_market_support_and_normalization(self):
        model, packet = self.model_packet()
        p = forecast_packet(model, packet)['probabilities']
        merged = blend(p, p, .5)
        self.assertNotIn('hhad', merged)
        for market in merged:
            self.assertEqual(merged[market], p[market])
        with self.assertRaises(ValueError):
            blend(p, p, 1.1)

    def test_runtime_requires_explicit_version_and_frozen_snapshot(self):
        model, packet = self.model_packet()
        model['input_reference'] = 'inputs'
        config = {'enhanced_kind': SCHEMA, 'enhanced_reference': 'model', 'enhanced_digest': model['digest']}
        with patch('worldcup_mvp.console.observation_epl_personnel.load', side_effect=[model, {'history': []}]):
            with self.assertRaisesRegex(ValueError, '原始FPL快照'):
                runtime_packet(config, self.fixture, self.at, {})
        for bad in (dict(config, enhanced_kind='unknown'), config):
            fixture = dict(self.fixture, competition_id='esp.laliga') if bad == config else self.fixture
            with self.assertRaises(ValueError):
                runtime_packet(bad, fixture, self.at, {})
        collected = {'supplemental': {'numeric_sources': {'fpl': 'snapshot'}}}
        snapshot = {'payload': self.payload, 'captured_at': self.capture, 'team_mapping': self.mapping}
        with patch('worldcup_mvp.console.observation_epl_personnel.load', side_effect=[model, {'history': []}, snapshot]), \
             patch('worldcup_mvp.console.observation_epl_personnel.feature_packet', return_value=packet):
            a, p = runtime_packet(config, self.fixture, self.at, collected)
        self.assertEqual(a, model)
        self.assertEqual(p['personnel_snapshot_reference'], 'snapshot')

    def test_live_blend_uses_the_frozen_baseline_and_weight(self):
        model, packet = self.model_packet()
        pure = forecast_packet(model, packet)['probabilities']
        base = {m: dict(zip(dist, reversed(list(dist.values())))) for m, dist in pure.items()}
        baseline = SimpleNamespace(trained_before=model['train_before'], team_counts={CID+':h':10,CID+':a':10},
                                   forecast=lambda fixture, line: {'markets':base})
        model.update(blend_baseline_reference='dc', blend_weight=.25)
        with patch('worldcup_mvp.console.observation_epl_personnel.load',return_value={'model':{}}), \
             patch('worldcup_mvp.console.training.GoalModel.from_dict',return_value=baseline):
            result = forecast_packet(model, packet)
            self.assertEqual(result['probabilities'], blend(base,pure,.25))
            baseline.trained_before = self.fixture['kickoff']
            with self.assertRaises(ValueError):forecast_packet(model,packet)


if __name__ == '__main__':
    unittest.main()
