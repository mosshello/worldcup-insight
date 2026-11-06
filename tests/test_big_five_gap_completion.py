from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.collect_big_five_scoreai import fixture, measurement, cached_json, SOURCE
from scripts.resolve_big_five_dates import reconcile, fetch_evidence
from tests.test_big_five_dataset import canonical, archive
from worldcup_mvp.console.big_five_dataset import prepare


def public_fixture():
    return {'id':'fd-1','home':'arsenal','away':'chelsea','competition':'PL','season':'2026/27',
            'state':'finished','kickoffTentative':False,'utcDate':'2026-10-31T15:00Z','score':[2,1],'halfTime':[1,0]}

def detail():
    return {'matchId':'fd-1','views':{'stats':{'provider':'API-Football','status':'ok','fetchedAt':'2026-11-01T10:00Z',
        'statistics':[{'teamId':team,'items':[{'label':'Expected goals (xG)','value':v}]} for team,v in [('arsenal','0.00'),('chelsea','1.20')]]}}}


class GapCompletionTests(unittest.TestCase):
    def test_fixture_requires_competition_exact_time_both_scores(self):
        r=canonical();r.update(home_name='Arsenal',away_name='Chelsea',season='2026',kickoff='2026-10-31T15:00Z',home_half_goals=1,away_half_goals=0)
        self.assertIs(fixture(public_fixture(),[r]),r)
        for change in [{'utcDate':'2026-10-31T15:15Z'},{'score':[1,2]},{'halfTime':[0,1]},
                       {'competition':'PD'},{'kickoffTentative':True},{'state':'upcoming'},{'season':'2025/26'}]:
            with self.assertRaises(ValueError):fixture({**public_fixture(),**change},[r])

    def test_statistics_must_be_postmatch_attributed_and_match_both_teams(self):
        self.assertEqual(measurement(public_fixture(),detail())['xg'],[0,1.2])
        for key,value in [('provider','AI'),('status','missing'),('fetchedAt','2026-10-31T16:00Z')]:
            d=detail();d['views']['stats'][key]=value
            with self.assertRaises(ValueError):measurement(public_fixture(),d)
        d=detail();d['matchId']='fd-2'
        with self.assertRaises(ValueError):measurement(public_fixture(),d)
        d=detail();d['views']['stats']['statistics'][1]['teamId']='arsenal'
        with self.assertRaises(ValueError):measurement(public_fixture(),d)
        for value in [None,'NaN','-1.00','unavailable']:
            d=detail();d['views']['stats']['statistics'][0]['items'][0]['value']=value
            with self.assertRaises(ValueError):measurement(public_fixture(),d)

    def test_import_keeps_provider_precision_time_without_feature_activation(self):
        r=canonical();fact=measurement(public_fixture(),detail());fact['approved_as_training_feature']=True
        r['supplemental_postmatch_facts']={SOURCE:fact}
        output=prepare({'manifest':{'competition_id':'eng.premier','source':'eng.1'},'matches':[r],'coverage':[]},'a'*64,'user')
        saved=output['matches'][0]['postmatch_xg_sources'][SOURCE]
        self.assertEqual(saved['precision_decimal_places'],[2,2]);self.assertFalse(saved['approved_as_training_feature'])
        self.assertEqual(saved['display_reported_fetched_at'],'2026-11-01T10:00Z')
        self.assertFalse(saved['upstream_collection_time_verified'])

    def test_display_precision_and_absent_optional_half_time_are_preserved(self):
        d=detail();d['views']['stats']['statistics'][1]['items'][0]['value']='0.8'
        self.assertEqual(measurement(public_fixture(),d)['precision_decimal_places'],[2,1])
        r=canonical();r.update(home_name='Arsenal',away_name='Chelsea',season='2026',kickoff='2026-10-31T15:00Z')
        m=public_fixture();del m['halfTime']
        self.assertIs(fixture(m,[r]),r)

    def test_corrupted_source_and_access_denials_never_reused(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/'data.json';p.write_text('{}');meta=p.with_suffix('.receipt.json')
            meta.write_text(json.dumps({'http_status':'200','transport_exit':0,'url':'https://example.test','sha256':'a'*64}))
            with self.assertRaisesRegex(ValueError,'integrity'):cached_json(p,'https://example.test',True)
            meta.write_text(json.dumps({'http_status':'403'}))
            with self.assertRaises(PermissionError):cached_json(p,'https://example.test',True)

    def test_date_exception_is_narrow_and_roster_conflicts_remain_missing(self):
        row=canonical();src=archive();src.update(home_shots=1,away_shots=1)
        review=('eng.1','10','1','2025-01-01','2025-01-01 15:00:00','url',[])
        data={'matches':[row],'understat_mapping':{'understat:1':{'espn_id':row['home_id']},'understat:2':{'espn_id':row['away_id']}}}
        roster=[{'understat_id':'1','league_code':'ENG','player_id':str(i),'team_id':str(i),'h_a':side,
            'goals':str(goals),'own_goals':'0','shots':'1','x_g':str(xg),'player':side} for i,side,goals,xg in [(1,'h',2,0),(2,'a',1,1.2)]]
        target,fact=reconcile(review,data,src,roster,{}, {},{})
        self.assertIs(target,row);self.assertFalse(fact['approved_as_training_feature']);self.assertTrue(fact['identity_exception']['source_date_conflict_preserved'])
        for change in [{'understat_id':'2'},{'match_date':'2025-01-02 15:00:00'},{'home_score':3},{'home_team_id':'3'},{'away_xg':2.0}]:
            with self.assertRaises(ValueError):reconcile(review,data,{**src,**change},roster,{},{},{})
        duplicate=deepcopy(data);duplicate['matches'].append(deepcopy(row))
        with self.assertRaises(ValueError):reconcile(review,duplicate,src,roster,{},{},{})

if __name__=='__main__':unittest.main()
