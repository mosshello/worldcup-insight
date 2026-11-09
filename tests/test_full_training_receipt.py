from datetime import datetime, timezone
import unittest
from tests.test_console_football_model import dataset, history
from scripts.train_big_five_full import check_receipt
from worldcup_mvp.console import model_methods as methods


class FullTrainingReceiptTests(unittest.TestCase):
    def test_full_receipt_contains_every_match_and_each_season(self):
        rows = history(60, half=True)
        at = datetime(2024,1,1,tzinfo=timezone.utc)
        data = dataset(rows)
        data['manifest']['competition_id'] = 'jp.j1'
        model = methods.fit({'kind':'dc','mode':'separate','half_life':180},rows,at,'jp.j1')
        receipt = check_receipt(data,model,at)
        self.assertEqual(set(receipt['row_ids']), {r['id'] for r in rows})
        self.assertEqual(receipt['rows'], len(rows))
        self.assertEqual(sum(receipt['season_counts'].values()), len(rows))
        self.assertEqual(receipt['omitted_ids'], [])
        model.sample_count -= 1
        with self.assertRaisesRegex(ValueError, 'sample/team counts'):
            check_receipt(data,model,at)

    def test_unavailable_rows_cannot_be_silently_dropped_from_full_fit(self):
        rows = history(60, half=True)
        at = methods.cutoff(rows[40])
        model = methods.fit({'kind':'dc','mode':'separate','half_life':180},rows,at,'jp.j1')
        with self.assertRaisesRegex(ValueError, 'unavailable matches'):
            check_receipt(dataset(rows),model,at)
