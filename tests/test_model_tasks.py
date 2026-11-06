"""Task boundaries, immutable inputs and selection concurrency in isolated storage."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from tests.test_console_football_model import dataset, history
from worldcup_mvp.console import model_registry as registry, training
from worldcup_mvp.console import model_tasks
from worldcup_mvp.console import model_evaluation
from worldcup_mvp.console.readiness import fixture_key
from worldcup_mvp.console.store import Store, fingerprint


class ModelTaskTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name, value in [('REGISTRY', self.root/'registry')]:
            mock = patch.object(registry, name, value)
            mock.start(); self.addCleanup(mock.stop)
        for name, value in [('MIN_ROWS', 10), ('MIN_SEASONS', 1), ('MIN_TEST', 2)]:
            mock = patch.object(training, name, value)
            mock.start(); self.addCleanup(mock.stop)
        self.store = Store(self.root/'test.sqlite')
        self.tasks = model_tasks.ModelTasks(self.store)
        self.data = dataset(history(45, half=True))
        registry.import_competition(self.data, 'jp.j1')

    def finish(self, job):
        self.tasks._threads[(self.tasks.key, job['id'])].join(10)
        row = next(r for r in self.tasks.list('jp.j1') if r['id'] == job['id'])
        self.assertEqual(row['status'], 'completed', row)
        return row

    def fitted(self):
        return self.finish(self.tasks.start('jp.j1', {'kind': 'fit', 'date_to': '2023-01-20'}))

    def test_fit_never_evaluates_selects_or_overstates_half_capability(self):
        with patch.object(model_tasks, 'evaluate', side_effect=AssertionError('implicit experiment')):
            job = self.fitted()
        manifest, bundle, report, _ = registry.load_run('jp.j1', job['result']['run_id'])
        self.assertEqual(report['status'], 'not_evaluated')
        self.assertEqual(report['test']['count'], 0)
        self.assertNotIn('hafu', bundle['models']['jp.j1']['supported_markets'])
        self.assertIsNone(registry.selection('jp.j1')['run_id'])
        self.assertEqual(registry.status('jp.j1')['candidate']['run_id'], manifest['run_id'])

    def test_cold_start_opt_in_failure_persists_report_without_candidate(self):
        from worldcup_mvp.console import cold_start
        with self.assertRaisesRegex(ValueError,'冷启动'):
            self.tasks.start('jp.j1',{'kind':'fit','cold_start_policy':cold_start.POLICY})
        report={'competition_id':'jp.j1','created_at':'2026-01-01T00:00:00+00:00',
                'evaluation_type':'cold_start_strategy','test':{'count':0},'records':[],
                'admission':{'policy':cold_start.POLICY,'passed':False,'allowed_strata':[]}}
        with patch.object(cold_start,'evaluate',return_value=report),patch.object(model_tasks,'evaluate',side_effect=AssertionError('implicit comparison')):
            job=self.finish(self.tasks.start('jp.j1',{'kind':'experiment','algorithm':'dynamic_gamma',
                'cold_start_policy':cold_start.POLICY}))
        self.assertFalse(job['result']['admitted'])
        self.assertFalse((registry.directory('jp.j1')/'candidate.json').exists())
        self.assertIsNone(registry.selection('jp.j1')['run_id'])
        self.assertEqual(registry.evaluation_reports('jp.j1')[0]['evaluation_type'],'cold_start_strategy')

    def test_cold_start_candidate_binds_immutable_report_and_does_not_select(self):
        from worldcup_mvp.console import cold_start
        policy={'policy':cold_start.POLICY,'passed':True,'allowed_strata':['3-4'],
                'identity_policy':'exact-espn-id-v1','target_stages':['regular']}
        report={'competition_id':'jp.j1','created_at':'2026-01-01T00:00:00+00:00',
                'evaluation_type':'cold_start_strategy','test':{'count':30},'records':[], 'admission':policy}
        with patch.object(cold_start,'evaluate',return_value=report):
            job=self.finish(self.tasks.start('jp.j1',{'kind':'experiment','algorithm':'dynamic_gamma',
                'cold_start_policy':cold_start.POLICY}))
        run=job['result']['run_id']
        manifest,bundle,artifact_report,_=registry.load_run('jp.j1',run)
        self.assertEqual(bundle['cold_start_policy']['allowed_strata'],['3-4'])
        self.assertEqual(bundle['cold_start_policy'],artifact_report['cold_start_policy'])
        self.assertIsNone(registry.selection('jp.j1')['run_id'])
        evidence=registry.directory('jp.j1')/'reports'/(bundle['cold_start_policy']['report_id']+'.json')
        evidence.write_text('{}',encoding='utf8')
        with self.assertRaises(ValueError):registry.load_run('jp.j1',run)

    def test_fixed_evaluation_never_refits_or_changes_artifact(self):
        fitted = self.fitted()
        run_id = fitted['result']['run_id']
        root = registry.directory('jp.j1')/'runs'/run_id
        original = {p.name: p.read_bytes() for p in root.iterdir()}
        with self.assertRaisesRegex(ValueError, '截止'):
            self.tasks.start('jp.j1', {'kind': 'evaluate', 'run_id': run_id, 'date_from': '2023-01-01'})
        with patch.object(training.GoalModel, 'fit', side_effect=AssertionError('refit')):
            job = self.finish(self.tasks.start('jp.j1', {'kind': 'evaluate', 'run_id': run_id,
                'date_from': '2023-01-22', 'date_to': '2023-02-10'}))
        self.assertEqual(job['result']['evaluation_type'], 'fixed_model')
        report = registry.evaluation_reports('jp.j1')[0]
        self.assertTrue(all(r['trained_before'] == report['records'][0]['trained_before'] for r in report['records']))
        self.assertEqual(original, {p.name:p.read_bytes() for p in root.iterdir()})
        registry.select_observation('jp.j1', run_id)
        self.assertEqual(registry.status('jp.j1')['current_evaluation']['id'], report['id'])

    def test_start_freezes_digest_and_history_survives_new_manager(self):
        started, release = threading.Event(), threading.Event()
        original = model_tasks.candidate
        def wait(*args, **kwargs):
            started.set(); release.wait(5)
            return original(*args, **kwargs)
        with patch.object(model_tasks, 'candidate', side_effect=wait):
            job = self.tasks.start('jp.j1', {'kind': 'fit'})
            self.assertTrue(started.wait(2))
            changed = copy.deepcopy(self.data)
            changed['matches'][0]['home_goals'] += 1
            registry.import_competition(changed, 'jp.j1')
            queued = self.tasks.start('jp.j1', {'kind': 'fit'})
            self.assertEqual(queued['status'], 'queued')
            release.set()
            done = self.finish(job)
        self.assertNotEqual(done['dataset_digest'], fingerprint(registry.dataset('jp.j1')))
        self.assertEqual(done['dataset_digest'], done['result']['dataset_digest'])
        persisted = model_tasks.ModelTasks(self.store).list('jp.j1')
        self.assertEqual({r['id'] for r in persisted}, {job['id'], queued['id']})
        self.assertEqual(next(r for r in persisted if r['id'] == queued['id'])['status'], 'completed')

    def test_selection_revision_and_historical_read_are_independent_from_training_gate(self):
        job = self.fitted()
        run = job['result']['run_id']
        with registry.training_lock('jp.j1'):
            selected = registry.select_observation('jp.j1', run, expected_revision=0)
        self.assertEqual(selected['revision'], 1)
        with self.assertRaisesRegex(ValueError, '刷新'):
            registry.select_observation('jp.j1', None, expected_revision=0)
        spec = {**registry.competition_spec('jp.j1'), 'training_enabled': False, 'training_stage_policy': {}}
        with patch.object(registry, 'competition_spec', return_value=spec):
            self.assertEqual(registry.load_run('jp.j1', run)[0]['run_id'], run)
            with self.assertRaises(ValueError):
                self.tasks.start('jp.j1', {'kind': 'fit'})

    def test_rolling_evaluation_has_no_candidate_or_selection_side_effect(self):
        job = self.finish(self.tasks.start('jp.j1', {'kind': 'evaluate', 'date_from': '2023-02-01'}))
        self.assertEqual(job['result']['evaluation_type'], 'rolling_strategy')
        self.assertFalse((registry.directory('jp.j1')/'candidate.json').exists())
        records = registry.evaluation_reports('jp.j1')[0]['records']
        self.assertTrue(all(r['trained_before'] < r['asof'] for r in records))

    def test_failed_publication_keeps_existing_candidate_and_old_files(self):
        old = self.fitted()['result']['run_id']
        root = registry.directory('jp.j1')
        before = {p:p.read_bytes() for p in (root/'runs'/old).iterdir()}
        with patch.object(registry, 'load_run', side_effect=ValueError('校验失败')):
            job = self.tasks.start('jp.j1', {'kind': 'fit'})
            self.tasks._threads[(self.tasks.key, job['id'])].join(10)
        self.assertEqual(self.tasks.list('jp.j1')[0]['status'], 'failed')
        self.assertEqual(registry.read(root/'candidate.json')['run_id'], old)
        self.assertEqual(before, {p:p.read_bytes() for p in (root/'runs'/old).iterdir()})
        with patch.object(training,'fit_bundle',side_effect=AssertionError('completed fit must be reused')):
            retry = self.finish(self.tasks.start('jp.j1',{'retry_of':job['id']}))
        self.assertEqual(registry.read(root/'candidate.json')['run_id'],retry['result']['run_id'])
        self.assertNotEqual(retry['result']['run_id'],old)
        self.assertEqual(retry['child_stages'][0]['reused_from'],job['id'])

    def test_explicit_experiment_keeps_strategy_scores_off_final_artifact(self):
        def experiment(data, progress, target, test_selected_only, artifact_sink):
            self.assertTrue(test_selected_only)
            self.assertIsNotNone(artifact_sink)
            return {'competition_id': 'jp.j1', 'created_at': '2026-01-01T00:00:00+00:00',
                    'dataset_digest': fingerprint(data), 'test': {'count': 9, 'log_loss': 1.1},
                    'selected_by_competition': {'jp.j1': {'kind': 'poisson', 'half_life': 180, 'mode': 'separate'}}}
        with patch.object(model_tasks, 'evaluate', side_effect=experiment):
            job = self.finish(self.tasks.start('jp.j1', {'kind': 'experiment'}))
        report = registry.load_run('jp.j1', job['result']['run_id'])[2]
        self.assertEqual(report['test']['count'], 0)
        self.assertEqual(report['status'], 'not_evaluated')
        self.assertEqual(report['strategy_report_id'], job['result']['strategy_evaluation']['report_id'])
        self.assertEqual(registry.evaluation_reports('jp.j1')[0]['test']['count'], 9)

    def test_interrupted_task_is_preserved_not_replaced(self):
        finished = self.fitted()
        abandoned = {**finished, 'id': 'abandoned', 'status': 'running', 'worker_pid': -1}
        self.tasks._write(abandoned)
        manager = model_tasks.ModelTasks(self.store)
        self.assertEqual(next(r for r in manager.list('jp.j1') if r['id'] == 'abandoned')['status'], 'running')
        rows = manager.list('jp.j1', reconcile=True)
        self.assertEqual(len(rows), 2)
        self.assertEqual(next(r for r in rows if r['id'] == 'abandoned')['status'], 'interrupted')

    def test_request_key_retries_reuse_frozen_job_and_reject_changed_request(self):
        body = {'kind': 'fit', 'request_key': 'click-1'}
        first = self.finish(self.tasks.start('jp.j1', body))
        changed = copy.deepcopy(self.data)
        changed['matches'][0]['home_goals'] += 1
        registry.import_competition(changed, 'jp.j1')
        replay = model_tasks.ModelTasks(self.store).start('jp.j1', body)
        self.assertEqual(replay['id'], first['id'])
        self.assertEqual(replay['dataset_digest'], first['dataset_digest'])
        self.assertEqual(replay['protocol_digest'], fingerprint(replay['protocol']))
        self.assertEqual(len(self.tasks.list('jp.j1')), 1)
        with self.assertRaisesRegex(ValueError, '不同任务'):
            self.tasks.start('jp.j1', {**body, 'algorithm': 'dc'})

    def test_batch_reports_rejected_partition_without_invalidating_good_partition(self):
        data = dataset(history(30) + history(30, league='kr.k1'))
        data['matches'][-1]['period'] = 'shootout'
        report = registry.import_batch(data)
        self.assertEqual(report['imported'], 1)
        self.assertEqual(report['rejected'], 1)
        self.assertEqual(report['partitions'][0]['competition_id'], 'jp.j1')
        self.assertEqual(report['partitions'][1]['status'], 'rejected')

    def test_experiment_test_segment_is_used_only_for_validation_winner(self):
        data = registry.dataset('jp.j1')
        calls = []
        def walk(rows, targets, config, vectors):
            calls.append((targets[0]['id'], config['kind']))
            p = [.8, .1, .1] if config['kind'] == 'poisson' else [.3, .4, .3]
            return [{'id': 'x', 'competition_id': 'jp.j1', 'season': '2023', 'stage': 'regular',
                     'probabilities': p, 'outcome': 0, 'market': None, 'markets': {'had': dict(zip('hda', p))},
                     'actual_markets': {'had': 'h'}}]
        candidates = [{'kind': kind, 'half_life': 180, 'mode': 'separate'} for kind in ('poisson', 'dc')]
        with patch.object(model_evaluation, 'walk', side_effect=walk):
            report = model_evaluation.evaluate(data, candidates=candidates, test_selected_only=True)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[-1][1], 'poisson')
        self.assertEqual(len(report['models']), 1)

    def test_exact_espn_identity_works_without_quotes_or_500_fallback(self):
        data = copy.deepcopy(self.data)
        data['team_mappings'] = [{'competition_id': 'jp.j1', 'provider': 'espn', 'provider_id': str(i), 'team_id': team}
                                 for i, team in ((99, 'a'), (100, 'b'))]
        registry.import_competition(data, 'jp.j1')
        job = self.fitted()
        _, bundle, report, frozen = registry.load_run('jp.j1', job['result']['run_id'])
        now = datetime(2023, 1, 22, tzinfo=timezone.utc)
        match = {'id': 'test', 'league_code': 'JD1', 'home_id': 'unmapped1', 'away_id': 'unmapped2',
                 'kickoff': '2023-01-23T12:00:00+00:00'}
        evidence = {'fixture': fixture_key(match), 'provider': 'espn', 'competition_slug': 'jpn.1',
                    'event_id': 'event', 'kickoff': match['kickoff'], 'captured_at': now.isoformat(),
                    'team_ids': {'home': '99', 'away': '100'}, 'source_url': 'https://example.org/test'}
        detail = {'context': {'espn_identity': evidence}, 'quality': {'eligible_markets': []}}
        with patch('worldcup_mvp.console.league_data.load_history', side_effect=AssertionError('fallback')):
            result = training.shadow_forecast(match, detail, now, artifacts=(bundle, report, frozen))
        self.assertEqual(result['status'], 'shadow')
        self.assertEqual(result['team_identity']['evidence_digest'], fingerprint(evidence))
        self.assertTrue(result['markets'][0]['selected_keys'])
        self.assertNotIn('hhad', [market['code'] for market in result['markets']])

    def test_fifo_queue_and_simultaneous_idempotent_requests(self):
        actual = model_tasks.candidate
        order = []
        def record(*args, **kwargs):
            order.append(kwargs['task']['id'])
            return actual(*args, **kwargs)
        with patch.object(model_tasks, 'candidate', side_effect=record):
            with registry.training_lock('jp.j1'):
                first = self.tasks.start('jp.j1', {'kind': 'fit', 'request_key': 'first'})
                with ThreadPoolExecutor(max_workers=2) as pool:
                    repeated = list(pool.map(lambda _: model_tasks.ModelTasks(self.store).start(
                        'jp.j1', {'kind': 'fit', 'request_key': 'second'}), range(2)))
                self.assertEqual(repeated[0]['id'], repeated[1]['id'])
                third = self.tasks.start('jp.j1', {'kind': 'fit', 'request_key': 'third'})
                with patch.object(self.tasks, '_write', side_effect=AssertionError('GET mutation')):
                    self.assertEqual([r['status'] for r in self.tasks.list('jp.j1')], ['queued']*3)
            self.finish(third)
        self.assertEqual(order, [first['id'], repeated[0]['id'], third['id']])

    def test_startup_recovery_resumes_queued_frozen_input(self):
        with patch.object(self.tasks, '_ensure_worker'):
            job = self.tasks.start('jp.j1', {'kind': 'fit'})
        changed = copy.deepcopy(self.data)
        changed['matches'][0]['home_goals'] += 1
        registry.import_competition(changed, 'jp.j1')
        manager = model_tasks.ModelTasks(self.store)
        manager.reconcile()
        manager._workers[(manager.key, 'jp.j1')].join(10)
        result = manager.list('jp.j1')[0]
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['result']['dataset_digest'], job['dataset_digest'])

    def test_import_retains_denominator_and_quarantine_provenance(self):
        data = copy.deepcopy(self.data)
        data['manifest'].update(unique_source_events=47, excluded=[{'id': 'x', 'reason': 'conflict'}],
                                sample_policy='test', continuity_policy='test-era', source_receipt={'digest': 'receipt'})
        registry.import_competition(data, 'jp.j1')
        manifest = registry.dataset('jp.j1')['manifest']
        for key in ('unique_source_events', 'excluded', 'sample_policy', 'continuity_policy', 'source_receipt'):
            self.assertEqual(manifest[key], data['manifest'][key])

    def test_new_fold_artifact_replays_forecast_with_exact_point_in_time_inputs(self):
        job = self.finish(self.tasks.start('jp.j1', {'kind':'evaluate','date_from':'2023-02-01'}))
        report = registry.evaluation_reports('jp.j1')[0]
        first = report['records'][0]
        ref = first['artifact_ref']
        artifact, model, inputs = model_tasks.load_fold_artifact('jp.j1', job['id'], ref['digest'])
        original = registry.dataset('jp.j1', job['dataset_digest'])
        target = next(r for r in original['matches'] if r['id'] == first['id'])
        self.assertEqual(model.forecast(target,target.get('handicap'))['markets'], first['markets'])
        self.assertNotIn(target['id'], artifact['input_receipt']['row_ids'])
        self.assertEqual(artifact['input_receipt']['digest'], fingerprint(inputs))
        cutoff = training.parse_date(artifact['cutoff'])
        self.assertTrue(all(training.parse_date(r['result_available_at']) < cutoff for r in inputs))
        self.assertTrue(all(r['competition_id'] == 'jp.j1' for r in inputs))
        self.assertEqual(job['child_stages'][0]['status'], 'completed')
        self.assertEqual(job['child_stages'][0]['artifacts'][0]['digest'], ref['digest'])
        changed = copy.deepcopy(self.data)
        changed['matches'][-1]['home_goals'] += 1
        registry.import_competition(changed,'jp.j1')
        _, old, _ = model_tasks.load_fold_artifact('jp.j1', job['id'], ref['digest'])
        self.assertEqual(old.forecast(target,target.get('handicap'))['markets'], first['markets'])
        with self.assertRaises((ValueError, FileNotFoundError)):
            model_tasks.load_fold_artifact('kr.k1',job['id'],ref['digest'])

    def test_fixed_evaluation_references_original_run_without_creating_fold_models(self):
        fitted = self.fitted()
        run_id = fitted['result']['run_id']
        job = self.finish(self.tasks.start('jp.j1', {'kind':'evaluate','run_id':run_id,'date_from':'2023-01-22'}))
        report = registry.evaluation_reports('jp.j1')[0]
        self.assertTrue(all(r['artifact_ref']['run_id'] == run_id for r in report['records']))
        self.assertTrue(all(r['artifact_ref']['digest'] == fitted['result']['model_digest'] for r in report['records']))
        self.assertFalse((registry.directory('jp.j1')/'task_artifacts'/job['id']/'folds').exists())

    def test_feature_model_fold_replays_elo_from_frozen_history(self):
        job = self.finish(self.tasks.start('jp.j1', {'kind':'evaluate','algorithm':'elo_logistic',
            'target':'had','date_from':'2023-02-01'}))
        report = registry.evaluation_reports('jp.j1')[0]
        first = report['records'][0]
        artifact, model, rows = model_tasks.load_fold_artifact('jp.j1',job['id'],first['artifact_ref']['digest'])
        target = next(r for r in self.data['matches'] if r['id']==first['id'])
        self.assertEqual(model.forecast(target)['markets'],first['markets'])
        self.assertEqual(artifact['feature_metadata']['policy'],'elo_recent_results_weekly')
        from worldcup_mvp.console.model_methods import HISTORY_FIELDS
        self.assertEqual(model.history, [{k: row[k] for k in HISTORY_FIELDS if k in row} for row in rows])
        self.assertEqual(artifact['input_receipt']['digest'], fingerprint(rows))
        self.assertTrue(all('odds' not in row and 'postmatch_statistics' not in row for row in model.history))

    def test_experiment_persists_candidate_stage_receipts_without_selecting_model(self):
        from worldcup_mvp.console.model_methods import configs
        with patch.object(model_evaluation, 'configs', return_value=configs()[:2]):
            job = self.finish(self.tasks.start('jp.j1', {'kind':'experiment'}))
        stages = {s['id']:s for s in job['child_stages']}
        self.assertEqual({s['status'] for s in stages.values()}, {'completed'})
        self.assertIn('validation:poisson:180', stages)
        self.assertIn('validation:dc:180', stages)
        self.assertEqual(len([k for k in stages if k.startswith('test:')]), 1)
        self.assertIn('final_fit', stages)
        self.assertIsNone(registry.selection('jp.j1')['run_id'])
        report = registry.evaluation_reports('jp.j1')[0]
        self.assertTrue(all(c['validation_records'][0]['artifact_ref']['kind'] == 'fold' for c in report['candidates']))
        self.assertTrue(all('hafu' not in c['supported_markets'] for c in report['candidates']))
        self.assertTrue(all('elo_difference' not in c['feature_metadata']['features'] for c in report['candidates']))
        self.assertEqual(report['evaluation_protocol'], 'weekly-cutoff-common-validation-v2')
        final_report = registry.load_run('jp.j1',job['result']['run_id'])[2]
        self.assertEqual(set(final_report['selected_by_competition']['jp.j1']), {'kind','mode','half_life'})
        self.assertEqual(final_report['status'], 'not_evaluated')

    def test_candidate_selection_uses_common_validation_rows_and_rejects_small_intersection(self):
        data = registry.dataset('jp.j1')
        rows = training.validate_dataset(data)
        _, _, _, validation, test = training.split_dataset(rows)
        configs = [{'kind':kind,'mode':'separate','half_life':180} for kind in ('poisson','dc')]
        def walk(all_rows, targets, config, vectors, **kwargs):
            is_validation = targets[0]['id'] == validation[0]['id']
            selected = targets[:5] if config['kind'] == 'poisson' else targets[:3]+targets[-2:]
            records = []
            for i,row in enumerate(selected):
                p = (.6 if i < 3 else .99) if config['kind']=='poisson' else (.7 if i < 3 else .01)
                markets = training.market_probabilities(training.score_grid(1.3,1.1))
                markets['had'] = dict(zip('hda', [p,(1-p)/2,(1-p)/2]))
                records.append({'id':row['id'],'competition_id':'jp.j1','season':row['season'],'stage':row['stage'],
                    'probabilities':[p,(1-p)/2,(1-p)/2],'outcome':0,'market':None,'markets':markets,
                    'actual_markets':{'had':'h','ttg':'s1','crs':training.score_key(1,0)},
                    'artifact_ref':{'digest':config['kind'], 'kind':'fold'}})
            kwargs.get('exclusions',[]).extend({'id':r['id'],'reason':'test missing input'} for r in targets if r not in selected)
            return records
        with patch.object(model_evaluation,'walk',side_effect=walk):
            report = model_evaluation.evaluate(data,candidates=configs,test_selected_only=True,artifact_sink=lambda x:x)
            self.assertEqual(report['selected']['kind'],'dc')
            by_kind = {c['kind']:c for c in report['candidates']}
            self.assertLess(by_kind['poisson']['validation_raw']['log_loss'], by_kind['dc']['validation_raw']['log_loss'])
            self.assertEqual(report['validation_sample']['common_count'],3)
            self.assertTrue(all(c['validation']['count']==3 for c in report['candidates']))
            with patch.object(training,'MIN_TEST',4), self.assertRaisesRegex(ValueError,'共同验证样本不足4'):
                model_evaluation.evaluate(data,candidates=configs,test_selected_only=True,artifact_sink=lambda x:x)

    def test_retry_reuses_completed_stages_and_partial_fold_without_refitting(self):
        from worldcup_mvp.console.model_methods import configs
        actual_fit = model_evaluation.fit
        dc_cutoffs = []
        def fail_second_dc(config, rows, at, cid, vectors):
            if config['kind'] == 'dc':
                dc_cutoffs.append(at)
                if len(dc_cutoffs) == 2:
                    raise RuntimeError('test interrupted fold')
            return actual_fit(config,rows,at,cid,vectors)
        with patch.object(model_evaluation,'configs',return_value=configs()[:2]), \
             patch.object(model_evaluation,'fit',side_effect=fail_second_dc):
            failed = self.tasks.start('jp.j1', {'kind':'experiment'})
            self.tasks._threads[(self.tasks.key,failed['id'])].join(10)
        prior = self.tasks.list('jp.j1')[0]
        self.assertEqual(prior['status'],'failed')
        self.assertTrue(prior['heartbeat_at'])
        self.assertTrue(prior['worker_token'])
        with patch.object(model_evaluation,'configs',return_value=configs()[:2]), \
             patch.object(model_evaluation,'fit',wraps=actual_fit) as fitted:
            retry = self.finish(self.tasks.start('jp.j1', {'retry_of':failed['id'],'request_key':'retry-1'}))
        self.assertEqual(retry['protocol_digest'],prior['protocol_digest'])
        self.assertEqual(retry['retry_of'],prior['id'])
        validation = next(s for s in retry['child_stages'] if s['id']=='validation:poisson:180')
        self.assertEqual(validation['reused_from'],prior['id'])
        self.assertFalse(any(c.args[0]['kind']=='dc' and c.args[2]==dc_cutoffs[0] for c in fitted.call_args_list))
        with self.assertRaises(ValueError):
            self.tasks.start('jp.j1', {'retry_of':failed['id'],'algorithm':'dc'})
        with self.assertRaises(ValueError):
            self.tasks.start('kr.k1', {'retry_of':failed['id']})
        self.assertIsNone(registry.selection('jp.j1')['run_id'])


if __name__ == '__main__':
    unittest.main()
