"""Background collection and frozen-input research execution, independent of GETs."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime, timezone
from .store import encode, fingerprint
from .observations import ObservationWorkspace, stamp, iso, identity
from . import observation_sources as source
from . import observation_models as enhanced
from .observation_ai import ObservationAI
from .observation_features import build_packet

ROOT = Path(__file__).resolve().parents[2]


class ObservationRuntime:
    def __init__(self, store):
        self.store = store
        self.workspace = ObservationWorkspace(store)
        self.ai = ObservationAI(store)
        self.threads = []
        self.last_fixtures = 0
        self.last_handicap = 0
        self.last_insights = 0
        self.last_rosters = 0
        self.checked = {}
        self.models = {}
        with store.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS observation_packets(
                id TEXT PRIMARY KEY,fixture_id TEXT NOT NULL,revision TEXT NOT NULL,
                captured_at TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS observation_packet_lookup ON observation_packets(fixture_id,captured_at);
                CREATE TABLE IF NOT EXISTS observation_source_status(
                id TEXT PRIMARY KEY,checked_at TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observation_fixture_aliases(
                alias TEXT PRIMARY KEY,fixture_id TEXT NOT NULL,evidence TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observation_raw_receipts(
                digest TEXT PRIMARY KEY,payload TEXT NOT NULL);''')
        from .observation_rosters import CurrentRosterStore
        self.rosters=CurrentRosterStore(store)

    def status(self, ident, value):
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO observation_source_status VALUES(?,?,?)',(ident,iso(),encode(value)))

    def statuses(self):
        with self.store.connect() as db:
            rows = [{'id':r['id'],'checked_at':r['checked_at'],**json.loads(r['payload'])}
                    for r in db.execute("SELECT * FROM observation_source_status WHERE id!='research_status'")]
        return rows + [self.ai.budget_status()]

    def bootstrap(self):
        """Pin explicit research candidates; never mutate production selections."""
        from .model_registry import load_run
        from ..env_config import get_deepseek_model
        index_path = ROOT/'data/research/observation-models/candidate-index.json'
        index = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {}
        requested_model=get_deepseek_model()
        with self.store.connect() as db:
            calls=db.execute("SELECT payload FROM observation_ai_calls WHERE status='completed' AND model=? ORDER BY rowid DESC",(requested_model,)).fetchall()
        response_model=next((json.loads(r[0]).get('response_model') for r in calls if json.loads(r[0]).get('response_model')),None)
        base_runs={}
        for cid in source.LEAGUES:
            try:
                base_runs[cid]=json.loads((ROOT/f'data/research/big-five/2026-11-09/full-training/{cid}.json').read_text(encoding='utf-8'))['fits']['dc']['run_id']
            except (OSError,ValueError,KeyError):base_runs[cid]=None
        family='observation-v4-'+fingerprint({'base_runs':base_runs,'enhanced':{k:v.get('digest') for k,v in index.items()},
            'requested_model':requested_model,'response_model':response_model,'prompt':'observation-ai-v4','evidence':'v3','market_aware':False})[:20]
        for cid in source.LEAGUES:
            try:
                report = json.loads((ROOT/f'data/research/big-five/2026-11-09/full-training/{cid}.json').read_text(encoding='utf-8'))
                run_id = report['fits']['dc']['run_id']
                manifest, _, _, _ = load_run(cid,run_id)
                candidate = index.get(cid,{})
                config = {'schema':'observation-experiment-v1','base_run_id':run_id,
                          'base_model_id':manifest['model_id'],'enhanced_digest':candidate.get('digest'),
                          'ai_mode':'blind','ai_prompt_version':'observation-ai-v4','evidence_version':'v3',
                          'ai_requested_model':requested_model,'ai_response_model':response_model,
                          'ai_market_aware':False,
                          'selection_policy':'top1_per_market',
                          'evaluation_protocol':'observation-forward-v1','evaluation_family':family,
                          'movement_policy':{'version':'descriptive-v1','signal_enabled':False},
                          'eligibility':'league_only_regular_time','promotion':'manual_after_evaluation'}
                self.workspace.pin_experiment(cid,config)
            except (OSError,ValueError,KeyError):
                self.status('model:'+cid,{'status':'unavailable','error':'研究模型未就绪'})

    def sync_fixtures(self):
        def collect(cid):
            try:
                rows, receipts = source.fetch_fixtures(cid)
                for fixture in rows:
                    self.workspace.ingest_fixture(fixture)
                self.status('fixtures:'+cid,{'status':'available','count':len(rows),'receipts':receipts,'last_success':iso()})
                return len(rows)
            except Exception as exc:
                self.status('fixtures:'+cid,{'status':'failed','error':'赛程采集失败','error_type':type(exc).__name__})
                return 0
        with ThreadPoolExecutor(max_workers=5) as pool:
            counts = list(pool.map(collect,source.LEAGUES))
        self.last_fixtures = time.monotonic()
        return sum(counts)

    def collect_fixture(self, fixture):
        try:
            current, context, quotes, raw = source.fetch_summary(fixture)
            quotes += self.official_quotes(current)
            revision = self.workspace.ingest_fixture(current)
            supplemental={'context':{'evidence':[]},'source_status':[]}
            if current.get('season') and current.get('status')=='upcoming':
                from .observation_personnel_sources import cached_fixture_evidence
                try:
                    supplemental=cached_fixture_evidence(current)
                except (ValueError,KeyError,OSError):
                    supplemental['source_status']=[{'status':'unavailable','error':'人员及跨赛事来源待重试'}]
            captured=iso()
            roster_facts={'facts':[],'unknown':[]}
            if current.get('season') and current.get('status')=='upcoming':
                try:
                    roster_facts=self.rosters.fixture_evidence(current,captured)
                    for row in roster_facts['facts']:
                        snapshot=self.rosters.read_team(current['competition_id'],row['team_id'],current['season'],captured)
                        row['players']=[{k:p.get(k) for k in ('player_id','name','position')} for p in snapshot.get('players',[])]
                except (ValueError,KeyError):
                    roster_facts['unknown']=[{'status':'unverified'}]
            packet = {'fixture':current,'context':context,'quotes':quotes,
                      'captured_at':captured,'summary_captured_at':current['captured_at'],
                      'supplemental':supplemental,'source_url':current['source_url'],
                      'rosters':roster_facts,
                      'raw_digest':fingerprint(raw)}
            from .observation_insights import evidence_summary
            try:
                packet['evidence_summary']=evidence_summary(current,packet,raw)
            except ValueError:
                packet['evidence_summary']={'captured_at':current['captured_at'],'personnel':[],
                    'workload':[],'unknown':['资料身份或时间待核查'],'alerts':[]}
            with self.store.connect() as db:
                db.execute('INSERT OR IGNORE INTO observation_raw_receipts VALUES(?,?)',(packet['raw_digest'],encode(raw)))
                db.execute('INSERT OR IGNORE INTO observation_packets VALUES(?,?,?,?,?)',
                           (fingerprint(packet),current['id'],revision,captured,encode(packet)))
            for quote in quotes:
                self.workspace.ingest_quotes(current['id'],quote)
            self._settle(current,raw)
            self.status('fixture:'+current['id'],{'status':'available','captured_at':current['captured_at'],'quote_count':len(quotes)})
        except Exception as exc:
            self.status('fixture:'+fixture['id'],{'status':'failed','error':'逐场资料采集失败','error_type':type(exc).__name__})
        self.checked[fixture['id']] = time.monotonic()

    def official_quotes(self, fixture):
        """Reuse only a previously verified source-ID association, never fuzzy names."""
        from .readiness import fixture_key
        from .official_quotes import read
        quotes = []
        for match in self.store.list_matches():
            cached = self.store.detail(match['id']) or {}
            context = cached.get('data',{}).get('context',{})
            evidence = context.get('espn_identity',{})
            if (evidence.get('fixture')!=fixture_key(match) or evidence.get('provider')!='espn'
                or str(evidence.get('event_id'))!=fixture['provider_id']
                or evidence.get('competition_slug')!=fixture['source_slug']
                or not evidence.get('source_url')): continue
            try:
                if stamp(evidence['kickoff'])!=stamp(fixture['kickoff']) or stamp(match['kickoff'])!=stamp(fixture['kickoff']): continue
            except (KeyError,ValueError): continue
            if any('espn:'+str(evidence.get('team_ids',{}).get(s))!=fixture[s+'_id'] for s in ('home','away')): continue
            with self.store.connect() as db:
                db.execute('INSERT OR REPLACE INTO observation_fixture_aliases VALUES(?,?,?)',(match['id'],fixture['id'],encode(evidence)))
            value = read(self.store,match['id'])
            if value['stale'] or value['closed']: continue
            for code,pool in value['markets'].items():
                if not pool.get('options'): continue
                quotes.append({'source':'sporttery','source_name':'体彩SP','market':code,
                               'line':pool.get('line'),'options':pool['options'],
                               'captured_at':value['checked_at'],'published_at':pool.get('updated_at'),
                               'source_url':value.get('source_url'),'is_opening':False,
                               'fixture_identity':identity(fixture),'settlement_policy':'sporttery_regular_time'})
        return quotes

    def resolve_fixture(self, ident):
        if not ident: return ident
        with self.store.connect() as db:
            row = db.execute('SELECT fixture_id FROM observation_fixture_aliases WHERE alias=?',(ident,)).fetchone()
        return row[0] if row else ident

    def sync_handicap(self):
        from .observation_handicap_collector import HandicapCollector
        worker=getattr(self,'_handicap_thread',None)
        if worker and worker.is_alive():return {'status':'running'}
        if not hasattr(self,'_handicap_collector'):self._handicap_collector=HandicapCollector(self)
        self.last_handicap=time.monotonic()
        self._handicap_thread=threading.Thread(target=self._handicap_collector.sync,name='football-handicap-collector',daemon=True)
        self._handicap_thread.start()
        return {'status':'started'}

    def _settle(self, fixture, raw):
        comp = raw['header']['competitions'][0]
        if fixture['status'] == 'cancelled':
            if stamp(fixture['kickoff']) <= stamp(): self.workspace.settle(fixture['id'],{'cancelled':True})
            return
        if fixture['status'] != 'completed' or comp.get('status',{}).get('period') != 2:
            return
        regulation = raw.get('format',{}).get('regulation',{})
        if regulation.get('periods') != 2 or regulation.get('clock') != 2700:
            return
        pair = source.sides(comp)
        score = {'period':'regular_time'}
        for side in ('home','away'):
            value = pair[side].get('score')
            if isinstance(value,dict): value = value.get('displayValue')
            if not str(value).isdigit(): return
            score[side+'_goals'] = int(value)
            halves = [x for x in pair[side].get('linescores',[]) if x.get('period') == 1]
            if len(halves)==1 and str(halves[0].get('displayValue','')).isdigit():
                score[side+'_half_goals'] = int(halves[0]['displayValue'])
        self.workspace.settle(fixture['id'],score)

    def collect_due(self, limit=12):
        now = stamp()
        with self.store.connect() as db:
            fixtures = [json.loads(r[0]) for r in db.execute('SELECT payload FROM observation_fixtures')]
        due = []
        for fixture in fixtures:
            delta = (stamp(fixture['kickoff'])-now).total_seconds()
            if delta < -2*86400 or fixture['status'] in ('cancelled','postponed'): continue
            interval = 45 if -7200 < delta < 5400 else 180 if delta < 10800 else 3600
            elapsed = time.monotonic()-self.checked.get(fixture['id'],0)
            if elapsed >= interval:
                due.append((0 if 0<delta<5400 else 1, self.checked.get(fixture['id'],0),fixture))
        chosen = sorted(due,key=lambda x:(x[0],x[1],x[2]['kickoff']))[:limit]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(self.collect_fixture,[v[2] for v in chosen]))
        return len(chosen)

    def packet(self, job):
        with self.store.connect() as db:
            row = db.execute('SELECT payload FROM observation_packets WHERE fixture_id=? AND revision=? AND captured_at<=? ORDER BY captured_at DESC LIMIT 1',
                             (job['fixture']['id'],job['revision'],job['cutoff'])).fetchone()
        if not row: raise ValueError('缺少截止前逐场资料')
        packet = json.loads(row[0])
        if identity(packet.get('fixture',{})) != identity(job['fixture']) or fingerprint(identity(packet['fixture'])) != job['revision']:
            raise ValueError('冻结资料比赛身份不匹配')
        if (stamp(job['cutoff'])-stamp(packet['captured_at'])).total_seconds()>180:
            raise ValueError('逐场资料已过期')
        if packet.get('summary_captured_at') and (stamp(job['cutoff'])-stamp(packet['summary_captured_at'])).total_seconds()>180:
            raise ValueError('逐场比分及身份快照已过期')
        return packet

    def run(self, job):
        fixture, config = job['fixture'], job['experiment']
        quotes = job['quotes']
        latest = {}
        for q in quotes:
            # Prefer official integer handicap; two-way spreads never become hhad.
            if q['market'] not in latest or q['source']=='sporttery': latest[q['market']]=q
        hhad = latest.get('hhad',{})
        line = hhad.get('line') if hhad.get('source')=='sporttery' and type(hhad.get('line')) is int else None
        snapshot = {'fixture':fixture,'quotes':quotes,'cutoff':job['cutoff']}
        if job['source']=='market':
            from .sources import EXPECTED_OPTIONS
            probabilities = {}
            for code,q in latest.items():
                if code not in EXPECTED_OPTIONS or {o['key'] for o in q['options']} != set(EXPECTED_OPTIONS[code]): continue
                if code=='hhad' and line is None: continue
                total = sum(1/o['odds'] for o in q['options'])
                probabilities[code]={o['key']:(1/o['odds'])/total for o in q['options']}
            if not probabilities: return {'status':'unavailable','error':'缺少同期完整市场报价','input_snapshot':snapshot}
            result = {'probabilities':probabilities,'model_id':'normalized_market_reference','input_snapshot':snapshot}
        else:
            packet = self.packet(job)
            fixture = packet['fixture']
            if fixture['status']!='upcoming' or fixture.get('neutral_site') is not False:
                return {'status':'unavailable','error':'赛程状态或场地规则待核实','input_snapshot':snapshot}
            if job['source']=='base':
                from .model_registry import load_run
                from .model_methods import restore
                key = (fixture['competition_id'],config['base_run_id'])
                if key not in self.models:
                    manifest,bundle,_,_ = load_run(*key)
                    self.models[key] = (manifest,restore(bundle['models'][fixture['competition_id']]))
                manifest,model = self.models[key]
                if stamp(model.trained_before)>stamp(job['cutoff']): raise ValueError('模型尚未生成')
                if any(model.team_counts.get(fixture['competition_id']+':'+fixture[s+'_id'],0)<5 for s in ('home','away')):
                    return {'status':'unavailable','error':'球队有效训练样本不足'}
                value = model.forecast(fixture,line)
                result = {'probabilities':value['markets'],'model_id':manifest['model_id'],
                          'input_snapshot':{**snapshot,'packet_digest':fingerprint(packet)},'half_method':value.get('half_method')}
            else:
                from ..env_config import get_deepseek_model
                if config.get('ai_requested_model') and config['ai_requested_model']!=get_deepseek_model():
                    raise ValueError('AI配置已改变，需要新实验')
                digest = config.get('enhanced_digest')
                if not digest: return {'status':'unavailable','error':'增强模型未配置'}
                from . import observation_epl_personnel as epl_personnel
                epl_mode=config.get('enhanced_kind')==epl_personnel.SCHEMA
                if epl_mode:
                    artifact,evidence=epl_personnel.runtime_packet(config,fixture,job['cutoff'],packet)
                else:
                    if config.get('enhanced_kind'):
                        raise ValueError('未知增强模型类型，不回退旧模型')
                    artifact = enhanced.load(enhanced.ROOT/fixture['competition_id']/(digest+'.json'),fixture['competition_id'])
                    evidence = enhanced.feature_packet(artifact,fixture,job['cutoff'])
                from .observation_evidence import evidence_packet, merge_packets
                raw = packet.get('raw')
                if raw is None:
                    with self.store.connect() as db:
                        receipt=db.execute('SELECT payload FROM observation_raw_receipts WHERE digest=?',(packet['raw_digest'],)).fetchone()
                    raw=json.loads(receipt[0]) if receipt else None
                context=packet['context']
                if config.get('evidence_version')=='v3':
                    context={**context,'evidence':context.get('evidence',[])+packet.get('supplemental',{}).get('context',{}).get('evidence',[])}
                news = evidence_packet(fixture,context,job['cutoff'],summary=raw,captured_at=packet.get('summary_captured_at',packet['captured_at']))
                if config.get('evidence_version') in ('v2','v3'):
                    evidence = merge_packets(evidence,news)
                else:
                    legacy=build_packet(fixture,packet['context'],job['cutoff'])
                    evidence['evidence'] += legacy['evidence']
                    evidence['excluded'] = legacy['excluded']
                prompt_version=config.get('ai_prompt_version','observation-ai-v1')
                def invoke_ai(method, *args, **kwargs):
                    if prompt_version!='observation-ai-v4':
                        return method(*args,prompt_version=prompt_version,**kwargs)
                    from .observation_ai import retry_once
                    return retry_once(lambda retry:method(*args,prompt_version=prompt_version,allow_retry=retry,**kwargs),
                                      version=prompt_version,deadline=job.get('deadline'))
                def verify_ai_model(value):
                    if config.get('ai_response_model') and value.get('response_model')!=config['ai_response_model']:
                        raise ValueError('供应商返回模型已变化，需要新实验')
                if job['source']=='enhanced':
                    extraction = invoke_ai(self.ai.extract,evidence)
                    verify_ai_model(extraction)
                    result = (epl_personnel.forecast_packet(artifact,evidence,extraction,handicap=line) if epl_mode
                              else enhanced.forecast(artifact,fixture,job['cutoff'],extracted=extraction,handicap=line))
                    result['ai_call'] = extraction
                else:
                    result = invoke_ai(self.ai.predict,evidence)
                    verify_ai_model(result)
                    result['model_id'] = result['model']
                    result['input_policy'] = 'no_explicit_market; source_text_may_contain_market_information'
                    if config.get('ai_market_aware',True) and latest.get('had'):
                        market_packet = {**evidence,'market':{'fixture':fixture,
                            'captured_at':latest['had']['captured_at'],'markets':latest}}
                        try:
                            result['market_aware'] = invoke_ai(self.ai.predict,market_packet,market_aware=True)
                            verify_ai_model(result['market_aware'])
                        except ValueError:
                            result['market_aware'] = {'status':'failed','error':'带赔率AI对照未完成'}
        result.update(status='completed',line=line,selections={code:[max(p,key=p.get)] for code,p in result['probabilities'].items()})
        return result

    def start(self, stop):
        if any(t.is_alive() for t in self.threads): return
        def collect():
            self.bootstrap()
            while not stop.is_set():
                try:
                    if time.monotonic()-self.last_insights>900:
                        from .observation_insights import research_status
                        from .observation_evaluation import evaluate
                        insights=research_status(self.store)
                        insights['evaluation']=evaluate(self.store)
                        self.status('research_status',{'status':'available','value':insights})
                        self.last_insights=time.monotonic()
                    elapsed = time.monotonic()-self.last_fixtures
                    failed = any(s['id'].startswith('fixtures:') and s['status']!='available' for s in self.statuses())
                    if elapsed>900 or (failed and elapsed>60): self.sync_fixtures()
                    if time.monotonic()-self.last_handicap>60: self.sync_handicap()
                    if time.monotonic()-self.last_rosters>300 and not getattr(self,'_roster_thread',threading.Thread()).is_alive():
                        self.last_rosters=time.monotonic()
                        def sync_rosters():
                            try:
                                rows=self.rosters.sync_current_fixtures(max_teams=8)
                                self.status('current-rosters',{'status':'observed','teams':rows,'scope':'source_listing_only'})
                            except Exception as exc:
                                self.status('current-rosters',{'status':'failed','error':'球队名单刷新待重试','error_type':type(exc).__name__})
                        self._roster_thread=threading.Thread(target=sync_rosters,name='football-observation-rosters',daemon=True)
                        self._roster_thread.start()
                    self.collect_due()
                    self.workspace.schedule()
                except Exception as exc:
                    self.status('collector',{'status':'failed','error':'观察采集待重试','error_type':type(exc).__name__})
                stop.wait(5)
        def run(sources):
            while not stop.is_set():
                try:
                    self.workspace.run_pending(self.run,limit=4,sources=sources)
                except Exception as exc:
                    self.status('runner',{'status':'failed','error':'观察任务待重试','error_type':type(exc).__name__})
                stop.wait(.5)
        self.threads=[threading.Thread(target=collect,name='football-observation-collect',daemon=True)]
        self.threads += [threading.Thread(target=run,args=(group,),name='football-observation-'+str(i),daemon=True)
                         for i,group in enumerate((['base','market'],['enhanced'],['ai'],['enhanced'],['ai']))]
        for thread in self.threads: thread.start()
