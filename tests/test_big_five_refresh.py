from copy import deepcopy
import unittest
from tests.test_big_five_history import fixture
from tests.test_big_five_dataset import canonical
from worldcup_mvp.console.big_five_refresh import collect_increment


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.row=canonical();self.row['supplemental_postmatch_facts']={'archive':{'xg':[1.1,0.8]}}
        self.base={'manifest':{'competition_id':'eng.premier'},'matches':[self.row],'coverage':[]}
        self.event,self.summary=fixture()
        self.board={'leagues':[{'id':'700','slug':'eng.1'}],'events':[self.event]}
        self.receipt={'observed_at':'2026-11-05T00:00:00Z'}

    def test_unchanged_rows_keep_facts_and_original_timestamps(self):
        before=deepcopy(self.base)
        result,report=collect_increment('eng.1',self.base,'.',2024,lambda *a:(self.board,self.receipt))
        self.assertEqual(result,self.base);self.assertEqual(self.base,before)
        self.assertEqual(report['existing_checked'],1);self.assertEqual(report['added'],0)

    def test_changed_score_requires_review_not_label_rewrite(self):
        self.event['competitions'][0]['competitors'][0]['score']='9'
        with self.assertRaisesRegex(ValueError,'existing_fixture_changed'):
            collect_increment('eng.1',self.base,'.',2024,lambda *a:(self.board,self.receipt))
        self.assertEqual(self.base['matches'][0]['home_goals'],2)

    def test_foreign_competition_and_empty_board_never_publish(self):
        for board in [{'leagues':[],'events':[self.event]}, {'leagues':self.board['leagues'],'events':[]}]:
            with self.assertRaises(ValueError):collect_increment('eng.1',self.base,'.',2024,lambda *a:(board,self.receipt))
        self.base['matches'][0]['competition_id']='esp.laliga'
        with self.assertRaisesRegex(ValueError,'cross_competition'):collect_increment('eng.1',self.base,'.',2024)

    def test_new_fixture_requires_verified_summary_and_preserves_parent(self):
        self.base['matches'][0]['id']='espn:older'
        self.base['matches'][0]['kickoff']='2024-12-01T15:00:00+00:00'
        def fetch(slug,endpoint,folder):return (self.board,self.receipt) if endpoint.startswith('scoreboard') else (self.summary,self.receipt)
        result,report=collect_increment('eng.1',self.base,'.',2024,fetch)
        self.assertEqual(report['added'],1);self.assertEqual(len(result['matches']),2)
        self.assertEqual(result['matches'][0],self.base['matches'][0]);self.assertEqual(len(self.base['matches']),1)

    def test_new_source_id_cannot_duplicate_existing_fixture(self):
        self.base['matches'][0]['id']='espn:older'
        def fetch(slug,endpoint,folder):return (self.board,self.receipt) if endpoint.startswith('scoreboard') else (self.summary,self.receipt)
        with self.assertRaisesRegex(ValueError,'duplicate_fixture'):
            collect_increment('eng.1',self.base,'.',2024,fetch)


if __name__=='__main__':unittest.main()
