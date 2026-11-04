"""Durable competition tasks with frozen inputs and independent artifact/report writes."""
from datetime import datetime, timedelta, timezone
import json
import os
import re
import sqlite3
import threading
import time
from uuid import uuid4

from . import model_registry as registry, training
from .model_methods import NAMES, FEATURE_VERSION, capabilities, restore
from .model_evaluation import evaluate, evaluate_task
from .sources import parse_date
from .store import fingerprint, now_iso


def boundary(value, *, end=False):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('日期范围无效')
    if len(value) == 10:
        value += 'T00:00:00+08:00'
        result = parse_date(value)
        return result + timedelta(days=1) if end else result
    result = parse_date(value)
    if result.tzinfo is None:
        raise ValueError('日期范围须包含时区')
    return result


def candidate(competition_id, data, config, *, cutoff, date_from=None, strategy_report=None, task=None, cold_start_policy=None):
    config = {k:config[k] for k in ('kind', 'mode', 'half_life')}
    report = {'created_at': now_iso(), 'competition_id': competition_id,
              'dataset_digest': fingerprint(data), 'model_schema': training.MODEL_SCHEMA,
              'selected_by_competition': {competition_id: config}, 'status': 'not_evaluated',
              'evaluation_type': 'unassessed_artifact', 'production_enabled': False,
              'training_range': {'date_from': date_from.isoformat() if date_from else None,
                                 'date_to': cutoff.isoformat()},
              'strategy_report_id': strategy_report,
              'task_id': task['id'] if task else None,
              'protocol_digest': task.get('protocol_digest') if task else None,
              'target': task['request']['target'] if task else None,
              'test': {'count': 0}, 'by_competition': {}}
    bundle = training.fit_bundle(data, report, now=cutoff)
    if cold_start_policy:
        report['cold_start_policy'] = cold_start_policy
        bundle['cold_start_policy'] = cold_start_policy
        bundle.pop('id',None);bundle.pop('digest',None)
        bundle['id'] = training.MODEL_SCHEMA + ':' + fingerprint(bundle)[:16]
        bundle['digest'] = fingerprint(bundle)
    report['model_id'] = bundle['id']
    run_id = uuid4().hex
    manifest = {'run_id': run_id, 'competition_id': competition_id, 'created_at': now_iso(),
                'dataset_digest': fingerprint(data), 'model_id': bundle['id'],
                'task_id': task['id'] if task else None,
                'protocol_digest': task.get('protocol_digest') if task else None,
                'model_digest': fingerprint(bundle), 'evaluation_digest': fingerprint(report)}
    manifest['digest'] = fingerprint(manifest)
    root = registry.directory(competition_id)
    run = root / 'pending_runs' / run_id
    for name, value in (('model', bundle), ('evaluation', report), ('manifest', manifest)):
        training.atomic_json(run / (name + '.json'), value)
    destination = root / 'runs' / run_id
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(run, destination)
    registry.load_run(competition_id, run_id)
    training.atomic_json(root / 'candidate.json', {'run_id': run_id})
    return manifest


def save_report(competition_id, report, task_id):
    report_id = uuid4().hex
    report = {**report, 'id': report_id, 'task_id': task_id}
    report['digest'] = fingerprint(report)
    training.atomic_json(registry.directory(competition_id) / 'reports' / (report_id + '.json'), report)
    return {'report_id': report_id, 'report_digest': report['digest'],
            'evaluation_type': report['evaluation_type'], 'test': report['test'],
            'run_id': report.get('run_id'), 'model_id': report.get('model_id')}


def fold_sink(competition_id, task):
    root = registry.directory(competition_id)
    ancestors = [task['id']] + task.get('reuse_tasks', [])
    index = {}
    verified = {}
    for parent in ancestors:
        for path in (root/'task_artifacts'/parent/'folds').glob('*.json'):
            value = registry.read(path)
            if value.get('protocol_digest') == task['protocol_digest'] and value.get('dataset_digest') == task['dataset_digest']:
                key = (value['cutoff'],fingerprint(value['configuration']),value['input_receipt']['digest'])
                index[key] = (parent,path.stem)
    def save(payload):
        if payload['competition_id'] != competition_id:
            raise ValueError('折模型赛事不一致')
        value = {**payload, 'schema': 'competition-fold-v1', 'task_id': task['id'],
                 'dataset_digest': task['dataset_digest'], 'protocol_digest': task['protocol_digest']}
        digest = fingerprint(value)
        value['digest'] = digest
        relative = 'task_artifacts/'+task['id']+'/folds/'+digest+'.json'
        path = root/relative
        if path.exists():
            if registry.read(path) != value:
                raise ValueError('已有折模型版本校验失败')
        else:
            training.atomic_json(path, value)
        index[(value['cutoff'],fingerprint(value['configuration']),value['input_receipt']['digest'])] = (task['id'],digest)
        return {'kind': 'fold', 'competition_id': competition_id, 'task_id': task['id'],
                'digest': digest, 'model_digest': value['model_digest'], 'path': relative,
                'dataset_digest': value['dataset_digest'], 'cutoff': value['cutoff']}
    def restore_fold(cid, at, config, rows):
        inputs = [r for r in rows if r['competition_id'] == cid and parse_date(r['kickoff']) < at and parse_date(r['result_available_at']) < at]
        key = (at.isoformat(),fingerprint({k:config[k] for k in ('kind','mode','half_life')}),fingerprint(inputs))
        found = index.get(key)
        if not found:
            return None
        value, model, _ = load_fold_artifact(cid,*found)
        return model, {'kind':'fold','competition_id':cid,'task_id':found[0],'digest':found[1],
                       'model_digest':value['model_digest'],'dataset_digest':value['dataset_digest'],
                       'cutoff':value['cutoff'],'path':'task_artifacts/'+found[0]+'/folds/'+found[1]+'.json'}
    def checkpoint(stage,config,rows,targets,records=None,exclusions=None):
        receipt = {'stage':stage,'configuration':{k:config[k] for k in ('kind','mode','half_life')},
                   'input_digest':fingerprint(rows),'target_digest':fingerprint(targets),
                   'dataset_digest':task['dataset_digest'],'protocol_digest':task['protocol_digest']}
        key = fingerprint(receipt)
        if records is None:
            for parent in ancestors:
                path = root/'task_artifacts'/parent/'stages'/(key+'.json')
                if not path.exists():
                    continue
                value = registry.read(path)
                if value.get('receipt') != receipt or value.get('digest') != fingerprint({k:v for k,v in value.items() if k!='digest'}):
                    raise ValueError('任务阶段断点校验失败')
                for ref in {r['artifact_ref']['digest']:r['artifact_ref'] for r in value['records']}.values():
                    if ref['digest'] in verified:
                        continue
                    if ref['kind'] == 'fold':
                        load_fold_artifact(competition_id,ref['task_id'],ref['digest'])
                    elif registry.load_run(competition_id,ref['run_id'])[0]['model_digest'] != ref['digest']:
                        raise ValueError('评估模型断点校验失败')
                    verified[ref['digest']] = True
                return value
            return None
        value = {'schema':'model-stage-v1','task_id':task['id'],'receipt':receipt,'records':records,'exclusions':exclusions}
        value['digest'] = fingerprint(value)
        path = root/'task_artifacts'/task['id']/'stages'/(key+'.json')
        if path.exists() and registry.read(path) != value:
            raise ValueError('已完成阶段不可覆盖')
        if not path.exists():
            training.atomic_json(path,value)
        return value
    save.restore_fold = restore_fold
    save.checkpoint = checkpoint
    return save


def load_fold_artifact(competition_id, task_id, digest):
    if not isinstance(task_id, str) or not re.fullmatch(r'[0-9a-f]{32}', task_id) or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('折模型标识无效')
    value = registry.read(registry.directory(competition_id)/'task_artifacts'/task_id/'folds'/(digest+'.json'))
    if (value.get('digest') != digest or fingerprint({k:v for k,v in value.items() if k!='digest'}) != digest
        or value.get('competition_id') != competition_id or value.get('task_id') != task_id
        or fingerprint(value.get('model')) != value.get('model_digest')):
        raise ValueError('折模型归属或版本校验失败')
    data = registry.dataset(competition_id, value['dataset_digest'], admission=False)
    indexed = {r['id']:r for r in data['matches']}
    try:
        rows = [indexed[row_id] for row_id in value['input_receipt']['row_ids']]
    except KeyError:
        raise ValueError('折模型输入版本缺失') from None
    cutoff = parse_date(value['cutoff'])
    if (len(rows) != value['input_receipt']['rows'] or fingerprint(rows) != value['input_receipt']['digest']
        or len({r['id'] for r in rows}) != len(rows)
        or any(r['competition_id'] != competition_id or parse_date(r['result_available_at']) >= cutoff
               or parse_date(r['kickoff']) >= cutoff for r in rows)
        or value['model'].get('mode') != 'separate' or parse_date(value['model']['trained_before']) != cutoff):
        raise ValueError('折模型输入范围或可用时间校验失败')
    return value, restore(value['model']), rows


def reusable_candidate(job, config):
    wanted = {k:config[k] for k in ('kind','mode','half_life')}
    for path in (registry.directory(job['competition_id'])/'runs').glob('*/manifest.json'):
        manifest = registry.read(path)
        if manifest.get('task_id') not in job.get('reuse_tasks',[]) or manifest.get('protocol_digest') != job['protocol_digest']:
            continue
        manifest,_,report,_ = registry.load_run(job['competition_id'],manifest['run_id'])
        if report['selected_by_competition'][job['competition_id']] == wanted:
            training.atomic_json(registry.directory(job['competition_id'])/'candidate.json',{'run_id':manifest['run_id']})
            return manifest
    return None


class ModelTasks:
    _threads = {}
    _workers = {}
    _guard = threading.RLock()

    def __init__(self, store):
        self.store = store
        self.key = str(store.path.resolve())
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS model_tasks(
                id TEXT PRIMARY KEY, competition_id TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, payload TEXT NOT NULL)''')
            if 'request_key' not in {row['name'] for row in db.execute('PRAGMA table_info(model_tasks)')}:
                db.execute('ALTER TABLE model_tasks ADD COLUMN request_key TEXT')
            db.execute('CREATE INDEX IF NOT EXISTS model_tasks_competition ON model_tasks(competition_id,created_at)')
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS model_tasks_request ON model_tasks(competition_id,request_key) WHERE request_key IS NOT NULL')

    def _write(self, job):
        job['updated_at'] = now_iso()
        with self.store.connect() as db:
            db.execute('''INSERT INTO model_tasks(id,competition_id,status,created_at,payload,request_key) VALUES(?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET status=excluded.status,payload=excluded.payload''',
                (job['id'], job['competition_id'], job['status'], job['created_at'],
                 json.dumps(job, ensure_ascii=False), job.get('request_key')))

    def _existing(self, competition_id, key, digest):
        if key is None:
            return None
        with self.store.connect() as db:
            row = db.execute('SELECT payload FROM model_tasks WHERE competition_id=? AND request_key=?', (competition_id, key)).fetchone()
        if row:
            job = json.loads(row['payload'])
            if job.get('request_digest') != digest:
                raise ValueError('同一请求标识不能用于不同任务参数')
            return job
        return None

    def list(self, competition_id, reconcile=False):
        registry.competition_spec(competition_id)
        if reconcile:
            self.reconcile(competition_id)
        with self.store.connect() as db:
            jobs = [json.loads(row['payload']) for row in db.execute(
                'SELECT payload FROM model_tasks WHERE competition_id=? ORDER BY created_at DESC,id DESC',
                (competition_id,))]
        return jobs

    def reconcile(self, competition_id=None):
        with self.store.connect() as db:
            ids = [row['competition_id'] for row in db.execute(
                "SELECT DISTINCT competition_id FROM model_tasks WHERE status IN ('queued','running')")]
        for cid in ids:
            if competition_id is not None and cid != competition_id:
                continue
            try:
                with registry.training_lock(cid):
                    self._recover_running(cid)
            except ValueError:
                pass
            if self._pending(cid):
                self._ensure_worker(cid)

    def _recover_running(self, competition_id):
        for job in self.list(competition_id):
            if job['status'] == 'running':
                job.update(status='interrupted', phase='interrupted', error='任务执行已中断', finished_at=now_iso())
                self._write(job)

    def _pending(self, competition_id):
        with self.store.connect() as db:
            row = db.execute("SELECT payload FROM model_tasks WHERE competition_id=? AND status='queued' ORDER BY rowid LIMIT 1",
                             (competition_id,)).fetchone()
        return json.loads(row['payload']) if row else None

    def _ensure_worker(self, competition_id, job_id=None):
        with self._guard:
            key = (self.key, competition_id)
            worker = self._workers.get(key)
            if worker is None or not worker.is_alive():
                worker = threading.Thread(target=self._worker, args=(competition_id,), daemon=True)
                self._workers[key] = worker
                worker.start()
            if job_id:
                self._threads[(self.key, job_id)] = worker

    def _worker(self, competition_id):
        try:
            while self._pending(competition_id):
                lock = registry.training_lock(competition_id)
                try:
                    lock.__enter__()
                except ValueError:
                    time.sleep(.1)
                    continue
                try:
                    self._recover_running(competition_id)
                    job = self._pending(competition_id)
                    if job:
                        self._run(job)
                finally:
                    lock.__exit__(None, None, None)
        finally:
            with self._guard:
                self._workers.pop((self.key, competition_id), None)
                if self._pending(competition_id):
                    self._ensure_worker(competition_id)

    def start(self, competition_id, body):
        if not isinstance(body, dict):
            raise ValueError('任务参数无效')
        registry.competition_spec(competition_id)
        request_key = body.get('request_key')
        if request_key is not None and (not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 120):
            raise ValueError('请求标识无效')
        request_digest = fingerprint({k:v for k,v in body.items() if k != 'request_key'})
        existing = self._existing(competition_id, request_key, request_digest)
        if existing:
            return existing
        retry = None
        if body.get('retry_of'):
            if set(body)-{'retry_of','request_key'}:
                raise ValueError('断点重试不能修改原任务参数')
            with self.store.connect() as db:
                row = db.execute('SELECT payload FROM model_tasks WHERE id=? AND competition_id=?',
                                 (body['retry_of'],competition_id)).fetchone()
            retry = json.loads(row['payload']) if row else None
            if not retry or retry['status'] not in ('failed','interrupted'):
                raise ValueError('仅能重试本赛事失败或中断的任务')
            if retry.get('protocol',{}).get('checkpoint_schema') != 'model-stage-v1':
                raise ValueError('旧任务没有可恢复断点，请新建任务')
            body = {**retry['request'],'retry_of':retry['id'],'request_key':request_key}
        kind = body.get('kind')
        if kind not in ('fit', 'evaluate', 'experiment'):
            raise ValueError('请选择训练、评估或实验任务')
        target = body.get('target', 'full_time')
        algorithm = body.get('algorithm', 'poisson')
        half_life = body.get('half_life', 180)
        if target not in ('full_time', 'had') or algorithm not in NAMES or half_life not in (180, 365):
            raise ValueError('模型目标、算法或衰减周期无效')
        if target == 'full_time' and not {'had', 'ttg', 'crs'}.issubset(capabilities(algorithm)) and kind != 'experiment':
            raise ValueError('所选算法不支持全场进球目标')
        start, end = boundary(body.get('date_from')), boundary(body.get('date_to'), end=True)
        now = datetime.now(timezone.utc)
        if start and end and start >= end:
            raise ValueError('日期范围无效')
        if end and end > now:
            raise ValueError('任务截止时间不能晚于当前时间')
        mode = body.get('evaluation_mode', 'fixed' if body.get('run_id') else 'rolling')
        cold_policy = body.get('cold_start_policy')
        if cold_policy is not None:
            from .cold_start import POLICY
            if cold_policy != POLICY or kind != 'experiment' or algorithm != 'dynamic_gamma' or half_life != 180:
                raise ValueError('冷启动策略仅支持显式动态Gamma180实验')
        if kind == 'evaluate' and mode not in ('fixed', 'rolling'):
            raise ValueError('评估方式无效')
        data = registry.dataset(competition_id, body.get('dataset_digest'))
        digest = fingerprint(data)
        fixed = None
        if kind == 'evaluate' and mode == 'fixed':
            manifest, bundle, _, _ = registry.load_run(competition_id, body.get('run_id'))
            fixed = restore(bundle['models'][competition_id])
            if target == 'full_time' and not {'had', 'ttg', 'crs'}.issubset(registry.supported_markets(bundle['models'][competition_id], competition_id)):
                raise ValueError('所选模型不支持全场进球目标')
            algorithm, half_life = fixed.kind, fixed.half_life
            if start is None or start - timedelta(minutes=60) <= parse_date(fixed.trained_before):
                raise ValueError('固定模型评估须指定晚于训练截止的测试区间')
        fit_until = end or now
        if retry:
            start, end = boundary(retry['protocol']['date_from']), boundary(retry['protocol']['date_to'])
            fit_until = boundary(retry['protocol']['fit_cutoff'])
        if kind in ('fit', 'experiment'):
            rows = [r for r in data['matches'] if parse_date(r['result_available_at']) < fit_until
                    and parse_date(r['kickoff']) < fit_until
                    and (start is None or parse_date(r['kickoff']) >= start)]
            if len(rows) < training.MIN_ROWS or len({r['season'] for r in rows}) < training.MIN_SEASONS:
                raise ValueError(f'训练至少需要{training.MIN_ROWS}场、{training.MIN_SEASONS}个赛季')
        request = {**body, 'kind': kind, 'target': target, 'algorithm': algorithm,
                   'half_life': half_life, 'dataset_digest': digest, 'evaluation_mode': mode}
        job = {'id': uuid4().hex, 'competition_id': competition_id, 'kind': kind,
               'status': 'queued', 'phase': 'queued', 'created_at': now_iso(),
               'dataset_digest': digest, 'request': request, 'result': None, 'error': None, 'child_stages': [],
               'worker_pid': os.getpid(), 'request_key': request_key, 'request_digest': request_digest,
               'protocol': {'dataset_digest': digest, 'frozen_at': now.isoformat(),
                            'date_from': start.isoformat() if start else None,
                            'date_to': end.isoformat() if end else None, 'fit_cutoff': fit_until.isoformat(),
                            'rule_revision': fingerprint(registry.competition_spec(competition_id)),
                            'model_schema': training.MODEL_SCHEMA, 'feature_version': FEATURE_VERSION,
                            'fold_artifacts': 'competition-fold-v1',
                            'evaluation_protocol': 'weekly-cutoff-common-validation-v2',
                            'checkpoint_schema': 'model-stage-v1',
                            'kind': kind, 'target': target, 'algorithm': algorithm, 'half_life': half_life,
                            'evaluation_mode': mode, 'run_id': body.get('run_id')}}
        if cold_policy is not None:
            job['protocol']['cold_start_policy'] = cold_policy
        if retry:
            job['protocol'] = retry['protocol']
            job['retry_of'] = retry['id']
            job['reuse_tasks'] = list(dict.fromkeys([retry['id']]+retry.get('reuse_tasks',[])))
        job['protocol_digest'] = fingerprint(job['protocol'])
        try:
            self._write(job)
        except sqlite3.IntegrityError:
            existing = self._existing(competition_id, request_key, request_digest)
            if existing:
                return existing
            raise
        self._ensure_worker(competition_id, job['id'])
        return {**job}

    def _run(self, job):
        progress_lock = threading.RLock()
        heartbeat_stop = threading.Event()
        def update(value):
            stage_key = value.get('stage_key')
            stages = job.setdefault('child_stages', [])
            if stage_key:
                stage = next((s for s in stages if s['id'] == stage_key), None)
                if stage is None:
                    stage = {'id': stage_key, 'started_at': now_iso()}
                    stages.append(stage)
                stage.update({k:v for k,v in value.items() if k in ('configuration','algorithm','metrics','artifacts','selected_key','coverage','reused_from')})
                stage['status'] = value.get('stage_status', 'running')
                if stage['status'] in ('completed', 'failed'):
                    stage['finished_at'] = now_iso()
            if value.get('status') == 'failed':
                for stage in stages:
                    if stage.get('status') == 'running':
                        stage.update(status='failed', error=value.get('error'), finished_at=now_iso())
            job.update(value)
            self._write(job)
        def progress(value):
            with progress_lock:
                update(value)
        def heartbeat():
            while not heartbeat_stop.wait(5):
                with progress_lock:
                    if job['status'] != 'running':
                        return
                    progress({'heartbeat_at':now_iso(), 'lease_expires_at':(datetime.now(timezone.utc)+timedelta(seconds=20)).isoformat()})
        pulse = threading.Thread(target=heartbeat,daemon=True)
        try:
            progress({'status': 'running', 'phase': job['kind'], 'worker_pid': os.getpid(), 'started_at': now_iso(),
                      'worker_token':uuid4().hex,'heartbeat_at':now_iso(),
                      'lease_expires_at':(datetime.now(timezone.utc)+timedelta(seconds=20)).isoformat()})
            pulse.start()
            cid, kind = job['competition_id'], job['kind']
            protocol = job['protocol']
            if fingerprint(protocol) != job['protocol_digest']:
                raise ValueError('任务输入协议校验失败')
            if protocol.get('model_schema') != training.MODEL_SCHEMA or protocol.get('feature_version') != FEATURE_VERSION:
                raise ValueError('任务模型或特征协议已变更，请重新创建任务')
            data = registry.dataset(cid, protocol['dataset_digest'], admission=False)
            config = {'kind': protocol['algorithm'], 'mode': 'separate', 'half_life': protocol['half_life']}
            start, end = boundary(protocol['date_from']), boundary(protocol['date_to'])
            fit_until = boundary(protocol['fit_cutoff'])
            fixed = None
            fixed_artifact = None
            sink = fold_sink(cid, job) if protocol.get('fold_artifacts') == 'competition-fold-v1' else None
            if kind == 'evaluate' and protocol['evaluation_mode'] == 'fixed':
                manifest, bundle, _, _ = registry.load_run(cid, protocol['run_id'])
                fixed = restore(bundle['models'][cid])
                fixed_artifact = {'kind':'run', 'competition_id':cid, 'run_id':manifest['run_id'],
                    'model_id':manifest['model_id'], 'digest':manifest['model_digest'],
                    'model_digest':manifest['model_digest'], 'dataset_digest':manifest['dataset_digest']}
            if kind == 'fit':
                progress({'phase':'fit','stage_key':'fit','stage_status':'running','configuration':config})
                result = reusable_candidate(job,config) or candidate(cid, data, config, cutoff=fit_until, date_from=start, task=job)
                progress({'phase':'fit','stage_key':'fit','stage_status':'completed',
                          'reused_from':result.get('task_id') if result.get('task_id') != job['id'] else None,
                          'artifacts':[{'kind':'run','run_id':result['run_id'],'model_id':result['model_id'],'digest':result['model_digest']}]})
            elif kind == 'evaluate':
                progress({'phase':'evaluate','stage_key':'evaluate','stage_status':'running','configuration':config})
                report = evaluate_task(data, config, fixed_model=fixed, date_from=start, date_to=end, progress=progress,
                                       artifact_sink=sink, fixed_artifact=fixed_artifact)
                if fixed:
                    manifest = registry.load_run(cid, job['request']['run_id'])[0]
                    report.update(run_id=manifest['run_id'], model_id=manifest['model_id'],
                                  model_digest=manifest['model_digest'], training_dataset_digest=manifest['dataset_digest'])
                result = save_report(cid, report, job['id'])
                progress({'phase':'evaluate','stage_key':'evaluate','stage_status':'completed','metrics':report['test'],
                          'reused_from':report.get('reused_stage_from'),
                          'artifacts':list({r['artifact_ref']['digest']:r['artifact_ref'] for r in report['records'] if r.get('artifact_ref')}.values())})
            else:
                experiment_data = {**data, 'matches': [r for r in data['matches']
                    if parse_date(r['result_available_at']) < fit_until and parse_date(r['kickoff']) < fit_until
                    and (start is None or parse_date(r['kickoff']) >= start)]}
                teams = {r[key] for r in experiment_data['matches'] for key in ('home_id', 'away_id')}
                experiment_data['team_mappings'] = [r for r in data.get('team_mappings', []) if r['team_id'] in teams]
                cold_policy = None
                if protocol.get('cold_start_policy'):
                    from .cold_start import evaluate as evaluate_cold, CONFIG
                    report = evaluate_cold(experiment_data, progress, sink)
                    report['selected_by_competition'] = {cid:CONFIG}
                else:
                    # Explicit experiment alone compares the registered candidate set.
                    report = evaluate(experiment_data, progress, target=job['request']['target'], test_selected_only=True, artifact_sink=sink)
                    report['evaluation_type'] = 'rolling_strategy'
                report.update(dataset_digest=fingerprint(data),
                              sample_digest=fingerprint(experiment_data), date_from=start.isoformat() if start else None,
                              date_to=fit_until.isoformat())
                evaluation = save_report(cid, report, job['id'])
                if protocol.get('cold_start_policy'):
                    if not report['admission']['passed']:
                        progress({'status':'completed','phase':'completed','result':{**evaluation,'admitted':False},
                                  'finished_at':now_iso(),'lease_expires_at':now_iso()})
                        return
                    cold_policy = {**report['admission'],'dataset_digest':fingerprint(data),
                                   'report_id':evaluation['report_id'],'report_digest':evaluation['report_digest']}
                progress({'phase': 'fit', 'strategy_report_id': evaluation['report_id'],
                          'stage_key':'final_fit','stage_status':'running','configuration':{k:report['selected_by_competition'][cid][k] for k in ('kind','mode','half_life')}})
                result = reusable_candidate(job,report['selected_by_competition'][cid]) or candidate(
                    cid, data, report['selected_by_competition'][cid], cutoff=fit_until,
                    date_from=start, strategy_report=evaluation['report_id'], task=job, cold_start_policy=cold_policy)
                result['strategy_evaluation'] = evaluation
                progress({'phase':'fit','stage_key':'final_fit','stage_status':'completed',
                          'reused_from':result.get('task_id') if result.get('task_id') != job['id'] else None,
                          'artifacts':[{'kind':'run','run_id':result['run_id'],'model_id':result['model_id'],'digest':result['model_digest']}]})
            progress({'status': 'completed', 'phase': 'completed', 'result': result, 'finished_at': now_iso(),'lease_expires_at':now_iso()})
        except Exception as exc:
            progress({'status': 'failed', 'phase': 'failed', 'error': str(exc), 'finished_at': now_iso()})
        finally:
            heartbeat_stop.set()
            if pulse.ident is not None:
                pulse.join(2)
