"""Isolated, append-only pre-match observation experiments; GETs never run jobs."""
import json
import math
import time
import uuid
from datetime import datetime, timedelta, timezone
from .store import encode, fingerprint
from .sources import EXPECTED_OPTIONS, BEIJING
from .football_markets import outcome, score_key

COMPETITIONS = {'eng.premier':'英超','esp.laliga':'西甲','ger.bundesliga':'德甲','ita.serie_a':'意甲','fra.ligue1':'法甲'}
SOURCES = {'market':'市场参考','base':'基础模型','enhanced':'情报增强','ai':'AI独立判断'}
NODES = {'opening':'开盘观察','t60':'赛前60分钟','t15':'赛前15分钟'}

def stamp(value=None):
    d = value or datetime.now(timezone.utc)
    if isinstance(d, str): d = datetime.fromisoformat(d.replace('Z','+00:00'))
    if d.tzinfo is None: raise ValueError('时间必须包含时区')
    return d.astimezone(timezone.utc)

def iso(value=None): return stamp(value).isoformat()

def safe_error(exc):
    """Public diagnostics cannot echo provider bodies, credentials or prompt text."""
    from .observation_ai import ObservationAIError
    if isinstance(exc, ObservationAIError): return str(exc)
    if isinstance(exc, TimeoutError): return '预测服务超时，当前节点未完成'
    if isinstance(exc, ConnectionError): return '预测服务连接失败，当前节点未完成'
    if isinstance(exc, ValueError):
        known={'缺少预测概率','玩法概率选项不完整','概率无效','选择无效','让球线缺失','结果状态无效','未来采集时间','比赛身份不完整'}
        return str(exc) if str(exc) in known else '预测输入或输出校验失败，请查看数据与模型状态'
    return '预测任务失败，请检查服务状态与模型配置'

def identity(f):
    return {k:f.get(k) for k in ('id','competition_id','home_id','away_id','kickoff')}

def validate_result(result):
    if result.get('status','completed') != 'completed': return
    distributions = result.get('probabilities',{})
    if not distributions: raise ValueError('缺少预测概率')
    for code,p in distributions.items():
        if code not in EXPECTED_OPTIONS or set(p)!=set(EXPECTED_OPTIONS[code]): raise ValueError('玩法概率选项不完整')
        if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in p.values()) or abs(sum(p.values())-1)>1e-6: raise ValueError('概率无效')
        selected=result.get('selections',{}).get(code,[])
        if len(selected)!=len(set(selected)) or not set(selected)<=set(p): raise ValueError('选择无效')
    if 'hhad' in distributions and type(result.get('line')) is not int: raise ValueError('让球线缺失')

class ObservationWorkspace:
    def __init__(self, store):
        self.store=store
        with store.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS observation_experiments(id TEXT PRIMARY KEY,competition_id TEXT NOT NULL,created_at TEXT NOT NULL,config TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS observation_enrollments(id TEXT PRIMARY KEY,fixture_id TEXT NOT NULL,revision TEXT NOT NULL,experiment_id TEXT NOT NULL,created_at TEXT NOT NULL,reason TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS observation_enrollment_fixture ON observation_enrollments(fixture_id,revision,created_at);
            CREATE TABLE IF NOT EXISTS observation_fixtures(id TEXT PRIMARY KEY,competition_id TEXT NOT NULL,revision TEXT NOT NULL,payload TEXT NOT NULL,observed_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS observation_fixture_versions(id TEXT NOT NULL,revision TEXT NOT NULL,payload TEXT NOT NULL,observed_at TEXT NOT NULL,PRIMARY KEY(id,revision));
            CREATE TABLE IF NOT EXISTS observation_quotes(id TEXT PRIMARY KEY,fixture_id TEXT NOT NULL,revision TEXT NOT NULL,captured_at TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS observation_quote_fixture ON observation_quotes(fixture_id,captured_at);
            CREATE TABLE IF NOT EXISTS observation_events(id TEXT PRIMARY KEY,fixture_id TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS observation_jobs(id TEXT PRIMARY KEY,fixture_id TEXT NOT NULL,revision TEXT NOT NULL,experiment_id TEXT NOT NULL,node TEXT NOT NULL,source TEXT NOT NULL,due_at TEXT NOT NULL,deadline TEXT NOT NULL,status TEXT NOT NULL,lease_until TEXT,token TEXT,attempts INTEGER NOT NULL DEFAULT 0,payload TEXT NOT NULL,result TEXT,created_at TEXT NOT NULL,finished_at TEXT);
            CREATE INDEX IF NOT EXISTS observation_job_queue ON observation_jobs(status,due_at);
            CREATE INDEX IF NOT EXISTS observation_job_fixture ON observation_jobs(fixture_id,revision,source,created_at);
            CREATE TABLE IF NOT EXISTS observation_outcomes(id TEXT PRIMARY KEY,fixture_id TEXT NOT NULL,revision TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS observation_outcome_fixture ON observation_outcomes(fixture_id,revision,created_at);
            ''')

    def pin_experiment(self, competition_id, config, now=None):
        if competition_id not in COMPETITIONS: raise ValueError('仅支持五大联赛')
        config={**config,'competition_id':competition_id}
        ident=fingerprint(config)
        with self.store.connect() as db: db.execute('INSERT OR IGNORE INTO observation_experiments VALUES(?,?,?,?)',(ident,competition_id,iso(now),encode(config)))
        return ident

    def enroll_experiment(self,fixture_id,experiment_id,now=None,reason=''):
        """Explicit, append-only activation; archives never change experiments."""
        now=stamp(now)
        if not isinstance(reason,str) or not reason.strip():raise ValueError('实验登记必须填写原因')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM observation_fixtures WHERE id=?',(fixture_id,)).fetchone()
            exp=db.execute('SELECT * FROM observation_experiments WHERE id=?',(experiment_id,)).fetchone()
            if not row or not exp:raise ValueError('比赛或实验不存在')
            fixture=json.loads(row['payload'])
            if exp['competition_id']!=row['competition_id']:raise ValueError('实验赛事与比赛不一致')
            if stamp(exp['created_at'])>now or stamp(row['observed_at'])>now:raise ValueError('登记不能早于实验或比赛观测时间')
            if stamp(fixture['kickoff'])<=now or fixture.get('status','scheduled') not in ('scheduled','upcoming','not_started','pre','未开赛'):raise ValueError('仅可登记未开赛比赛')
            active=db.execute('SELECT * FROM observation_enrollments WHERE fixture_id=? AND revision=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(fixture_id,row['revision'])).fetchone()
            if active and stamp(active['created_at'])>now:raise ValueError('登记时间不能倒退')
            if active and active['experiment_id']==experiment_id:return json.loads(active['payload'])
            running=db.execute("SELECT id FROM observation_jobs WHERE fixture_id=? AND (status='running' OR (status='pending' AND attempts>0)) LIMIT 1",(fixture_id,)).fetchone()
            if running:raise ValueError('存在正在运行或等待恢复的任务，不能切换实验')
            used=db.execute('SELECT id FROM observation_enrollments WHERE fixture_id=? AND revision=? AND experiment_id=? LIMIT 1',(fixture_id,row['revision'],experiment_id)).fetchone()
            used_jobs=db.execute('SELECT id FROM observation_jobs WHERE fixture_id=? AND revision=? AND experiment_id=? LIMIT 1',(fixture_id,row['revision'],experiment_id)).fetchone()
            if used or (active and used_jobs):raise ValueError('不能复用已退休的实验登记，请创建新的实验版本')
            previous_ids=[r[0] for r in db.execute('SELECT DISTINCT experiment_id FROM observation_jobs WHERE fixture_id=? AND revision=? AND experiment_id!=?',(fixture_id,row['revision'],experiment_id))]
            old_jobs=db.execute("SELECT * FROM observation_jobs WHERE fixture_id=? AND revision=? AND experiment_id!=? AND status='pending'",(fixture_id,row['revision'],experiment_id)).fetchall()
            payload={'fixture_id':fixture_id,'revision':row['revision'],'competition_id':row['competition_id'],
                'experiment_id':experiment_id,'created_at':iso(now),'reason':reason.strip(),
                'previous_experiment_id':active['experiment_id'] if active else previous_ids[0] if len(previous_ids)==1 else None,
                'previous_experiment_ids':previous_ids,
                'retired_job_ids':[r['id'] for r in old_jobs],'operation':'explicit_enrollment'}
            ident=fingerprint(payload);payload['id']=ident
            for job in old_jobs:
                value=json.loads(job['payload']);value.update(cancelled_by_enrollment=ident,cancellation_reason=reason.strip())
                db.execute("UPDATE observation_jobs SET status='cancelled',payload=?,finished_at=?,result=? WHERE id=? AND status='pending'",
                    (encode(value),iso(now),encode({'status':'cancelled','error':'显式登记新实验：'+reason.strip(),'enrollment_id':ident}),job['id']))
            db.execute('INSERT INTO observation_enrollments VALUES(?,?,?,?,?,?,?)',(ident,fixture_id,row['revision'],experiment_id,iso(now),reason.strip(),encode(payload)))
        return payload

    def ingest_fixture(self, fixture, now=None):
        f=dict(fixture); now=stamp(now)
        if f.get('competition_id') not in COMPETITIONS: raise ValueError('赛事不在观察范围')
        if not all(f.get(k) for k in ('id','home_id','away_id','home','away','kickoff')) or f['home_id']==f['away_id']: raise ValueError('比赛身份不完整')
        f['kickoff']=iso(f['kickoff']); rev=fingerprint(identity(f))
        if f.get('captured_at') and stamp(f['captured_at'])>now: raise ValueError('未来采集时间')
        with self.store.connect() as db:
            previous=db.execute('SELECT * FROM observation_fixtures WHERE id=?',(f['id'],)).fetchone()
            if previous and previous['competition_id']!=f['competition_id']: raise ValueError('比赛赛事身份冲突')
            if previous and previous['revision']!=rev:
                db.execute("UPDATE observation_jobs SET status='cancelled',finished_at=? WHERE fixture_id=? AND status IN ('pending','running')",(iso(now),f['id']))
            db.execute('INSERT OR IGNORE INTO observation_fixture_versions VALUES(?,?,?,?)',(f['id'],rev,encode(f),iso(now)))
            db.execute('INSERT OR REPLACE INTO observation_fixtures VALUES(?,?,?,?,?)',(f['id'],f['competition_id'],rev,encode(f),iso(now)))
        return rev

    def ingest_quotes(self, fixture_id, quotes, now=None):
        now=stamp(now); q=dict(quotes); captured=stamp(q['captured_at']); published=stamp(q.get('published_at') or q['captured_at'])
        if captured>now or published>captured: raise ValueError('报价时间无效')
        if q.get('market') not in (*EXPECTED_OPTIONS,'asian_handicap','spread','totals'): raise ValueError('未知市场')
        opts=q.get('options',[])
        if len(opts)<2 or len({o['key'] for o in opts})!=len(opts) or any(type(o.get('odds')) not in (int,float) or not math.isfinite(o['odds']) or o['odds']<=1 for o in opts): raise ValueError('报价无效')
        if not q.get('source'): raise ValueError('缺报价来源')
        q.update(captured_at=iso(captured),published_at=iso(published) if quotes.get('published_at') else None,
                 time_basis=quotes.get('time_basis') or ('published' if quotes.get('published_at') else 'observed_only'))
        with self.store.connect() as db:
            f=db.execute('SELECT * FROM observation_fixtures WHERE id=?',(fixture_id,)).fetchone()
            if not f: raise ValueError('比赛不存在')
            fixture=json.loads(f['payload'])
            if captured>=stamp(fixture['kickoff']): return None
            if q.get('fixture_identity') and q['fixture_identity']!=identity(fixture): raise ValueError('报价比赛身份冲突')
            ident=fingerprint([fixture_id,f['revision'],q])
            previous=db.execute('SELECT payload FROM observation_quotes WHERE fixture_id=? AND revision=? AND captured_at<=? ORDER BY captured_at DESC',(fixture_id,f['revision'],iso(captured))).fetchall()
            previous=next((json.loads(r[0]) for r in previous if json.loads(r[0]).get('source')==q['source'] and json.loads(r[0]).get('market')==q['market'] and json.loads(r[0]).get('period')==q.get('period') and json.loads(r[0]).get('provider_market_id')==q.get('provider_market_id')),None)
            db.execute('INSERT OR IGNORE INTO observation_quotes VALUES(?,?,?,?,?)',(ident,fixture_id,f['revision'],iso(captured),encode(q)))
            prices=lambda quote:{o['key']:o['odds'] for o in quote['options']}
            if previous and (previous.get('line')!=q.get('line') or prices(previous)!=prices(q)):
                same_line=previous.get('line')==q.get('line')
                event={'kind':'price_change' if same_line else 'line_change','source':q['source'],'market':q['market'],'line':q.get('line'),'before':previous,'after':q,'seconds_to_kickoff':(stamp(fixture['kickoff'])-captured).total_seconds(),
                    'observation_gap_seconds':(captured-stamp(previous['captured_at'])).total_seconds(),
                    'price_deltas':{key:value-prices(previous)[key] for key,value in prices(q).items() if key in prices(previous)} if same_line else {},
                    'duration_seconds':None}
                db.execute('INSERT OR IGNORE INTO observation_events VALUES(?,?,?,?)',(fingerprint([fixture_id,f['revision'],event]),fixture_id,iso(captured),encode(event)))
        return ident

    def schedule(self, now=None):
        now=stamp(now); count=0
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute('SELECT * FROM observation_fixtures').fetchall():
                f=json.loads(row['payload']); kickoff=stamp(f['kickoff'])
                active=f.get('status','scheduled') in ('scheduled','upcoming','not_started','pre','未开赛')
                if not active or kickoff<=now:
                    db.execute("UPDATE observation_jobs SET status=?,finished_at=? WHERE fixture_id=? AND status IN ('pending','running')",('cancelled' if f.get('status') in ('cancelled','postponed') else 'missed',iso(now),f['id']))
                    continue
                # A fixture revision is one observation series. Later model
                # registrations apply to new series, never split its nodes.
                enrollment=db.execute('SELECT * FROM observation_enrollments WHERE fixture_id=? AND revision=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(f['id'],row['revision'])).fetchone()
                if enrollment and stamp(enrollment['created_at'])>now:continue
                experiments=db.execute('SELECT * FROM observation_experiments WHERE id=?',(enrollment['experiment_id'],)).fetchall() if enrollment else db.execute('SELECT e.* FROM observation_experiments e JOIN observation_jobs j ON j.experiment_id=e.id WHERE j.fixture_id=? AND j.revision=? ORDER BY j.created_at,j.id LIMIT 1',(f['id'],row['revision'])).fetchall()
                if not experiments:
                    experiments=db.execute('SELECT * FROM observation_experiments WHERE competition_id=? AND created_at<=? ORDER BY created_at DESC,id DESC LIMIT 1',(f['competition_id'],iso(now))).fetchall()
                if not experiments: continue
                exp=experiments[0]; config=json.loads(exp['config'])
                available_from=max(stamp(exp['created_at']),stamp(enrollment['created_at'])) if enrollment else None
                first=db.execute('SELECT * FROM observation_quotes WHERE fixture_id=? AND revision=? AND captured_at<=? AND captured_at>=? ORDER BY captured_at LIMIT 1',(f['id'],row['revision'],iso(now),iso(available_from) if available_from else '')).fetchone()
                specs=[('t60',kickoff-timedelta(minutes=60)),('t15',kickoff-timedelta(minutes=15))]
                if first: specs.append(('opening',stamp(first['captured_at'])))
                for node,due in specs:
                    predates_enrollment=bool(available_from and due<available_from)
                    deadline=min(due+timedelta(seconds=180),kickoff-timedelta(microseconds=1))
                    for source in SOURCES:
                        ident=fingerprint([f['id'],row['revision'],exp['id'],node,source])
                        label=NODES[node]
                        if node=='opening':
                            q=json.loads(first['payload']); verified=q.get('is_opening') and q.get('opening_verified') and q.get('published_at') and 0<=(stamp(q['captured_at'])-stamp(q['published_at'])).total_seconds()<=180
                            label='开盘观察' if verified else '本实验首次采集' if enrollment else '首次采集'
                        payload={'id':ident,'fixture':f,'revision':row['revision'],'experiment_id':exp['id'],'experiment':config,'node':node,'source':source,'label':label,'scheduled_at':iso(due),'deadline':iso(deadline)}
                        if enrollment:payload.update(enrollment_id=enrollment['id'],enrolled_at=enrollment['created_at'])
                        state='missed' if predates_enrollment or now>deadline else 'pending'
                        cur=db.execute('INSERT OR IGNORE INTO observation_jobs(id,fixture_id,revision,experiment_id,node,source,due_at,deadline,status,payload,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(ident,f['id'],row['revision'],exp['id'],node,source,iso(due),iso(deadline),state,encode(payload),iso(now)))
                        count+=cur.rowcount
                        if cur.rowcount and predates_enrollment:
                            db.execute('UPDATE observation_jobs SET result=?,finished_at=? WHERE id=?',(encode({'status':'missed','error':'登记前已错过该节点','enrollment_id':enrollment['id']}),iso(now),ident))
        return count

    def run_pending(self, runner, now=None, limit=4, sources=None):
        selected=tuple(SOURCES) if sources is None else tuple(dict.fromkeys(sources))
        if any(source not in SOURCES for source in selected): raise ValueError('观察组无效')
        if not selected: return []
        # An injected test epoch still advances with elapsed work. Production
        # reads the wall clock for every claim and completion, never once per batch.
        anchor=stamp(now) if now is not None else None
        elapsed_start=time.monotonic()
        def clock(): return anchor+timedelta(seconds=time.monotonic()-elapsed_start) if anchor else stamp()
        completed=[]
        for _ in range(limit):
            now=clock()
            token=uuid.uuid4().hex
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("UPDATE observation_jobs SET status='pending',token=NULL WHERE status='running' AND lease_until<?",(iso(now),))
                db.execute("UPDATE observation_jobs SET status='missed',finished_at=? WHERE status='pending' AND deadline<?",(iso(now),iso(now)))
                row=db.execute("SELECT * FROM observation_jobs WHERE status='pending' AND due_at<=? AND deadline>=? AND source IN ("+','.join('?' for _ in selected)+") ORDER BY due_at,fixture_id,node,source,id LIMIT 1",(iso(now),iso(now),*selected)).fetchone()
                if not row: break
                db.execute("UPDATE observation_jobs SET status='running',token=?,lease_until=?,attempts=attempts+1 WHERE id=?",(token,iso(now+timedelta(seconds=150)),row['id']))
                job=json.loads(row['payload'])
                if 'cutoff' not in job:
                    job.update(started_at=iso(now),cutoff=iso(now),late_seconds=max(0,(now-stamp(row['due_at'])).total_seconds()))
                    quotes=db.execute('SELECT payload FROM observation_quotes WHERE fixture_id=? AND revision=? AND captured_at<=? ORDER BY captured_at',(row['fixture_id'],row['revision'],iso(now))).fetchall()
                    job['quotes']=[json.loads(q[0]) for q in quotes if (now-stamp(json.loads(q[0])['captured_at'])).total_seconds()<=180]
                    # Freeze all four sources atomically, including sources that
                    # will execute in a later worker batch. Their data cutoff is
                    # identical; execution times are recorded independently.
                    siblings=db.execute('SELECT id,payload FROM observation_jobs WHERE fixture_id=? AND revision=? AND experiment_id=? AND node=?',
                        (row['fixture_id'],row['revision'],row['experiment_id'],row['node'])).fetchall()
                    for sibling in siblings:
                        payload=json.loads(sibling['payload'])
                        if 'cutoff' not in payload:
                            payload.update({k:job[k] for k in ('cutoff','late_seconds','quotes')})
                            db.execute('UPDATE observation_jobs SET payload=? WHERE id=?',(encode(payload),sibling['id']))
                job['started_at']=iso(now)
                db.execute('UPDATE observation_jobs SET payload=? WHERE id=?',(encode(job),row['id']))
            began=time.monotonic()
            try:
                result=runner(job)
                validate_result(result)
                state=result.get('status','completed')
                if state not in ('completed','unavailable','unsupported','failed'): raise ValueError('结果状态无效')
            except Exception as exc:
                result={'status':'failed','error':safe_error(exc),'error_type':type(exc).__name__}; state='failed'
            finished=clock()
            tolerance=min(180,max(0,float(job.get('experiment',{}).get('node_tolerance_seconds',60)))) if job['node']!='opening' else 180
            result={**result,'created_at':iso(finished),'started_at':job['started_at'],'cutoff':job['cutoff'],'late_seconds':job['late_seconds'],'node_tolerance_seconds':tolerance,'strict_node':job['late_seconds']<=tolerance,'duration_ms':round((time.monotonic()-began)*1000)}
            with self.store.connect() as db:
                current=db.execute('SELECT * FROM observation_fixtures WHERE id=?',(row['fixture_id'],)).fetchone()
                if not current or current['revision']!=row['revision'] or json.loads(current['payload']).get('status','scheduled') not in ('scheduled','upcoming','not_started','pre','未开赛'): state='cancelled'
                elif finished>=stamp(job['fixture']['kickoff']) or finished>stamp(job['deadline']): state='missed'
                db.execute('UPDATE observation_jobs SET status=?,result=?,finished_at=?,token=NULL WHERE id=? AND status=? AND token=?',(state,encode(result),iso(finished),row['id'],'running',token))
            completed.append({'id':row['id'],'status':state})
        return completed

    def settle(self, fixture_id, result, now=None):
        """Append regulation score revisions; archived predictions remain untouched."""
        now=stamp(now)
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM observation_fixtures WHERE id=?',(fixture_id,)).fetchone()
            if not row: raise ValueError('比赛不存在')
            if stamp(json.loads(row['payload'])['kickoff'])>now: raise ValueError('比赛尚未开赛')
            if not result.get('cancelled'):
                if result.get('period')!='regular_time' or any(type(result.get(k)) is not int or result[k]<0 for k in ('home_goals','away_goals')): raise ValueError('需明确90分钟赛果')
                for k in ('home_half_goals','away_half_goals'):
                    if result.get(k) is not None and (type(result[k]) is not int or not 0<=result[k]<=result[k.replace('_half','')]): raise ValueError('半场赛果无效')
            last=db.execute('SELECT payload FROM observation_outcomes WHERE fixture_id=? AND revision=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(fixture_id,row['revision'])).fetchone()
            if last and json.loads(last[0])==result: return
            db.execute('INSERT INTO observation_outcomes VALUES(?,?,?,?,?)',(uuid.uuid4().hex,fixture_id,row['revision'],iso(now),encode(result)))

    def detail(self, fixture_id):
        with self.store.connect() as db:
            f=db.execute('SELECT * FROM observation_fixtures WHERE id=?',(fixture_id,)).fetchone()
            if not f: raise ValueError('比赛不存在')
            observations=[]
            for row in db.execute('SELECT * FROM observation_jobs WHERE fixture_id=? ORDER BY due_at,source',(fixture_id,)):
                observations.append({**json.loads(row['payload']),'status':row['status'],'attempts':row['attempts'],'result':json.loads(row['result']) if row['result'] else None})
            enrollment=db.execute('SELECT * FROM observation_enrollments WHERE fixture_id=? AND revision=? ORDER BY created_at DESC,id DESC LIMIT 1',(fixture_id,f['revision'])).fetchone()
            active=enrollment['experiment_id'] if enrollment else next((r['experiment_id'] for r in reversed(observations) if r['revision']==f['revision']),None)
            experiments=[{'id':r['id'],'created_at':r['created_at'],'active':r['id']==active} for r in db.execute('SELECT DISTINCT e.* FROM observation_experiments e LEFT JOIN observation_jobs j ON j.experiment_id=e.id WHERE j.fixture_id=? OR e.id=? ORDER BY e.created_at DESC,e.id DESC',(fixture_id,active))]
            return {'fixture':json.loads(f['payload']),'observations':observations,'active_experiment_id':active,'experiments':experiments,'quotes':[json.loads(r[0]) for r in db.execute('SELECT payload FROM observation_quotes WHERE fixture_id=? ORDER BY captured_at',(fixture_id,))],'events':[json.loads(r[0]) for r in db.execute('SELECT payload FROM observation_events WHERE fixture_id=? ORDER BY created_at',(fixture_id,))]}

    def read(self, day='', competition='', source='base', market='had', experiment=None, node='t60', fixture_id=None):
        if source not in SOURCES or market not in EXPECTED_OPTIONS or node not in NODES: raise ValueError('观察筛选无效')
        if competition and competition not in COMPETITIONS: raise ValueError('赛事筛选无效')
        if day:
            try:
                if datetime.strptime(day,'%Y-%m-%d').date().isoformat()!=day: raise ValueError()
            except (ValueError,TypeError): raise ValueError('日期必须为 YYYY-MM-DD') from None
        rows=[]; stats={'node':node,'targets':0,'completed':0,'failed':0,'unavailable':0,'unsupported':0,'missed':0,'pending':0,'running':0,'cancelled':0,'late':0,'scored':0,'single_scored':0,'multi_scored':0,'single_correct':0,'multi_covered':0,'log_loss':None,'brier':None}
        losses=[]; briers=[]; calibration=[]
        with self.store.connect() as db:
            fixtures=db.execute('SELECT * FROM observation_fixtures ORDER BY json_extract(payload,\'$.kickoff\') DESC,id').fetchall()
            for f in fixtures:
                fixture=json.loads(f['payload']); date=stamp(fixture['kickoff']).astimezone(BEIJING).date().isoformat()
                if (competition and competition!=f['competition_id']) or (day and date!=day) or (fixture_id and fixture_id!=f['id']): continue
                out={**fixture,'day':date,'observations':{}}
                jobs=db.execute('SELECT * FROM observation_jobs WHERE fixture_id=? AND revision=? AND source=? ORDER BY created_at,id',(f['id'],f['revision'],source)).fetchall()
                enrollment=db.execute('SELECT * FROM observation_enrollments WHERE fixture_id=? AND revision=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(f['id'],f['revision'])).fetchone()
                chosen=experiment or (enrollment['experiment_id'] if enrollment else jobs[-1]['experiment_id'] if jobs else None)
                for j in jobs:
                    if j['experiment_id']!=chosen: continue
                    r=json.loads(j['result']) if j['result'] else {}
                    display_status='unsupported' if j['status']=='completed' and market not in r.get('probabilities',{}) else j['status']
                    job=json.loads(j['payload'])
                    visible={k:v for k,v in r.items() if k in ('probabilities','selections','line','model_id','model_version','created_at','cutoff','late_seconds','strict_node','duration_ms','error','error_type')}
                    out['observations'][j['node']]={**{k:v for k,v in job.items() if k in ('id','experiment_id','node','source','label','scheduled_at','deadline','cutoff','started_at')},**visible,'status':display_status,'result':visible}
                if node not in out['observations']:
                    state=fixture.get('status','scheduled'); past=stamp(fixture['kickoff'])<=stamp()
                    before_enrollment=bool(enrollment and chosen==enrollment['experiment_id'] and node in ('t60','t15') and stamp(fixture['kickoff'])-timedelta(minutes=60 if node=='t60' else 15)<stamp(enrollment['created_at']))
                    out['observations'][node]={'status':'cancelled' if state in ('cancelled','postponed') else 'missed' if past or before_enrollment or state not in ('scheduled','upcoming','not_started','pre','未开赛') else 'pending','node':node,'source':source}
                    if before_enrollment:out['observations'][node]['error']='登记前已错过该节点'
                rows.append(out); stats['targets']+=1
                obs=out['observations'][node]
                stats[obs['status']]=stats.get(obs['status'],0)+1
                if obs['status']!='completed': continue
                if not obs.get('strict_node',False): stats['late']+=1; continue
                p=obs.get('probabilities',{}).get(market)
                if not p: continue
                actualrow=db.execute('SELECT payload FROM observation_outcomes WHERE fixture_id=? AND revision=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(f['id'],f['revision'])).fetchone()
                if not actualrow: continue
                score=json.loads(actualrow[0])
                if score.get('cancelled'): continue
                h,a=score['home_goals'],score['away_goals']; actual={'had':outcome(h,a),'ttg':'s'+str(min(7,h+a)),'crs':score_key(h,a)}
                if type(obs.get('line')) is int: actual['hhad']=outcome(h+obs['line'],a)
                if score.get('home_half_goals') is not None and score.get('away_half_goals') is not None: actual['hafu']=outcome(score['home_half_goals'],score['away_half_goals'])+outcome(h,a)
                target=actual.get(market)
                if target not in p: continue
                obs['actual']=target; stats['scored']+=1
                losses.append(-math.log(max(p[target],1e-15))); briers.append(sum((v-(k==target))**2 for k,v in p.items()))
                top=max(p,key=p.get); calibration.append((p[top],int(top==target)))
                selected=obs.get('selections',{}).get(market,[])
                if len(selected)==1: stats['single_scored']+=1; stats['single_correct']+=target in selected
                elif len(selected)>1: stats['multi_scored']+=1; stats['multi_covered']+=target in selected
        bins=[]
        for lo in range(0,10):
            values=[(p,y) for p,y in calibration if lo/10<=p<(lo+1)/10 or lo==9 and p==1]
            if values: bins.append({'lower':lo/10,'upper':(lo+1)/10,'count':len(values),'mean_probability':sum(p for p,_ in values)/len(values),'frequency':sum(y for _,y in values)/len(values)})
        n=stats['single_scored']; hits=stats['single_correct']
        interval=None
        if n:
            z=1.95996398454; ph=hits/n; center=(ph+z*z/(2*n))/(1+z*z/n); radius=z*math.sqrt(ph*(1-ph)/n+z*z/(4*n*n))/(1+z*z/n)
            interval=[max(0,center-radius),min(1,center+radius)]
        stats.update(single_accuracy=hits/n if n else None,single_accuracy_interval=interval,interval_method='Wilson descriptive; matches may be correlated',calibration=bins,multi_coverage=stats['multi_covered']/stats['multi_scored'] if stats['multi_scored'] else None,log_loss=sum(losses)/len(losses) if losses else None,brier=sum(briers)/len(briers) if briers else None)
        return {'rows':rows,'summary':stats,'competitions':[{'id':k,'label':v} for k,v in COMPETITIONS.items()],'sources':[{'id':k,'label':v} for k,v in SOURCES.items()],'nodes':[{'id':k,'label':v} for k,v in NODES.items()]}

    def compare(self, day='', competition='', market='had', node='t60', experiment=None):
        """Only identical fixture/node/cutoff/contract pairs; no best-version selection."""
        groups={}
        for source in SOURCES:
            view=self.read(day=day,competition=competition,source=source,market=market,node=node,experiment=experiment)
            groups[source]={r['id']:r['observations'].get(node,{}) for r in view['rows']}
        def valid(o): return o.get('status')=='completed' and o.get('strict_node') and o.get('actual') in o.get('probabilities',{}).get(market,{})
        def metrics(records):
            if not records: return {'n':0,'log_loss':None,'brier':None,'top1_accuracy':None}
            loss=brier=hit=0
            for o in records:
                p=o['probabilities'][market]; actual=o['actual']; loss-=math.log(max(p[actual],1e-15)); brier+=sum((v-(k==actual))**2 for k,v in p.items()); hit+=max(p,key=p.get)==actual
            n=len(records); return {'n':n,'log_loss':loss/n,'brier':brier/n,'top1_accuracy':hit/n}
        def compatible(observations):
            return len({o.get('cutoff') for o in observations})==1 and len({o.get('experiment_id') for o in observations})==1 and (market!='hhad' or len({o.get('line') for o in observations})==1)
        common=[]
        for ident in groups['base']:
            obs=[groups[s].get(ident,{}) for s in SOURCES]
            if all(valid(o) for o in obs) and compatible(obs): common.append(ident)
        pairs={}
        for source in ('market','enhanced','ai'):
            ids=[i for i,o in groups['base'].items() if valid(o) and valid(groups[source].get(i,{})) and compatible([o,groups[source][i]])]
            pairs[source]={'fixture_ids':ids,'base':metrics([groups['base'][i] for i in ids]),source:metrics([groups[source][i] for i in ids])}
        return {'node':node,'market':market,'common_fixture_ids':common,'common_n':len(common),'groups':{s:metrics([groups[s][i] for i in common]) for s in SOURCES},'pairs':pairs}

    def movement_report(self, competition=''):
        """Descriptive event counts only, never an inference about manipulation."""
        windows=[('24h-6h',21600,86400),('6h-3h',10800,21600),('3h-90m',5400,10800),('90m-30m',1800,5400),('30m-5m',300,1800),('5m-0',0,300)]
        buckets={name:{'seconds':hi-lo,'events':0,'fixtures':set(),'line_changes':0,'price_moves':[],'observation_gaps':[]} for name,lo,hi in windows}
        with self.store.connect() as db:
            rows=db.execute('SELECT e.fixture_id,e.payload,f.competition_id FROM observation_events e JOIN observation_fixtures f ON f.id=e.fixture_id').fetchall()
        for r in rows:
            if competition and r['competition_id']!=competition: continue
            e=json.loads(r['payload']); seconds=e['seconds_to_kickoff']
            for name,lo,hi in windows:
                if lo<=seconds<hi:
                    b=buckets[name]; b['events']+=1; b['fixtures'].add(r['fixture_id']); b['line_changes']+=e['kind']=='line_change'
                    b['price_moves'].extend(abs(v) for v in e.get('price_deltas',{}).values())
                    if e.get('observation_gap_seconds') is not None: b['observation_gaps'].append(e['observation_gap_seconds'])
        return {'buckets':[{'window':name,**{k:v for k,v in b.items() if k not in ('fixtures','price_moves','observation_gaps')},'fixtures':len(b['fixtures']),
            'max_absolute_price_move':max(b['price_moves']) if b['price_moves'] else None,
            'mean_observation_gap_seconds':sum(b['observation_gaps'])/len(b['observation_gaps']) if b['observation_gaps'] else None,
            'sustained_duration_seconds':None} for name,b in buckets.items()],
            'sample_status':'no_events' if not any(b['events'] for b in buckets.values()) else 'descriptive_only',
            'interpretation':'Descriptive counts; unequal windows and missing observation coverage prohibit peak-time or causal claims. Sampling gaps do not establish sustained duration.'}
