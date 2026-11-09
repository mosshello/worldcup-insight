import copy
import unittest

from worldcup_mvp.console.daily_audit import count, paired


def observation(keys=('h',), hit=True):
    market = {'code': 'had', 'selected_keys': list(keys), 'options': [
        {'key': 'h', 'probability': 60}, {'key': 'd', 'probability': 25}, {'key': 'a', 'probability': 15}]}
    fixture = {'id': 'test:1', 'home': 'A', 'away': 'B', 'kickoff': '2026-11-08T10:00:00Z'}
    p = {'captured_at': '2026-11-08T09:00:00Z', 'fixture': fixture, 'market_assessments': [market],
         'market_reference': {'source_times': {'markets': '2026-11-08T08:59:00Z'}, 'market_assessments': [copy.deepcopy(market)]}}
    return {**fixture, 'observations': {'model': {'forecast': p, 'markets': [
        {'code': 'had', 'hit': hit, 'actual_key': 'h'}]}}}


class DailyAuditTests(unittest.TestCase):
    def test_single_multiple_missing_abstain_and_pending_are_disjoint(self):
        rows = [observation(), observation(('h', 'd')), observation((), None), observation(('h',), None), {'observations': {}}]
        stats = count(rows, 'model')['had']
        self.assertEqual(stats, {'single': {'hits': 1, 'settled': 1}, 'multiple': {'hits': 1, 'settled': 1},
                                 'missing': 1, 'pending': 1, 'abstained': 1, 'excluded': 0})
        self.assertEqual(count(rows, 'market')['had']['missing'], 5)

    def test_only_same_archive_reference_is_used_and_read_is_immutable(self):
        row = observation()
        row['observations']['market'] = {'forecast': {'market_assessments': []}}
        before = copy.deepcopy(row)
        stats = paired([row])['had']
        self.assertEqual(stats['settled'], 1)
        self.assertEqual(stats['model'], stats['market'])
        self.assertAlmostEqual(stats['model']['brier'], .245)
        self.assertAlmostEqual(stats['model']['log_loss'], .510826)
        self.assertEqual(row, before)
        del row['observations']['model']['forecast']['market_reference']
        self.assertEqual(paired([row])['had']['eligible'], 0)

    def test_wrong_identity_future_or_stale_quote_not_eligible(self):
        for stamp in ('2026-11-08T09:00:01Z', '2026-11-08T08:56:59Z', 'invalid'):
            row = observation()
            row['observations']['model']['forecast']['market_reference']['source_times']['markets'] = stamp
            self.assertEqual(paired([row])['had']['eligible'], 0)
        row = observation()
        row['away'] = 'C'
        self.assertEqual(paired([row])['had']['eligible'], 0)

    def test_unselected_probabilities_still_evaluated_but_conflicts_not(self):
        row = observation((), None)
        self.assertEqual(paired([row])['had']['settled'], 1)
        row['observations']['model']['conflict'] = True
        self.assertEqual(paired([row])['had']['settled'], 0)
        row = observation(('h',), None)
        row['observations']['model']['cancelled'] = True
        self.assertEqual(count([row], 'model')['had']['excluded'], 1)

    def test_invalid_probabilities_and_pending_do_not_create_scores(self):
        for value in (None, float('nan'), -1, 1000, True):
            row = observation()
            row['observations']['model']['forecast']['market_reference']['market_assessments'][0]['options'][0]['probability'] = value
            self.assertEqual(paired([row])['had']['eligible'], 0)
        row = observation(hit=None)
        row['observations']['model']['markets'][0]['actual_key'] = None
        stats = paired([row])['had']
        self.assertEqual((stats['eligible'], stats['settled']), (1, 0))
        self.assertIsNone(stats['model']['brier'])

    def test_handicap_lines_must_match(self):
        row = observation()
        model = row['observations']['model']['forecast']
        for m in (model['market_assessments'][0], model['market_reference']['market_assessments'][0]):
            m.update(code='hhad', line=-1)
        self.assertEqual(paired([row])['hhad']['eligible'], 1)
        model['market_reference']['market_assessments'][0]['line'] = 1
        self.assertEqual(paired([row])['hhad']['eligible'], 0)
