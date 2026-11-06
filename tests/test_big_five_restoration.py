from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from scripts.restore_big_five_boxscores import aggregate_passes, team_box
from scripts.import_big_five_prematch_archives import available_at, quote
from worldcup_mvp.console.big_five_dataset import validate_prematch_evidence
from worldcup_mvp.console.big_five_history import evidence_counts, usable_boxscore


class RestorationTests(unittest.TestCase):
    def test_a_single_empty_team_blocks_the_source_for_coverage(self):
        row={'training_eligible':True,'postmatch_statistics':{
            'home':{'possessionPct':0,'totalShots':0,'totalPasses':0,'redCards':0},
            'away':{'possessionPct':58,'totalShots':7,'totalPasses':433,'redCards':1}}}
        self.assertEqual(usable_boxscore(row),{})
        self.assertEqual(evidence_counts([row])['redCards_both'],0)
        self.assertEqual(evidence_counts([row],include_quarantined=True)['redCards_both'],1)
        row['additional_postmatch_statistics']={'verified':{'statistics':{'home':{'redCards':0},'away':{'redCards':1}}}}
        self.assertEqual(evidence_counts([row])['redCards_both'],1)

    def test_row_from_wrong_half_or_empty_box_cannot_restore_stats(self):
        row={'home_half_goals':1,'away_half_goals':0}
        stats={'home_goals_ht':1,'away_goals_ht':0}
        for side,poss in [('home',42),('away',58)]:
            for k,v in dict(possession=poss,shots_total=5,shots_on_goal=2,shots_inside_box=3,
                    shots_outside_box=2,pass_accuracy=81,fouls=9,corners=5,yellow_cards=2,red_cards=0).items():
                stats[side+'_'+k]=v
        self.assertEqual(team_box(stats,row)['home']['redCards'],0)
        for change in [{'away_goals_ht':1},{'away_possession':0},{'home_pass_accuracy':None},
                       {'home_fouls':-1},{'home_shots_on_goal':6},{'home_red_cards':None}]:
            with self.assertRaises(ValueError):team_box({**stats,**change},row)

    def test_pass_sum_requires_distinct_players_complete_values_and_both_team_ids(self):
        f={'match_id':'a','competition':'EPL','season':'2024-2025','home_team_id':'h','away_team_id':'v',
           'home_score':1,'away_score':1}
        players=[{**f,'player_id':side+str(i),'team_id':tid,'team_position':side,'passes':i+10,'total_minutes':90}
                 for side,tid in [('home','h'),('away','v')] for i in range(11)]
        self.assertEqual(aggregate_passes(players,'opta',f)[0]['home']['totalPasses'],165)
        for change in [{'passes':None},{'team_id':'other'},{'player_id':'home1'},{'passes':1.3}]:
            with self.assertRaises(ValueError):aggregate_passes([{**players[0],**change},*players[1:]],'opta',f)

    def test_quote_filters_kickoff_suspension_inplay_and_incomplete_markets(self):
        start=datetime(2026,1,1,12,tzinfo=timezone.utc);ts=start-timedelta(seconds=1)
        q={'Market':'FT_1X2','Phase':'Pre-match','Suspended':'0','Timestamp_UTC':ts.isoformat(),
           'Epoch':str(int(ts.timestamp())),'Book':'Bet365','Minute':'','Second':'','Score_Home':'','Score_Away':'',
           'Line':'','Price_Home':'2.3','Price_Draw':'3.1','Price_Away':'3.0'}
        self.assertIsNotNone(quote(q,start))
        for patch in [{'Phase':'In-play'},{'Suspended':'1'},{'Minute':'0'},{'Price_Draw':''},{'Price_Away':'nan'}]:
            self.assertIsNone(quote({**q,**patch},start))
        self.assertIsNone(quote(q,ts))
        with self.assertRaises(ValueError):quote({**q,'Epoch':'0'},start)

    def test_captures_must_be_prior_and_within_the_selected_window(self):
        start=datetime(2026,11,1,12,tzinfo=timezone.utc)
        snapshots={start-timedelta(days=4):'stale',start:'at kickoff',start+timedelta(hours=1):'future'}
        self.assertIsNone(available_at(snapshots,start))
        snapshots[start-timedelta(hours=72)]='boundary'
        self.assertEqual(available_at(snapshots,start),'boundary')

    def test_admission_rechecks_market_times_and_partial_availability_scope(self):
        row={'competition_id':'eng.premier','season':'2025','home_id':'a','away_id':'b','kickoff':'2026-01-01T12:00Z',
             'prematch_market_archive':{'safe_cutoff_utc':'2026-01-01T12:00Z','purpose':'market_reference_only',
                 'quotes':[{'book':'Example','quoted_at':'2026-01-01T11:59Z','market':'FT_1X2','line':None,
                            'decimal_prices':{'Home':2,'Draw':3,'Away':3}}]},
             'prematch_fpl_observation':{'captured_at':'2026-01-01T10:00Z','matchday_availability_complete':False,
                 'sides':{s:{'team_id':t,'listed_players':12,'reported_flags':[]} for s,t in [('home','a'),('away','b')]}}}
        validate_prematch_evidence(row)
        r=deepcopy(row);r['prematch_market_archive']['quotes'][0]['quoted_at']=row['kickoff']
        with self.assertRaises(ValueError):validate_prematch_evidence(r)
        for patch in [{'captured_at':row['kickoff']},{'matchday_availability_complete':True}]:
            r=deepcopy(row);r['prematch_fpl_observation'].update(patch)
            with self.assertRaises(ValueError):validate_prematch_evidence(r)
        with self.assertRaises(ValueError):validate_prematch_evidence({**row,'competition_id':'esp.laliga'})


if __name__=='__main__':unittest.main()
