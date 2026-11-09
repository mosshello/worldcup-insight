"""Current source-listed rosters, never registration or retrospective eligibility."""
import json
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from .sources import get_json
from .store import encode,fingerprint
from .observations import stamp,iso

SLUGS={'eng.premier':'eng.1','esp.laliga':'esp.1','ger.bundesliga':'ger.1','ita.serie_a':'ita.1','fra.ligue1':'fra.1'}
SCHEMA='observed-current-roster-v1'

def roster_url(cid,team_id):
    if cid not in SLUGS or not team_id.startswith('espn:') or not team_id[5:].isdigit():raise ValueError('球队或赛事来源ID无效')
    return f'https://site.api.espn.com/apis/site/v2/sports/soccer/{SLUGS[cid]}/teams/{team_id[5:]}/roster'

def normalize_roster(cid,team_id,season,raw,captured_at,ttl_hours=24):
    url=roster_url(cid,team_id);captured=stamp(captured_at)
    if raw.get('status')!='success' or str(raw.get('team',{}).get('id'))!=team_id[5:]:raise ValueError('名册球队身份不匹配')
    if str(raw.get('season',{}).get('year'))!=str(season):raise ValueError('名册赛季不匹配')
    if not isinstance(raw.get('athletes'),list):raise ValueError('名册缺少球员列表')
    if not 0<ttl_hours<=24:raise ValueError('当前名单有效期超过24小时')
    source_time=raw.get('timestamp')
    if source_time and stamp(source_time)>captured:raise ValueError('来源时间在采集时间之后')
    players=[];seen=set()
    for athlete in raw['athletes']:
        ident=str(athlete.get('id',''))
        if not ident.isdigit() or athlete.get('uid')!=f's:600~a:{ident}' or ident in seen:raise ValueError('球员来源ID缺失、冲突或重复')
        seen.add(ident)
        injuries=athlete.get('injuries')
        players.append({'player_id':'espn:'+ident,'provider_uid':athlete['uid'],'name':athlete.get('displayName') or athlete.get('fullName'),
            'team_id':team_id,'membership':'listed_by_source','position':athlete.get('position'),'shirt_number':athlete.get('jersey'),
            'source_status':athlete.get('status'),'injury_reports':injuries if isinstance(injuries,list) else None,
            'injury_coverage':'partial' if injuries else 'unknown','registration':None,'competition_eligibility':None,
            'match_availability':None,'suspension':None,'expected_minutes':None,'contract_start':None,'contract_end':None})
    # ESPN roster coach fields have demonstrably stale historical entries. They
    # are evidence of source content only, never a current coaching appointment.
    coaches=[{'coach_id':'espn:coach:'+str(c['id']) if str(c.get('id','')).isdigit() else None,
              'name':' '.join(str(c.get(k,'')) for k in ('firstName','lastName')).strip(),
              'relationship_status':'unverified_source_listing','current_appointment':None,'raw':c}
             for c in (raw.get('coach') or []) if isinstance(c,dict)]
    return {'schema':SCHEMA,'competition_id':cid,'team_id':team_id,'season':str(season),
        'team_name':raw['team'].get('displayName'),'captured_at':iso(captured),'available_from':iso(captured),
        'valid_until':iso(captured+timedelta(hours=ttl_hours)),'validity_basis':'application_refresh_window_not_contract_validity',
        'published_at':None,'source_response_at':source_time,'source_url':url,'source':'ESPN current team roster',
        'time_basis':'observed_only','players':players,'unverified_coach_listings':coaches,
        'coverage':{'source_listed_players':len(players),'reported_injury_players':sum(bool(p['injury_reports']) for p in players),
            'official_registration':False,'complete_injuries':False,'confirmed_current_coach':False},
        'unknown':['official_registration','match_eligibility','complete_injuries','complete_suspensions','confirmed_current_coach','contract_effective_dates'],
        'training_allowed':False,'raw_digest':fingerprint(raw)}

class CurrentRosterStore:
    def __init__(self,store,fetcher=get_json):
        self.store=store;self.fetcher=fetcher
        with store.connect() as db:db.executescript('''
            CREATE TABLE IF NOT EXISTS observation_roster_receipts(digest TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS observation_roster_snapshots(id TEXT PRIMARY KEY,competition_id TEXT NOT NULL,team_id TEXT NOT NULL,season TEXT NOT NULL,captured_at TEXT NOT NULL,valid_until TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS observation_roster_lookup ON observation_roster_snapshots(competition_id,team_id,season,captured_at);
            CREATE TABLE IF NOT EXISTS observation_roster_attempts(id TEXT PRIMARY KEY,attempted_at TEXT NOT NULL,status TEXT NOT NULL,error_type TEXT);''')

    def ingest(self,cid,team_id,season,raw,captured_at):
        snapshot=normalize_roster(cid,team_id,season,raw,captured_at);ident=fingerprint(snapshot)
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO observation_roster_receipts VALUES(?,?)',(snapshot['raw_digest'],encode(raw)))
            db.execute('INSERT OR IGNORE INTO observation_roster_snapshots VALUES(?,?,?,?,?,?,?)',(ident,cid,team_id,str(season),snapshot['captured_at'],snapshot['valid_until'],encode(snapshot)))
            key=f'{cid}:{team_id}:{season}'
            db.execute('''INSERT INTO observation_roster_attempts VALUES(?,?,?,NULL)
                ON CONFLICT(id) DO UPDATE SET attempted_at=excluded.attempted_at,status=excluded.status,error_type=NULL
                WHERE observation_roster_attempts.attempted_at<=excluded.attempted_at''',(key,snapshot['captured_at'],'available'))
        return {'id':ident,**snapshot}

    def sync_team(self,cid,team_id,season,now=None):
        now=stamp(now);key=f'{cid}:{team_id}:{season}';url=roster_url(cid,team_id)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');attempt=db.execute('SELECT * FROM observation_roster_attempts WHERE id=?',(key,)).fetchone()
            wait=21600 if attempt and attempt['status']=='available' else 300
            if attempt and (now-stamp(attempt['attempted_at'])).total_seconds()<wait:return {'status':'cached','team_id':team_id}
            db.execute('INSERT OR REPLACE INTO observation_roster_attempts VALUES(?,?,?,NULL)',(key,iso(now),'running'))
        try:
            raw=self.fetcher(url,timeout=12);captured=stamp();snapshot=self.ingest(cid,team_id,season,raw,captured)
            with self.store.connect() as db:db.execute('UPDATE observation_roster_attempts SET status=?,error_type=NULL WHERE id=?',('available',key))
            return {'status':'available','team_id':team_id,'id':snapshot['id'],'players':len(snapshot['players'])}
        except Exception as exc:
            with self.store.connect() as db:db.execute('UPDATE observation_roster_attempts SET status=?,error_type=? WHERE id=?',('failed',type(exc).__name__,key))
            return {'status':'failed','team_id':team_id,'error_type':type(exc).__name__}

    def sync_current_fixtures(self,max_teams=8):
        teams={}
        with self.store.connect() as db:
            for row in db.execute('SELECT payload FROM observation_fixtures'):
                f=json.loads(row[0])
                for side in ('home','away'):teams[(f['competition_id'],f[side+'_id'],str(f.get('season',stamp(f['kickoff']).year)))]=None
            attempts={r['id']:r['attempted_at'] for r in db.execute('SELECT * FROM observation_roster_attempts')}
        selected=sorted(teams,key=lambda t:attempts.get(':'.join(t),''))[:max(0,min(96,max_teams))]
        with ThreadPoolExecutor(max_workers=4) as pool:return list(pool.map(lambda t:self.sync_team(*t),selected))

    def read_team(self,cid,team_id,season,asof):
        at=stamp(asof);roster_url(cid,team_id)
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM observation_roster_snapshots WHERE competition_id=? AND team_id=? AND season=? AND captured_at<=? ORDER BY captured_at DESC,id DESC LIMIT 1',(cid,team_id,str(season),iso(at))).fetchone()
            if not row:return {'status':'unavailable','reason':'not_observed_before_cutoff','team_id':team_id}
            snapshot=json.loads(row['payload']);receipt=db.execute('SELECT payload FROM observation_roster_receipts WHERE digest=?',(snapshot['raw_digest'],)).fetchone()
        if fingerprint(snapshot)!=row['id'] or not receipt or fingerprint(json.loads(receipt[0]))!=snapshot['raw_digest']:raise ValueError('名单快照完整性校验失败')
        if at>stamp(snapshot['valid_until']):return {'status':'expired','team_id':team_id,'captured_at':snapshot['captured_at']}
        ids={p['player_id'] for p in snapshot['players']};conflicts={}
        with self.store.connect() as db:
            others=db.execute('''SELECT payload FROM (
                SELECT payload,team_id,valid_until,row_number() OVER(PARTITION BY competition_id,team_id,season ORDER BY captured_at DESC,id DESC) AS rank
                FROM observation_roster_snapshots WHERE captured_at<=? AND season=?) WHERE rank=1 AND team_id!=? AND valid_until>=?''',(iso(at),str(season),team_id,iso(at))).fetchall()
        for other in others:
            value=json.loads(other[0])
            for player in value['players']:
                if player['player_id'] in ids:conflicts.setdefault(player['player_id'],[]).append(value['team_id'])
        return {'status':'available','id':row['id'],**snapshot,'concurrent_listing_conflicts':conflicts}

    def fixture_evidence(self,fixture,asof):
        """Read-only facts for future/current snapshots; no name-based FPL joins."""
        facts=[];unknown=[]
        for side in ('home','away'):
            value=self.read_team(fixture['competition_id'],fixture[side+'_id'],fixture['season'],asof)
            if value['status']!='available':unknown.append({'side':side,'status':value['status']});continue
            facts.append({'kind':'roster','side':side,'team_id':value['team_id'],'competition_id':value['competition_id'],
                'snapshot_id':value['id'],'captured_at':value['captured_at'],'valid_until':value['valid_until'],'source_url':value['source_url'],
                'published_at':None,'time_basis':'observed_only','raw_digest':value['raw_digest'],'relation':'players_listed_by_source',
                'player_ids':[p['player_id'] for p in value['players']],'listed_count':len(value['players']),
                'concurrent_listing_conflicts':value['concurrent_listing_conflicts'],
                'registration':None,'eligibility':None,'complete_injuries':False,'current_coach':None,'training_allowed':False})
        return {'facts':facts,'unknown':unknown,'scope':'source_listing_only'}
