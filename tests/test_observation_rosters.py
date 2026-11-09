import copy
import tempfile
import unittest
from pathlib import Path
from worldcup_mvp.console.store import Store,encode
from worldcup_mvp.console.observation_rosters import normalize_roster,CurrentRosterStore

class RosterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'test.db');self.rosters=CurrentRosterStore(self.store)
        self.at='2026-11-09T12:00:00Z'
        self.raw={'status':'success','timestamp':self.at,'team':{'id':'359','displayName':'Arsenal'},'season':{'year':2026},
            'athletes':[{'id':'123','uid':'s:600~a:123','displayName':'Player','status':{'name':'Active'},'injuries':[]}],
            'coach':[{'id':'5','firstName':'Arsene','lastName':'Wenger'}]}
    def ingest(self):return self.rosters.ingest('eng.premier','espn:359','2026',self.raw,self.at)
    def test_source_membership_not_registration_health_or_coach(self):
        value=normalize_roster('eng.premier','espn:359','2026',self.raw,self.at)
        p=value['players'][0];self.assertEqual(p['player_id'],'espn:123')
        self.assertIsNone(p['registration']);self.assertIsNone(p['match_availability']);self.assertEqual(p['injury_coverage'],'unknown')
        self.assertFalse(value['coverage']['confirmed_current_coach']);self.assertFalse(value['training_allowed'])
        self.assertIsNone(value['unverified_coach_listings'][0]['current_appointment'])
    def test_identity_season_and_duplicate_rejection(self):
        for mutate in ('team','season','uid','duplicate','future'):
            raw=copy.deepcopy(self.raw)
            if mutate=='team':raw['team']['id']='360'
            if mutate=='season':raw['season']['year']=2025
            if mutate=='uid':raw['athletes'][0]['uid']='s:40~a:123'
            if mutate=='duplicate':raw['athletes']*=2
            if mutate=='future':raw['timestamp']='2026-11-09T13:00:00Z'
            with self.assertRaises(ValueError):normalize_roster('eng.premier','espn:359','2026',raw,self.at)
    def test_cutoff_expiry_and_competition_isolation(self):
        self.ingest()
        self.assertEqual(self.rosters.read_team('eng.premier','espn:359','2026','2026-11-09T11:59:59Z')['status'],'unavailable')
        self.assertEqual(self.rosters.read_team('eng.premier','espn:359','2026','2026-11-10T12:00:01Z')['status'],'expired')
        self.assertEqual(self.rosters.read_team('esp.laliga','espn:359','2026',self.at)['status'],'unavailable')
        self.assertEqual(self.rosters.read_team('eng.premier','espn:359','2026',self.at)['status'],'available')
    def test_append_only_integrity_and_conflicting_team_listings(self):
        one=self.ingest();self.assertEqual(one['id'],self.ingest()['id'])
        raw=copy.deepcopy(self.raw);raw['team']['id']='360'
        self.rosters.ingest('eng.premier','espn:360','2026',raw,self.at)
        value=self.rosters.read_team('eng.premier','espn:359','2026',self.at)
        self.assertEqual(value['concurrent_listing_conflicts'],{'espn:123':['espn:360']})
        with self.store.connect() as db:db.execute('UPDATE observation_roster_receipts SET payload=? WHERE digest=?',(encode({}),one['raw_digest']))
        with self.assertRaises(ValueError):self.rosters.read_team('eng.premier','espn:359','2026',self.at)
    def test_cached_evidence_does_not_fetch_or_merge_fpl(self):
        self.ingest();self.rosters.fetcher=lambda *args,**kwargs:self.fail('read must not fetch')
        fixture={'competition_id':'eng.premier','season':'2026','home_id':'espn:359','away_id':'espn:360'}
        result=self.rosters.fixture_evidence(fixture,self.at)
        self.assertEqual(result['facts'][0]['player_ids'],['espn:123']);self.assertEqual(result['unknown'][0]['side'],'away')
        self.assertEqual(self.rosters.fixture_evidence(fixture,'2026-11-08T12:00:00Z')['facts'],[])

if __name__=='__main__':unittest.main()
