import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

from worldcup_mvp.console.store import Store, encode, fingerprint
from worldcup_mvp.console.observation_runtime import ObservationRuntime
from worldcup_mvp.console import api


class ObservationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'db.sqlite')
        self.runtime = ObservationRuntime(self.store)
        self.now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        self.fixture = dict(id='espn:1', competition_id='eng.premier',
                            home_id='espn:10', away_id='espn:20', home='甲', away='乙',
                            kickoff=(self.now + timedelta(hours=1)).isoformat(),
                            status='upcoming', neutral_site=False)
        self.revision = self.runtime.workspace.ingest_fixture(self.fixture, self.now)
        self.job = dict(fixture=self.fixture, revision=self.revision,
                        cutoff=self.now.isoformat(), source='base',
                        experiment={'base_run_id': 'missing'}, quotes=[])

    def packet(self, delta=0, **fixture_changes):
        packet = dict(fixture={**self.fixture, **fixture_changes}, context={},
                      captured_at=(self.now + timedelta(seconds=delta)).isoformat())
        with self.store.connect() as db:
            db.execute('INSERT INTO observation_packets VALUES(?,?,?,?,?)',
                       (fingerprint(packet), self.fixture['id'], self.revision,
                        packet['captured_at'], encode(packet)))
        return packet

    def test_fresh_packet_and_stale_future_exclusion(self):
        self.packet(-181)
        with self.assertRaises(ValueError): self.runtime.packet(self.job)
        self.packet(1)
        with self.assertRaises(ValueError): self.runtime.packet(self.job)
        fresh = self.packet(-30)
        self.assertEqual(self.runtime.packet(self.job), fresh)

    def test_payload_identity_must_match_job_even_with_matching_sql_revision(self):
        self.packet(-10, away_id='espn:999')
        with self.assertRaises(ValueError): self.runtime.packet(self.job)

    def quote(self, market='had', source='sporttery', line=None):
        return dict(market=market, source=source, line=line, captured_at=self.now.isoformat(),
                    options=[{'key': k, 'odds': v} for k, v in zip(('h','d','a'),(2,3,4))])

    def test_market_reference_requires_official_integer_handicap(self):
        for line, provider in [(1.5, 'sporttery'), (1, 'public'), (None, 'sporttery')]:
            with self.subTest(line=line, provider=provider):
                result = self.runtime.run({**self.job, 'source':'market',
                    'quotes':[self.quote(), self.quote('hhad', provider, line)]})
                self.assertIn('had', result['probabilities'])
                self.assertNotIn('hhad', result['probabilities'])
        result = self.runtime.run({**self.job, 'source':'market',
            'quotes':[self.quote('hhad', line=-1)]})
        self.assertEqual(result['line'], -1)
        self.assertAlmostEqual(sum(result['probabilities']['hhad'].values()), 1)

    def test_public_spread_never_becomes_integer_handicap(self):
        quote = dict(market='spread', source='espn', line=-1,
                     options=[{'key':'h','odds':1.9},{'key':'a','odds':1.9}])
        result = self.runtime.run({**self.job, 'source':'market', 'quotes':[quote]})
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('probabilities', result)

    def test_missing_model_does_not_fall_back_to_market_or_another_league(self):
        self.packet(-10)
        with patch('worldcup_mvp.console.model_registry.load_run', side_effect=FileNotFoundError) as loader:
            with self.assertRaises(FileNotFoundError):
                self.runtime.run({**self.job, 'quotes':[self.quote()]})
            loader.assert_called_once_with('eng.premier', 'missing')

    def test_readonly_api_alias_contract_never_collects_or_predicts(self):
        with self.store.connect() as db:
            db.execute('INSERT INTO observation_fixture_aliases VALUES(?,?,?)',
                       ('sporttery:1','espn:1','{}'))
        app = SimpleNamespace(store=self.store, observations=self.runtime.workspace,
                              observation_runtime=self.runtime)
        with patch('worldcup_mvp.console.api.service', return_value=app), \
             patch.object(self.runtime, 'collect_due', side_effect=AssertionError('GET collected')), \
             patch.object(self.runtime, 'run', side_effect=AssertionError('GET predicted')):
            result = api.get('/api/v1/observations', {'fixture_id':['sporttery:1']})
            self.assertTrue(result['success'])
            self.assertEqual([r['id'] for r in result['rows']], ['espn:1'])
            self.assertIsNone(result['comparison'])
            detail = api.get('/api/v1/observations/sporttery%3A1', {})
            self.assertEqual(detail['fixture']['id'], 'espn:1')
            self.assertEqual(detail['observations'], [])

    def test_only_known_regular_time_results_settle(self):
        fixture = {**self.fixture, 'status':'completed'}
        comp = {'status':{'period':2}, 'competitors':[
            {'homeAway':'home','team':{'id':'10'},'score':'2','linescores':[{'period':1,'displayValue':'1'}]},
            {'homeAway':'away','team':{'id':'20'},'score':'1','linescores':[{'period':1,'displayValue':'0'}]}]}
        raw = {'header':{'competitions':[comp]}, 'format':{'regulation':{'periods':2,'clock':2700}}}
        with patch.object(self.runtime.workspace, 'settle') as settle:
            self.runtime._settle(fixture, raw)
            settle.assert_called_once_with('espn:1', {'period':'regular_time',
                'home_goals':2,'away_goals':1,'home_half_goals':1,'away_half_goals':0})
            settle.reset_mock()
            self.runtime._settle(fixture, {**raw, 'format':{}})
            comp['status']['period'] = 4
            self.runtime._settle(fixture, raw)
            settle.assert_not_called()

    def test_explicit_epl_personnel_dispatch_preserves_legacy_and_no_fallback(self):
        from worldcup_mvp.console.observation_epl_personnel import SCHEMA
        packet=self.packet(-10)
        packet['raw']={}
        job={**self.job,'source':'enhanced','experiment':{'enhanced_kind':SCHEMA,
             'enhanced_digest':'digest','enhanced_reference':'explicit','evidence_version':'v3'}}
        evidence={'fixture':self.fixture,'as_of':self.job['cutoff'],'evidence':[]}
        prediction={'probabilities':{'had':{'h':.4,'d':.3,'a':.3}},'model_id':'new'}
        with patch.object(self.runtime,'packet',return_value=packet), \
             patch('worldcup_mvp.console.observation_epl_personnel.runtime_packet',return_value=({},evidence)) as builder, \
             patch('worldcup_mvp.console.observation_evidence.evidence_packet',return_value=evidence), \
             patch('worldcup_mvp.console.observation_evidence.merge_packets',return_value=evidence), \
             patch.object(self.runtime.ai,'extract',return_value={'facts':[]}), \
             patch('worldcup_mvp.console.observation_epl_personnel.forecast_packet',return_value=prediction) as forecaster, \
             patch('worldcup_mvp.console.observation_models.forecast',side_effect=AssertionError('legacy fallback')):
            result=self.runtime.run(job)
            self.assertEqual(result['model_id'],'new')
            builder.assert_called_once();forecaster.assert_called_once()
        with patch.object(self.runtime,'packet',return_value=packet), \
             patch('worldcup_mvp.console.observation_epl_personnel.runtime_packet',side_effect=ValueError('missing source')), \
             patch.object(self.runtime.ai,'extract') as ai:
            with self.assertRaises(ValueError):self.runtime.run(job)
            ai.assert_not_called()


if __name__ == '__main__': unittest.main()
