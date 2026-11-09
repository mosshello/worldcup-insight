import copy
from datetime import datetime, timedelta, timezone
import unittest

import numpy as np

from scripts.research_big_five_context import (
    prepare, fit, grid, markets, actuals, movement, metric, OPTIONS, VARIANTS,
)
from worldcup_mvp.console.football_markets import market_probabilities, score_grid
from scripts.research_big_five_actual_lines import quote


def fixtures():
    start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    return [dict(id=f'm{i}', competition_id='eng.premier', season='2023',
                 home_id='A', away_id='B', home_goals=2, away_goals=0,
                 home_half_goals=1, away_half_goals=0,
                 kickoff=(start+timedelta(days=i*10)).isoformat(),
                 result_available_at=(start+timedelta(days=i*10+7)).isoformat())
            for i in range(12)]


class ContextResearchTest(unittest.TestCase):
    def test_target_and_future_labels_never_change_features(self):
        rows = fixtures()
        first = {s['id']:s for s in prepare('eng.premier', rows, {}, {})}
        changed = copy.deepcopy(rows)
        for r in changed[8:]:
            r.update(home_goals=0, away_goals=8, home_half_goals=0, away_half_goals=3)
            r['postmatch_xg_sources'] = {'understat_archive':{'xg':[.01,9.], 'source_receipt':{}}}
        second = {s['id']:s for s in prepare('eng.premier', changed, {}, {})}
        self.assertEqual(first['m8']['vectors'], second['m8']['vectors'])
        self.assertEqual(first['m8']['context_inputs'], second['m8']['context_inputs'])
        self.assertNotIn('m8',first['m8']['context_inputs'][0]['h2h'])

    def test_delayed_result_is_not_available(self):
        rows = fixtures()
        rows[6]['result_available_at'] = rows[-1]['result_available_at']
        s = {s['id']:s for s in prepare('eng.premier', rows, {}, {})}['m8']
        self.assertNotIn('m6',s['context_inputs'][0]['recent'])
        self.assertNotIn('m6',s['context_inputs'][0]['h2h'])
        self.assertNotIn('m6',s['inputs'][0]['results'])

    def test_h2h_orientation_and_sample_counts(self):
        rows = fixtures()
        rows[8]['home_id'], rows[8]['away_id'] = 'B','A'
        s = {s['id']:s for s in prepare('eng.premier', rows, {}, {})}['m8']
        self.assertLess(s['vectors'][0]['h2h_gf'], s['vectors'][1]['h2h_gf'])
        self.assertEqual(s['vectors'][0]['same_venue_n'],0)
        self.assertGreater(s['vectors'][0]['h2h_n'],0)

    def test_competition_and_duplicate_guard(self):
        rows = fixtures()
        rows[0]['competition_id']='esp.laliga'
        with self.assertRaisesRegex(ValueError,'Cross-competition'):
            prepare('eng.premier',rows,{}, {})
        rows=fixtures()
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            prepare('eng.premier',rows+[rows[0]],{}, {})

    def test_fixed_cutoff_fit_ignores_later_targets(self):
        samples=prepare('eng.premier',fixtures(),{}, {})
        cutoff=datetime(2023,3,25,tzinfo=timezone.utc)
        before=fit(samples,'all',cutoff)
        changed=copy.deepcopy(samples)
        for r in changed:
            if datetime.fromisoformat(r['available'])>=cutoff:
                r['goals']=[9,9]
                for v in r['vectors']:
                    for k in VARIANTS['all']:
                        v[k]=999
        after=fit(changed,'all',cutoff)
        self.assertEqual(before,after)
        self.assertTrue(np.isfinite(grid(before,samples[0])).all())

    def test_vector_projection_matches_existing_settlement(self):
        for h,a,line in ((1.8,1.1,-1),(.4,2.3,2),(3.,.8,0)):
            mat=np.array(score_grid(h,a,-.05))
            actual=markets(mat,(.41,.46),line)
            expected=market_probabilities(mat,line,(.41,.46))
            for code,values in actual.items():
                np.testing.assert_allclose(values,[expected[code][k] for k in OPTIONS[code]],atol=1e-12)
        self.assertNotIn('hhad',markets(mat,None))

    def test_actual_handicap_and_missing_half_labels(self):
        s={'goals':[1,2],'half_goals':[None,None]}
        a=actuals(s,1)
        self.assertEqual(OPTIONS['hhad'][a['hhad']],'d')
        self.assertNotIn('hafu',a)
        self.assertNotIn('hhad',actuals(s))

    def test_quote_cutoff_and_contract_change(self):
        hist=[{'updateDate':'2026-01-01','updateTime':t,'h':h,'d':'4.0','a':'2.0','goalLine':line}
              for t,h,line in [('10:00:00','2.0','-1'),('11:00:00','1.8','-2'),('13:00:00','1.1','-2')]]
        at=datetime.fromisoformat('2026-01-01T12:00:00+08:00')
        latest=quote(hist,at)
        self.assertEqual(latest['line'],-2)
        self.assertIn('11:00',latest['time'])
        x=movement(hist,at,latest,True)
        self.assertEqual(x[:4],[0.,0.,0.,1.])
        self.assertEqual(x[-1],1)

    def test_missing_market_history_is_explicit(self):
        samples=prepare('eng.premier',fixtures(),{}, {})
        self.assertEqual(samples[-1]['vectors'][0]['market_points_n'],0)
        self.assertEqual(samples[-1]['context_inputs'][0]['market'],[])

    def test_metrics_do_not_call_probability_coverage_accuracy(self):
        m=metric([[.4,.35,.25]],[1])
        self.assertEqual(m['accuracy'],0)
        self.assertAlmostEqual(m['log_loss'],-np.log(.35))
        self.assertIsNone(metric([],[])['accuracy'])


if __name__=='__main__':
    unittest.main()
