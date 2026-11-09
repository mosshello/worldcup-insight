import unittest
from copy import deepcopy
from worldcup_mvp.console.observation_sources import normalize_event, quotes_from_summary, decimal_odds


class ObservationSourceTests(unittest.TestCase):
    def setUp(self):
        self.at='2026-11-09T10:00:00+00:00'
        self.event={'id':'123','uid':'s:600~l:700~e:123','date':'2026-11-21T12:30:00Z','season':{'year':2026},
            'competitions':[{'id':'123','date':'2026-11-21T12:30:00Z','neutralSite':False,
                'status':{'type':{'name':'STATUS_SCHEDULED'}},
                'competitors':[{'homeAway':'home','team':{'id':'382','displayName':'Manchester City'}},
                               {'homeAway':'away','team':{'id':'370','displayName':'Fulham'}}]}]}
        self.fixture=normalize_event(self.event,'eng.premier',self.at)
        self.raw={'header':self.event,'odds':[{'provider':{'id':'100','name':'DraftKings'},
            'homeTeamOdds':{'team':{'id':'382'},'moneyLine':-230},
            'awayTeamOdds':{'team':{'id':'370'},'moneyLine':500},'drawOdds':{'moneyLine':380},
            'pointSpread':{'home':{'close':{'line':'-1.5','odds':105}},'away':{'close':{'line':'+1.5','odds':-165}}}}]}

    def test_current_quotes_do_not_invent_opening_or_publication(self):
        quotes=quotes_from_summary(self.fixture,self.raw,self.at)
        self.assertEqual({q['market'] for q in quotes},{'had','spread'})
        for q in quotes:
            self.assertIsNone(q['published_at'])
            self.assertFalse(q['is_opening'])
            self.assertEqual(q['time_basis'],'observed_only')
        self.assertEqual(quotes[1]['settlement_policy'],'unverified_display_only')

    def test_reject_cross_competition_and_reversed_teams(self):
        with self.assertRaises(ValueError): normalize_event(self.event,'esp.laliga',self.at)
        raw=deepcopy(self.raw)
        raw['header']['competitions'][0]['competitors'][0]['team']['id']='999'
        with self.assertRaises(ValueError): quotes_from_summary(self.fixture,raw,self.at)

    def test_contradictory_spread_excluded_without_losing_moneyline(self):
        self.raw['odds'][0]['pointSpread']['away']['close']['line']='-1.5'
        self.assertEqual([q['market'] for q in quotes_from_summary(self.fixture,self.raw,self.at)],['had'])

    def test_quote_identity_is_not_inferred_from_names(self):
        self.raw['odds'][0]['homeTeamOdds']['team']['id']='999'
        self.assertEqual(quotes_from_summary(self.fixture,self.raw,self.at),[])

    def test_invalid_prices_rejected(self):
        for value in [True,0,99,float('nan'),float('inf')]:
            with self.assertRaises(ValueError): decimal_odds(value)
        self.assertAlmostEqual(decimal_odds(-200),1.5)
        self.assertEqual(decimal_odds(200),3)
