import unittest
import tempfile
import os
from unittest.mock import patch
from pathlib import Path
from datetime import datetime, timedelta, timezone
from worldcup_mvp.console.store import Store
from worldcup_mvp.console.observation_features import build_packet
from worldcup_mvp.console.observation_ai import ObservationAI, ObservationAIError, validate
from worldcup_mvp.console.observation_evidence import evidence_packet, merge_packets, _workload
from worldcup_mvp.console.observation_models import vectors, history_rows, fit, forecast, feature_packet, save, load


class ObservationResearchTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.now(timezone.utc)
        self.fixture={'id':'m1','competition_id':'eng.premier','home_id':'a','away_id':'b','home':'A','away':'B','kickoff':(self.now+timedelta(days=1)).isoformat()}
        self.fact={'id':'raw','kind':'personnel','side':'home','name':'p','status':'doubtful','source':'club','published_at':(self.now-timedelta(minutes=2)).isoformat(),'captured_at':(self.now-timedelta(minutes=1)).isoformat()}
        self.packet=build_packet(self.fixture,{'evidence':[self.fact]},self.now)

    def test_future_other_competition_and_unknown_excluded(self):
        bad=[dict(self.fact,published_at=(self.now+timedelta(minutes=1)).isoformat()),dict(self.fact,competition_id='esp.laliga'),dict(self.fact,published_at=None)]
        p=build_packet(self.fixture,{'evidence':bad},self.now)
        self.assertEqual(len(p['excluded']),3)
        self.assertEqual(p['evidence'],[])

    def test_duplicate_sources_do_not_multiply_evidence(self):
        p=build_packet(self.fixture,{'evidence':[self.fact,dict(self.fact,source='repost')]},self.now)
        self.assertEqual(len(p['evidence']),1)

    def test_observed_only_preserves_unknown_publication_and_enforces_scope(self):
        row=dict(self.fact,published_at=None,time_basis='observed_only',source_url='https://club.example/lineup')
        p=build_packet(self.fixture,{'evidence':[row]},self.now)
        self.assertIsNone(p['evidence'][0]['published_at'])
        self.assertEqual(p['evidence'][0]['time_basis'],'observed_only')
        for bad in (dict(row,source_url=None),dict(row,captured_at=(self.now+timedelta(seconds=1)).isoformat()),dict(row,status='官方确认停赛')):
            self.assertEqual(build_packet(self.fixture,{'evidence':[bad]},self.now)['evidence'],[])
        self.assertEqual(len(build_packet(self.fixture,{'evidence':[dict(row,status='官方确认停赛',competition_id='eng.premier')]},self.now)['evidence']),1)

    def test_relationships_and_conflicts_not_resolved_by_guessing(self):
        rows=[dict(self.fact,player_id='p1',team_id='a'),dict(self.fact,player_id='p1',team_id='a',status='available',source='other')]
        p=evidence_packet(self.fixture,{'evidence':rows,'relationships':[dict(self.fact,relationship='registration',player_id='p1',team_id='a',valid_from=self.now.isoformat())]},self.now,include_bulletins=False)
        self.assertEqual(len(p['evidence']),2)
        self.assertTrue(all(r['conflicts'] for r in p['evidence']))
        self.assertEqual(p['excluded'][0]['reason'],'unverified_relationship_interval_or_scope')

    def test_last_five_is_partial_workload_and_unknown_minutes(self):
        fixture=dict(self.fixture,home_id='espn:1',away_id='espn:2',source_url='https://espn.example/summary')
        event={'id':'10','gameDate':(self.now-timedelta(days=3)).isoformat(),'homeTeamId':'1','awayTeamId':'3','gameResult':'W','competitionName':'Cup'}
        facts,rejected=_workload(fixture,{'lastFiveGames':[{'team':{'id':'1'},'events':[event,event,dict(event,id='11',gameDate=(self.now+timedelta(days=1)).isoformat())]}]},self.now.isoformat(),self.now)
        self.assertEqual(len(facts),1)
        self.assertEqual(facts[0]['workload']['observed_match_count'],1)
        self.assertEqual(facts[0]['workload']['matches_previous_14_days_lower_bound'],1)
        self.assertIsNone(facts[0]['workload']['player_minutes'])
        self.assertIsNone(facts[0]['history_refs'][0]['competition_id'])
        self.assertEqual(facts[0]['coverage'],'partial_last_five_team_matches')
        self.assertEqual(len(rejected),1)

    def test_merge_accepts_provider_metadata_but_rejects_wrong_team(self):
        model=dict(self.packet,fixture=dict(self.fixture,provider='espn',season='2026'))
        self.assertEqual(len(merge_packets(model,self.packet)['evidence']),1)
        with self.assertRaises(ValueError):
            merge_packets(dict(model,fixture=dict(model['fixture'],home_id='wrong')),self.packet)

    def test_extraction_cannot_mutate_source_numeric_or_status(self):
        ident=self.packet['evidence'][0]['id']
        r=validate({'facts':[{'evidence_id':ident,'status':'confirmed','value':999}]},self.packet,'extract')
        self.assertEqual(r['facts'][0]['status'],'doubtful')
        self.assertNotIn('value',r['facts'][0])

    def test_invalid_probability_and_invented_evidence_rejected(self):
        for probs,refs in [({'h':.9,'d':.3,'a':.3},[self.packet['evidence'][0]['id']]),({'h':.4,'d':.3,'a':.3},['fake'])]:
            with self.assertRaises(ValueError):validate({'probabilities':probs,'evidence_ids':refs,'rationale':'x'},self.packet,'blind')

    def test_calls_cached_and_blind_has_no_market(self):
        calls=[]
        def transport(messages,model):
            calls.append(messages)
            return {'probabilities':{'h':.4,'d':.3,'a':.3},'evidence_ids':[self.packet['evidence'][0]['id']],'rationale':'有不确定人员信息'}, {'total_tokens':50}
        with tempfile.TemporaryDirectory() as d:
            ai=ObservationAI(Store(Path(d)/'db.sqlite'),transport)
            self.packet['market']={'secret_market_marker':True}
            a=ai.predict(self.packet); b=ai.predict(self.packet)
            self.assertEqual(a,b);self.assertEqual(len(calls),1)
            self.assertNotIn('secret_market_marker',calls[0][1]['content'])
            self.assertEqual(a['supported_markets'],['had'])
            self.assertIsNone(a['cost'])

    def test_v3_provenance_compression_is_lossless(self):
        import json
        captured=[]
        self.packet['evidence'][0]['history_refs']={'results':['a','b'],'source_url':'https://example.org'}
        def transport(messages,model):
            captured.append(json.loads(messages[1]['content']))
            return {'facts':[{'evidence_id':self.packet['evidence'][0]['id']}]},{'total_tokens':1}
        with tempfile.TemporaryDirectory() as d:
            result=ObservationAI(Store(Path(d)/'db.sqlite'),transport).extract(self.packet,prompt_version='observation-ai-v3')
        compressed=captured[0]['evidence'][0]
        self.assertEqual(captured[0]['history_reference_catalog'][compressed['history_ref_id']],self.packet['evidence'][0]['history_refs'])
        self.assertEqual(result['facts'][0],self.packet['evidence'][0])

    def test_dated_tariff_is_an_estimate_with_expiry(self):
        from worldcup_mvp.console.observation_cost import estimate
        usage={'response_model':'deepseek-flash','prompt_cache_hit_tokens':1000,'prompt_cache_miss_tokens':1000,'prompt_tokens':2000,'completion_tokens':1000}
        result=estimate(usage,'2026-11-09T13:00:00+00:00','2026-11-09T13:00:10+00:00')
        self.assertAlmostEqual(result['maximum'],.000753)
        self.assertEqual(result['maximum'],result['minimum'])
        peak=estimate(usage,'2026-11-10T02:00:00+00:00','2026-11-10T02:00:10+00:00')
        self.assertEqual(peak['maximum'],2*peak['minimum'])
        self.assertIsNone(estimate(usage,'2026-11-17T13:00:00+00:00','2026-11-17T13:00:10+00:00'))

    def test_failed_calls_are_not_silently_rewritten(self):
        def transport(*args):raise ValueError('upstream')
        with tempfile.TemporaryDirectory() as d:
            ai=ObservationAI(Store(Path(d)/'db.sqlite'),transport)
            with self.assertRaises(ValueError):ai.predict(self.packet)
            with self.assertRaisesRegex(ValueError,'已失败'):ai.predict(self.packet)

    def test_provider_failure_circuit_recovers_only_after_cooldown(self):
        from worldcup_mvp.env_config import get_deepseek_model
        with tempfile.TemporaryDirectory() as d:
            ai=ObservationAI(Store(Path(d)/'db.sqlite'),lambda *args:self.fail('circuit must stop transport'))
            with ai.store.connect() as db:
                for i in range(10):
                    db.execute('INSERT INTO observation_ai_calls(id,day,status,created_at,model,mode,input_digest,error) VALUES(?,?,?,?,?,?,?,?)',
                               (str(i),self.now.date().isoformat(),'failed',self.now.isoformat(),get_deepseek_model(),'blind',str(i),'upstream'))
            with self.assertRaises(ObservationAIError) as raised:ai.predict(self.packet)
            self.assertEqual(raised.exception.code,'provider_circuit_open')
            with ai.store.connect() as db:
                db.execute('UPDATE observation_ai_calls SET created_at=?',((self.now-timedelta(hours=2)).isoformat(),))
            ai.transport=lambda *args:({'probabilities':{'h':.4,'d':.3,'a':.3},'evidence_ids':[self.packet['evidence'][0]['id']],'rationale':'有依据'}, {'total_tokens':10,'response_model':'provider-alias'})
            value=ai.predict(self.packet)
            self.assertEqual(value['response_model'],'provider-alias')
            self.assertIsNone(value['cost'])

    def test_call_and_token_budget_reject_before_transport(self):
        for env,code in [({'FOOTBALL_OBSERVATION_AI_DAILY_CALLS':'0'},'daily_call_budget'),({'FOOTBALL_OBSERVATION_AI_DAILY_TOKENS':'1'},'daily_token_budget')]:
            with self.subTest(code=code),tempfile.TemporaryDirectory() as d,patch.dict(os.environ,env):
                ai=ObservationAI(Store(Path(d)/'db.sqlite'),lambda *args:self.fail('budget exceeded must not call'))
                with self.assertRaises(ObservationAIError) as raised:ai.predict(self.packet)
                self.assertEqual(raised.exception.code,code)
                self.assertEqual(ai.budget_status()['calls_used'],0)

    def test_budget_uses_real_usage_and_failure_has_safe_reason(self):
        with tempfile.TemporaryDirectory() as d:
            ai=ObservationAI(Store(Path(d)/'db.sqlite'),lambda *args:({'probabilities':{'h':.4,'d':.3,'a':.3},'evidence_ids':[self.packet['evidence'][0]['id']],'rationale':'x'},{'total_tokens':50}))
            ai.predict(self.packet)
            self.assertEqual(ai.budget_status()['tokens_accounted'],50)
            self.assertEqual(ai.budget_status()['calls_used'],1)
            ai.transport=lambda *args:(_ for _ in ()).throw(RuntimeError('secret upstream details'))
            with self.assertRaises(ObservationAIError) as raised:ai.extract(self.packet)
            self.assertEqual(raised.exception.code,'output_invalid')
            self.assertNotIn('secret',str(raised.exception))
            self.assertGreater(ai.budget_status()['tokens_accounted'],50)

    def test_prompt_versions_keep_original_schema_and_separate_cache(self):
        messages=[]
        def transport(prompt,model):
            messages.append(prompt)
            return {'facts':[{'evidence_id':self.packet['evidence'][0]['id']}]},{'total_tokens':20}
        with tempfile.TemporaryDirectory() as d:
            ai=ObservationAI(Store(Path(d)/'db.sqlite'),transport)
            a=ai.extract(self.packet,prompt_version='observation-ai-v1')
            b=ai.extract(self.packet,prompt_version='observation-ai-v2')
            self.assertNotEqual(a['call_id'],b['call_id'])
            self.assertEqual(a['prompt_version'],'observation-ai-v1')
            self.assertEqual(b['prompt_version'],'observation-ai-v2')
            self.assertNotIn('最多32项',messages[0][0]['content'])
            self.assertIn('最多32项',messages[1][0]['content'])
            self.assertEqual(a,ai.extract(self.packet,prompt_version='observation-ai-v1'))
            self.assertEqual(len(messages),2)

    def test_historical_xg_assumption_not_allowed_live(self):
        rows=[]
        for i in range(6):
            when=self.now-timedelta(days=40-i*5)
            rows.append({'id':str(i),'competition_id':'eng.premier','home_id':'a','away_id':'b','kickoff':when.isoformat(),'result_available_at':(when+timedelta(days=1)).isoformat(),'home_goals':1,'away_goals':0,'xg':[1.2,.5],'xg_captured_at':None})
        with self.assertRaisesRegex(ValueError,'xG'):vectors(rows,self.fixture,self.now)
        v,refs=vectors(rows,self.fixture,self.now,historical=True)
        self.assertEqual(v[0]['xgf'],1.2)
        self.assertEqual(len(refs[0]['results']),6)

    def test_cross_league_history_rejected(self):
        with self.assertRaises(ValueError):history_rows([{'competition_id':'esp.laliga'}],'eng.premier')

if __name__=='__main__':unittest.main()
