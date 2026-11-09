import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests.test_console_domain import fixture
from worldcup_mvp.console import sources, api
from worldcup_mvp.console.daily_sweep import reference
from worldcup_mvp.console.service import ConsoleService
from worldcup_mvp.console.store import Store


class DailySweepTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name)/'football.sqlite3')
        self.app = ConsoleService(self.store)
        self.sweep = self.app.daily_sweep
        self.now = datetime.now(timezone.utc)
        self.match = sources.normalize_match(fixture())
        self.match.update(kickoff=(self.now+timedelta(hours=4)).isoformat(), sale_date='2026-11-04', sale_status='selling')
        self.match['markets']['hhad'] = {'code':'hhad'}
        for code, pool in self.match['markets'].items():
            pool['sale_status'] = 'selling'
            pool['updated_at'] = self.now.isoformat()
            pool['options'] = [{'key': k, 'odds': 2+i, 'label': sources.option_label(code,k)} for i,k in enumerate(sources.EXPECTED_OPTIONS[code])]
        self.match['markets']['hhad']['line'] = -1.0
        self.store.upsert_matches([self.match], self.now.isoformat())

    def tick(self, now=None):
        self.sweep.tick(now=now or self.now, generate=False)

    def row(self, kind='auto'):
        return self.sweep.read('2026-11-04', kind)['rows'][0]

    def result(self, score='2-1', extra=None, **kwargs):
        self.store.save_detail(self.match['id'], {'official_score':score, 'results':extra or [], **kwargs})

    def test_five_markets_freeze_once_and_remain_in_sale_day(self):
        self.tick()
        first = self.row()
        self.assertEqual(len(first['markets']), 5)
        self.assertEqual(first['forecast']['kind'], 'market')
        self.match['markets']['had']['options'].reverse()
        self.store.upsert_matches([self.match], self.now.isoformat())
        self.tick(self.now+timedelta(days=1))
        self.assertEqual(first['forecast'], self.row()['forecast'])
        self.assertEqual(len(self.row()['settlement_versions']), 1)
        self.assertEqual(self.sweep.read('2026-11-05')['rows'], [])

    def test_next_day_settlement_waits_for_half_time_and_corrects_append_only(self):
        self.tick()
        frozen = self.row()['forecast']
        self.result()
        self.tick(self.now+timedelta(days=1))
        markets = {m['code']:m for m in self.row()['markets']}
        self.assertTrue(markets['had']['hit'])
        self.assertEqual(markets['hhad']['actual_key'], 'd')
        self.assertIsNone(markets['hafu']['hit'])
        self.result('0-1', [{'code':'hafu','combination':'aa'}])
        self.tick(self.now+timedelta(days=1))
        self.assertFalse(next(m for m in self.row()['markets'] if m['code']=='had')['hit'])
        self.result()
        self.tick(self.now+timedelta(days=1))
        self.assertEqual(len(self.row()['settlement_versions']), 4)
        self.assertEqual(frozen, self.row()['forecast'])

    def test_conflicting_results_and_cancelled_never_count(self):
        self.tick()
        self.result('2-1',[{'code':'had','combination':'a'}])
        self.tick()
        self.assertTrue(self.row()['conflict'])
        self.assertTrue(all(m['hit'] is None for m in self.row()['markets']))
        self.result('2-1',cancelled=True)
        self.tick()
        self.assertTrue(all(m['hit'] is None for m in self.row()['markets']))
        self.assertEqual(self.sweep.read('2026-11-04')['summary']['market']['had']['settled'],0)

    def test_legacy_is_reference_missing_markets_not_invented(self):
        self.match['kickoff']=(self.now-timedelta(hours=1)).isoformat()
        self.store.upsert_matches([self.match],self.now.isoformat())
        legacy = {'match_id':self.match['provider_id'],'home':self.match['home'],'away':self.match['away'],
                  'kickoff_beijing':self.match['kickoff'],'recorded_at':(self.now-timedelta(hours=3)).isoformat(),
                  'initial_direction':'客胜','direction':'主胜','initial_predicted_score':'1-2','predicted_score':'2-1'}
        with patch.object(self.app,'legacy_entries',return_value=[legacy]):
            self.tick()
        self.result('1-2')
        self.tick()
        row=self.row()
        self.assertEqual(row['forecast']['kind'],'legacy')
        self.assertEqual(next(m for m in row['markets'] if m['code']=='had')['selection'],'客胜')
        self.assertEqual(sum(m['hit'] is True for m in row['markets']),2)
        for code in ('hhad','hafu','ttg'):
            self.assertIsNone(next(m for m in row['markets'] if m['code']==code)['hit'])
        self.assertEqual(self.sweep.read('2026-11-04')['summary']['model']['had']['settled'],0)

    def test_stale_quotes_and_finished_fixture_do_not_create_predictions(self):
        self.tick(self.now+timedelta(minutes=4))
        self.assertIsNone(self.row()['forecast'])
        self.result('1-0')
        self.tick()
        self.assertIsNone(self.row()['forecast'])

    def test_read_has_no_writes_and_rejects_invalid_parameters(self):
        with patch('worldcup_mvp.console.api.service',return_value=self.app):
            api.get('/api/v1/daily-sweep',{})
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM daily_fixtures').fetchone()[0],0)
        for day, kind in [('yesterday','auto'),('2026-11-04','bad')]:
            with self.assertRaises(ValueError):self.sweep.read(day,kind)

    def test_competition_filter_also_scopes_audit_denominators(self):
        self.tick()
        report = self.sweep.read('2026-11-04')
        cid = report['competitions'][0]['value']
        self.assertEqual(len(self.sweep.read('2026-11-04', competition=cid)['rows']), 1)
        other = self.sweep.read('2026-11-04', competition='not-this-competition')
        self.assertEqual(other['rows'], [])
        self.assertEqual(other['audit']['sources']['model']['had']['missing'], 0)
        self.assertEqual(other['competitions'], report['competitions'])

    def test_changed_fixture_identity_suspends_comparison(self):
        self.tick()
        self.result('1-0')
        self.tick()
        self.assertTrue(any(m['unit_return'] is not None for m in self.row()['markets']))
        self.match['away']='另一个球队'
        self.store.upsert_matches([self.match],self.now.isoformat())
        self.result('1-0')
        self.tick()
        self.assertTrue(self.row()['conflict'])
        self.assertTrue(all(m['hit'] is None for m in self.row()['markets']))
        self.assertTrue(all(m['unit_return'] is None and m['unit_profit'] is None
                            for m in self.row()['markets'] + self.row().get('exploration_results', [])))

    def test_no_handicap_settlement_without_exact_score(self):
        self.tick()
        self.result('',[{'code':'hhad','combination':'h'}])
        self.tick()
        self.assertIsNone(next(m for m in self.row()['markets'] if m['code']=='hhad')['hit'])

    def test_model_first_archive_is_used_and_market_remains_separate(self):
        self.tick()
        p=reference(self.match)
        p.update(schema_version=2,prediction_type='football',model_version='fixture-only')
        first=self.store.save_analysis(self.match['id'],p,{'match':self.match})
        p['market_assessments'][0]['selection']='后续版本'
        self.store.save_analysis(self.match['id'],p,{'match':self.match,'revision':2})
        self.tick()
        row=self.row()
        self.assertEqual(row['forecast']['id'],first['id'])
        self.assertEqual(row['forecast']['kind'],'model')
        self.assertEqual(self.row('market')['forecast']['kind'],'market')
        self.assertEqual(len(self.store.versions(self.match['id'])),2)
        report = self.sweep.read('2026-11-04')
        self.assertIn('market', report['rows'][0]['observations'])
        self.assertIn('model', report['rows'][0]['observations'])
        self.assertEqual(report['audit']['sources']['market']['had']['missing'], 0)
        self.assertEqual(report['audit']['sources']['market']['had']['pending'], 1)

    def test_incomplete_market_is_not_fabricated(self):
        self.match['markets']['hafu']['options'].pop()
        self.assertNotIn('hafu',[m['code'] for m in reference(self.match)['market_assessments']])

    def test_future_quote_is_not_available_to_reference(self):
        self.match['markets']['had']['updated_at'] = (self.now+timedelta(hours=1)).isoformat()
        self.assertNotIn('had',[m['code'] for m in reference(self.match)['market_assessments']])

    def test_late_market_is_added_before_kickoff_without_changing_first_choices(self):
        pool = self.match['markets'].pop('hafu')
        self.store.upsert_matches([self.match],self.now.isoformat())
        self.tick()
        first = self.row()['forecast']['market_assessments']
        self.match['markets']['hafu'] = pool
        self.match['markets']['had']['options'][0]['odds'] = 30
        self.store.upsert_matches([self.match],self.now.isoformat())
        self.tick()
        after = self.row()['forecast']['market_assessments']
        self.assertEqual(len(after),5)
        self.assertEqual(first,[m for m in after if m['code']!='hafu'])
        self.tick(self.now+timedelta(days=1))
        self.assertEqual(after,self.row()['forecast']['market_assessments'])

    def test_legacy_exact_score_is_not_treated_as_other_score_bucket(self):
        self.sweep.save(self.match,'legacy',{'market_assessments':[{'code':'crs','selected_keys':['s1sh'],'selection':'9-0'}]},self.now.isoformat())
        self.result('8-0')
        self.tick()
        crs=next(m for m in self.row('legacy')['markets'] if m['code']=='crs')
        self.assertFalse(crs['hit'])

    def test_auto_generation_uses_existing_prediction_gate_and_is_idempotent(self):
        saved={'created':self.now.isoformat(),'market_assessments':reference(self.match)['market_assessments']}
        with patch('worldcup_mvp.console.model_registry.selection',return_value={'run_id':'selected'}), patch.object(self.app,'prediction',return_value={'prediction':saved}) as predict:
            self.sweep.tick(now=self.now)
            self.sweep.tick(now=self.now+timedelta(minutes=6))
            self.assertEqual(predict.call_count,1)
            self.assertEqual(predict.call_args.kwargs['request_key'],'daily-sweep:'+self.match['id'])

    def test_failed_model_generation_keeps_reference_and_retries_later(self):
        with patch('worldcup_mvp.console.model_registry.selection',return_value={'run_id':'selected'}), patch.object(self.app,'prediction',side_effect=ValueError('输入不齐')) as predict:
            self.sweep.tick(now=self.now)
            self.sweep.tick(now=self.now+timedelta(seconds=60))
            self.assertEqual(predict.call_count,1)
            self.assertEqual(self.row()['forecast']['kind'],'market')
