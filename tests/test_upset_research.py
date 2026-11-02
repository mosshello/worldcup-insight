import copy
from datetime import datetime, timedelta, timezone
import unittest

from worldcup_mvp.console import upset_research as u


def records(count=160):
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [{'id': str(i), 'competition_id': 'jp.j1', 'asof': (start+timedelta(days=i)).isoformat(),
             'trained_before': (start+timedelta(days=i-7)).isoformat(),
             'result_available_at': (start+timedelta(days=i+2)).isoformat(),
             'probabilities': [.65, .2, .15], 'outcome': i % 3} for i in range(count)]


class UpsetResearchTests(unittest.TestCase):
    def setUp(self):
        self.policy = u.registry.read(u.PROTOCOL_PATH)

    def test_market_is_not_inferred_from_model_or_future_odds(self):
        r = records(1)[0]
        self.assertEqual(u.market_reference(r, 10800)[1], 'missing_odds')
        r.update(odds={'h': 1.5, 'd': 4., 'a': 6.}, odds_observed_at=r['asof'])
        p, error = u.market_reference(r, 10800)
        self.assertIsNone(error); self.assertAlmostEqual(sum(p), 1.)
        r['odds_observed_at'] = (u.parse_date(r['asof'])+timedelta(seconds=1)).isoformat()
        self.assertEqual(u.market_reference(r, 10800)[1], 'future_odds')
        r['odds_observed_at'] = (u.parse_date(r['asof'])-timedelta(seconds=10801)).isoformat()
        self.assertEqual(u.market_reference(r, 10800)[1], 'stale_odds')
        r['odds'] = {'h': 1.5, 'd': 4.}
        self.assertEqual(u.market_reference(r, 10800)[1], 'incomplete_odds')

    def test_favorite_is_fixed_before_outcomes_and_calibration(self):
        rs = records(3)
        lookup = {r['id']: [.2, .3, .5] for r in rs}
        risk = u.risk_metrics(rs, lookup, self.policy, 'model')
        self.assertEqual(risk['favorite_fails']['events'], 2)
        self.assertAlmostEqual(risk['favorite_fails']['predicted'], .8)
        self.assertEqual(risk['underdog_win']['events'], 1)
        self.assertEqual(risk['draw']['events'], 1)
        rs[0]['outcome'] = 2
        again = u.risk_metrics(rs, lookup, self.policy, 'model')
        self.assertEqual(risk['cohort_ids'], again['cohort_ids'])
        self.assertEqual(again['favorite_fails']['events'], 3)
        market = u.risk_metrics(rs, lookup, self.policy, 'market')
        self.assertEqual(market['favorite_fails']['count'], 0)
        self.assertEqual(market['skipped'], {'missing_odds': 3})

    def test_no_favorite_for_balanced_draw_or_tie(self):
        for p in ([.4, .3, .3], [.2, .6, .2], [.5, 0., .5]):
            self.assertIsNone(u.favorite(p, .5))
        self.assertEqual(u.favorite([.2, .2, .6], .5), 2)

    def test_market_watch_rule_requires_market_favorite_and_gap(self):
        rs = records(3)
        for r in rs:
            r.update(odds={'h': 1.5, 'd': 4., 'a': 6.}, odds_observed_at=r['asof'])
        output = u.risk_metrics(rs, {r['id']: [.4, .3, .3] for r in rs}, self.policy)
        self.assertEqual(output['longshot_win']['count'], 3)
        self.assertEqual(output['watchlist']['count'], 3)
        self.assertEqual(output['watchlist']['events'], 1)
        self.assertIsNone(output['return_metrics'])

    def test_fit_rejects_unavailable_labels_cross_competition_duplicates(self):
        rs = records(9)
        before = u.parse_date(rs[-1]['result_available_at'])+timedelta(days=1)
        for mutation in ('future', 'competition', 'duplicate', 'trained_after'):
            changed = copy.deepcopy(rs)
            if mutation == 'future': changed[0]['result_available_at'] = before.isoformat()
            if mutation == 'competition': changed[0]['competition_id'] = 'kr.k1'
            if mutation == 'duplicate': changed[0]['id'] = changed[1]['id']
            if mutation == 'trained_after': changed[0]['trained_before'] = before.isoformat()
            with self.assertRaises(ValueError):
                u.fit_calibrator(changed, 'jp.j1', 'temperature', before, .02)

    def test_calibration_is_deterministic_scoped_and_normalized(self):
        rs = records(90)
        before = u.parse_date(rs[-1]['result_available_at'])+timedelta(days=1)
        a = u.fit_calibrator(rs, 'jp.j1', 'temperature_bias', before, .02)
        b = u.fit_calibrator(rs, 'jp.j1', 'temperature_bias', before, .02)
        self.assertEqual(a, b)
        adjusted = u.transform([.65, .2, .15], a, 'jp.j1')
        self.assertAlmostEqual(sum(adjusted), 1.)
        self.assertLess(adjusted[0], .65)
        self.assertGreater(min(adjusted), 0.)
        with self.assertRaises(ValueError): u.transform([.65, .2, .15], a, 'kr.k1')
        with self.assertRaises(ValueError): u.transform([65., 20., 15.], a, 'jp.j1')

    def test_selection_purges_delayed_results_and_has_no_test_input(self):
        rs = records()
        test_start = u.parse_date(rs[-1]['asof'])+timedelta(days=10)
        result = u.select_calibration(rs, 'jp.j1', test_start, self.policy)
        fitting = set(result['fit_ids']); selection = set(result['selection_ids'])
        self.assertFalse(fitting & selection)
        cutoff = u.parse_date(result['fit_cutoff'])
        self.assertTrue(all(u.parse_date(r['result_available_at']) < cutoff for r in rs if r['id'] in fitting))
        self.assertLess(len(fitting)+len(selection), len(rs))
        # Change only labels not available during fit; fitted parameters stay identical.
        changed = copy.deepcopy(rs)
        for r in changed:
            if r['id'] in selection: r['outcome'] = (r['outcome']+1) % 3
        again = u.select_calibration(changed, 'jp.j1', test_start, self.policy)
        self.assertEqual(result['artifacts'], again['artifacts'])

    def test_no_cross_market_mutation_and_paired_interval(self):
        rs = records(100)
        before = u.parse_date(rs[-1]['result_available_at'])+timedelta(days=1)
        a = u.fit_calibrator(rs, 'jp.j1', 'identity', before, .02)
        with self.assertRaises(ValueError): u.apply_calibrator(rs, a)
        for r in rs:
            r['asof'] = (u.parse_date(r['asof'])+timedelta(days=200)).isoformat()
        original = copy.deepcopy(rs)
        changed = u.apply_calibrator(rs, a)
        self.assertEqual(rs, original)
        self.assertNotIn('markets', changed[0])
        interval = u.paired_interval(rs, changed, self.policy)
        self.assertAlmostEqual(interval['delta_log_loss'], 0., places=12)
        self.assertGreaterEqual(interval['blocks'], 10)
        changed[0]['outcome'] = 2
        with self.assertRaises(ValueError): u.paired_interval(rs, changed, self.policy)


if __name__ == '__main__':
    unittest.main()
