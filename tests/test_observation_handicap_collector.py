import io
import json
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from worldcup_mvp.console.store import Store
from worldcup_mvp.console.observation_runtime import ObservationRuntime
from worldcup_mvp.console.observation_handicap_collector import HandicapCollector

class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.runtime=ObservationRuntime(Store(Path(self.tmp.name)/'db.sqlite'))
        self.c=HandicapCollector(self.runtime);self.now=datetime.now(timezone.utc)
        self.events=[]
        for i in range(5):
            kickoff=(self.now+timedelta(hours=2,minutes=i)).isoformat()
            self.runtime.workspace.ingest_fixture({'id':f'canon{i}','competition_id':'eng.premier','home_id':'h','away_id':'a','home':'Home','away':'Away','kickoff':kickoff,'status':'upcoming'})
            self.events.append({'id':f'evt_test{i}','league':{'id':'lg_T9268DXK'},'home_team':{'name':'Home'},'away_team':{'name':'Away'},'scheduled_at':kickoff,'status':'scheduled'})
    def response(self,*args,**kwargs):return io.BytesIO(json.dumps({'data':self.events}).encode())
    def packet(self,ident,**kwargs):
        at=datetime.now(timezone.utc).isoformat()
        return {'event_id':ident,'stale':False,'_received_at':at,'odds':[{'bookmaker':'crown','market_type':'asian_handicap','period':'full_time','line':-.25,'handicap_components':[0,-.5],'prices':{'home':1.9,'away':2.0},'format':'decimal','status':'open','as_of':at}]}
    def test_bounded_collection_persists_receipts_and_restart_cache(self):
        with patch('worldcup_mvp.console.observation_handicap_collector.fetch_board',return_value={'matches':[],'generated_at':self.now.isoformat()}),patch('worldcup_mvp.console.observation_handicap_collector.urllib.request.urlopen',side_effect=self.response) as discovery,patch('worldcup_mvp.console.observation_handicap_collector.fetch_infer_odds',side_effect=self.packet) as odds:
            self.assertEqual(self.c.sync()['status'],'completed');self.assertEqual(discovery.call_count,1);self.assertEqual(odds.call_count,3)
            self.assertEqual(HandicapCollector(self.runtime).sync()['status'],'cached');self.assertEqual(odds.call_count,3)
        with self.runtime.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM observation_quotes').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT count(*) FROM observation_handicap_receipts').fetchone()[0],5)
        statuses={s['id']:s for s in self.runtime.statuses()}
        self.assertEqual(len(statuses['market-research']['reports']),5)
        self.assertTrue(all(not r['production_enabled'] for r in statuses['market-research']['reports']))
        self.assertEqual(statuses['asian-handicap:infersports']['requests_this_cycle'],3)
    def test_stale_quotes_saved_as_evidence_but_not_ingested(self):
        def stale(ident,**kwargs):
            packet=self.packet(ident);packet['odds'][0]['as_of']=(self.now-timedelta(hours=1)).isoformat();return packet
        with patch('worldcup_mvp.console.observation_handicap_collector.fetch_board',side_effect=TimeoutError),patch('worldcup_mvp.console.observation_handicap_collector.urllib.request.urlopen',side_effect=self.response),patch('worldcup_mvp.console.observation_handicap_collector.fetch_infer_odds',side_effect=stale):self.c.sync()
        with self.runtime.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM observation_quotes').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM observation_handicap_receipts').fetchone()[0],4)
        s=next(s for s in self.runtime.statuses() if s['id']=='asian-handicap:infersports')
        self.assertEqual(s['stored_rejected_records'],3)
    def test_runtime_starts_background_worker_once(self):
        from threading import Event
        started=Event();release=Event()
        def run():started.set();release.wait(5)
        with patch.object(HandicapCollector,'sync',side_effect=run):
            self.assertEqual(self.runtime.sync_handicap()['status'],'started');self.assertTrue(started.wait(1))
            self.assertEqual(self.runtime.sync_handicap()['status'],'running');release.set();self.runtime._handicap_thread.join(2)

if __name__=='__main__':unittest.main()
