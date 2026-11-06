from copy import deepcopy
import unittest

from scripts.big_five_shot_archive import shot_evidence, goal_keys
from scripts.complete_big_five_events import displayed_red_cards, parse_lineup_update, quarantine_empty_box
from scripts.complete_big_five_shot_sums import shot_sums


class EventEvidenceTests(unittest.TestCase):
    def test_derived_xg_never_prices_an_own_goal_or_defaults_missing_shots_to_zero(self):
        fx={'match_id':'x','season':'2026-2027','competition':'Serie_A','home_team_id':'a','away_team_id':'b',
            'home_score':'0','away_score':'1'}
        common={'match_id':'x','season':'2026-2027','competition':'Serie_A','minute':4,'second':12,
                'thin_feed':False,'is_goal':False,'is_own_goal':False,'type_id':13}
        events=[{**common,'team_id':t,'event_id':i,'player_id':t,'xg':x} for t,i,x in [('a',1,.7),('b',2,.1)]]
        events.append({**events[0],'event_id':3,'type_id':16,'is_goal':True,'is_own_goal':True,'xg':None})
        players=[{**e,'total_shots':1} for e in events[:2]]
        values,evidence=shot_sums(fx,events,players,[1,1])
        self.assertEqual(values,[.7,.1]); self.assertEqual(len(evidence['excluded_own_goal_ids']),1)
        for change in [{'xg':None},{'thin_feed':True},{'match_id':'different'},{'xg':float('nan')}]:
            with self.assertRaises(ValueError):shot_sums(fx,[{**events[0],**change},*events[1:]],players,[1,1])
        for changes in [[*events,events[0]],events[:-1],events[1:]]:
            with self.assertRaises(ValueError):shot_sums(fx,changes,players,[1,1])
        with self.assertRaises(ValueError):shot_sums(fx,events,[{**players[0],'player_id':'other'},players[1]],[1,1])

    def test_own_goal_is_credited_to_opponent_and_keeps_original_shooter(self):
        meta = {'league_code':'ESP','season':'2023','understat_id':'1','home_team':'A','away_team':'B',
                'home_score':1,'away_score':0,'match_date':'2023-05-01','home_shots':0,'away_shots':0,
                'home_xg':0,'away_xg':0}
        entry = {'id':'10','match_id':'1','understat_id':'1','h_team':'A','a_team':'B','h_goals':'1',
                 'a_goals':'0','date':'2023-05-01','season':'2023','league_code':'ESP','h_a':'a',
                 'is_home':False,'x_g':'0','x':'0.5','y':'0.5','minute':'54','result':'OwnGoal','player':'Íñigo'}
        result = shot_evidence(meta, {'10': entry})
        self.assertEqual(goal_keys(result['goals']), [('home',55,'inigo',True)])
        for change in [{'match_id':'2'},{'result':'Goal'},{'is_home':True},{'x_g':'nan'},{'date':'2023-05-02'}]:
            with self.assertRaises(ValueError): shot_evidence(meta, {'10':{**entry,**change}})

    def test_rendered_cards_require_explicit_match_row_and_team_order(self):
        row = '<div class="MkFootballModuleContentMatchStatsTeamStatsRowStats"><div>Tarjetas rojas:A 1 versus B 0</div>'
        self.assertEqual(displayed_red_cards(row, 'A', 'B'), [1,0])
        for html in [row+row, '<script>'+row+'</script>', row.replace('A 1','A –'), row.replace('A 1','B 1')]:
            with self.assertRaises(ValueError): displayed_red_cards(html, 'A', 'B')

    def test_lineup_uses_last_update_and_rejects_post_kickoff_edits(self):
        names = [f'Player {n}' for n in range(11)]
        spec = ('a','Confirmed:',names,list(range(11)))
        players = [{'id':f'understat:{n}'} for n in range(11)]
        entry = {'@type':'BlogPosting','url':'https://example.test/#a','articleBody':'Confirmed:'+','.join(names)+'.',
                 'datePublished':'2023-02-11T18:15:00Z','dateModified':'2023-02-11T19:25:00Z'}
        def check(update, roster=players):
            return parse_lineup_update(update, spec, 'https://example.test/', '2023-02-11T20:00:00Z', roster)
        self.assertEqual(check(entry)['usable_no_earlier_than'], '2023-02-11T19:25:00+00:00')
        for change in [{'dateModified':'2023-02-11T20:00:00Z'},{'dateModified':'2023-02-11T19:25:00'},
                       {'articleBody':entry['articleBody'].replace('Player 9','Another player')}]:
            with self.assertRaises(ValueError): check({**entry,**change})
        with self.assertRaises(ValueError): check(entry,players[:-1])

    def test_placeholder_box_is_quarantined_without_modifying_historical_values(self):
        stats = {side:{'possessionPct':0,'totalShots':0,'redCards':0} for side in ['home','away']}
        row = {'postmatch_statistics':deepcopy(stats)}
        self.assertTrue(quarantine_empty_box(row))
        self.assertFalse(row['supplemental_quality']['espn_boxscore']['usable'])
        self.assertEqual(row['postmatch_statistics'],stats)
        row['postmatch_statistics']['home']['possessionPct']=43
        self.assertFalse(quarantine_empty_box(row))


if __name__ == '__main__':
    unittest.main()
