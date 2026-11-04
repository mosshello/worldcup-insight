import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from worldcup_mvp.console.identity import IdentityRepository
from worldcup_mvp.console.store import Store, now_iso
from worldcup_mvp.console.readiness import fixture_key
from worldcup_mvp.console.sources import verify_summary_identity, SourceError, espn_state
from tests.test_espn_history import fixtures


class SourceBridgeTests(unittest.TestCase):
    def test_summary_binding_rejects_other_teams_season_and_time(self):
        event, summary=fixtures(); match={'kickoff':event['date']}
        board={'leagues':[{'id':'3902','slug':'afc.champions'}]}
        verify_summary_identity(match,board,event,summary,'afc.champions')
        changes=[lambda s:s['header']['competitions'][0]['competitors'][0]['team'].update(id='other'),
                 lambda s:s['header']['season'].update(year=2022),
                 lambda s:s['header']['competitions'][0].update(date='2024-02-03T11:00Z'),
                 lambda s:s['header'].update(uid='s:600~l:other~e:123')]
        for change in changes:
            s=copy.deepcopy(summary);change(s)
            with self.assertRaises(SourceError):verify_summary_identity(match,board,event,s,'afc.champions')

    def test_cancel_postpone_unknown_are_not_upcoming(self):
        self.assertEqual(espn_state({'name':'STATUS_POSTPONED','state':'pre'}),'postponed')
        self.assertEqual(espn_state({'name':'STATUS_CANCELED','state':'post','completed':True}),'cancelled')
        self.assertEqual(espn_state({}),'unknown')
        event, summary=fixtures()
        event['status']={'type':{'name':'STATUS_CANCELED'}}
        summary['header']['competitions'][0]['status']={'type':{'name':'STATUS_SCHEDULED'}}
        verify_summary_identity({'kickoff':event['date']},{'leagues':[{'id':'3902','slug':'afc.champions'}]},event,summary,'afc.champions')
        self.assertEqual(espn_state(event['status']['type']),'cancelled')

    def test_alias_links_keep_ids_and_coalesce_participation_without_cross_category(self):
        with tempfile.TemporaryDirectory() as td:
            repo=IdentityRepository(Store(Path(td)/'db.sqlite'))
            a=repo.bind('sporttery','1','Team',{'proof':'one'})
            b=repo.bind('espn','2','Team',{'proof':'two'})
            repo.participate('afc.champions','2026',a,{'source':'a'})
            repo.participate('afc.champions','2026',b,{'source':'b'})
            repo.link(b,a,{'verified_fixture':'exact'})
            result=repo.teams('afc.champions')
            self.assertEqual(len(result),1)
            self.assertEqual(result[0]['source_ids'],{'sporttery':'1','espn':'2'})
            with repo.store.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM identity_teams').fetchone()[0],2)
            with self.assertRaisesRegex(ValueError,'类别冲突'):
                repo.bind('espn','u21','Youth',{'proof':'youth'},team_id=a,category='u21')
            other=repo.bind('sporttery','99','Other',{'proof':'other'})
            with self.assertRaisesRegex(ValueError,'来源标识冲突'):repo.link(b,other,{'proof':'bad'})

    def test_context_bridge_requires_exact_fresh_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            repo=IdentityRepository(Store(Path(td)/'db.sqlite'))
            match={'id':'s:1','provider':'sporttery','league_code':'ACL','home_id':'a','away_id':'b',
                   'home':'Home','away':'Away','kickoff':'2026-11-04T18:00:00+08:00'}
            evidence={'provider':'espn','competition_slug':'afc.champions','fixture':fixture_key(match),
                'event_id':'event','source_url':'https://espn.com/event','kickoff':match['kickoff'],
                'team_ids':{'home':'11','away':'22'},'captured_at':now_iso()}
            with patch('worldcup_mvp.console.catalog.classify',return_value={'id':'afc.champions'}),patch('worldcup_mvp.console.catalog.catalog',return_value=[{'id':'afc.champions','providers':{'espn':'afc.champions'}}]):
                self.assertFalse(repo.index_context(match,{'espn_identity':{**evidence,'captured_at':'2000-01-01T00:00:00+00:00'}}))
                self.assertTrue(repo.index_context(match,{'espn_identity':evidence}))
                self.assertTrue(repo.index_context(match,{'espn_identity':evidence}))
            with repo.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM identity_links').fetchone()[0],2)
