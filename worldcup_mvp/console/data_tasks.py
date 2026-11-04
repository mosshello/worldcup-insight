"""Durable, competition-scoped history collection; never imports training data."""
import json
import hashlib
import threading
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .catalog import catalog
from .competition_rules import file_lock
from .store import now_iso, fingerprint
from .training import atomic_json


class DataTasks:
    workers = {}
    mutex = threading.RLock()
    espn_competitions = {'uefa.ucl', 'uefa.uel', 'eng.championship'}
    supported = {'jp.j1', 'kr.k1', 'afc.champions'} | espn_competitions
    heartbeat_seconds = 10
    lease_seconds = 60

    def __init__(self, store):
        self.store = store
        self.root = Path(store.path).parent / 'ingestion'
        self.owner = uuid.uuid4().hex
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS ingestion_tasks(
                id TEXT PRIMARY KEY, competition_id TEXT NOT NULL, request_key TEXT NOT NULL,
                request_digest TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL,
                UNIQUE(competition_id,request_key))''')

    def _scope(self, cid):
        if cid not in self.supported:
            raise ValueError('该赛事尚无历史采集适配器')
        spec = next((s for s in catalog() if s['id'] == cid), None)
        if not spec:
            raise ValueError('赛事未登记')
        return spec

    def _save(self, task):
        with self.mutex:
            task['updated_at'] = now_iso()
            if task['status'] == 'running':
                task['heartbeat_at'] = task['updated_at']
                task['lease_until'] = (datetime.now(timezone.utc)+timedelta(seconds=self.lease_seconds)).isoformat()
                task['lease_owner'] = self.owner
            elif task['status'] not in ('queued',):
                task['lease_until'] = None
            with self.store.connect() as db:
                db.execute('UPDATE ingestion_tasks SET payload=? WHERE id=?',
                           (json.dumps(task, ensure_ascii=False), task['id']))

    def list(self, cid, reconcile=False):
        self._scope(cid)
        with self.store.connect() as db:
            rows = db.execute('SELECT payload FROM ingestion_tasks WHERE competition_id=? ORDER BY created_at DESC', (cid,)).fetchall()
        tasks = [json.loads(row['payload']) for row in rows]
        if reconcile:
            try:
                with file_lock(self.root/cid/'ingestion.lock'):
                    for task in tasks:
                        key = (str(self.store.path.resolve()), task['id'])
                        worker = self.workers.get(key)
                        age = (datetime.now(timezone.utc)-datetime.fromisoformat(task['updated_at'])).total_seconds()
                        if task['status'] in ('queued', 'running') and not (worker and worker.is_alive()) and age > 60:
                            task.update(status='interrupted', error='采集进程已中断，保留已完成记录')
                            self._save(task)
            except ValueError:
                pass  # A different process owns the collection lock.
        return tasks

    def start(self, cid, body):
        spec = self._scope(cid)
        if not isinstance(body, dict) or set(body)-{'request_key', 'years', 'seasons', 'retry_of'}:
            raise ValueError('采集参数无效')
        parent = None
        retry_of = body.get('retry_of')
        if retry_of is not None:
            if not isinstance(retry_of,str):
                raise ValueError('重试任务标识无效')
            # Explicit POST may reconcile an expired orphan; ordinary GET never does.
            self.list(cid,reconcile=True)
            with self.store.connect() as db:
                record = db.execute('SELECT payload FROM ingestion_tasks WHERE id=? AND competition_id=?',(retry_of,cid)).fetchone()
            parent = json.loads(record['payload']) if record else None
            if not parent or parent['status'] not in ('failed','interrupted'):
                raise ValueError('仅允许重试本赛事失败或中断的采集任务')
            if any(k in body for k in ('years','seasons')):
                raise ValueError('重试保留原任务范围；修改范围请新建采集任务')
            body = {**parent['request'], **body}
        request_key = body.get('request_key') or uuid.uuid4().hex
        if not isinstance(request_key, str) or len(request_key) > 160:
            raise ValueError('请求标识无效')
        if parent and request_key == parent['request_key']:
            raise ValueError('重试须使用新的请求标识')
        if cid == 'afc.champions' or cid in self.espn_competitions:
            if 'seasons' in body:
                raise ValueError('该来源按日历年采集')
            year = datetime.now(timezone.utc).year
            years = body.get('years', spec.get('history_years', list(range(year-2, year+1))))
            if (not isinstance(years, list) or not years or len(years)>30
                    or any(type(y) is not int or not 2000<=y<=year for y in years)):
                raise ValueError('采集年份无效')
            request = {'years': sorted(set(years))}
        else:
            from .history_pipeline import SEASONS
            known = {s[1] for s in SEASONS if s[0] == cid}
            seasons = body.get('seasons', sorted(known))
            if 'years' in body or not isinstance(seasons, list) or not seasons or any(not isinstance(s,str) or s not in known for s in seasons):
                raise ValueError('采集赛季无效')
            request = {'seasons': sorted(set(seasons))}
        digest = fingerprint({**request,'retry_of':retry_of}) if parent else fingerprint(request)
        with self.store.connect() as db:
            prior = db.execute('SELECT request_digest,payload FROM ingestion_tasks WHERE competition_id=? AND request_key=?', (cid, request_key)).fetchone()
        if prior:
            if prior['request_digest'] != digest:
                raise ValueError('请求标识已用于不同采集参数')
            return json.loads(prior['payload'])
        # Short queue lock. Worker takes the same file lock for the entire collection;
        # network operations never hold a SQLite transaction.
        with self.mutex, file_lock(self.root/cid/'ingestion.lock'):
            with self.store.connect() as db:
                prior = db.execute('SELECT request_digest,payload FROM ingestion_tasks WHERE competition_id=? AND request_key=?', (cid, request_key)).fetchone()
                if prior:
                    if prior['request_digest'] != digest:
                        raise ValueError('请求标识已用于不同采集参数')
                    return json.loads(prior['payload'])
                for record in db.execute('SELECT payload FROM ingestion_tasks WHERE competition_id=?', (cid,)):
                    active = json.loads(record['payload'])
                    if active['status'] in ('queued', 'running'):
                        raise ValueError('该赛事已有采集任务，请检查任务状态')
                task = {'id': uuid.uuid4().hex, 'kind': 'ingestion', 'competition_id': cid,
                        'status': 'queued', 'created_at': now_iso(), 'updated_at': now_iso(),
                        'request_key': request_key, 'request': request, 'checkpoint': {},
                        'rules_revision': fingerprint(spec), 'result': None, 'error': None}
                task.update(retry_of=retry_of, resume_mode='verified_cache_replay' if parent else 'new_collection',
                            reused_responses=0, lease_owner=None, lease_until=None, heartbeat_at=None)
                qualifying_years = [y for y in spec.get('research_policy',{}).get('qualifying_years',[])
                                    if y in request.get('years',[])]
                task['source_policy'] = parent.get('source_policy',{}) if parent else {
                    'allow_qualifying':cid=='uefa.ucl' and bool(qualifying_years),
                    'qualifying_years':qualifying_years}
                db.execute('INSERT INTO ingestion_tasks VALUES(?,?,?,?,?,?)',
                    (task['id'],cid,request_key,digest,task['created_at'],json.dumps(task,ensure_ascii=False)))
            key = (str(self.store.path.resolve()),task['id'])
            worker = threading.Thread(target=self._run, args=(task,), daemon=True, name='ingestion-'+task['id'])
            self.workers[key] = worker
        try:
            worker.start()
        except Exception as exc:
            self.workers.pop(key, None)
            task.update(status='failed',error=str(exc)); self._save(task)
            raise
        return task.copy()

    def _checkpoint(self, task, value):
        task['checkpoint'] = value
        self._save(task)

    def _parent_raw(self, task):
        parent = task.get('retry_of')
        if not parent or len(parent)!=32 or any(c not in '0123456789abcdef' for c in parent):
            return None
        return self.root/task['competition_id']/'runs'/parent/'raw'

    @staticmethod
    def _recent(stamp, seconds):
        try:
            return 0 <= (datetime.now(timezone.utc)-datetime.fromisoformat(stamp)).total_seconds() <= seconds
        except (ValueError,TypeError):
            return False

    def _reuse_japan_korea(self, task, directory):
        parent = self._parent_raw(task)
        if parent is None or not parent.exists():
            return
        for meta_path in parent.rglob('*.json'):
            try:
                meta=json.loads(meta_path.read_text(encoding='utf8'))
                raw=meta_path.with_suffix('.body')
                scope=raw.relative_to(parent).parts[0]
                current_day=datetime.now(timezone.utc).strftime('%Y-%m-%d')
                if scope not in ('completed_seasons',current_day):
                    continue
                ttl=86400 if scope=='completed_seasons' else 180
                if meta.get('status')!=200 or not self._recent(meta.get('collected_at'),ttl) or not raw.is_file():
                    continue
                content=raw.read_bytes()
                body=json.dumps(meta['body'],sort_keys=True).encode() if meta.get('body') is not None else b''
                if hashlib.sha256(meta['url'].encode()+body).hexdigest()!=meta_path.stem:
                    continue
                if hashlib.sha256(content).hexdigest()!=meta.get('sha256'):
                    continue
                content.decode(meta['encoding'])
                target=directory/'raw'/raw.relative_to(parent)
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(content)
                atomic_json(target.with_suffix('.json'),{**meta,'raw_file':str(target),'reused_from':str(raw)})
                task['reused_responses']+=1
                task['cache_reuse_basis']='verified_archive_files_preloaded'
            except (ValueError,TypeError,KeyError,OSError):
                continue

    def _fetch_acl(self, task, endpoint, directory, source=None):
        from .espn_history import BASE, fetch_receipt
        if source is not None:
            from . import espn_competition_history as provider
            if provider.SOURCE_SPECS.get(source,{}).get('competition_id') != task['competition_id']:
                raise ValueError('采集来源与赛事不一致')
            BASE = provider.BASE+source+'/'
            fetch_receipt = lambda query, folder: provider.fetch(source,query,folder)
        parent=self._parent_raw(task)
        if parent is not None and parent.exists():
            for path in parent.glob('*.receipt.json'):
                try:
                    receipt=json.loads(path.read_text(encoding='utf8'))
                    # Current-year scoreboard facts change throughout the day.
                    max_age=180 if endpoint.startswith('scoreboard?dates='+str(datetime.now(timezone.utc).year)) else 86400
                    if (receipt.get('url') != BASE+endpoint or receipt.get('status')!='succeeded'
                            or receipt.get('http_status')!='200' or not self._recent(receipt.get('observed_at'),max_age)):
                        continue
                    raw=Path(receipt['raw_path']).resolve()
                    if not raw.is_relative_to(parent.resolve()):
                        continue
                    content=raw.read_bytes()
                    if hashlib.sha256(content).hexdigest()!=receipt.get('sha256'):
                        continue
                    value=json.loads(content)
                    key=uuid.uuid4().hex;target=directory/'raw'/(key+'.raw')
                    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
                    reused={**receipt,'id':key,'raw_path':str(target),'reused_from':str(path),'reused_at':now_iso()}
                    atomic_json(target.with_suffix('.receipt.json'),reused)
                    task['reused_responses']+=1
                    return value,reused
                except (ValueError,TypeError,KeyError,OSError):
                    continue
        return fetch_receipt(endpoint,directory/'raw')

    def _collect(self, task, directory):
        cid = task['competition_id']
        if cid in self.espn_competitions:
            from .espn_competition_history import collect_competition
            policy = task.get('source_policy',{})
            data = collect_competition(cid,task['request']['years'],directory,
                progress=lambda progress:self._checkpoint(task,progress),
                fetcher=lambda source,endpoint,raw:self._fetch_acl(task,endpoint,directory,source),
                allow_qualifying=policy.get('allow_qualifying',False),
                qualifying_years=policy.get('qualifying_years',[]))
            rows = data['matches']
            if not rows or any(r.get('competition_id') != cid for r in rows):
                raise ValueError('采集结果为空或混入其他赛事')
            manifest = data['manifest']
            result = {'unique_count':manifest['unique_source_events'],'admitted':len(rows),
                'quarantined':len(manifest['excluded']),'empty':False,
                'candidate_path':str(directory/'candidate.json'),'coverage':data['coverage'],
                'receipts':manifest.get('source_receipts',[]),'source_policy':policy}
            task['result'] = result
            return result
        if cid != 'afc.champions':
            self._reuse_japan_korea(task,directory)
            from .history_pipeline import collect, SEASONS
            specs = [s for s in SEASONS if s[0] == cid and s[1] in task['request']['seasons']]
            data = collect(output=directory/'staging.json', specs=specs, raw_root=directory/'raw',
                           progress=lambda coverage: self._checkpoint(task, coverage))
            rows = data.get('matches', [])
            result = {'original_count': len(rows), 'unique_count': len({r['id'] for r in rows}),
                      'admitted': 0, 'quarantined': len(rows), 'empty': not rows,
                      'staging_path': str(directory/'staging.json'), 'coverage': data.get('coverage', [])}
            task['result'] = result
            if (not rows or data.get('status') != 'collected' or len(data.get('coverage',[])) != len(specs)
                    or any(c.get('issues') or c.get('conflicts') or not c.get('structure_ok') for c in data.get('coverage', []))):
                raise ValueError('采集返回空数据或存在未完成核查，未更新当前采集版本')
            if any(r.get('competition_id') != cid for r in rows):
                raise ValueError('采集混入其他赛事')
            return result
        from .espn_history import prepare
        boards, summaries, receipts = [], {}, []
        for year in task['request']['years']:
            board, receipt = self._fetch_acl(task,'scoreboard?dates='+str(year)+'&limit=1000', directory)
            receipts.append(receipt)
            if not isinstance(board.get('events'), list) or not board['events']:
                task['result'] = {'empty': board.get('events') == [], 'receipts': receipts}
                raise ValueError('来源返回空赛程或格式错误，未更新当前采集版本')
            boards.append(board)
            self._checkpoint(task, {'phase': 'scoreboards', 'completed_year': year, 'receipts': receipts.copy()})
        events = {str(e.get('id')): e for b in boards for e in b['events']}
        for eid, event in events.items():
            season = event.get('season', {})
            comp = event.get('competitions', [{}])[0]
            if (season.get('year') in (2020,2021,2022) or season.get('slug') not in ('group-stage','league-stage')
                    or comp.get('status', {}).get('type', {}).get('name') != 'STATUS_FULL_TIME'):
                continue
            summary, receipt = self._fetch_acl(task,'summary?event='+eid, directory)
            receipts.append(receipt)
            summaries[eid] = {'header': summary.get('header', {})}
            self._checkpoint(task, {'phase': 'summaries', 'completed_event': eid, 'completed': len(summaries),
                                    'receipt_count': len(receipts), 'last_receipt': receipt})
        data = prepare(boards,summaries,now_iso(),'Explicit ingestion task '+task['id'])
        data['manifest']['source_receipts'] = receipts
        atomic_json(directory/'candidate.json', data)
        result = {'original_count': sum(len(b['events']) for b in boards),
                  'unique_count': data['manifest']['unique_source_events'], 'admitted': len(data['matches']),
                  'quarantined': len(data['manifest']['excluded']), 'empty': not data['matches'],
                  'candidate_path': str(directory/'candidate.json'), 'coverage': data['coverage'],
                  'receipts': receipts}
        task['result'] = result
        bad = {'summary_identity_unverified','summary_identity_mismatch','summary_result_conflict',
               'competition_identity_mismatch','fixture_identity_mismatch','score_invalid'}
        if not data['matches'] or any(e['reason'] in bad for e in data['manifest']['excluded']):
            raise ValueError('采集存在空数据或来源核验失败，未更新当前采集版本')
        return result

    def _run(self, task):
        key = (str(self.store.path.resolve()),task['id'])
        stop=threading.Event()
        heartbeat=None
        try:
            with file_lock(self.root/task['competition_id']/'ingestion.lock'):
                task['status'] = 'running'; self._save(task)
                def pulse():
                    while not stop.wait(self.heartbeat_seconds):
                        try:
                            self._save(task)
                        except Exception:
                            # OS lock remains authoritative even if a heartbeat write fails.
                            pass
                heartbeat=threading.Thread(target=pulse,daemon=True,name='ingestion-heartbeat-'+task['id'])
                heartbeat.start()
                directory = self.root/task['competition_id']/'runs'/task['id']
                directory.mkdir(parents=True, exist_ok=False)
                task['output_dir'] = str(directory)
                self._save(task)
                result = self._collect(task, directory)
                task['resume_mode'] = ('verified_cache_replay' if task.get('reused_responses') else 'restart') if task.get('retry_of') else 'new_collection'
                result['reused_responses']=task.get('reused_responses',0)
                atomic_json(directory/'result.json', result)
                task.update(status='succeeded', result=result)
                self._save(task)
                atomic_json(self.root/task['competition_id']/'latest.json',
                            {'task_id': task['id'], 'result_path': str(directory/'result.json'),
                             'result_digest': fingerprint(result), 'updated_at': now_iso()})
        except Exception as exc:
            if task.get('retry_of'):
                task['resume_mode']='verified_cache_replay' if task.get('reused_responses') else 'restart'
            task.update(status='failed', error=str(exc)); self._save(task)
        finally:
            stop.set()
            if heartbeat:
                heartbeat.join(timeout=2)
            self.workers.pop(key, None)
