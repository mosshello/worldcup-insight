import copy
import json
import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from worldcup_mvp.console import catalog, model_registry
from worldcup_mvp.console.competition_rules import save
from worldcup_mvp.console.identity import IdentityRepository
from worldcup_mvp.console.store import Store,encode,fingerprint
from worldcup_mvp.console.service import ConsoleService
from worldcup_mvp.console.quality import assess
from worldcup_mvp.console.forecast_result import build, read_archived
from worldcup_mvp.console.readiness import fixture_key
from tests.test_console_domain import fixture
from worldcup_mvp.console.sources import normalize_match
from tests.football_v2_helpers import football,selected_model


class FootballWorkspaceV2(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store=Store(Path(self.folder.name)/'test.sqlite')
        self.now=datetime.now(timezone.utc)
        self.match=normalize_match(fixture())
        self.match.update(league_code='JD1',captured_at=self.now.isoformat(),kickoff=(self.now+timedelta(hours=3)).isoformat())
        self.store.upsert_matches([self.match],self.now.isoformat())
        self.detail={'markets':{},'history':{},'context':{'available':True,'state':'upcoming','fixture_identity':fixture_key(self.match),'captured_at':self.now.isoformat()},'captured_at':self.now.isoformat(),'odds_fresh':False}

    def test_required_inputs_do_not_include_unused_personnel_or_prices(self):
        quality=assess(self.match,self.detail,self.now,football=football(self.match))
        self.assertTrue(quality['can_predict'])
        self.assertFalse(quality['quotes_ready'])
        self.assertFalse(quality['fundamentals']['ready'])
        self.assertFalse(assess(self.match,self.detail,self.now,football={'status':'unavailable','reason':'缺模型'})['can_predict'])

    def test_no_quotes_archives_football_then_retries_same_request(self):
        app=ConsoleService(self.store); app.current_ids={self.match['id']}
        with patch.object(app,'sync'),patch.object(app,'detail',return_value=self.detail),selected_model(self.match):
            first=app.prediction(self.match['id'],request_key='one')['prediction']
            second=app.prediction(self.match['id'],request_key='one')['prediction']
        self.assertEqual(first,second)
        self.assertEqual(len(self.store.versions()),1)
        self.assertEqual(first['schema_version'],2)
        self.assertIsNone(first['market_reference'])
        self.assertEqual(first['probabilities'],[50,30,20])

    def test_selection_change_during_prediction_is_rejected(self):
        app=ConsoleService(self.store);app.current_ids={self.match['id']}
        with patch.object(app,'sync'),patch.object(app,'detail',return_value=self.detail),selected_model(self.match),patch.object(model_registry,'selection',return_value={'run_id':'other','revision':1}):
            with self.assertRaisesRegex(ValueError,'选用已变更'):app.prediction(self.match['id'])
        self.assertEqual(self.store.versions(),[])

    def test_transaction_rechecks_fixture_and_kickoff(self):
        expected=fixture_key(self.match)
        changed={**self.match,'kickoff':(self.now-timedelta(seconds=1)).isoformat()}
        self.store.upsert_matches([changed],self.now.isoformat())
        with self.assertRaisesRegex(ValueError,'变更'):
            self.store.save_analysis(self.match['id'],{'model_version':'test'},{},expected_fixture=expected)
        with self.assertRaisesRegex(ValueError,'已开赛'):
            self.store.save_analysis(self.match['id'],{'model_version':'test'},{},expected_fixture=fixture_key(changed))
        self.assertEqual(self.store.versions(),[])

    def test_source_identity_shared_but_participation_and_category_separate(self):
        repo=IdentityRepository(self.store)
        team=repo.bind('espn','123','Team',{'source':'verified'})
        repo.participate('jp.j1','2026',team,{'fixture':'one'})
        repo.participate('afc.champions','2026',team,{'fixture':'two'})
        youth=repo.bind('espn','123','Team U21',{'source':'other'},category='u21')
        self.assertNotEqual(team,youth)
        self.assertEqual(repo.teams('jp.j1')[0]['id'],repo.teams('afc.champions')[0]['id'])
        with self.assertRaisesRegex(ValueError,'冲突'):
            repo.bind('espn','123','Team',{'source':'changed'},team_id='other')
        repo.record_fixture(self.match)
        repo.record_fixture(self.match)
        repo.record_fixture({**self.match,'kickoff':(self.now+timedelta(days=1)).isoformat()})
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fixture_revisions').fetchone()[0],2)

    def test_rules_dynamic_and_revision_checked_without_rewriting_history(self):
        path=Path(self.folder.name)/'competitions.json'
        path.write_text(encode([]),encoding='utf-8')
        spec={'id':'test.cup','name':'Test','providers':{'sporttery':'X'},'seasons':[],
              'training_scope':'competition_only','target_period':'regular_time','training_enabled':False}
        with patch.object(catalog,'REGISTRY_PATH',path),patch.object(model_registry,'REGISTRY',Path(self.folder.name)/'models'):
            self.assertIsNone(catalog.classify({**self.match,'league_code':'X'})['period'])
            result=save(spec)
            self.assertEqual(catalog.classify({**self.match,'league_code':'X'})['id'],'test.cup')
            with self.assertRaisesRegex(ValueError,'已变更'):save({**spec,'name':'Changed'},None)
            updated=save({**spec,'name':'Changed'},result['revision'])
            self.assertNotEqual(updated['revision'],result['revision'])
            self.assertEqual(len(list((path.parent/'football_rules/test.cup').glob('*.json'))),2)

    def test_unregistered_codes_do_not_share_generic_league_identity(self):
        one=catalog.classify({**self.match,'league_code':'UNKNOWN-A','league_id':1})
        two=catalog.classify({**self.match,'league_code':'UNKNOWN-B','league_id':1})
        self.assertNotEqual(one['id'],two['id'])
        self.assertIsNone(one['period'])

    def test_legacy_adapter_preserves_payload_and_separates_probabilities(self):
        old={'probabilities':[20,30,50],'football_model':{'markets':[]}}
        before=copy.deepcopy(old)
        parts=read_archived(old)
        self.assertEqual(parts['market_reference']['probabilities'],[20,30,50])
        self.assertEqual(old,before)

    def test_optional_quote_failure_never_changes_football_probabilities(self):
        model=football(self.match)
        detail={**self.detail,'quality':assess(self.match,self.detail,football=model)}
        first=build(self.match,detail,model)
        changed={**detail,'markets':self.match['markets'],'odds_fresh':False}
        second=build(self.match,changed,model)
        self.assertEqual(first['probabilities'],second['probabilities'])
        self.assertIsNone(second['market_reference'])

    def test_cancellation_arriving_before_commit_blocks_archive(self):
        self.store.save_detail(self.match['id'],{'cancelled':True})
        with self.assertRaisesRegex(ValueError,'状态已变更'):
            self.store.save_analysis(self.match['id'],{'model_version':'test'},{},expected_fixture=fixture_key(self.match))
        self.assertEqual(self.store.versions(),[])

    def test_deduplicated_archive_still_binds_new_request_key(self):
        result={'model_version':'test'}
        first=self.store.save_analysis(self.match['id'],result,{'one':1})
        self.store.save_analysis(self.match['id'],result,{'one':1},request_key='retry')
        self.store.save_analysis(self.match['id'],result,{'one':2})
        self.assertEqual(self.store.saved_request('retry',self.match['id']),first)

    def test_formal_result_corrections_append_versions_even_returning_to_old_result(self):
        archive=self.store.save_analysis(self.match['id'],{'model_version':'test'},{})
        one=self.store.save_settlement(archive['id'],{'result':'1-0'})
        same=self.store.save_settlement(archive['id'],{'result':'1-0'})
        two=self.store.save_settlement(archive['id'],{'result':'0-1'})
        three=self.store.save_settlement(archive['id'],{'result':'1-0'})
        self.assertEqual(one,same)
        self.assertEqual(two['previous_id'],one['id'])
        self.assertEqual(three['previous_id'],two['id'])
        self.assertEqual(self.store.versions(),[archive])


if __name__=='__main__':unittest.main()
