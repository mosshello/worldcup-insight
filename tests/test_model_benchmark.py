import copy
import unittest
from datetime import datetime, timezone
from tests.test_console_football_model import history
from worldcup_mvp.console.model_benchmark import JointDC, features, cutoff


class BenchmarkTests(unittest.TestCase):
    def test_features_do_not_see_target_future_or_other_competition(self):
        rows = history(50, half=True)
        row = rows[30]; at = cutoff(row)
        expected = features(rows,row,at)
        changed = copy.deepcopy(rows)
        for r in changed:
            if r['result_available_at'] >= at.isoformat():
                r['home_goals'] = 99
        changed += history(60,league='kr.k1',half=True)
        self.assertEqual(expected,features(changed,row,at))

    def test_joint_fit_is_normalized_and_isolated(self):
        rows=history(60,half=True)
        at=datetime(2023,4,1,tzinfo=timezone.utc)
        a=JointDC().fit(rows,at,'jp.j1')
        b=JointDC().fit(rows+history(70,league='kr.k1',half=True),at,'jp.j1')
        self.assertEqual(a.forecast(rows[0]),b.forecast(rows[0]))
        for probabilities in a.forecast(rows[0]).values():
            self.assertAlmostEqual(sum(probabilities.values()),1,places=9)
            self.assertTrue(all(0<=p<=1 for p in probabilities.values()))
        self.assertAlmostEqual(sum(a.attack),0)
        self.assertAlmostEqual(sum(a.defence),0)

    def test_delayed_results_are_excluded(self):
        rows=history(60,half=True)
        at=datetime(2023,4,1,tzinfo=timezone.utc)
        expected=JointDC().fit(rows,at,'jp.j1').forecast(rows[0])
        future=copy.deepcopy(rows[0]); future['home_goals']=90
        future['result_available_at']='2027-01-01T00:00:00+00:00'
        self.assertEqual(expected,JointDC().fit(rows+[future],at,'jp.j1').forecast(rows[0]))
