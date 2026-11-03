"""Evidence boundaries, Chinese display and read-only legacy linking."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests.test_console_domain import fixture
from worldcup_mvp.console import intelligence, legacy_predictions, sources
from worldcup_mvp.console.service import ConsoleService
from worldcup_mvp.console.store import Store
from worldcup_mvp.console.team_labels import chinese_team, localize_context


class IntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.match = sources.normalize_match(fixture())
        self.match['kickoff'] = (self.now + timedelta(hours=2)).isoformat()
        self.detail = {'context': {}, 'history': {}}

    def quote(self, seconds, h, line=-1):
        return {'updated_at': (self.now + timedelta(seconds=seconds)).isoformat(), 'line': line,
                'options': [{'key': k, 'odds': n} for k, n in zip('hda', [h, 3.2, 3.6])]}

    def test_time_cutoff_sort_and_reversal(self):
        self.detail['history']['had'] = [self.quote(-60, 2.1), self.quote(60, 1.2),
                                         self.quote(-180, 2.2), self.quote(-120, 2.0)]
        result = intelligence.build(self.match, self.detail, self.now)['markets'][0]
        self.assertEqual(result['count'], 3)
        self.assertEqual(result['rows'][0]['first'], 2.2)
        self.assertEqual(result['rows'][0]['last'], 2.1)
        self.assertTrue(result['rows'][0]['reversal'])
        # After full time, ignore any quotes stamped at/after kickoff too.
        self.detail['history']['had'].append(self.quote(10800, 1.1))
        result = intelligence.build(self.match, self.detail, self.now + timedelta(days=1))
        self.assertEqual(result['markets'][0]['count'], 4)
        self.assertEqual(result['phase'], 'postgame')

    def test_handicap_change_and_nonfinite_prices_not_comparable(self):
        self.detail['history']['hhad'] = [self.quote(-180, 2, 1), self.quote(-60, 2.1, -1),
                                          self.quote(-30, float('nan'))]
        result = intelligence.build(self.match, self.detail, self.now)['markets'][0]
        self.assertFalse(result['same_line'])
        self.assertEqual(result['giving_team'], self.match['home'])
        self.assertIsNone(result['rows'][0]['delta_pp'])
        self.assertFalse(result['rows'][0]['reversal'])

    def test_evidence_requires_publication_and_recording_before_kickoff(self):
        early = (self.now - timedelta(hours=1)).isoformat()
        late = (self.now + timedelta(hours=3)).isoformat()
        self.detail['evidence'] = [
            {'id': 'early', 'kind': 'opinion', 'published_at': early, 'recorded_at': early},
            {'id': 'late', 'kind': 'fact', 'published_at': early, 'recorded_at': late},
        ]
        r = intelligence.build(self.match, self.detail, self.now)
        self.assertEqual([e['id'] for e in r['evidence']], ['early'])
        self.assertEqual(r['evidence'][0]['label'], '待验证观点')
        r = intelligence.build(self.match, self.detail, self.now + timedelta(days=1))
        self.assertFalse(r['evidence'][1]['pregame_eligible'])

    def test_lineup_compare_does_not_mutate_forecast(self):
        lineup = {'side': 'home', 'status': 'confirmed', 'players': [{'id': str(i), 'name': str(i)} for i in range(11)]}
        self.detail['context'] = {'lineups': [lineup]}
        prediction = {'direction': '主胜', 'intelligence': intelligence.build(self.match, self.detail, self.now)}
        before = copy.deepcopy(prediction)
        lineup['players'][0] = {'id': 'changed', 'name': '新增球员'}
        linked = intelligence.review_link(prediction, self.detail, self.match)
        self.assertIn('有1人变化', ' '.join(linked['findings']))
        self.assertEqual(prediction, before)
        lineup['players'] = [lineup['players'][0]] * 11
        self.assertFalse(intelligence.build(self.match, self.detail)['lineups'][0]['confirmed'])

    def test_chinese_labels_preserve_original_and_do_not_guess_unknown_identity(self):
        self.assertEqual(chinese_team('Borussia Mönchengladbach'), '门兴格拉德巴赫')
        self.assertEqual(chinese_team('Fenerbahçe'), '费内巴切')
        self.assertEqual(chinese_team('Unknown FC'), '球队译名待补充')
        data = {'recent': [[{'opponent': 'Konyaspor', 'opponent_id': '123'}], []]}
        localize_context(data)
        localize_context(data)
        self.assertEqual(data['recent'][0][0], {'opponent': '科尼亚体育', 'opponent_original': 'Konyaspor', 'opponent_id': '123'})

    def test_legacy_bridge_never_creates_prediction_or_settlement(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = Store(root / 'console' / 'football.sqlite3')
            store.upsert_matches([self.match], self.now.isoformat())
            path = root / 'cache' / 'prediction_journal.json'
            path.parent.mkdir()
            entry = {'match_id': self.match['provider_id'], 'home': self.match['home'], 'away': self.match['away'],
                     'kickoff_beijing': self.match['kickoff'], 'recorded_at': self.now.isoformat(),
                     'direction': '客胜', 'initial_direction': '主胜'}
            path.write_text(json.dumps({'entries': [entry]}), encoding='utf-8')
            original = path.read_bytes()
            app = ConsoleService(store)
            row = app.reviews()[0]
            self.assertEqual(row['legacy_prediction']['direction'], '主胜')
            self.assertIsNone(row['prediction'])
            self.assertIsNone(row['consistent'])
            self.assertEqual(row['market_results'], [])
            self.assertEqual(store.versions(), [])
            self.assertEqual(path.read_bytes(), original)
            self.assertIsNone(legacy_predictions.for_match({**self.match, 'away': '错配球队'}, [entry]))

    def test_result_refresh_does_not_depend_on_lineup_provider(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'test.sqlite3')
            self.match['kickoff'] = (self.now - timedelta(hours=3)).isoformat()
            store.upsert_matches([self.match], self.now.isoformat())
            app = ConsoleService(store)
            with patch.object(sources, 'fetch_odds', return_value={'official_score': '1-0', 'results': []}), \
                 patch.object(sources, 'fetch_context', side_effect=sources.SourceError('unavailable')) as fetch:
                self.assertEqual(app.reviews(refresh=True)[0]['postmatch_review']['score'], '1-0')
                fetch.assert_not_called()
                app.refresh_review_context()
                self.assertEqual(app.reviews()[0]['postmatch_review']['score'], '1-0')


if __name__ == '__main__':
    unittest.main()
