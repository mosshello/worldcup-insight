"""Additive source identity index. Names never establish identity or eligibility."""
import json
from .store import encode, fingerprint, now_iso
from .readiness import fixture_key


class IdentityRepository:
    def __init__(self, store, initialize=True):
        self.store = store
        if not initialize:
            return
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS identity_teams(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS identity_mappings(
                    provider TEXT NOT NULL, provider_id TEXT NOT NULL, category TEXT NOT NULL,
                    team_id TEXT NOT NULL REFERENCES identity_teams(id), evidence TEXT NOT NULL,
                    revision TEXT NOT NULL, PRIMARY KEY(provider,provider_id,category));
                CREATE TABLE IF NOT EXISTS competition_participations(
                    competition_id TEXT NOT NULL, season TEXT NOT NULL, team_id TEXT NOT NULL,
                    evidence TEXT NOT NULL, PRIMARY KEY(competition_id,season,team_id));
                CREATE TABLE IF NOT EXISTS fixture_revisions(
                    match_id TEXT NOT NULL, revision TEXT NOT NULL, observed_at TEXT NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(match_id,revision));
                CREATE TABLE IF NOT EXISTS data_issues(
                    id TEXT PRIMARY KEY, competition_id TEXT NOT NULL, object_id TEXT NOT NULL,
                    kind TEXT NOT NULL, status TEXT NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS identity_links(
                    alias_id TEXT PRIMARY KEY REFERENCES identity_teams(id),
                    canonical_id TEXT NOT NULL REFERENCES identity_teams(id),
                    evidence TEXT NOT NULL, created_at TEXT NOT NULL);
            ''')

    def bind(self, provider, provider_id, name, evidence, team_id=None, category='senior_men'):
        if not all(isinstance(v, str) and v for v in (provider, str(provider_id), name, category)) or not evidence:
            raise ValueError('球队映射缺少身份或证据')
        provider_id = str(provider_id)
        team_id = team_id or provider + ':' + category + ':' + provider_id
        revision = fingerprint({'team_id':team_id, 'evidence':evidence})
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT team_id FROM identity_mappings WHERE provider=? AND provider_id=? AND category=?',
                             (provider, provider_id, category)).fetchone()
            existing = db.execute('SELECT category FROM identity_teams WHERE id=?',(team_id,)).fetchone()
            if existing and existing['category'] != category:
                raise ValueError('球队类别冲突')
            if old and self._canonical(db,old['team_id']) != self._canonical(db,team_id):
                raise ValueError('球队来源映射冲突')
            db.execute('INSERT OR IGNORE INTO identity_teams VALUES(?,?,?)', (team_id,name,category))
            db.execute('INSERT OR IGNORE INTO identity_mappings VALUES(?,?,?,?,?,?)',
                       (provider,provider_id,category,team_id,encode(evidence),revision))
        return team_id

    @staticmethod
    def _canonical(db, team):
        seen=set()
        while True:
            if team in seen:
                raise ValueError('球队身份关联循环')
            seen.add(team)
            row=db.execute('SELECT canonical_id FROM identity_links WHERE alias_id=?',(team,)).fetchone()
            if not row:
                return team
            team=row['canonical_id']

    def link(self, alias, canonical, evidence):
        """Append a verified relationship; old IDs, mappings and archives remain intact."""
        if not evidence:
            raise ValueError('球队关联缺少核验证据')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            alias,canonical=self._canonical(db,alias),self._canonical(db,canonical)
            if alias==canonical:
                return canonical
            rows=db.execute('SELECT category FROM identity_teams WHERE id IN (?,?)',(alias,canonical)).fetchall()
            if len(rows)!=2 or len({r['category'] for r in rows})!=1:
                raise ValueError('球队关联类别冲突')
            providers={}
            for row in db.execute('SELECT * FROM identity_mappings'):
                if self._canonical(db,row['team_id']) in (alias,canonical):
                    ids=providers.setdefault(row['provider'],set());ids.add(row['provider_id'])
                    if len(ids)>1:
                        raise ValueError('球队关联来源标识冲突')
            db.execute('INSERT INTO identity_links VALUES(?,?,?,?)',(alias,canonical,encode(evidence),now_iso()))
        return canonical

    def index_context(self, match, context):
        from .catalog import catalog, classify
        from .sources import parse_date
        from .quality import age_seconds
        evidence=context.get('espn_identity') or {}
        cid=classify(match)['id']
        spec=next((s for s in catalog() if s['id']==cid),{})
        try:
            valid=(evidence.get('fixture')==fixture_key(match) and evidence.get('provider')=='espn'
                and evidence.get('competition_slug')==spec.get('providers',{}).get('espn')
                and parse_date(evidence['kickoff'])==parse_date(match['kickoff'])
                and 0<=age_seconds(evidence.get('captured_at'))<=180
                and evidence.get('event_id') and evidence.get('source_url') and not context.get('stale'))
        except (KeyError,TypeError,ValueError):
            valid=False
        if not valid:
            return False
        if len(set(str(v) for v in (evidence.get('team_ids') or {}).values())) != 2:
            return False
        for side in ('home','away'):
            sourceid=(evidence.get('team_ids') or {}).get(side)
            if not sourceid or not match.get(side+'_id'):
                return False
        for side in ('home','away'):
            canonical=self.bind(match.get('provider') or 'sporttery',str(match[side+'_id']),match[side],evidence)
            alias=self.bind('espn',str(evidence['team_ids'][side]),match[side],evidence)
            try:
                self.link(alias,canonical,evidence)
            except ValueError as exc:
                self.issue(cid,match['id'],'identity_conflict',str(exc),evidence)
                return False
        return True

    def participate(self, cid, season, team_id, evidence):
        if not season:
            return
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO competition_participations VALUES(?,?,?,?)',
                       (cid,str(season),team_id,encode(evidence)))

    def record_fixture(self, match):
        value = fixture_key(match)
        revision = fingerprint(value)
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO fixture_revisions VALUES(?,?,?,?)',
                       (match['id'],revision,now_iso(),encode(value)))
        return revision

    def index_matches(self, matches):
        from .catalog import classify
        for match in matches:
            spec = classify(match)
            self.record_fixture(match)
            for side in ('home','away'):
                if not match.get(side+'_id'):
                    continue
                evidence = {'match_id':match['id'], 'observed_at':match.get('captured_at'),
                            'fixture_revision':fingerprint(fixture_key(match))}
                team_id = self.bind(match.get('provider') or 'sporttery', str(match[side+'_id']), match[side], evidence)
                self.participate(spec['id'],spec.get('season'),team_id,evidence)

    def index_dataset(self, cid, data):
        mappings = {}
        digest = fingerprint(data)
        for row in data.get('team_mappings', []):
            if row.get('competition_id') == cid:
                mappings.setdefault(row['team_id'], []).append(row)
        seen = set()
        for row in data['matches']:
            for side in ('home','away'):
                key = (row['season'],row[side+'_id'])
                if key in seen:
                    continue
                seen.add(key)
                links = mappings.get(row[side+'_id'], [])
                preferred = next((r for r in links if r['provider']=='sporttery'), None) or next((r for r in links if r['provider']=='espn'), None)
                provider = preferred['provider'] if preferred else 'training:'+cid
                provider_id = str(preferred['provider_id']) if preferred else str(row[side+'_id'])
                if not preferred and ':' in provider_id:
                    namespace,source_id=provider_id.split(':',1)
                    if namespace in ('espn','500') and source_id:
                        provider,provider_id=namespace,source_id
                evidence = {'dataset_digest':digest, 'source_url':row.get('source_url'), 'row_id':row['id']}
                team = self.bind(provider,provider_id,row.get(side) or row.get(side+'_name') or str(row[side+'_id']),evidence)
                for link in links:
                    alias=self.bind(link['provider'],str(link['provider_id']),row.get(side) or row.get(side+'_name') or str(row[side+'_id']),evidence)
                    try:
                        self.link(alias,team,evidence)
                    except ValueError as exc:
                        self.issue(cid,row['id'],'identity_conflict',str(exc),evidence)
                self.participate(cid,row['season'],team,evidence)

    def teams(self, cid):
        with self.store.connect() as db:
            rows = db.execute('SELECT p.*,t.name FROM competition_participations p JOIN identity_teams t ON t.id=p.team_id WHERE p.competition_id=? ORDER BY p.season DESC,t.name', (cid,)).fetchall()
            mappings = db.execute('SELECT * FROM identity_mappings').fetchall()
            canonical={r['team_id']:self._canonical(db,r['team_id']) for r in [*rows,*mappings]}
        values={}
        for r in rows:
            team=canonical[r['team_id']]
            values.setdefault((team,r['season']),{'id':team,'name':r['name'],'season':r['season'],'status':'已登记',
                'source_ids':{m['provider']:m['provider_id'] for m in mappings if canonical[m['team_id']]==team},
                'evidence':json.loads(r['evidence'])})
        return list(values.values())

    def issue(self, cid, object_id, kind, reason, evidence=None, status='open'):
        value = {'name':object_id,'reason':reason,'evidence':evidence,'status':status}
        key = fingerprint([cid,object_id,kind,evidence])
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO data_issues VALUES(?,?,?,?,?,?,?)',
                       (key,cid,object_id,kind,status,now_iso(),encode(value)))
        return key

    def issues(self, cid):
        with self.store.connect() as db:
            rows = db.execute('SELECT * FROM data_issues WHERE competition_id=? ORDER BY observed_at DESC', (cid,)).fetchall()
        return [{**json.loads(r['payload']), 'id':r['id'], 'kind':r['kind'], 'observed_at':r['observed_at']} for r in rows]
