"""V2 read API acceptance on disposable SQLite backups and artifact copies."""
from datetime import datetime, timezone
from contextlib import closing
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tests.test_console_football_model import dataset, history
from worldcup_mvp.console import api, history_pipeline, model_registry as registry, training
from worldcup_mvp.console.catalog import classify
from worldcup_mvp.console.model_tasks import candidate, save_report
from worldcup_mvp.console.service import ConsoleService
from worldcup_mvp.console.store import Store, fingerprint


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and not p.name.endswith('-shm')}


def backup(source, target):
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)


class WorkspaceV2APITests(unittest.TestCase):
    def setUp(self):
        # These fixtures deliberately exercise a fixed sales/calendar date.
        # Keep them pregame even when the suite runs after their kickoff.
        for module in ('workspace_v2', 'model_readiness'):
            clock = patch(f'worldcup_mvp.console.{module}.datetime', wraps=datetime)
            clock.start().now.return_value = datetime(2026, 11, 4, 10, tzinfo=timezone.utc)
            self.addCleanup(clock.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = Store(self.root/'seed.sqlite')
        backup(source.path, self.root/'console.sqlite')
        for obj, name, value in ((registry, 'REGISTRY', self.root/'registry'),
                                 (history_pipeline, 'STAGING', self.root/'staging.json')):
            p = patch.object(obj, name, value)
            p.start(); self.addCleanup(p.stop)
        self.app = ConsoleService(Store(self.root/'console.sqlite'))
        p = patch.object(api, 'service', return_value=self.app)
        p.start(); self.addCleanup(p.stop)
        self.data = dataset(history(40))
        registry.import_competition(self.data, 'jp.j1')

    def read(self, cid='jp.j1', date='2026-11-04'):
        return api.get('/api/v2/workspaces', {'competition_id': [cid], 'date': [date]})['workspace']

    def fit(self):
        return candidate('jp.j1', registry.dataset('jp.j1'),
                         {'kind': 'poisson', 'mode': 'separate', 'half_life': 180},
                         cutoff=datetime.now(timezone.utc))

    def match(self, id, league='JD1', kickoff='2026-11-04T12:00:00Z', sale_date='2026-11-04'):
        return {'id': id, 'provider': 'sporttery', 'provider_id': id, 'home_id': '1', 'away_id': '2',
                'home': 'A', 'away': 'B', 'league_code': league, 'league_id': '999' if league=='ZZZ' else '1',
                'league': league, 'kickoff': kickoff, 'sale_date': sale_date, 'markets': {}}

    def test_new_unevaluated_candidate_does_not_break_workspace_or_claim_a_score(self):
        run = self.fit()
        value = self.read()
        self.assertEqual(value['models'][0]['run_id'], run['run_id'])
        self.assertIsNone(value['selected_report'])
        self.assertNotIn('hafu', value['models'][0]['supported_markets'])

    def test_selected_workspace_only_reads_its_competition_and_scores_once(self):
        from worldcup_mvp.console import competition_workspace
        self.app.store.upsert_matches([self.match('jp'), self.match('kr', league='KD1')],
                                      '2026-11-04T00:00:00Z')
        self.app.store.status('live-scores', {'status': 'available'})
        with patch.object(competition_workspace, 'training_status', wraps=competition_workspace.training_status) as status, \
             patch.object(self.app, 'scores', wraps=self.app.scores) as scores:
            result = api.get('/api/v2/workspaces', {'competition_id': ['jp.j1'], 'date': ['2026-11-04']})
        self.assertEqual([c.args[1] for c in status.call_args_list], ['jp.j1'])
        self.assertEqual(scores.call_count, 1)
        self.assertEqual(list(result['competition_workspaces']), ['jp.j1'])
        ids = [c['id'] for c in result['competitions']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn('kr.k1', ids)

    def test_validated_artifacts_reused_only_within_one_read_request(self):
        first, second = self.fit(), self.fit()
        with patch.object(registry, '_load_run', wraps=registry._load_run) as validated:
            with registry.validated_read_scope():
                for _ in range(3):
                    registry.load_run('jp.j1', first['run_id'])
                registry.load_run('jp.j1', second['run_id'])
                self.assertEqual(validated.call_count, 2)
            # Outside the read scope, normal prediction/selection always validates.
            registry.load_run('jp.j1', first['run_id'])
            self.assertEqual(validated.call_count, 3)
        path = registry.directory('jp.j1') / 'runs' / first['run_id'] / 'model.json'
        path.write_text('{}', encoding='utf-8')
        with registry.validated_read_scope():
            with self.assertRaises(ValueError):
                registry.load_run('jp.j1', first['run_id'])

    def test_read_scope_does_not_reuse_another_competition_artifact(self):
        run = self.fit()
        with registry.validated_read_scope():
            registry.load_run('jp.j1', run['run_id'])
            with self.assertRaises(ValueError):
                registry.load_run('kr.k1', run['run_id'])

    def test_selected_report_is_bound_to_selected_run_not_latest_candidate(self):
        first = self.fit()
        registry.select_observation('jp.j1', first['run_id'])
        def report(run):
            return {'created_at': datetime.now(timezone.utc).isoformat(), 'competition_id': 'jp.j1',
                    'dataset_digest': run['dataset_digest'], 'run_id': run['run_id'], 'model_id': run['model_id'],
                    'evaluation_type': 'fixed_model', 'test': {'count': 7, 'log_loss': 1.1}}
        save_report('jp.j1', report(first), 'first-report')
        second = self.fit()
        save_report('jp.j1', report(second), 'second-report')
        value = self.read()
        self.assertEqual(value['selected_report']['run_id'], first['run_id'])
        self.assertEqual(value['training']['candidate']['run_id'], second['run_id'])
        self.assertEqual([m['run_id'] for m in value['models'] if m['selected']], [first['run_id']])

    def test_every_get_preserves_database_and_artifact_bytes(self):
        run = self.fit()
        self.app.store.status('training-job:jp.j1', {'status': 'running'})
        self.app.model_tasks._write({'id': 'abandoned', 'competition_id': 'jp.j1', 'status': 'running',
                                    'created_at': '2026-11-04T00:00:00Z', 'worker_pid': -1})
        before = hashes(self.root)
        self.read()
        api.get('/api/v2/competitions/jp.j1/tasks', {})
        api.get('/api/v2/competitions/jp.j1/ingestion', {})
        api.get('/api/v2/competitions/jp.j1/runs/'+run['run_id'], {})
        self.assertEqual(hashes(self.root), before)
        self.assertEqual(self.app.model_tasks.list('jp.j1')[0]['status'], 'running')

    def test_natural_date_uses_beijing_timezone_and_unknown_matches_remain_visible(self):
        self.app.store.upsert_matches([
            self.match('utc-late', kickoff='2026-11-04T17:30:00Z', sale_date='2026-11-03'),
            self.match('unknown', league='ZZZ')], '2026-11-04T00:00:00Z')
        self.assertEqual(self.read(date='2026-11-05')['overview']['total'], 1)
        self.assertEqual(self.read(date='2026-11-04')['overview']['total'], 0)
        all_scopes = api.get('/api/v2/workspaces', {'date': ['2026-11-04']})['competition_workspaces']
        unknown = classify(self.match('unknown', league='ZZZ'))['id']
        self.assertEqual(all_scopes[unknown]['overview']['total'], 1)
        self.assertEqual(all_scopes[unknown]['overview']['unknown'], 1)

    def test_disabled_training_does_not_mark_valid_historical_dataset_corrupt(self):
        original = registry.competition_spec
        def spec(cid):
            value = original(cid)
            return {**value, 'training_enabled': False, 'training_stage_policy': {}} if cid=='jp.j1' else value
        with patch.object(registry, 'competition_spec', side_effect=spec):
            value = self.read()
        self.assertNotEqual(value['datasets']['versions'][0]['status'], '校验失败')

    def test_sources_counts_are_scoped_and_absent_halves_are_not_trained_inputs(self):
        self.app.store.upsert_matches([self.match('jp'), self.match('kr', league='KD1')], '2026-11-04T00:00:00Z')
        self.app.store.status('sporttery', {'status': 'available', 'count': 999})
        value = self.read()
        sporttery = next(s for s in value['sources'] if s['id']=='sporttery')
        self.assertEqual(sporttery['count'], 1)
        half = next(s for s in value['fundamentals'] if s['id']=='halves')
        self.assertFalse(half['training'])

    def test_public_source_actual_coverage_and_freshness_are_separate(self):
        self.app.store.upsert_matches([self.match('jp'),self.match('kr',league='KD1')], '2026-11-04T00:00:00Z')
        self.app.store.save_detail('jp',{'context':{'public_facts_source':{
            'source':'FotMob','source_url':'https://www.fotmob.com/match/one','captured_at':'2020-01-01T00:00:00Z'}}})
        value = self.read()
        source = next(s for s in value['sources'] if s['id']=='public:fotmob:jp.j1')
        self.assertEqual(source['count'],1)
        self.assertEqual(source['fresh_count'],0)
        self.assertEqual(source['status'],'stale')
        self.assertFalse(source['training'])

    def test_overview_recomputes_current_model_without_network(self):
        self.app.store.upsert_matches([self.match('jp')], '2026-11-04T00:00:00Z')
        current = {'status': 'unavailable', 'reason': 'new-selection'}
        with patch.object(registry, 'forecast', return_value=current) as forecast:
            value = self.read()
        self.assertTrue(forecast.called)
        self.assertFalse(forecast.call_args.kwargs['allow_network'])
        self.assertIn('new-selection', value['overview']['matches'][0]['reasons'])

    def test_no_network_forecast_does_not_attempt_500_mapping(self):
        run = self.fit()
        _, bundle, report, frozen = registry.load_run('jp.j1', run['run_id'])
        match = self.match('jp', kickoff='2026-11-05T12:00:00Z')
        match['home_id'] = 'unmapped'
        with patch('worldcup_mvp.console.league_data.load_history', side_effect=AssertionError('network')):
            result = training.shadow_forecast(match, {}, datetime.now(timezone.utc),
                                             artifacts=(bundle,report,frozen), allow_network=False)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['reason'], '球队标识尚未映射')

    def test_v2_selection_requires_a_real_revision_not_null(self):
        run = self.fit()
        with self.assertRaises(ValueError):
            api.post('/api/v2/competitions/jp.j1/selection', {'run_id': run['run_id'], 'expected_revision': None})
        self.assertIsNone(registry.selection('jp.j1')['run_id'])

    def test_workspace_revision_survives_stop_and_allows_reselection(self):
        run = self.fit()
        self.assertEqual(self.read()['selection_revision'], 0)
        api.post('/api/v2/competitions/jp.j1/selection', {'run_id': run['run_id'], 'expected_revision': 0})
        self.assertEqual(self.read()['selection_revision'], 1)
        api.post('/api/v2/competitions/jp.j1/selection', {'run_id': None, 'expected_revision': 1})
        self.assertEqual(self.read()['selection_revision'], 2)
        api.post('/api/v2/competitions/jp.j1/selection', {'run_id': run['run_id'], 'expected_revision': 2})
        self.assertEqual(self.read()['selection_revision'], 3)

    def test_old_client_writes_cannot_bypass_schema_or_selection_revision(self):
        with patch.object(self.app, 'prediction') as predict:
            with self.assertRaisesRegex(ValueError, '刷新'):
                api.post('/api/v1/matches/legacy/predict', {})
            predict.assert_not_called()
        run = self.fit()
        with self.assertRaisesRegex(ValueError, '刷新'):
            api.post('/api/v1/models/select', {'competition_id': 'jp.j1', 'run_id': run['run_id']})
        self.assertEqual(registry.selection('jp.j1')['revision'], 0)

    def test_v2_import_rejects_malformed_rows_as_validation_errors(self):
        with self.assertRaises(ValueError):
            api.post('/api/v2/competitions/jp.j1/datasets/import',
                     {'dataset': {'manifest': {}, 'matches': ['bad-row'], 'team_mappings': []}})

    @unittest.skipUnless(os.environ.get('FOOTBALL_WORKSPACE_COPY_SMOKE') == '1', 'explicit real-copy smoke')
    def test_production_backup_gets_are_read_only(self):
        from worldcup_mvp.console.store import ROOT
        target = self.root/'production-copy'
        target.mkdir()
        backup(ROOT/'data/console/football.sqlite3', target/'console.sqlite')
        shutil.copytree(ROOT/'data/training/competitions', target/'registry')
        app = ConsoleService(Store(target/'console.sqlite'))
        with patch.object(api, 'service', return_value=app), patch.object(registry, 'REGISTRY', target/'registry'):
            before = hashes(target)
            value = api.get('/api/v2/workspaces', {'date': ['2026-11-04']})
            self.assertIn('afc.champions', value['competition_workspaces'])
            self.assertEqual(hashes(target), before)


if __name__ == '__main__':
    unittest.main()
