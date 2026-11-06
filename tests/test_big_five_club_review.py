import unittest
from scripts.audit_big_five_clubs import minutes_for_side, roster
from worldcup_mvp.console.big_five_history import event_half_score


class ClubReviewTests(unittest.TestCase):
    def test_minutes_require_a_full_side_and_match_scoped_players(self):
        f={'match_id':'m','home_team_id':'h','match_date':'2024-01-01Z','competition':'EPL','season':'2023-2024'}
        r={'competition_id':'eng.premier','id':'espn:1','season':'2023','home_id':'espn:2','kickoff':'2024-01-01T12:00Z'}
        pp=[{'match_id':'m','team_id':'h','team_position':'home','match_date':'2024-01-01Z','season':'2023-2024',
             'competition':'EPL','is_starter':True,'player_id':str(i),'minutes_played':90,'player_name':str(i),
             'position':'Defender'} for i in range(11)]
        self.assertEqual(sum(x['minutes'] for x in minutes_for_side(r,'home',f,pp)),990)
        for change in [{'team_id':'a'},{'player_id':'1'},{'minutes_played':None},{'match_id':'other'},{'is_starter':False}]:
            with self.assertRaises(ValueError):minutes_for_side(r,'home',f,[{**pp[0],**change},*pp[1:]])
        with self.assertRaises(ValueError):minutes_for_side(r,'home',f,[{**p,'minutes_played':45} for p in pp])

    def test_roster_requires_season_identity_and_distinct_provider_players(self):
        player={'id':'1','uid':'s:600~a:1','displayName':'A'}
        doc={'season':{'year':2021},'team':{'id':'5'},'status':'success','athletes':[player]}
        self.assertEqual(list(roster(doc,'2021','espn:5')),['espn:1'])
        for change in [{'season':{'year':2026}},{'team':{'id':'9'}},{'athletes':[player,player]}]:
            with self.assertRaises(ValueError):roster({**doc,**change},'2021','espn:5')

    def test_only_a_reconstructed_full_result_supports_a_half_time_review(self):
        row={'home_goals':1,'away_goals':1,'postmatch_events':[
            {'id':'1','period':1,'side':'home','type':'goal---header'},
            {'id':'2','period':2,'side':'away','type':'own-goal'},
            {'id':'3','period':2,'side':'away','type':'var---goal-not-awarded'}]}
        self.assertEqual(event_half_score(row),[1,0])
        self.assertIsNone(event_half_score({**row,'home_goals':2}))
        row['postmatch_events'][1]['id']='1'
        self.assertIsNone(event_half_score(row))
