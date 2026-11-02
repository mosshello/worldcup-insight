import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worldcup_mvp.console.service import ConsoleService
from worldcup_mvp.console.store import Store


class ModelWorkspaceStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = ConsoleService(Store(Path(self.tmp.name) / 'console.sqlite'))

    def test_collected_data_is_not_reported_as_missing_or_ready(self):
        training = dict(blockers=[], dataset_available=False, rows=0, reason='等待授权历史数据', ready=False, model=None)
        staging = dict(available=True, rights_status='unverified', coverage=[
            dict(competition_id='jp.j1', season='2023', collected=306, issues=['开赛时间待核查']),
            dict(competition_id='kr.k1', season='2026', collected=200, issues=[]),
        ])
        with patch('worldcup_mvp.console.model_registry.status', return_value=training), \
             patch('worldcup_mvp.console.competition_workspace.staging_status', return_value=staging):
            result = self.app.training_status("jp.j1")
        self.assertEqual(result['collected_rows'], 306)
        self.assertEqual(result['rows'], 0)
        self.assertFalse(result['ready'])
        self.assertEqual(result['reason'], '历史数据待验收')
        self.assertEqual(len(result['blockers']), 2)

    def test_invalid_imported_data_keeps_validation_error(self):
        with patch('worldcup_mvp.console.model_registry.status', return_value=dict(
                dataset_available=True, rows=0, ready=False, reason='数据文件无效')), \
             patch('worldcup_mvp.console.competition_workspace.staging_status', return_value=dict(available=True, coverage=[])):
            self.assertEqual(self.app.training_status("jp.j1")['reason'], '数据文件无效')

    def test_untrained_model_is_not_reported_as_evaluated(self):
        from worldcup_mvp.console import model_registry
        with patch.object(model_registry, 'REGISTRY', Path(self.tmp.name)/'empty-models'):
            scopes = self.app.capabilities()['competition_workspaces']
        self.assertEqual(scopes['jp.j1']['models'], [])
        self.assertIsNone(scopes['kr.k1']['training']['model'])
        self.assertFalse(scopes['uefa.ucl']['training']['ready'])

    def test_season_counts_do_not_combine_special_and_regular_competitions(self):
        match = dict(id='sporttery:1', league_code='JD1', kickoff='2026-11-01T13:00:00+08:00')
        training = dict(staging={'coverage':[{'id':'jp.j1:2026-special','collected':200},
                                             {'id':'jp.j1:2026-27','collected':126}]},
                        season_coverage=[{'id':'jp.j1:2026-27','imported':126}])
        with patch.object(self.app.store, 'list_matches', return_value=[match]), \
             patch.object(self.app.store, 'detail', return_value={'data':{'context':{'available':True}}}):
            seasons = self.app.competitions(training)[0]['seasons']
        by_id = {s['id']:s for s in seasons}
        self.assertEqual(by_id['2026-special']['match_count'],0)
        self.assertEqual(by_id['2026-special']['historical_count'],200)
        self.assertEqual(by_id['2026-27']['context_count'],1)
        self.assertEqual(by_id['2026-27']['training_count'],126)


if __name__ == '__main__': unittest.main()
