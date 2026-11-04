"""New competition collection remains a separate, repeatable operation."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from worldcup_mvp.console.data_tasks import DataTasks
from worldcup_mvp.console.store import Store


class NewCompetitionTaskTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.tasks = DataTasks(Store(self.root/'console.sqlite'))
        specs = [{'id':cid,'history_years':[2024,2025,2026],
                  'research_policy':{'qualifying_years':[2026]} if cid=='uefa.ucl' else {}}
                 for cid in DataTasks.espn_competitions]
        scoped = patch('worldcup_mvp.console.data_tasks.catalog',return_value=specs)
        scoped.start();self.addCleanup(scoped.stop)

    def start(self, cid, **body):
        with patch('worldcup_mvp.console.data_tasks.threading.Thread.start'):
            return self.tasks.start(cid,body)

    def test_new_competitions_accept_calendar_scope_and_keep_requests_independent(self):
        for cid in sorted(DataTasks.espn_competitions):
            task = self.start(cid,request_key='same',years=[2025,2026])
            self.assertEqual(task['request']['years'],[2025,2026])
            self.assertEqual(task['source_policy']['allow_qualifying'],cid=='uefa.ucl')
        self.assertEqual(sum(len(self.tasks.list(cid)) for cid in DataTasks.espn_competitions),3)

    def test_frozen_qualification_policy_and_collection_do_not_import_or_train(self):
        task = self.start('uefa.ucl',request_key='collect',years=[2026])
        def collect(cid, years, directory, **options):
            self.assertEqual(cid,'uefa.ucl')
            self.assertTrue(options['allow_qualifying'])
            self.assertEqual(options['qualifying_years'],[2026])
            value = {'matches':[{'id':'one','competition_id':cid}],
                     'manifest':{'unique_source_events':2,'excluded':[{'id':'two','reason':'unknown'}]},
                     'coverage':{'seasons':{'2026':1}}}
            (directory/'candidate.json').write_text(json.dumps(value))
            return value
        with patch('worldcup_mvp.console.data_tasks.catalog',return_value=[{'id':'uefa.ucl'}]), \
             patch('worldcup_mvp.console.espn_competition_history.collect_competition',side_effect=collect), \
             patch('worldcup_mvp.console.model_registry.import_competition') as imported, \
             patch('worldcup_mvp.console.model_tasks.ModelTasks.start') as trained:
            self.tasks._run(task)
            imported.assert_not_called(); trained.assert_not_called()
        result = self.tasks.list('uefa.ucl')[0]
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(result['result']['admitted'],1)
        self.assertEqual(result['result']['quarantined'],1)

    def test_source_failure_keeps_previous_pointer(self):
        task = self.start('uefa.uel',request_key='failed',years=[2026])
        pointer = self.tasks.root/'uefa.uel'/'latest.json'
        pointer.write_text('{"task_id":"previous"}')
        with patch('worldcup_mvp.console.espn_competition_history.collect_competition',side_effect=ValueError('HTTP429')):
            self.tasks._run(task)
        self.assertEqual(json.loads(pointer.read_text())['task_id'],'previous')
        self.assertEqual(self.tasks.list('uefa.uel')[0]['status'],'failed')

    def test_cross_competition_fetch_rejected_before_network(self):
        task = self.start('uefa.uel',request_key='one',years=[2026])
        with patch('worldcup_mvp.console.espn_competition_history.fetch') as fetch:
            with self.assertRaisesRegex(ValueError,'赛事不一致'):
                self.tasks._fetch_acl(task,'scoreboard?dates=2026',self.root,source='eng.2')
            fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
