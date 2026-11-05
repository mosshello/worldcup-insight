import copy
import unittest
from datetime import timedelta
from worldcup_mvp.console.exploration import candidates
from worldcup_mvp.console.sources import EXPECTED_OPTIONS, option_label
from tests import test_console_daily_sweep as daily_tests


def market(code, main, extra):
    keys=EXPECTED_OPTIONS[code]
    probabilities={k:40/(len(keys)-2) for k in keys}
    probabilities.update({main:40,extra:20})
    return {'code':code,'selected_keys':[main],'selection':option_label(code,main),'origin':'model',
            'options':[{'key':k,'label':option_label(code,k),'probability':p} for k,p in probabilities.items()]}


class ExplorationTests(unittest.TestCase):
    setUp = daily_tests.DailySweepTests.setUp
    tick = daily_tests.DailySweepTests.tick
    row = daily_tests.DailySweepTests.row
    result = daily_tests.DailySweepTests.result
    def prepare(self):
        self.sweep.save(self.match,'model',{'market_assessments':[
            market('crs','s01s01','s03s01'),market('ttg','s2','s4'),market('hafu','hh','dh')]},self.now.isoformat())

    def test_candidates_have_probability_support_and_never_modify_primary(self):
        markets=[market('crs','s01s01','s03s01'),market('ttg','s2','s4'),market('hafu','hh','dh')]
        before=copy.deepcopy(markets)
        choices=candidates(markets)
        self.assertEqual([m['selection'] for m in choices],['3-1','4球','平胜'])
        self.assertEqual(markets,before)
        for m in choices:self.assertEqual(m['coverage'],20)
        markets[0]['options'].pop()
        self.assertEqual(candidates(markets[:1]),[])

    def test_no_fabricated_big_score_when_tail_probability_is_too_small(self):
        m=market('crs','s01s01','s03s01')
        for o in m['options']:
            if o['key']=='s01s01':o['probability']=59
            if o['key']=='s03s01':o['probability']=1
        self.assertEqual(candidates([m]),[])

    def test_exploration_freezes_and_settles_separately(self):
        self.prepare();self.tick()
        first=self.row()['forecast']
        self.assertEqual(len(first['explorations']),3)
        self.result('3-1',[{'code':'hafu','combination':'dh'}]);self.tick(self.now+timedelta(days=1))
        row=self.row()
        self.assertEqual(first,row['forecast'])
        self.assertTrue(all(r['hit'] for r in row['exploration_results']))
        self.assertFalse(next(m for m in row['markets'] if m['code']=='crs')['hit'])
        self.assertEqual(self.sweep.read('2026-11-04')['summary']['model']['crs']['hits'],0)

    def test_started_match_cannot_get_retrospective_exploration(self):
        self.prepare();self.result('3-1');self.tick(self.now+timedelta(days=1))
        self.assertNotIn('explorations',self.row()['forecast'])

    def test_legacy_without_probabilities_never_gets_alternatives(self):
        self.sweep.save(self.match,'legacy',{'market_assessments':[market('crs','s01s01','s03s01')]},self.now.isoformat())
        self.tick()
        self.assertNotIn('explorations',self.row('legacy')['forecast'])
