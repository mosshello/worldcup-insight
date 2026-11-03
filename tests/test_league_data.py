import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from worldcup_mvp.console.league_data import historical_context, match_fixture, team_mappings
from worldcup_mvp.console.research_dataset import prepare_local_research
from worldcup_mvp.console.sources import SourceError
from worldcup_mvp.console.store import fingerprint
from worldcup_mvp.console.training import reference_checks, validate_dataset

NOW = datetime(2026, 11, 1, 3, tzinfo=timezone.utc)


def fixture():
    return dict(id='500:f', competition_id='jp.j1', season='2026-27', stage='regular',
                home='町田泽维亚', away='大阪钢巴', home_id='500:1', away_id='500:2',
                kickoff='2026-11-01T15:00:00+08:00', collected_at=NOW.isoformat(), source_url='https://example.com/f')


def match():
    return dict(id='sporttery:f', league_code='JD1', home='町田泽维', away='大阪钢巴',
                home_id='1', away_id='2', kickoff=fixture()['kickoff'])


def history_row(i=1, days=2, home=True):
    return dict(id='500:'+str(i), competition_id='jp.j1', season='2026-27', stage='regular',
                home='町田泽维亚' if home else '大阪钢巴', away='大阪钢巴' if home else '町田泽维亚',
                home_id='500:1' if home else '500:2', away_id='500:2' if home else '500:1',
                kickoff=(NOW-timedelta(days=days)).isoformat(), collected_at=NOW.isoformat(),
                source_url='https://example.com/'+str(i), period='regular_time',
                home_goals=2, away_goals=1, home_half_goals=1, away_half_goals=0)


class LeagueDataTests(unittest.TestCase):
    def test_exact_alias_season_and_both_identities_are_required(self):
        data={'fixtures':[fixture()]}
        self.assertEqual(team_mappings([match()],data)[0]['team_id'],'500:1')
        for bad in ({**fixture(),'season':'2026-special'}, {**fixture(),'home':'大阪钢巴','away':'町田泽维亚'},
                    {**fixture(),'kickoff':'2026-11-01T13:00:00+08:00'}):
            with self.assertRaises(SourceError):match_fixture(match(),{'fixtures':[bad]})
        with self.assertRaises(SourceError):
            match_fixture(match(),{'fixtures':[fixture(),{**fixture(),'home_id':'500:other'}]})

    def test_venue_sample_not_filtered_from_only_last_five(self):
        rows=[history_row(i, i+1, home=False) for i in range(1,6)]+[history_row(6,10,home=True)]
        context=historical_context(match(),{'fixtures':[fixture()],'matches':rows},NOW)
        self.assertEqual(len(context['recent'][0]),5)
        self.assertEqual([r['id'] for r in context['venue_recent'][0]],['500:6'])
        self.assertEqual(context['recent'][0][0]['score'],'1-2')
        self.assertEqual(context['season_stats'][0]['matches'],6)
        self.assertFalse(context['injuries_verified'])

    def test_unobserved_future_conflicting_or_unresolved_results_excluded(self):
        rows=[history_row(), history_row(2,-1),
              {**history_row(3),'collected_at':(NOW+timedelta(hours=1)).isoformat()},
              {**history_row(4),'independent_verification':'conflict'},
              {**history_row(5),'kickoff_review_required':True}]
        c=historical_context(match(),{'fixtures':[fixture()],'matches':rows},NOW)
        self.assertEqual([r['id'] for r in c['recent'][0]],['500:1'])
        with self.assertRaises(SourceError):
            historical_context(match(),{'fixtures':[fixture()],'matches':rows},NOW+timedelta(days=3))

    def test_local_admission_preserves_scope_and_actual_recent_availability(self):
        data={'status':'collected','matches':[history_row(),history_row(2,10),
            {**history_row(3),'kickoff_review_required':True}],
            'coverage':[{'id':'jp.j1:2026-27','issues':['1场开赛时间差异待核查']}]}
        data['digest']=fingerprint({k:data[k] for k in ('matches','coverage')})
        with tempfile.TemporaryDirectory() as directory, patch('worldcup_mvp.console.training.datetime', wraps=datetime) as clock:
            clock.now.return_value=NOW+timedelta(seconds=1)
            source=Path(directory)/'staging.json'; out=Path(directory)/'dataset.json'
            source.write_text(json.dumps(data),encoding='utf8')
            with self.assertRaises(ValueError):prepare_local_research(source,out,[],'')
            self.assertFalse(out.exists())
            result=prepare_local_research(source,out,[],'explicit local experiment request')
            self.assertEqual(len(result['matches']),2)
            self.assertEqual(result['matches'][0]['result_available_at'],NOW.isoformat())
            self.assertEqual(result['matches'][1]['availability_basis'],'assumed_7_day_lag')
            self.assertFalse(result['manifest']['commercial_use_permitted'])
            self.assertNotIn('license_reference',result['manifest'])
            changed=copy.deepcopy(result);changed['manifest']['commercial_use_permitted']=True
            with self.assertRaises(ValueError):validate_dataset(changed)
            data['matches'][0]['home_goals']=4
            source.write_text(json.dumps(data),encoding='utf8')
            with self.assertRaises(ValueError):prepare_local_research(source,out,[],'request')
            self.assertEqual(json.loads(out.read_text(encoding='utf8')),result)

    def test_reference_check_does_not_mark_weaker_model_as_validated(self):
        checks=reference_checks({'by_competition':{
            'jp.j1':{'count':100,'log_loss':1.02,'brier':.61},
            'kr.k1':{'count':100,'log_loss':1.11,'brier':.67}}})
        self.assertEqual(checks['jp.j1']['label'],'优于均匀基线')
        self.assertEqual(checks['kr.k1']['label'],'未超过均匀基线')


if __name__=='__main__':unittest.main()
