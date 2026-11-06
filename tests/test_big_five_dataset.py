from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.supplement_big_five import associate,deduplicate
from tests.test_big_five_history import fixture
from worldcup_mvp.console.big_five_history import parse_event,enrich
from worldcup_mvp.console import big_five_dataset as admission,catalog


def canonical():return enrich(parse_event('eng.1',fixture()[0],'2026-11-05T00:00:00Z'),fixture()[1],{})
def archive():
    return {'league_code':'ENG','league':'EPL','season':'2024','season_input':'2024','understat_id':'1',
        'home_team':'home','away_team':'away','home_team_id':'1','away_team_id':'2',
        'home_score':2,'away_score':1,'home_xg':0.,'away_xg':1.2,
        'match_url':'https://understat.com/match/1','match_date':'2025-01-01 15:00:00'}


class BigFiveAdmissionTests(unittest.TestCase):
    def test_duplicate_request_season_is_not_conflicting_fixture_season(self):
        a=archive();b={**a,'season_input':'2023'}
        rows,errors,duplicates=deduplicate([a,b]);self.assertEqual(duplicates,1);self.assertFalse(errors)
        self.assertEqual(rows[0]['season'],'2024');self.assertEqual(rows[0]['archive_requested_seasons'],['2023','2024'])
        b['home_xg']=2
        self.assertFalse(deduplicate([a,b])[0]);self.assertTrue(deduplicate([a,b])[1])

    def test_source_date_timezone_and_market_separation(self):
        joined,_,errors=associate('eng.1',[canonical()],[archive()],{'observed_at':'2026-11-05T00:00:00Z'})
        self.assertFalse(errors);x=joined['espn:10'];self.assertIsNone(x['archive_timezone'])
        self.assertFalse(x['approved_as_training_feature']);self.assertEqual(x['xg'],[0,1.2])
        a=archive();a['match_date']='2025-01-02 15:00:00'
        self.assertEqual(associate('eng.1',[canonical()],[a],{})[2][0]['reason'],'fixture_date_or_season_unmatched')
        a=archive();a['away_score']=2
        self.assertEqual(associate('eng.1',[canonical()],[a],{})[2][0]['reason'],'score_conflict')

    def test_unknown_names_and_cross_competition_do_not_join(self):
        a=archive();a['home_team']='HOME'
        self.assertFalse(associate('eng.1',[canonical()],[a],{})[0])
        self.assertFalse(associate('esp.1',[canonical()],[archive()],{})[0])

    def test_admission_filters_uncertain_labels_without_rewriting_source(self):
        row=canonical();bad=deepcopy(row);bad['id']='espn:11';bad['training_eligible']=False
        data={'manifest':{'competition_id':'eng.premier','source':'eng.1'},'matches':[row,bad],'coverage':[]}
        original=deepcopy(data);out=admission.prepare(data,'f'*64,'user request')
        self.assertEqual(len(out['matches']),1);self.assertEqual(out['matches'][0]['stage'],'regular')
        self.assertEqual(len(out['manifest']['excluded']),1);self.assertEqual(data,original)
        self.assertFalse(out['manifest']['source_manifest']['enhanced_features_trained'])
        data['matches'][1]['competition_id']='esp.laliga'
        with self.assertRaisesRegex(ValueError,'cross_competition'):admission.prepare(data,'f'*64,'user')

    def test_registry_additive_and_conflicts_fail(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'registry.json';prior={'id':'existing','providers':{'sporttery':'X'},'custom':'keep'}
            path.write_text(json.dumps([prior]))
            with patch.object(catalog,'REGISTRY_PATH',path):
                admission.register();values=catalog.catalog();self.assertEqual(values[0],prior);self.assertEqual(len(values),6)
                before=path.read_bytes();admission.register();self.assertEqual(before,path.read_bytes())
                values[-1]['training_enabled']=False;path.write_text(json.dumps(values))
                with self.assertRaisesRegex(ValueError,'definition_conflict'):admission.register()


if __name__=='__main__':unittest.main()
