import copy
import unittest
from worldcup_mvp.console.observation_handicap import adapt_board, bind_fixture, adapt_infer

class HandicapTests(unittest.TestCase):
    def setUp(self):
        self.now='2026-11-09T12:00:00Z'
        self.fixture={'id':'espn:1','competition_id':'eng.premier','home_id':'h1','away_id':'a1','home':'Home','away':'Away','kickoff':'2026-11-09T14:00:00Z'}
        self.match={'id':'100','league':'England Premier League','home':'Home','away':'Away','kickoff_utc':self.fixture['kickoff'],'in_play':False,'group_status':{'938':{'polled_at':self.now}},'quotes':[]}
        for side,line,odds in [('Home',-.25,1.9),('Away',.25,1.95)]:
            self.match['quotes'].append({'group_id':'938','group_name':'Asian Handicap','market_id':'938','side':side,'handicap':str(line),'odds_dec':odds,'ts':'2026-11-09T01:00:00Z'})
        self.board={'generated_at':self.now,'_received_at':self.now,'matches':[self.match]}
    def test_quarter_prices_and_time_semantics(self):
        result=adapt_board(self.board,[self.fixture],self.now)
        quote=result['bindings'][0]['quotes'][0]
        self.assertEqual(quote['line'],-.25);self.assertEqual(quote['market'],'asian_handicap')
        self.assertEqual(quote['options'][0]['odds'],1.9)
        self.assertFalse(quote['is_opening']);self.assertFalse(quote['settlement_enabled'])
        self.assertIsNone(quote['published_at'])
    def test_missing_other_side_wrong_line_suspended_and_stale(self):
        for mutation in ['missing','line','suspended','stale','future']:
            b=copy.deepcopy(self.board);m=b['matches'][0]
            if mutation=='missing': m['quotes'].pop()
            if mutation=='line': m['quotes'][1]['handicap']='0.5'
            if mutation=='suspended': m['quotes'][1]['suspended']=True
            if mutation=='stale': m['group_status']['938']['polled_at']='2026-11-09T11:50:00Z'
            if mutation=='future': m['quotes'][1]['ts']='2026-11-09T12:01:00Z'
            self.assertEqual(adapt_board(b,[self.fixture],self.now)['bindings'],[],mutation)
    def test_identity_ambiguity_no_fuzzy_or_crossleague(self):
        self.assertIsNone(bind_fixture(self.match,[self.fixture,dict(self.fixture,id='other')]))
        self.assertIsNone(bind_fixture(dict(self.match,home='Hom'),[self.fixture]))
        self.assertIsNone(bind_fixture(self.match,[dict(self.fixture,competition_id='ita.serie_a')],id_map={'100':'espn:1'}))
        self.assertIsNotNone(bind_fixture(dict(self.match,home='Home FC'),[self.fixture],aliases={'h1':['Home FC']}))
    def test_started_stale_board_and_half_market_rejected(self):
        self.match['in_play']=True
        self.assertEqual(adapt_board(self.board,[self.fixture],self.now)['bindings'],[])
        self.match['in_play']=False; self.board['generated_at']='2026-11-09T11:00:00Z'
        self.assertEqual(adapt_board(self.board,[self.fixture],self.now)['rejected'],{'stale_board':1})
        self.board['generated_at']=self.now
        for quote in self.match['quotes']: quote['period']='1'
        self.assertEqual(adapt_board(self.board,[self.fixture],self.now)['bindings'],[])

    def test_infer_real_schema_quarter_lines_and_source_timestamp(self):
        event={'id':'evt_test','league':{'id':'lg_PGYWSDEA'},'home_team':{'name':'Home'},'away_team':{'name':'Away'},'scheduled_at':self.fixture['kickoff'],'status':'scheduled'}
        packet={'event_id':'evt_test','stale':False,'_received_at':self.now,'odds':[{'bookmaker':'crown','market_type':'asian_handicap','period':'full_time','line':-3.25,'handicap_components':[-3,-3.5],'prices':{'home':2.35,'away':1.62},'format':'decimal','status':'open','as_of':self.now}]}
        fixture=dict(self.fixture,competition_id='ger.bundesliga')
        result=adapt_infer(event,packet,[fixture],self.now)
        quote=result['bindings'][0]['quotes'][0];self.assertEqual(quote['line'],-3.25)
        self.assertNotIn('source_checked_at',quote)
        packet['odds'][0]['as_of']='2026-11-09T08:00:00Z'
        self.assertEqual(adapt_infer(event,packet,[fixture],self.now)['bindings'],[])

if __name__=='__main__': unittest.main()
