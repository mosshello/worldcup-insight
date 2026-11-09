import copy,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from worldcup_mvp.console.store import Store,encode,fingerprint
from worldcup_mvp.console.observation_ai import prepare_input,restore_input,ObservationAI,ObservationAIError,retry_once


class InputV4Tests(unittest.TestCase):
    def setUp(self):
        self.at=datetime.now(timezone.utc)
        self.packet={'fixture':{'id':'m','kickoff':(self.at+timedelta(days=1)).isoformat()},'as_of':self.at.isoformat(),
                     'evidence':[{'id':'e1','kind':'personnel','text':'uncertain','history_refs':['a']}],
                     'excluded':['rejected'],'digest':'old','market':{'marker':'do-not-show-in-blind'}}

    def test_legacy_inputs_match_exact_original_algorithms(self):
        original={k:v for k,v in self.packet.items() if k not in ('digest','excluded','market')}
        for version in ('observation-ai-v1','observation-ai-v2'):
            self.assertEqual(prepare_input(self.packet,'blind',version),original)
        refs=['a'];key=fingerprint(refs)
        expected={**original,'evidence':[{'id':'e1','kind':'personnel','text':'uncertain','history_ref_id':key}],
                  'history_reference_catalog':{key:refs},'source_packet_digest':fingerprint(original)}
        self.assertEqual(prepare_input(self.packet,'blind','observation-ai-v3'),expected)

    def test_v4_lossless_duplicate_text_and_control_like_source_arrays(self):
        text='https://official.example/source/'+'a'*250
        packet=copy.deepcopy(self.packet)
        packet['evidence']=[{'id':f'e{i}','kind':'personnel','text':text,'published_at':None,'value':i,
            'history_refs':{'source_url':text,'literal':['$text',0],'also':['$literal',['$text',1]],'id':['$text',2]}} for i in range(52)]
        before=copy.deepcopy(packet)
        for mode in ('blind','extract','market'):
            value=prepare_input(packet,mode,'observation-ai-v4')
            expected={k:v for k,v in packet.items() if k not in ('digest','excluded') and (k!='market' or mode=='market')}
            self.assertEqual(restore_input(value),expected)
            self.assertEqual([r['id'] for r in value['evidence']],[r['id'] for r in packet['evidence']])
            self.assertLess(len(encode(value)),len(encode(expected)))
            if mode!='market':self.assertNotIn('do-not-show-in-blind',encode(value))
        self.assertEqual(packet,before)

    def test_unique_oversize_is_rejected_without_truncation_or_call(self):
        self.packet['evidence'][0]['text']='x'*40001
        with tempfile.TemporaryDirectory() as directory:
            ai=ObservationAI(Store(Path(directory)/'db'),lambda *args:self.fail('network must not run'))
            with self.assertRaises(ObservationAIError) as caught:ai.predict(self.packet,prompt_version='observation-ai-v4')
            self.assertEqual(caught.exception.code,'input_too_large')
            self.assertEqual(ai.budget_status()['calls_used'],0)

    def test_ui_snapshot_is_canonical_and_actual_request_is_archived(self):
        url='https://official.example/source/'+'long-path-'*20
        self.packet['evidence']=[{'id':f'e{i}','source_url':url,'text':'reported','captured_at':self.at.isoformat()} for i in range(12)]
        sent=[]
        def transport(messages,model):
            import json
            sent.append(json.loads(messages[1]['content']))
            return {'probabilities':{'h':.4,'d':.3,'a':.3},'evidence_ids':['e0'],'rationale':'reported'},{'total_tokens':50}
        with tempfile.TemporaryDirectory() as directory:
            ai=ObservationAI(Store(Path(directory)/'db'),transport)
            result=ai.predict(self.packet,prompt_version='observation-ai-v4')
            self.assertEqual(result['input_snapshot']['evidence'][0]['source_url'],url)
            self.assertEqual(result['input_snapshot']['evidence'][0]['captured_at'],self.at.isoformat())
            self.assertEqual(result['request_snapshot'],sent[0])
            self.assertEqual(result['input_digest'],fingerprint(sent[0]))
            self.assertEqual(result['source_input_digest'],fingerprint(result['input_snapshot']))

    def test_retry_retains_failed_receipt_and_accounts_both_attempts(self):
        count=[]
        def transport(*args):
            count.append(1)
            if len(count)==1:raise ObservationAIError('provider_unavailable','赛前AI请求失败或返回无效')
            return {'probabilities':{'h':.4,'d':.3,'a':.3},'evidence_ids':['e1'],'rationale':'uncertain'}, {'total_tokens':50}
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'db');ai=ObservationAI(store,transport)
            value=retry_once(lambda retry:ai.predict(self.packet,prompt_version='observation-ai-v4',allow_retry=retry),
                             version='observation-ai-v4',deadline=(self.at+timedelta(minutes=5)).isoformat())
            with store.connect() as db:rows=db.execute('SELECT id,status,error FROM observation_ai_calls ORDER BY rowid').fetchall()
            self.assertEqual([r['status'] for r in rows],['failed','completed'])
            self.assertEqual(rows[0]['error'],'赛前AI请求失败或返回无效')
            self.assertEqual(rows[1]['id'],rows[0]['id']+'-2')
            self.assertEqual(value['call_id'],rows[1]['id'])
            self.assertEqual(ai.budget_status()['calls_used'],2)
            self.assertGreater(ai.budget_status()['tokens_accounted'],50)
            self.assertEqual(ai.predict(self.packet,prompt_version='observation-ai-v4'),value)

    def test_short_deadline_and_nontransient_or_legacy_do_not_retry(self):
        for version,code,seconds in [('observation-ai-v4','provider_unavailable',45),('observation-ai-v3','provider_unavailable',300),
                ('observation-ai-v4','output_invalid',300),('observation-ai-v4','provider_http_402',300),('observation-ai-v4','daily_call_budget',300)]:
            calls=[]
            def invoke(retry):calls.append(retry);raise ObservationAIError(code,'safe')
            with self.subTest(version=version,code=code,seconds=seconds),self.assertRaises(ObservationAIError):
                retry_once(invoke,version=version,deadline=(self.at+timedelta(seconds=seconds)).isoformat())
            self.assertEqual(calls,[False])

    def test_retry_still_honors_budget(self):
        def transport(*args):raise ObservationAIError('provider_unavailable','赛前AI请求失败或返回无效')
        with tempfile.TemporaryDirectory() as directory,patch.dict('os.environ',{'FOOTBALL_OBSERVATION_AI_DAILY_CALLS':'1'}):
            ai=ObservationAI(Store(Path(directory)/'db'),transport)
            with self.assertRaises(ObservationAIError) as caught:
                retry_once(lambda retry:ai.predict(self.packet,prompt_version='observation-ai-v4',allow_retry=retry),version='observation-ai-v4',deadline=(self.at+timedelta(minutes=5)).isoformat())
            self.assertEqual(caught.exception.code,'daily_call_budget')
            self.assertEqual(ai.budget_status()['calls_used'],1)


if __name__=='__main__':unittest.main()
