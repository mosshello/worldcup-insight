from copy import deepcopy
import unittest
import gzip
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from worldcup_mvp.console.big_five_history import parse_event, enrich, coverage, fetch_url
from scripts.collect_big_five_xg import extract
from scripts.finalize_big_five import link_xg, verified_body


def fixture():
    sides = [{'homeAway':s,'team':{'id':i,'displayName':s},'score':g,
              'linescores':[{'displayValue':g},{'displayValue':'0'}]}
             for s,i,g in [('home','1','2'),('away','2','1')]]
    comp = {'id':'10','date':'2025-01-01T15:00Z','competitors':sides,'neutralSite':False,
            'format':{'regulation':{'periods':2}},
            'status':{'period':2,'type':{'name':'STATUS_FULL_TIME','completed':True}}}
    event = {'id':'10','uid':'s:600~l:700~e:10','date':comp['date'],
             'season':{'year':2024,'slug':'2024-25-english-premier-league'},'competitions':[comp]}
    summary = {'header':{'id':'10','uid':event['uid'],'season':{'year':2024},'competitions':[deepcopy(comp)]}}
    return event, summary


class BigFiveHistoryTests(unittest.TestCase):
    def row(self):
        return parse_event('eng.1',fixture()[0],'2026-11-05T00:00:00+00:00')

    def test_identity_and_competition_isolation(self):
        with self.assertRaises(ValueError): parse_event('esp.1',fixture()[0],'2026-11-05T00:00:00+00:00')
        event=fixture()[0];event['competitions'][0]['competitors'][1]['team']['id']='1'
        with self.assertRaises(ValueError):parse_event('eng.1',event,'2026-11-05T00:00:00+00:00')

    def test_extra_time_and_unknown_stage_rejected(self):
        event=fixture()[0];event['competitions'][0]['status']['period']=4
        with self.assertRaises(ValueError):parse_event('eng.1',event,'2026-11-05T00:00:00+00:00')
        event=fixture()[0];event['season']['slug']='playoffs'
        with self.assertRaises(ValueError):parse_event('eng.1',event,'2026-11-05T00:00:00+00:00')

    def test_historical_availability_not_fake_observation(self):
        r=self.row();self.assertEqual(r['result_available_at'],'2025-01-08T15:00:00+00:00')
        self.assertIn('assumption',r['availability_basis']);self.assertFalse(r['training_eligible'])
        with self.assertRaises(ValueError):parse_event('eng.1',fixture()[0],'2025-01-01T15:01:00+00:00')

    def test_summary_agreement_and_halves(self):
        r=enrich(self.row(),fixture()[1],{'sha256':'test'})
        self.assertTrue(r['training_eligible']);self.assertEqual(r['home_half_goals'],2)
        s=fixture()[1];s['header']['competitions'][0]['competitors'][0]['score']='3'
        with self.assertRaises(ValueError):enrich(self.row(),s,{})

    def test_unknown_neutral_not_eligible(self):
        s=fixture()[1];s['header']['competitions'][0].pop('neutralSite')
        self.assertFalse(enrich(self.row(),s,{})['training_eligible'])

    def test_half_conflict_not_filled(self):
        s=fixture()[1];s['header']['competitions'][0]['competitors'][0]['linescores'][0]['displayValue']='3'
        row=enrich(self.row(),s,{})
        self.assertIsNone(row['home_half_goals'])
        self.assertFalse(row['training_eligible'])

    def test_unrelated_stats_and_duplicate_starters(self):
        s=fixture()[1];s['boxscore']={'teams':[{'team':{'id':'999'},'statistics':[]}]}
        with self.assertRaises(ValueError):enrich(self.row(),s,{})
        s=fixture()[1];s['rosters']=[{'team':{'id':'1'},'homeAway':'home','roster':[
            {'starter':True,'athlete':{'id':'4'}} for _ in range(11)]}]
        self.assertEqual(enrich(self.row(),s,{})['postmatch_lineups'],{})

    def test_missing_statistics_not_zero(self):
        r=enrich(self.row(),fixture()[1],{})
        self.assertEqual(r['postmatch_statistics'],{})
        c=coverage('eng.1',[r])[0]
        self.assertFalse(c['round_robin_complete']);self.assertEqual(c['xg_both'],0)

    def test_league_size_change_and_duplicates(self):
        r=self.row();r['season']='2022'
        self.assertEqual(coverage('fra.1',[r])[0]['expected_full_season'],380)
        r['season']='2023'
        c=coverage('fra.1',[r,r])[0]
        self.assertEqual(c['expected_full_season'],306);self.assertEqual(c['duplicate_pairings'],1)

    def test_serie_a_2022_stage_is_explicit(self):
        e=fixture()[0];e['uid']='s:600~l:730~e:10';e['season']={'year':2022,'slug':'regular-season'}
        self.assertEqual(parse_event('ita.1',e,'2026-11-05T00:00:00Z')['season'],'2022')
        e['season']['slug']='relegation-playoff'
        with self.assertRaises(ValueError):parse_event('ita.1',e,'2026-11-05T00:00:00Z')


class SourceIntegrityTests(unittest.TestCase):
    def test_cache_corruption_cannot_trigger_network_when_offline(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);url='https://www.fotmob.com/api/data/matchDetails?matchId=1'
            key=hashlib.sha256(url.encode()).hexdigest()
            receipt={'status':'succeeded','file':'body.gz','sha256':'wrong','observed_at':'2026-01-01T00:00:00Z'}
            (root/'body.gz').write_bytes(gzip.compress(b'{}'))
            (root/(key+'.receipt.json')).write_text(json.dumps(receipt))
            with patch('worldcup_mvp.console.big_five_history.subprocess.run') as network:
                with self.assertRaisesRegex(ValueError,'not_cached'):fetch_url(url,root,allow_network=False)
                network.assert_not_called()
            with self.assertRaisesRegex(ValueError,'integrity'):verified_body(root,receipt)

    def test_xg_identity_scores_period_and_zero(self):
        f={'id':4,'season':'2024','home':{'id':1,'name':'home'},'away':{'id':2,'name':'away'},
           'status':{'utcTime':'2025-01-01T15:00Z','scoreStr':'2 - 1','reason':{'short':'FT'}}}
        s={'general':{'matchId':4,'parentLeagueId':47,'matchTimeUTCDate':f['status']['utcTime'],
            'finished':True,'homeTeam':{'id':1},'awayTeam':{'id':2}},
           'header':{'status':{'finished':True,'reason':{'short':'FT'}},'teams':[{'id':1,'score':2},{'id':2,'score':1}]},
           'content':{'stats':{'Periods':{'All':{'stats':[{'stats':[{'key':'expected_goals','rawStats':[{'value':0},{'value':1.2}]}]}]}}}}}
        r=extract('eng.1',f,s,{'observed_at':'2026-11-05T00:00:00Z'})
        self.assertEqual(r['xg'],[0,1.2]);self.assertFalse(r['training_eligible'])
        with self.assertRaises(ValueError):extract('esp.1',f,s,{})
        s['header']['status']['reason']['short']='AET'
        with self.assertRaisesRegex(ValueError,'period'):extract('eng.1',f,s,{})

    def test_join_requires_exact_fixture_and_score(self):
        r=parse_event('eng.1',fixture()[0],'2026-11-05T00:00:00Z')
        f={**r,'id':'fotmob:4','home_id':'fotmob:1','away_id':'fotmob:2','xg':[1.4,0.7],'source_receipt':{}}
        joined,_,errors=link_xg([r],[f])
        self.assertEqual(len(joined),1);self.assertFalse(errors)
        f['away_goals']=2
        self.assertEqual(len(link_xg([r],[f])[2]),1)
        f['away_name']='AWAY'
        self.assertEqual(link_xg([r],[f])[2][0]['reason'],'unresolved_team_identity')

    def test_awarded_score_is_not_regular_time_label(self):
        e,s=fixture();e['uid']='s:600~l:720~e:711556';e['id']='711556'
        e['season']['slug']='2024-25-german-bundesliga';e['competitions'][0]['id']='711556'
        for side,goals in zip(e['competitions'][0]['competitors'],('0','2')):side['score']=goals
        s['header']['uid']=e['uid'];s['header']['competitions']=deepcopy(e['competitions'])
        r=enrich(parse_event('ger.1',e,'2026-11-05T00:00:00Z'),s,{})
        self.assertFalse(r['training_eligible']);self.assertEqual(r['period'],'administrative_award')
        self.assertEqual(r['on_field_outcome']['home'],1)


if __name__=='__main__':unittest.main()
