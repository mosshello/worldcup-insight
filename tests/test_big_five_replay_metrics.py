import unittest
from scripts.replay_big_five_backtests import accuracy, summarize
from worldcup_mvp.console.league_data import load_history
from worldcup_mvp.console.sources import SourceError


class ReplayMetricsTest(unittest.TestCase):
    def test_top_one_is_not_multi_selection_coverage(self):
        rows = [{'markets': {'had': {'h':.4,'d':.35,'a':.25},
                              'ttg': {'s2':.4,'s3':.35,'s1':.25}},
                 'actual_markets': {'had':'d','ttg':'s3'}}]
        result = summarize(rows)
        self.assertEqual(result['had']['accuracy'], 0)
        self.assertEqual(result['ttg']['accuracy'], 0)
        self.assertEqual(result['ttg']['top_k_coverage'], 1)
        self.assertIsNone(result['hhad']['accuracy'])

    def test_close_predictions_abstain_without_hiding_forced_top_one(self):
        rows = [{'markets': {'had': {'h':.39,'d':.38,'a':.23}}, 'actual_markets': {'had':'h'}}]
        result = summarize(rows)['had']
        self.assertEqual(result['accuracy'], 1)
        self.assertEqual(result['policy_abstained'], 1)
        self.assertIsNone(result['policy_hit_or_coverage'])

    def test_no_samples_is_not_zero_accuracy(self):
        self.assertIsNone(accuracy(0,0)['accuracy'])
        self.assertLess(accuracy(5,10)['wilson95'][0], .5)
        self.assertGreater(accuracy(5,10)['wilson95'][1], .5)

    def test_history_no_longer_defaults_to_global_staging(self):
        with self.assertRaisesRegex(SourceError, '须指定赛事'):
            load_history()
