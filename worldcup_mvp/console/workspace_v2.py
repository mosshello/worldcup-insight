"""Competition read models with explicit denominators and version-bound reports."""
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from . import model_registry
from .catalog import catalog, classify
from .competition_workspace import workspaces
from .identity import IdentityRepository
from .store import now_iso, fingerprint


def read(service, competition_id=None, date=None):
    with service.store.connection_scope(), model_registry.validated_read_scope():
        return _read(service, competition_id, date)


def _read(service, competition_id=None, date=None):
    date = date or datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    datetime.strptime(date, '%Y-%m-%d')
    scores = service.scores()
    scopes = workspaces(service,date=date,competition_id=competition_id,scores=scores)
    matches = service.store.list_matches()
    specs = catalog()
    identities = IdentityRepository(service.store,initialize=False)
    registered = {spec['id'] for spec in specs}
    identities_by_match = {m['id']: classify(m, specs) for m in matches}
    for match in matches:
        cid = identities_by_match[match['id']]['id']
        if cid not in registered and cid not in scopes:
            spec = {'id':cid,'name':match.get('league') or match.get('league_code') or '待登记赛事',
                    'providers':{'sporttery':match.get('league_code')},'seasons':[], 'training_enabled':False, 'discovered':True}
            specs.append(spec)
            scopes[cid] = {'competition':spec, 'training':{'rows':0,'runs':[],'coverage':[],'season_coverage':[],'staging':{'coverage':[]}},
                          'models':[], 'sources':[], 'fundamentals':[], 'odds':[]}
    selected_scopes = {cid: value for cid,value in scopes.items() if competition_id in (None,cid)}
    if competition_id and not selected_scopes:
        raise ValueError('赛事不存在')
    for cid,value in selected_scopes.items():
        training = value['training']
        scoped = [m for m in matches if identities_by_match[m['id']]['id']==cid]
        from .sources import parse_date
        today = [m for m in scoped if m.get('sale_date')==date or parse_date(m['kickoff']).astimezone(timezone(timedelta(hours=8))).date().isoformat()==date]
        results = []
        excluded_count = 0
        for m in today:
            detail = (service.store.detail(m['id']) or {}).get('data',{})
            detail = {**detail,'live_score':scores.get(m['id']) or detail.get('live_score')}
            football = model_registry.forecast(m,detail,allow_network=False)
            from .model_readiness import check
            readiness = check(m,detail,football)
            state = (detail.get('live_score') or {}).get('state') or detail.get('context',{}).get('state')
            out_of_scope = parse_date(m['kickoff']) <= datetime.now(timezone.utc) or state in ('live','completed','cancelled','suspended','postponed') or bool(detail.get('cancelled'))
            excluded_count += int(out_of_scope)
            results.append({'id':m['id'],'name':m['home']+' vs '+m['away'],'kickoff':m['kickoff'],
                            'status':('已结束' if state=='completed' else '不在赛前范围') if out_of_scope else '可预测' if readiness['can_predict'] else '待处理','reasons':[] if out_of_scope else readiness['reasons'],
                            'model_inputs':readiness,'competition_id':cid,'in_prediction_scope':not out_of_scope})
        versions, current_data, imported_issues = [], None, []
        if not value['competition'].get('discovered'):
            root = model_registry.directory(cid)
            for path in sorted((root/'datasets').glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True):
                try:
                    data = model_registry.dataset(cid,path.stem,admission=False)
                    versions.append({'digest':path.stem,'rows':len(data['matches']),
                        'created_at':datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat(),
                        'status':'当前' if path.stem==training.get('dataset_digest') else '历史', 'manifest':data['manifest']})
                    if path.stem==training.get('dataset_digest'):
                        current_data = data
                except (ValueError,OSError,KeyError) as exc:
                    versions.append({'digest':path.stem,'status':'校验失败','reason':str(exc)})
        seasons = []
        staged = {str(r['season']):r for r in training.get('staging',{}).get('coverage',[])}
        imported = {str(r['season']):r for r in training.get('season_coverage',[])}
        for season in sorted(set(staged)|set(imported),reverse=True):
            source, included = staged.get(season,{}), imported.get(season,{}).get('imported',0)
            collected = source.get('collected')
            seasons.append({'id':cid+':'+season,'season':season,'collected':collected,'imported':included,
                            'not_imported':collected-included if collected is not None and collected>=included else None,
                            'excluded':None,
                            'half_matches':source.get('half_matches'), 'verified':source.get('independently_verified'),
                            'evidence':source})
        tasks = service.model_tasks.list(cid) if not value['competition'].get('discovered') else []
        if current_data:
            quarantined = defaultdict(list)
            for item in current_data['manifest'].get('excluded',[]):
                quarantined[item.get('reason','待核查')].append(item)
            labels = {'centralised_edition_venue_unverified':'赛会制场地待核实','stage_rules_unsupported':'暂不支持该阶段',
                'regular_time_result_unconfirmed':'90分钟赛果未确认','summary_identity_unverified':'详情身份缺少核验',
                'neutral_site':'中立场不适用','neutral_site_unknown':'场地口径未核实'}
            imported_issues = [{'id':fingerprint([cid,reason]),'name':str(len(items))+' 场',
                'reason':labels.get(reason,reason),'status':'隔离','evidence':items} for reason,items in quarantined.items()]
        accounting = {'included':len(current_data['matches']) if current_data else 0,
            'unique_source_events':current_data['manifest'].get('unique_source_events') if current_data else None,
            'excluded':len(current_data['manifest'].get('excluded',[])) if current_data and 'excluded' in current_data['manifest'] else None}
        eligible = sum(r['model_inputs']['can_predict'] for r in results)
        value.update(selection_revision=model_registry.selection(cid)['revision'] if not value['competition'].get('discovered') else 0,
            teams=identities.teams(cid),datasets={'versions':versions,'season_coverage':seasons,
            'current_digest':training.get('dataset_digest'),'accounting':accounting},tasks=tasks,issues=identities.issues(cid)+imported_issues,
            ingestion_supported=cid in service.data_tasks.supported,
            ingestion_tasks=service.data_tasks.list(cid) if cid in service.data_tasks.supported else [],
            selected_report=training.get('current_evaluation'),
            overview={'date':date,'scope':'销售日及北京时间自然日已收录比赛','checked_at':now_iso(),
                'date_type':'sale_or_beijing_calendar','observed_total':len(today),'excluded':excluded_count,
                'total':len(today)-excluded_count,'eligible':eligible,'blocked':len(today)-excluded_count-eligible,
                'unknown':sum(identities_by_match[m['id']].get('season') is None for m in today if any(r['id']==m['id'] and r['in_prediction_scope'] for r in results)),'matches':results})
    return {'schema_version':2,'competitions':specs,'competition_workspaces':selected_scopes,
            'workspace':selected_scopes.get(competition_id) if competition_id else None,'checked_at':now_iso()}
