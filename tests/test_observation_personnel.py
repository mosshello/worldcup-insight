import copy,json,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from worldcup_mvp.console.observation_personnel import personnel_features,h2h_features
from worldcup_mvp.console.observation_personnel_models import fit,VARIANTS
from worldcup_mvp.console.observation_personnel_sources import normalize_team_schedule,fpl_evidence,fpl_team_mapping,schedule_evidence,cached_fixture_evidence
from worldcup_mvp.console.observation_club_load import club_features


class PersonnelTests(unittest.TestCase):
    def setUp(self):
        self.at=datetime(2026,11,9,tzinfo=timezone.utc)
        self.fixture={'id':'target','competition_id':'eng.premier','season':'2026','home_id':'espn:1','away_id':'espn:2','kickoff':(self.at+timedelta(days=1)).isoformat()}
        rows=[]
        for side,team in enumerate(('espn:1','espn:2')):
            for i in range(5):
                day=self.at-timedelta(days=40-i*7)
                rows.append({'fixture_id':f'{side}-{i}','competition_id':'eng.premier','team_id':team,'kickoff':day.isoformat(),
                    'historical_available_at':(day+timedelta(days=7)).isoformat(),'captured_at':(self.at-timedelta(days=1)).isoformat(),
                    'source':'opta','coach_id':'hf:1' if i<4 else 'hf:2',
                    'players':[{'id':f'opta:{n}','starter':True,'minutes':80} for n in range(11)]+[{'id':'opta:sub','starter':False,'minutes':110}]})
        self.history={'competition_id':'eng.premier','rows':rows}

    def test_only_past_usage_no_target_lineup_leak(self):
        values,refs=personnel_features(self.history,self.fixture,self.at)
        self.assertEqual(values[0]['starter_retention'],1)
        self.assertEqual(values[0]['last_rotation'],0)
        self.assertAlmostEqual(values[0]['bench_minute_share'],110/990)
        self.assertEqual(values[0]['observed_coach_change'],1)
        injected=copy.deepcopy(self.history);current=dict(injected['rows'][0],fixture_id='target',kickoff=self.fixture['kickoff'],coach_id='hf:999')
        injected['rows'].append(current)
        self.assertEqual(values,personnel_features(injected,self.fixture,self.at)[0])
        self.assertNotIn('target',refs[0]['personnel_fixture_ids'])

    def test_actual_capture_not_replaced_with_historical_assumption(self):
        changed=copy.deepcopy(self.history)
        for r in changed['rows']:r['captured_at']=(self.at+timedelta(days=1)).isoformat()
        with self.assertRaises(ValueError):personnel_features(changed,self.fixture,self.at)
        self.assertEqual(len(personnel_features(changed,self.fixture,self.at,historical=True)[0]),2)

    def test_mixed_provider_identity_not_inferred(self):
        changed=copy.deepcopy(self.history)
        for r in changed['rows'][-2:]:r['source']='other'
        with self.assertRaisesRegex(ValueError,'同来源'):personnel_features(changed,self.fixture,self.at)

    def test_cross_league_rows_rejected(self):
        changed=copy.deepcopy(self.history);changed['rows'][0]['competition_id']='esp.laliga'
        with self.assertRaisesRegex(ValueError,'其他赛事'):personnel_features(changed,self.fixture,self.at)

    def test_no_coach_report_keeps_missing_indicator(self):
        changed=copy.deepcopy(self.history)
        for r in changed['rows']:r['coach_id']=None
        values,_=personnel_features(changed,self.fixture,self.at)
        self.assertEqual(values[0]['coach_comparison_missing'],1)
        self.assertEqual(values[0]['observed_same_coach_games'],0)

    def test_future_training_labels_excluded_and_isolation_enforced(self):
        vector={k:0.0 for k in VARIANTS['xg_personnel_coach']}
        samples=[{'id':str(i),'competition_id':'eng.premier','available_at':(self.at-timedelta(days=1)).isoformat(),
            'vectors':[vector,vector],'goals':[1,2],'half_goals':[0,1]} for i in range(100)]
        samples.append(dict(samples[0],id='future',available_at=(self.at+timedelta(days=1)).isoformat(),goals=[99,99]))
        artifact=fit(samples,'eng.premier',self.at,'xg_personnel','history','personnel')
        self.assertEqual(artifact['train_count'],100);self.assertNotIn('future',artifact['train_ids'])
        self.assertFalse(artifact['automatic_selection'])
        with self.assertRaises(ValueError):fit(samples,'esp.laliga',self.at,'xg_personnel','history','personnel')

    def test_all_competition_schedule_identity_and_partial_scope(self):
        competitors=[{'homeAway':'home','team':{'id':'1'},'score':{'$ref':'https://x/leagues/uefa.champions/events/10/scores/1'}},
                     {'homeAway':'away','team':{'id':'3'},'score':{'$ref':'https://x/leagues/uefa.champions/events/10/scores/3'}}]
        day=(self.at-timedelta(days=3)).isoformat()
        event={'id':'10','date':day,'competitions':[{'id':'10','date':day,'competitors':competitors,'status':{'period':2,'type':{'completed':True,'name':'STATUS_FULL_TIME'}}}]}
        payload={'team':{'id':'1'},'requestedSeason':{'year':2026},'events':[event,event]}
        snapshot=normalize_team_schedule('espn:1',2026,payload,self.at.isoformat(),'https://espn.example/all')
        self.assertEqual(len(snapshot['rows']),1)
        self.assertEqual(snapshot['rows'][0]['source_competition_slug'],'uefa.champions')
        evidence=schedule_evidence(self.fixture,snapshot,self.at)
        self.assertIsNone(evidence['workload']['player_minutes'])
        self.assertEqual(evidence['workload']['observed_club_matches_14d'],1)
        with self.assertRaises(ValueError):normalize_team_schedule('espn:2',2026,payload,self.at.isoformat(),'url')
        with self.assertRaises(ValueError):schedule_evidence(self.fixture,snapshot,self.at-timedelta(seconds=1))

    def test_fpl_is_current_report_not_target_lineup_or_other_league(self):
        payload={'events':[{'deadline_time':'2026-08-20T12:00:00Z'}],'teams':[{'id':15,'name':'Man City'}],'elements':[{'id':99,'team':15,'status':'s','news':'Suspended','news_added':None,'first_name':'P','second_name':'X'}]}
        mapping,unmapped=fpl_team_mapping(payload,{'espn:1':'Manchester City'})
        self.assertEqual(mapping,{'15':'espn:1'});self.assertEqual(unmapped,[])
        rows=fpl_evidence(self.fixture,payload,self.at.isoformat(),mapping)
        self.assertEqual(rows[0]['player_id'],'fpl:2026:99')
        self.assertEqual(rows[0]['time_basis'],'observed_only');self.assertIsNone(rows[0]['expected_minutes'])
        self.assertEqual(fpl_evidence(dict(self.fixture,competition_id='esp.laliga'),payload,self.at.isoformat(),mapping),[])
        self.assertEqual(fpl_evidence(dict(self.fixture,season='2027'),payload,self.at.isoformat(),mapping),[])

    def test_h2h_streaks_use_only_prior_available_same_league_results(self):
        history=[]
        for i in range(4):
            at=self.at-timedelta(days=300-i*50)
            history.append({'id':str(i),'competition_id':'eng.premier','home_id':'espn:1','away_id':'espn:2','kickoff':at.isoformat(),
                'result_available_at':(at+timedelta(days=7)).isoformat(),'result_captured_at':(self.at-timedelta(days=1)).isoformat(),
                'home_goals':1,'away_goals':0 if i>1 else 1})
        values,refs=h2h_features(history,self.fixture,self.at)
        self.assertEqual(values[0]['h2h_win_streak'],2)
        self.assertEqual(values[1]['h2h_win_streak'],0)
        self.assertEqual(values[0]['h2h_draw_share'],3/7)
        future=dict(history[-1],id='future',result_captured_at=(self.at+timedelta(days=1)).isoformat())
        self.assertEqual(values,h2h_features(history+[future],self.fixture,self.at)[0])
        with self.assertRaises(ValueError):h2h_features([dict(history[0],competition_id='esp.laliga')],self.fixture,self.at)

    def test_cached_read_preserves_receipt_without_network(self):
        capture=datetime.now(timezone.utc).isoformat()
        cached={f'schedule:espn:{i}':{'team_id':f'espn:{i}','season':'2026','captured_at':capture,
                   'source_url':'https://espn.example/schedule','rows':[],'coverage':'provider_returned_matches'} for i in (1,2)}
        with patch('worldcup_mvp.console.observation_personnel_sources._CACHE',cached),patch('worldcup_mvp.console.observation_personnel_sources._initial_cache'),patch('worldcup_mvp.console.observation_personnel_sources.get_json',side_effect=AssertionError('must not fetch')):
            result=cached_fixture_evidence(self.fixture,refresh=False)
            self.assertEqual(len(result['context']['evidence']),2)
            self.assertEqual(result['context']['evidence'][0]['captured_at'],capture)
            self.assertEqual(result['source_status'][2]['error'],'source_cache_stale_or_missing')
            old=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
            for row in cached.values():row['captured_at']=old
            stale=cached_fixture_evidence(self.fixture,refresh=False)
            self.assertEqual(stale['context']['evidence'],[])
            self.assertEqual(stale['source_status'][0]['captured_at'],old)
            self.assertTrue(stale['source_status'][0]['stale'])

    def test_club_day_precision_uses_day_end_plus_seven_and_no_player_minutes(self):
        day=self.at-timedelta(days=7)
        row={'fixture_id':'opta:cup','team_id':'espn:1','competition_name':'FA_Cup','day_start':day.isoformat(),
             'day_end':(day+timedelta(days=1)).isoformat(),'historical_available_at':(day+timedelta(days=8)).isoformat(),
             'captured_at':(self.at-timedelta(hours=1)).isoformat()}
        history={'competition_id':'eng.premier','rows':[row],'coverage':'partial_club_archive'}
        historical,refs=club_features(history,self.fixture,self.at,historical=True)
        actual,refs=club_features(history,self.fixture,self.at)
        self.assertEqual(historical[0]['observed_club_games_14d'],0)
        self.assertEqual(actual[0]['observed_nonleague_games_30d'],1)
        self.assertEqual(actual[0]['days_since_known_club_game_upper_bound'],7)
        self.assertIsNone(refs[0]['national_team_minutes'])
        self.assertEqual(refs[0]['date_precision'],'day')
        with self.assertRaises(ValueError):club_features(history,dict(self.fixture,competition_id='esp.laliga'),self.at)


if __name__=='__main__':unittest.main()
