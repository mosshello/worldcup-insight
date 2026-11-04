import json
import hashlib
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from worldcup_mvp.console.store import Store
from worldcup_mvp.console.store import now_iso
from worldcup_mvp.console.data_tasks import DataTasks


class DataTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service=DataTasks(Store(Path(self.tmp.name)/'db.sqlite'))
        self.scope=patch('worldcup_mvp.console.data_tasks.catalog',return_value=[{'id':x} for x in DataTasks.supported])
        self.scope.start();self.addCleanup(self.scope.stop)

    def enqueue(self,cid='afc.champions',body=None):
        with patch('worldcup_mvp.console.data_tasks.threading.Thread.start'):
            return self.service.start(cid,body or {'request_key':'one','years':[2024]})

    def test_idempotent_parameters_and_competition_isolation(self):
        task=self.enqueue()
        self.assertEqual(self.enqueue()['id'],task['id'])
        with self.assertRaisesRegex(ValueError,'不同采集参数'):
            self.enqueue(body={'request_key':'one','years':[2023]})
        other=self.enqueue('jp.j1',{'request_key':'one','seasons':['2024']})
        self.assertNotEqual(task['id'],other['id'])
        self.assertEqual(len(self.service.list('afc.champions')),1)

    def test_transport_errors_fail_without_publishing_pointer(self):
        for error in ['HTTP 403','HTTP 429','timeout']:
            task=self.enqueue(body={'request_key':error,'years':[2024]})
            with patch('worldcup_mvp.console.espn_history.fetch_receipt',side_effect=ValueError(error)):
                self.service._run(task)
            self.assertEqual(self.service.list('afc.champions')[0]['status'],'failed')
            self.assertFalse((self.service.root/'afc.champions'/'latest.json').exists())

    def test_successful_empty_is_distinct_from_transport_failure(self):
        task=self.enqueue()
        with patch('worldcup_mvp.console.espn_history.fetch_receipt',return_value=({'events':[]},{'status':'succeeded_empty'})):
            self.service._run(task)
        result=self.service.list('afc.champions')[0]
        self.assertEqual(result['status'],'failed')
        self.assertTrue(result['result']['empty'])
        self.assertEqual(result['result']['receipts'][0]['status'],'succeeded_empty')

    def test_partial_japan_is_failed_and_preserves_prior_pointer(self):
        cid='jp.j1';directory=self.service.root/cid;directory.mkdir(parents=True)
        pointer=directory/'latest.json';pointer.write_text('{"task_id":"old"}')
        task=self.enqueue(cid,{'request_key':'one','seasons':['2024']})
        data={'matches':[{'id':'500:1','competition_id':cid}], 'coverage':[{'issues':['timeout'],'structure_ok':False}]}
        with patch('worldcup_mvp.console.history_pipeline.collect',return_value=data):self.service._run(task)
        self.assertEqual(self.service.list(cid)[0]['status'],'failed')
        self.assertEqual(json.loads(pointer.read_text())['task_id'],'old')

    def test_success_is_collection_only_and_restart_detects_interruption(self):
        task=self.enqueue()
        with patch.object(self.service,'_collect',return_value={'original_count':8,'unique_count':8,'admitted':5,'quarantined':3}):
            self.service._run(task)
        self.assertEqual(self.service.list('afc.champions')[0]['status'],'succeeded')
        self.assertTrue((self.service.root/'afc.champions'/'latest.json').exists())
        second=self.enqueue(body={'request_key':'two','years':[2024]})
        second['updated_at']='2000-01-01T00:00:00+00:00'
        with self.service.store.connect() as db:
            db.execute('UPDATE ingestion_tasks SET payload=? WHERE id=?',(json.dumps(second),second['id']))
        rows=DataTasks(self.service.store).list('afc.champions',reconcile=True)
        self.assertEqual(next(x for x in rows if x['id']==second['id'])['status'],'interrupted')

    def test_unsupported_and_invalid_selection_rejected(self):
        with self.assertRaises(ValueError):self.enqueue('uefa.champions')
        with self.assertRaises(ValueError):self.enqueue(body={'years':[]})
        with self.assertRaises(ValueError):self.enqueue('jp.j1',{'seasons':['unknown']})

    def test_acl_verified_batch_publishes_candidate_but_incomplete_summary_does_not(self):
        from tests.test_espn_history import fixtures
        event,summary=fixtures()
        board={'leagues':[{'id':'3902','slug':'afc.champions'}],'events':[event]}
        task=self.enqueue()
        with patch('worldcup_mvp.console.espn_history.fetch_receipt',side_effect=[(board,{'status':'succeeded'}),(summary,{'status':'succeeded'})]):
            self.service._run(task)
        complete=self.service.list('afc.champions')[0]
        self.assertEqual(complete['status'],'succeeded')
        self.assertEqual(complete['result']['admitted'],1)
        self.assertEqual(complete['result']['quarantined'],0)
        pointer=self.service.root/'afc.champions'/'latest.json'
        prior=pointer.read_bytes()
        second=self.enqueue(body={'request_key':'two','years':[2024]})
        with patch('worldcup_mvp.console.espn_history.fetch_receipt',side_effect=[(board,{'status':'succeeded'}),({}, {'status':'succeeded'})]):
            self.service._run(second)
        self.assertEqual(self.service.list('afc.champions')[0]['status'],'failed')
        self.assertEqual(pointer.read_bytes(),prior)

    def test_explicit_retry_reuses_verified_raw_preserves_parent_and_scope(self):
        from tests.test_espn_history import fixtures
        from worldcup_mvp.console.espn_history import BASE
        event,summary=fixtures()
        board={'leagues':[{'id':'3902','slug':'afc.champions'}],'events':[event]}
        parent=self.enqueue()
        def partial(task,directory):
            folder=directory/'raw';folder.mkdir()
            raw=folder/'original.raw';raw.write_text(json.dumps(board))
            receipt={'id':'original','url':BASE+'scoreboard?dates=2024&limit=1000',
                'status':'succeeded','http_status':'200','observed_at':now_iso(),
                'raw_path':str(raw),'sha256':hashlib.sha256(raw.read_bytes()).hexdigest()}
            (folder/'original.receipt.json').write_text(json.dumps(receipt))
            raise TimeoutError('summary timed out')
        with patch.object(self.service,'_collect',side_effect=partial):self.service._run(parent)
        parent_before=json.dumps(self.service.list('afc.champions')[0],sort_keys=True)
        retry=self.enqueue(body={'request_key':'retry','retry_of':parent['id']})
        self.assertEqual(retry['request'],parent['request'])
        with patch('worldcup_mvp.console.espn_history.fetch_receipt',return_value=(summary,{'status':'succeeded'})) as fetch:
            self.service._run(retry)
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[0],'summary?event=123')
        rows=self.service.list('afc.champions')
        complete=next(r for r in rows if r['id']==retry['id'])
        self.assertEqual(complete['status'],'succeeded')
        self.assertEqual(complete['reused_responses'],1)
        self.assertEqual(complete['resume_mode'],'verified_cache_replay')
        self.assertEqual(json.dumps(next(r for r in rows if r['id']==parent['id']),sort_keys=True),parent_before)
        with self.assertRaisesRegex(ValueError,'保留原任务范围'):
            self.enqueue(body={'retry_of':parent['id'],'years':[2023]})
        with self.assertRaisesRegex(ValueError,'本赛事失败或中断'):
            self.enqueue('jp.j1',{'retry_of':parent['id']})

    def test_retry_without_cache_is_explicit_restart_and_succeeded_cannot_retry(self):
        parent=self.enqueue()
        with patch.object(self.service,'_collect',side_effect=TimeoutError('failed')):self.service._run(parent)
        retry=self.enqueue(body={'retry_of':parent['id'],'request_key':'retry'})
        with patch.object(self.service,'_collect',return_value={'admitted':1}):self.service._run(retry)
        final=self.service.list('afc.champions')[0]
        self.assertEqual(final['resume_mode'],'restart')
        with self.assertRaisesRegex(ValueError,'本赛事失败或中断'):
            self.enqueue(body={'retry_of':retry['id']})

    def test_heartbeat_during_network_work_and_get_remains_read_only(self):
        task=self.enqueue()
        self.service.heartbeat_seconds=0.01
        def collect(task,directory):
            first=self.service.list('afc.champions')[0]['heartbeat_at']
            time.sleep(0.08)
            live=self.service.list('afc.champions')[0]
            self.assertGreater(live['heartbeat_at'],first)
            self.assertEqual(live['lease_owner'],self.service.owner)
            self.assertTrue(live['lease_until'])
            return {'admitted':1}
        with patch.object(self.service,'_collect',side_effect=collect):self.service._run(task)
        final=self.service.list('afc.champions')[0]
        self.assertEqual(final['status'],'succeeded')
        self.assertIsNone(final['lease_until'])
        before=json.dumps(final,sort_keys=True)
        self.assertEqual(json.dumps(self.service.list('afc.champions')[0],sort_keys=True),before)

    def test_retry_corrupt_or_expired_receipt_refetches_instead_of_trusting_cache(self):
        from worldcup_mvp.console.espn_history import BASE
        parent=self.enqueue()
        with patch.object(self.service,'_collect',side_effect=TimeoutError('failed')):self.service._run(parent)
        folder=Path(parent['output_dir'])/'raw';folder.mkdir()
        raw=folder/'old.raw';raw.write_text('{"events":[]}')
        receipt={'url':BASE+'scoreboard?dates=2024&limit=1000','status':'succeeded','http_status':'200',
                 'observed_at':now_iso(),'raw_path':str(raw),'sha256':'incorrect'}
        (folder/'old.receipt.json').write_text(json.dumps(receipt))
        retry=self.enqueue(body={'retry_of':parent['id']})
        directory=self.service.root/'afc.champions'/'runs'/retry['id']
        with patch('worldcup_mvp.console.espn_history.fetch_receipt',return_value=({'events':[]},{'status':'succeeded_empty'})) as fetch:
            self.service._fetch_acl(retry,'scoreboard?dates=2024&limit=1000',directory)
        fetch.assert_called_once()
        receipt.update(sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),observed_at='2000-01-01T00:00:00+00:00')
        (folder/'old.receipt.json').write_text(json.dumps(receipt))
        with patch('worldcup_mvp.console.espn_history.fetch_receipt',return_value=({'events':[]},{})) as fetch:
            self.service._fetch_acl(retry,'scoreboard?dates=2024&limit=1000',directory)
        fetch.assert_called_once()
        self.assertEqual(retry['reused_responses'],0)
