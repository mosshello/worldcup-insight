import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from tests.test_console_domain import fixture
from worldcup_mvp.console import sources, official_quotes as quotes
from worldcup_mvp.console.store import Store, encode
from worldcup_mvp.console.service import ConsoleService


class OfficialQuotesTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.store=Store(Path(tmp.name)/'test.db');self.app=ConsoleService(self.store)
        self.now=datetime.now(timezone.utc)
        self.match=sources.normalize_match(fixture())
        self.match.update(kickoff=(self.now+timedelta(hours=4)).isoformat(),sale_status='selling')
        self.pool=self.match['markets']['had'];self.pool['updated_at']=(self.now-timedelta(hours=2)).isoformat()
        self.store.upsert_matches([self.match],self.now.isoformat())

    def cache(self, history, **kwargs):
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO official_odds_cache VALUES(?,?)',(self.match['id'],encode({
                'fixture':quotes.identity(self.match),'history':history,'captured_at':self.now.isoformat(),**kwargs})))

    def test_latest_quote_changes_without_changing_forecast(self):
        self.app.daily_sweep.tick(generate=False)
        before=self.app.daily_sweep.read(self.match['sale_date'])['rows'][0]['forecast']
        old=deepcopy(self.pool);new=deepcopy(old);new['options'][0]['odds']=4.25
        new['updated_at']=(self.now-timedelta(minutes=1)).isoformat()
        self.cache({'had':[old,new]})
        row=self.app.daily_sweep.read(self.match['sale_date'])['rows'][0]
        self.assertEqual(row['current_quotes']['markets']['had']['options'][0]['odds'],4.25)
        self.assertEqual(row['forecast'],before)

    def test_incomplete_and_future_history_cannot_replace_current(self):
        partial=deepcopy(self.pool);partial['options']=partial['options'][:1];partial['updated_at']=self.now.isoformat()
        future=deepcopy(self.pool);future['updated_at']=(self.now+timedelta(days=1)).isoformat()
        self.cache({'had':[partial,future]})
        result=quotes.read(self.store,self.match['id'],self.now)
        self.assertEqual(result['markets']['had'],self.pool)
        self.assertEqual(len(result['history']['had']),1)

    def test_unchanged_price_is_single_point_and_reversion_is_kept(self):
        first=deepcopy(self.pool);first['updated_at']=(self.now-timedelta(hours=3)).isoformat()
        self.cache({'had':[first]})
        self.assertEqual(len(quotes.read(self.store,self.match['id'])['history']['had']),1)
        changed=deepcopy(first);changed['options'][0]['odds']=9;changed['updated_at']=(self.now-timedelta(hours=2,minutes=30)).isoformat()
        self.cache({'had':[first,changed]})
        self.assertEqual(len(quotes.read(self.store,self.match['id'])['history']['had']),3)

    def test_refresh_failure_preserves_history_and_throttles(self):
        self.cache({'had':[self.pool]})
        with patch.object(self.app,'sync'),patch.object(sources,'fetch_odds',side_effect=sources.SourceError('offline')) as fetch:
            result=quotes.refresh(self.app,self.match['id'])
            quotes.refresh(self.app,self.match['id'])
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(result['error'],'offline')
        self.assertEqual(result['history_checked_at'],self.now.isoformat())
        self.assertEqual(len(result['history']['had']),1)

    def test_identity_change_ignores_cached_history(self):
        bad=deepcopy(self.pool);bad['options'][0]['odds']=99;bad['updated_at']=self.now.isoformat()
        self.cache({'had':[bad]})
        self.match['away']='另一球队';self.store.upsert_matches([self.match],self.now.isoformat())
        self.assertEqual(quotes.read(self.store,self.match['id'])['markets']['had'],self.pool)

    def test_stale_and_closed_are_explicit(self):
        result=quotes.read(self.store,self.match['id'],self.now+timedelta(hours=5))
        self.assertTrue(result['closed']);self.assertTrue(result['stale'])

    def test_list_snapshots_preserve_changes_between_history_requests(self):
        original=self.pool['options'][0]['odds']
        self.pool['options'][0]['odds']=9
        self.pool['updated_at']=(self.now-timedelta(minutes=10)).isoformat()
        self.store.upsert_matches([self.match],self.now.isoformat())
        self.pool['options'][0]['odds']=original
        self.pool['updated_at']=(self.now-timedelta(minutes=1)).isoformat()
        self.store.upsert_matches([self.match],self.now.isoformat())
        points=quotes.read(self.store,self.match['id'])['history']['had']
        self.assertEqual([p['options'][0]['odds'] for p in points],[original,9,original])
