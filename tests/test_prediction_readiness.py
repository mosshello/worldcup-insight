import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests.test_console_domain import fixture
from tests import test_console_public_enrichment as public_fixtures
from worldcup_mvp.console import sources, schedules, public_enrichment, fundamentals, supplemental_facts
from worldcup_mvp.console.context_merge import merge
from worldcup_mvp.console.readiness import check, fixture_key
from worldcup_mvp.console.quality import assess
from worldcup_mvp.console.service import ConsoleService
from worldcup_mvp.console.store import Store
from tests.football_v2_helpers import football, selected_model


def ready_context(match, now):
    stamp = now.isoformat()
    groups = [[dict(id=f'{side}-{i}', date=(now-timedelta(days=i+1)).isoformat(),
                    gf=1, ga=0, venue=('主', '客')[side], opponent='对手') for i in range(5)] for side in range(2)]
    return dict(available=True, state='upcoming', fixture_identity=fixture_key(match), captured_at=stamp,
                venue_captured_at=stamp, recent=groups, venue_recent=copy.deepcopy(groups), h2h_checked=True,
                lineups=[dict(side=s, status='confirmed', captured_at=stamp,
                             players=[dict(id=f'{s}-{i}', name=f'球员{i}') for i in range(11)]) for s in ('home', 'away')],
                personnel_verification=dict(complete=True, fixture_identity=fixture_key(match),
                    sides=['home', 'away'], source_url='https://example.com/verified', captured_at=stamp))


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.match = sources.normalize_match(fixture())
        self.match.update(league_code='JD1', kickoff=(self.now+timedelta(hours=3)).isoformat(), captured_at=self.now.isoformat())
        for market in self.match['markets'].values():
            market.update(updated_at=self.now.isoformat(), sale_status='selling')
        self.context = ready_context(self.match, self.now)

    def test_complete_pregame_does_not_require_future_match_xg_or_events(self):
        self.assertTrue(check(self.match, self.context, self.now)['ready'])
        self.assertFalse(check(self.match, self.context, self.now)['training_features_changed'])

    def test_every_required_field_fails_closed(self):
        for field in ('fixture_identity', 'captured_at', 'venue_captured_at', 'recent', 'venue_recent',
                      'h2h_checked', 'lineups', 'personnel_verification'):
            with self.subTest(field=field):
                c = copy.deepcopy(self.context); c.pop(field)
                self.assertFalse(check(self.match, c, self.now)['ready'])

    def test_one_side_duplicate_future_invalid_or_small_sample_is_not_complete(self):
        for field in ('recent', 'venue_recent'):
            for kind in ('one_side', 'duplicate', 'future', 'nan', 'boolean', 'small', 'wrong_venue'):
                with self.subTest(field=field, kind=kind):
                    c = copy.deepcopy(self.context)
                    if kind == 'one_side': c[field][1] = []
                    if kind == 'duplicate': c[field][0][1] = copy.deepcopy(c[field][0][0])
                    if kind == 'future': c[field][0][0]['date'] = (self.now+timedelta(days=1)).isoformat()
                    if kind == 'nan': c[field][0][0]['gf'] = float('nan')
                    if kind == 'boolean': c[field][0][0]['gf'] = True
                    if kind == 'small': c[field][0].pop()
                    if kind == 'wrong_venue':
                        if field != 'venue_recent': continue
                        c[field][0][0]['venue'] = '客'
                    self.assertFalse(check(self.match, c, self.now)['ready'])

    def test_unknown_stale_or_future_time_cannot_be_refreshed_by_enriched_at(self):
        for age in (1801, -1):
            c = copy.deepcopy(self.context)
            c.update(captured_at=(self.now-timedelta(seconds=age)).isoformat(), enriched_at=self.now.isoformat())
            self.assertFalse(check(self.match, c, self.now)['ready'])
        for field in ('recent', 'venue_recent', 'lineups'):
            c = {**self.context, 'stale_fields': {field: 'timeout'}}
            self.assertFalse(check(self.match, c, self.now)['ready'])

    def test_lineup_phase_duplicates_conflicts_and_single_side(self):
        for kind in ('duplicate', 'single_side', 'conflict', 'late_observed'):
            c = copy.deepcopy(self.context)
            if kind == 'duplicate': c['lineups'][0]['players'][1] = c['lineups'][0]['players'][0]
            if kind == 'single_side': c['lineups'][1]['side'] = 'home'
            if kind == 'conflict': c['lineups'][0]['conflicts'] = ['球员0']
            if kind == 'late_observed': c['lineups'][0]['observed_before_kickoff'] = False
            self.assertFalse(check(self.match, c, self.now)['ready'], kind)
        c = copy.deepcopy(self.context)
        c['estimated_lineups'] = c.pop('lineups')
        for r in c['estimated_lineups']: r['status'] = 'estimated'
        self.assertTrue(check(self.match, c, self.now)['ready'])
        near = {**self.match, 'kickoff': (self.now+timedelta(minutes=30)).isoformat()}
        c['fixture_identity'] = fixture_key(near)
        c['personnel_verification']['fixture_identity'] = fixture_key(near)
        self.assertFalse(check(near, c, self.now)['ready'])

    def test_reschedule_cross_competition_cancelled_and_postmatch_block(self):
        for change in ({'league_code': 'KD1'}, {'home_id': 'different'}, {'kickoff': (self.now+timedelta(days=1)).isoformat()}):
            self.assertFalse(check({**self.match, **change}, self.context, self.now)['ready'])
        for state in ('live', 'completed', 'postponed', 'cancelled', 'suspended'):
            self.assertFalse(check(self.match, {**self.context, 'state': state}, self.now)['ready'])
        self.assertFalse(check(self.match, self.context, self.now+timedelta(hours=4))['ready'])

    def test_empty_or_partial_bulletins_never_prove_full_personnel_clearance(self):
        for value in ({}, {'complete': True}, {**self.context['personnel_verification'], 'sides': ['home']},
                      {**self.context['personnel_verification'], 'source_url': None}):
            c = {**self.context, 'injuries': [], 'injuries_verified': True, 'personnel_verification': value}
            self.assertFalse(check(self.match, c, self.now)['ready'])

    def test_partial_failure_preserves_original_time_but_successful_empty_replaces(self):
        old = {**self.context, 'estimated_lineups': [{'side': 'home'}],
               'external_odds': {'rows': [1], 'captured_at': '2026-01-01T00:00:00Z'}}
        before = copy.deepcopy(old)
        for message in ('HTTP 429', 'HTTP 403', 'timeout', 'invalid HTML'):
            result = merge(self.match, {**self.context, 'supplementary_error': message}, old)
            self.assertEqual(result['external_odds'], old['external_odds'])
            self.assertIn('estimated_lineups', result['stale_fields'])
        fresh = merge(self.match, {**self.context, 'estimated_lineups': [], 'external_odds': {'rows': []}}, old)
        self.assertEqual(fresh['estimated_lineups'], [])
        self.assertEqual(fresh['external_odds']['rows'], [])
        self.assertEqual(old, before)
        changed = {**self.match, 'league_code': 'KD1'}
        self.assertNotIn('external_odds', merge(changed, {'supplementary_error': 'timeout'}, old))

    def test_malformed_source_payload_is_visible_and_cannot_pass_readiness(self):
        with patch.object(fundamentals, '_base_context', return_value=None), \
             patch.object(public_enrichment, 'fetch', return_value={}), \
             patch.object(supplemental_facts, 'fetch', return_value={'rows': [], 'errors': [], 'xg': None}):
            result = fundamentals.fetch_context(self.match)
        self.assertFalse(result['available'])
        self.assertIn('格式', result['reason'])
        self.assertIn('supplementary_error', result)
        self.assertFalse(check(self.match, result, self.now)['ready'])

    def test_saved_identity_skips_rolling_index_but_rejects_changed_header(self):
        sample = public_fixtures.PublicEnrichmentTests(); sample.setUp()
        m = {**sample.match, '_source_identity': {'fixture': fixture_key(sample.match), 'identity': sample.identity}}
        def page(url):
            self.assertNotIn('live.500.com', url)
            return sample.detail(), self.now.isoformat()
        with patch.object(public_enrichment, 'page', side_effect=page):
            self.assertEqual(public_enrichment.fetch(m)['source_identity'], m['_source_identity'])
        with patch.object(public_enrichment, 'page', return_value=(sample.detail().replace('/team/22/', '/team/99/'), self.now.isoformat())):
            with self.assertRaises(sources.SourceError): public_enrichment.fetch(m)

    def test_public_prediction_entry_blocks_missing_data_without_archive_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder)/'test.sqlite'); store.upsert_matches([self.match], self.now.isoformat())
            app = ConsoleService(store); app.current_ids = {self.match['id']}
            detail = dict(markets=self.match['markets'], captured_at=self.now.isoformat(), odds_fresh=True, context={})
            with patch.object(app, 'sync'), patch.object(app, 'detail', return_value=detail), \
                 patch('worldcup_mvp.console.service.predict') as predict:
                with self.assertRaisesRegex(ValueError, '模型|映射|赛事|样本'): app.prediction(self.match['id'])
                predict.assert_not_called()
            self.assertEqual(store.versions(), [])

    def test_complete_inputs_archive_readiness_receipt_and_keep_old_versions(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder)/'test.sqlite'); store.upsert_matches([self.match], self.now.isoformat())
            app = ConsoleService(store); app.current_ids = {self.match['id']}
            detail = dict(markets=self.match['markets'], history={}, captured_at=self.now.isoformat(),
                          odds_fresh=True, context=self.context, evidence=[])
            with patch.object(app, 'sync'), patch.object(app, 'detail', return_value=detail), \
                 selected_model(self.match):
                result = app.prediction(self.match['id'])
                self.assertIsNotNone(result['prediction'])
                before = store.versions()
                detail['context']['lineups'] = []
                second = app.prediction(self.match['id'])
                self.assertEqual(second['prediction']['probabilities'],result['prediction']['probabilities'])
            self.assertIn(before[0],store.versions())
            with store.connect() as db:
                import json
                receipt = json.loads(db.execute('SELECT input_snapshot FROM analyses').fetchone()[0])
            self.assertTrue(receipt['data_receipt']['quality']['fundamentals']['ready'])
            self.assertEqual(receipt['data_receipt']['fundamentals_captured_at'], self.context['captured_at'])

    def test_identity_change_during_generation_cannot_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder)/'test.sqlite'); store.upsert_matches([self.match], self.now.isoformat())
            app = ConsoleService(store); app.current_ids = {self.match['id']}
            detail = dict(markets=self.match['markets'], history={}, captured_at=self.now.isoformat(),
                          odds_fresh=True, context=self.context, evidence=[])
            def changed(*args):
                store.upsert_matches([{**self.match, 'kickoff': (self.now+timedelta(days=1)).isoformat()}], self.now.isoformat())
                return football(self.match)
            with patch.object(app, 'sync'), patch.object(app, 'detail', return_value=detail), \
                 patch('worldcup_mvp.console.model_registry.forecast', side_effect=changed):
                with self.assertRaisesRegex(ValueError, '变更'): app.prediction(self.match['id'])
            self.assertEqual(store.versions(), [])

    def test_readiness_is_rechecked_after_generation_before_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder)/'test.sqlite'); store.upsert_matches([self.match], self.now.isoformat())
            app = ConsoleService(store); app.current_ids = {self.match['id']}
            detail = dict(markets=self.match['markets'], history={}, captured_at=self.now.isoformat(),
                          odds_fresh=True, context=self.context, evidence=[])
            from worldcup_mvp.console.forecast_result import build
            def expires(match, data, model):
                result = build(match,data,model)
                match['captured_at'] = (self.now-timedelta(hours=1)).isoformat()
                return result
            with patch.object(app, 'sync'), patch.object(app, 'detail', return_value=detail), \
                 selected_model(self.match), patch('worldcup_mvp.console.forecast_result.build', side_effect=expires):
                with self.assertRaisesRegex(ValueError, '归档前'): app.prediction(self.match['id'])
            self.assertEqual(store.versions(), [])

    def test_venue_history_excludes_wrong_competition_side_future_neutral_and_extra_time(self):
        event = {'id': 'one', 'date': (self.now-timedelta(days=1)).isoformat(), 'league': {'slug': 'uefa.champions'},
                 'competitions': [{'status': {'type': {'completed': True, 'name': 'STATUS_FULL_TIME'}}, 'competitors': [
                     {'homeAway': 'home', 'team': {'id': '1'}, 'score': {'displayValue': '2'}},
                     {'homeAway': 'away', 'team': {'id': '2'}, 'score': {'displayValue': '0'}}]}]}
        m = {**self.match, 'league_code': 'UCL'}
        self.assertEqual(len(schedules.venue_history({'team': {'id': '1'}, 'events': [event, event]}, '1', 'home', m, self.now)), 1)
        for kind in ('league', 'future', 'neutral', 'extra_time', 'wrong_team', 'reverse'):
            e = copy.deepcopy(event)
            if kind == 'league': e['league']['slug'] = 'eng.1'
            if kind == 'future': e['date'] = (self.now+timedelta(days=1)).isoformat()
            if kind == 'neutral': e['competitions'][0]['neutralSite'] = True
            if kind == 'extra_time': e['competitions'][0]['status']['type']['name'] = 'STATUS_FINAL_AET'
            if kind == 'wrong_team': e['competitions'][0]['competitors'][0]['team']['id'] = '3'
            if kind == 'reverse': e['competitions'][0]['competitors'][0]['homeAway'] = 'away'
            self.assertEqual(schedules.venue_history({'team': {'id': '1'}, 'events': [e]}, '1', 'home', m, self.now), [], kind)


if __name__ == '__main__':
    unittest.main()
