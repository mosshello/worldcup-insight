"""Bounded background collector; persisted receipts/cache survive worker restart."""
import json
import threading
import urllib.request
from datetime import timedelta
from pathlib import Path
from .observations import stamp,iso,COMPETITIONS
from .store import encode,fingerprint
from .observation_handicap import fetch_board,adapt_board,fetch_infer_odds,adapt_infer,bind_fixture,INFER_LEAGUES
from .observation_market_research import analyze_timeline,fit_rule,save_artifact

class HandicapCollector:
    def __init__(self,runtime):
        self.runtime=runtime;self.store=runtime.store;self.lock=threading.Lock()
        with self.store.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS observation_handicap_cache(
                id TEXT PRIMARY KEY,checked_at TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observation_handicap_receipts(
                digest TEXT PRIMARY KEY,provider TEXT NOT NULL,received_at TEXT NOT NULL,payload TEXT NOT NULL);''')

    def cache(self,key):
        with self.store.connect() as db:row=db.execute('SELECT * FROM observation_handicap_cache WHERE id=?',(key,)).fetchone()
        return dict(row) if row else None

    def put(self,key,payload,now):
        with self.store.connect() as db:db.execute('INSERT OR REPLACE INTO observation_handicap_cache VALUES(?,?,?)',(key,iso(now),encode(payload)))

    def receipt(self,provider,payload,now):
        digest=fingerprint(payload)
        with self.store.connect() as db:db.execute('INSERT OR IGNORE INTO observation_handicap_receipts VALUES(?,?,?,?)',(digest,provider,iso(now),encode(payload)))
        return digest

    def ingest(self,result):
        for binding in result['bindings']:
            for quote in binding['quotes']:self.runtime.workspace.ingest_quotes(binding['fixture_id'],quote)

    def infer(self,fixtures,aliases,now):
        # Persist attempts as well as successes, so failures/restarts cannot spin.
        league=sorted(INFER_LEAGUES,key=lambda key:(self.cache('league:'+key) or {}).get('checked_at',''))[0]
        cached=self.cache('league:'+league)
        if not cached or (now-stamp(cached['checked_at'])).total_seconds()>=900:
            payload=json.loads(cached['payload']) if cached else {'data':[]}
            try:
                url=f'https://api.infersports.dev/v1/events?league={league}&status=scheduled&limit=200'
                with urllib.request.urlopen(url,timeout=8) as response:raw=response.read(2_000_001)
                if len(raw)>2_000_000:raise ValueError('response size')
                payload=json.loads(raw)
                if not isinstance(payload.get('data'),list):raise ValueError('schema')
                payload['_received_at']=iso();payload['_receipt']=self.receipt('infersports_discovery',payload,stamp())
                payload['_state']='available'
            except Exception as exc:payload={**payload,'_state':'failed','_error_type':type(exc).__name__}
            self.put('league:'+league,payload,now)
        events=[];unmapped=0;discovery_failures=0
        for lid in INFER_LEAGUES:
            row=self.cache('league:'+lid)
            if not row:continue
            packet=json.loads(row['payload']);discovery_failures+=packet.get('_state')=='failed'
            for event in packet.get('data',[]):
                if event.get('league',{}).get('id')!=lid:continue
                match={'id':event['id'],'league':INFER_LEAGUES[lid],'home':event['home_team']['name'],
                    'away':event['away_team']['name'],'kickoff_utc':event['scheduled_at']}
                if stamp(event['scheduled_at'])<=now:continue
                if bind_fixture(match,fixtures,aliases):events.append(event)
                else:unmapped+=1
        events.sort(key=lambda e:((self.cache('quote:'+e['id']) or {}).get('checked_at',''),e['scheduled_at'],e['id']))
        selected=[]
        for event in events:
            cached=self.cache('quote:'+event['id'])
            if not cached or (now-stamp(cached['checked_at'])).total_seconds()>=300:selected.append(event)
        fetched=0;bound=0;rejected={};receipts=[]
        for event in selected[:3]:
            try:
                packet=fetch_infer_odds(event['id'],timeout=8);received=stamp()
                digest=self.receipt('infersports_odds',{'event':event,'packet':packet},received);receipts.append(digest)
                result=adapt_infer(event,packet,fixtures,received,aliases)
                self.ingest(result);bound+=len(result['bindings'])
                for key,count in result['rejected'].items():rejected[key]=rejected.get(key,0)+count
                self.put('quote:'+event['id'],{'state':'available' if result['bindings'] else 'rejected','receipt':digest,'rejected':result['rejected']},received)
            except Exception as exc:
                self.put('quote:'+event['id'],{'state':'failed','error_type':type(exc).__name__},stamp())
                rejected['request_failed']=rejected.get('request_failed',0)+1
            fetched+=1
        # Retain last evidence through cache-only cycles; report cycle count separately.
        status={'status':'available' if bound else 'no_fresh_coverage','provider':'InferSports','matched_fixtures':len(events),
            'unmapped_fixtures':unmapped,'discovery_failures':discovery_failures,'requests_this_cycle':fetched,
            'fixtures_this_cycle':bound,'rejected_this_cycle':rejected,'receipts':receipts,'rate_policy':'1 discovery + 3 odds / minute; per event >= 300 seconds'}
        with self.store.connect() as db:
            states=[json.loads(r[0]) for r in db.execute("SELECT payload FROM observation_handicap_cache WHERE id LIKE 'quote:%'")]
        status['stored_rejected_records']=sum(p.get('state')=='rejected' for p in states)
        status['stored_failed_records']=sum(p.get('state')=='failed' for p in states)
        self.runtime.status('asian-handicap:infersports',status)
        return status

    def research(self,now):
        cached=self.cache('research')
        if cached and (now-stamp(cached['checked_at'])).total_seconds()<900:return json.loads(cached['payload'])
        # Completed UTC day boundary; fixture labels crossing it are purged.
        train_end=now.replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=1)
        reports=[]
        directory=Path(self.store.path).parent/'market_research'
        for cid in COMPETITIONS:
            events=[];covered=0;fixtures=0
            with self.store.connect() as db:
                for row in db.execute("SELECT * FROM observation_fixture_versions WHERE json_extract(payload,'$.competition_id')=?",(cid,)).fetchall():
                    fixture=json.loads(row['payload']);quotes=[json.loads(q[0]) for q in db.execute('SELECT payload FROM observation_quotes WHERE fixture_id=? AND revision=?',(row['id'],row['revision']))]
                    timeline=analyze_timeline(fixture,quotes,now);events+=timeline['events'];covered+=timeline['covered_seconds'];fixtures+=1
            report=fit_rule(events,cid,train_end,now);path=save_artifact(report,directory)
            reports.append({'competition_id':cid,'status':report['status'],'artifact_id':report['artifact_id'],
                'artifact_path':str(path),'covered_fixture_seconds':covered,'fixture_versions':fixtures,'events':len(events),
                'threshold':report['threshold'],'monitoring_validated':report['monitoring_validated'],'production_enabled':False,
                'train':report['train'],'validation':report['validation']})
        value={'status':'available','asof':iso(now),'target':'price_persistence_not_match_accuracy','reports':reports}
        self.put('research',value,now);self.runtime.status('market-research',value)
        return value

    def sync(self):
        if not self.lock.acquire(blocking=False):return {'status':'running'}
        try:
            now=stamp()
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                previous=db.execute("SELECT checked_at FROM observation_handicap_cache WHERE id='cycle'").fetchone()
                if previous and (now-stamp(previous['checked_at'])).total_seconds()<60:return {'status':'cached'}
                db.execute('INSERT OR REPLACE INTO observation_handicap_cache VALUES(?,?,?)',('cycle',iso(now),encode({'status':'started'})))
            with self.store.connect() as db:fixtures=[json.loads(r[0]) for r in db.execute('SELECT payload FROM observation_fixtures')]
            fixtures=[f for f in fixtures if f.get('status') in ('scheduled','upcoming','not_started','pre','未开赛')]
            aliases={}
            for f in fixtures:
                for side in ('home','away'):aliases.setdefault(str(f[side+'_id']),set()).update([f[side],f.get(side+'_name',f[side])])
            try:
                board=fetch_board(timeout=8);receipt=self.receipt('moviodds',board,stamp());result=adapt_board(board,fixtures,aliases=aliases)
                self.ingest(result)
                self.runtime.status('asian-handicap',{'status':'available' if result['bindings'] else 'no_coverage',
                    'provider':'MoviOdds','captured_at':result.get('captured_at'),'fixtures':len(result['bindings']),'rejected':result['rejected'],'receipt':receipt})
            except Exception as exc:self.runtime.status('asian-handicap',{'status':'failed','error':'亚洲盘采集失败','error_type':type(exc).__name__})
            try:self.infer(fixtures,aliases,now)
            except Exception as exc:self.runtime.status('asian-handicap:infersports',{'status':'failed','error':'亚洲盘采集失败','error_type':type(exc).__name__})
            try:self.research(stamp())
            except Exception as exc:self.runtime.status('market-research',{'status':'failed','error':'盘口研究刷新失败','error_type':type(exc).__name__})
            return {'status':'completed'}
        finally:self.lock.release()
