import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from worldcup_mvp.console import ai_reviews as ai
from worldcup_mvp.console.service import ConsoleService
from worldcup_mvp.console.store import Store, encode
from worldcup_mvp.console.sources import normalize_match
from tests.test_console_domain import fixture


class AIReviewTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=Store(Path(temp.name)/'test.sqlite')
        self.app=ConsoleService(self.store);self.engine=self.app.ai_reviews
        for p in [patch.object(ai,'get_deepseek_api_key',return_value='test-only'),
                  patch.object(ai,'get_deepseek_model',return_value='test-model'),
                  patch.dict(os.environ,{'FOOTBALL_AI_REVIEW_ENABLED':'1'})]:
            p.start();self.addCleanup(p.stop)
        m=normalize_match(fixture());m.update(kickoff='2020-01-02T12:00:00+00:00',league='日职')
        self.match=m;self.store.upsert_matches([m],'2020-01-01T00:00:00Z')
        saved=self.store.save_analysis(m['id'],{'direction':'主胜','direction_key':'h','model_version':'market-test',
            'probabilities':[50,25,25],'evidence':[],'competition':{'id':'jp.j1'}},
            {'context':{},'private_notes':'PRIVATE_NOTE_MUST_NOT_LEAVE'})
        saved['created']='2020-01-02T10:00:00+00:00'
        with self.store.connect() as db:db.execute('UPDATE analyses SET payload=? WHERE id=?',(encode(saved),saved['id']))
        self.store.save_detail(m['id'],{'results':[{'code':'HAD','combination':'h'},{'code':'CRS','combination':'2:0'}]})
        self.reply=encode({'summary':'方向与正式赛果一致，原因无法确认。',
            'findings':[{'text':'归档主胜，正式赛果2-0。','evidence_ids':['official-result','market:had']}],
            'hypotheses':[],'missing_data':['比赛事件'],'next_checks':['补充正式比赛事件后核对。']})

    def row(self):return self.app.reviews()[0]

    def test_completed_review_is_cached_and_prediction_is_unchanged(self):
        before=self.store.versions(self.match['id'])
        self.engine.enqueue(self.row())
        with patch('worldcup_mvp.ai_analyst._call_deepseek',return_value=self.reply) as call:
            self.assertTrue(self.engine.process_one())
            packet=json.loads(call.call_args.args[0][1]['content'])
            self.assertNotIn('PRIVATE_NOTE_MUST_NOT_LEAVE',encode(packet))
            self.engine.enqueue(self.row(),retry=True)
            self.assertFalse(self.engine.process_one())
            self.assertEqual(call.call_count,1)
        self.assertEqual(self.row()['ai_review']['status'],'completed')
        self.assertEqual(before,self.store.versions(self.match['id']))

    def test_unofficial_result_and_postmatch_prediction_are_rejected(self):
        row=self.row();row['result_source']='ESPN'
        with self.assertRaisesRegex(ValueError,'正式'):self.engine.enqueue(row)
        row=self.row();row['prediction']['created']='2020-01-03T10:00:00Z'
        with self.assertRaisesRegex(ValueError,'赛前'):self.engine.enqueue(row)
        row=self.row();row['score']='0-2'
        with self.assertRaisesRegex(ValueError,'冲突'):self.engine.enqueue(row)

    def test_unknown_citations_fail_and_retry_is_bounded(self):
        self.engine.enqueue(self.row())
        bad=json.loads(self.reply);bad['findings'][0]['evidence_ids']=['invented-red-card']
        with patch('worldcup_mvp.ai_analyst._call_deepseek',return_value=encode(bad)) as call:
            for _ in range(3):
                self.assertTrue(self.engine.process_one())
                with self.store.connect() as db:db.execute('UPDATE ai_review_jobs SET retry_at=0')
            self.assertFalse(self.engine.process_one())
            self.assertEqual(call.call_count,3)
        result=self.row()['ai_review'];self.assertEqual(result['status'],'failed');self.assertIsNone(result['report'])
        self.engine.enqueue(self.row(),retry=True)
        with patch('worldcup_mvp.ai_analyst._call_deepseek',return_value=self.reply):self.engine.process_one()
        self.assertEqual(self.row()['ai_review']['status'],'completed')

    def test_corrected_result_has_its_own_report_and_old_report_survives(self):
        self.engine.enqueue(self.row())
        with patch('worldcup_mvp.ai_analyst._call_deepseek',return_value=self.reply):self.engine.process_one()
        old=self.row()['ai_review']['id']
        self.store.save_detail(self.match['id'],{'results':[{'code':'HAD','combination':'h'},{'code':'CRS','combination':'3:0'}]})
        self.assertEqual(self.row()['ai_review']['status'],'pending')
        new=self.engine.enqueue(self.row())['id'];self.assertNotEqual(old,new)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT status FROM ai_review_jobs WHERE id=?',(old,)).fetchone()[0],'completed')

    def test_lease_prevents_duplicate_provider_calls(self):
        self.engine.enqueue(self.row());started=threading.Event();release=threading.Event()
        def complete(*args,**kwargs):started.set();release.wait(3);return self.reply
        with patch('worldcup_mvp.ai_analyst._call_deepseek',side_effect=complete) as call:
            worker=threading.Thread(target=self.engine.process_one);worker.start()
            self.assertTrue(started.wait(1));self.assertFalse(self.engine.process_one());release.set();worker.join(3)
            self.assertEqual(call.call_count,1)

    def test_no_key_or_disabled_does_not_call_provider(self):
        with patch.object(ai,'get_deepseek_api_key',return_value=None):
            self.assertEqual(self.row()['ai_review']['status'],'not_configured')
            with self.assertRaises(ValueError):self.engine.enqueue(self.row())
            self.assertFalse(self.engine.process_one())
        with patch.dict(os.environ,{'FOOTBALL_AI_REVIEW_ENABLED':'0'}):
            self.assertEqual(self.row()['ai_review']['status'],'disabled')
            with self.assertRaises(ValueError):self.engine.enqueue(self.row())

    def test_restart_recovers_expired_job_without_duplicate_completed_report(self):
        self.engine.enqueue(self.row())
        with self.store.connect() as db:db.execute("UPDATE ai_review_jobs SET status='running',attempts=1,lease_until=0")
        self.assertFalse(self.engine.process_one())
        self.assertEqual(self.row()['ai_review']['status'],'failed')

    def test_background_worker_enqueues_after_result_check(self):
        row=self.row()
        def reply(*args,**kwargs):
            self.app.collector_stop.set()
            return self.reply
        with patch.object(self.app,'reviews',return_value=[row]) as reviews, patch('worldcup_mvp.ai_analyst._call_deepseek',side_effect=reply) as provider:
            self.engine.start();self.engine.thread.join(3)
            self.assertFalse(self.engine.thread.is_alive())
            reviews.assert_any_call()
            self.assertEqual(provider.call_count,1)
        self.assertEqual(self.row()['ai_review']['status'],'completed')

    def test_result_change_before_processing_skips_provider(self):
        self.engine.enqueue(self.row())
        self.store.save_detail(self.match['id'],{'results':[{'code':'HAD','combination':'h'},{'code':'CRS','combination':'3:0'}]})
        with patch('worldcup_mvp.ai_analyst._call_deepseek') as provider:
            self.engine.process_one();provider.assert_not_called()


if __name__=='__main__':unittest.main()
