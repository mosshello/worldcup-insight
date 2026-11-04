"""Shared experiment for CLI and console. No selection side effects."""
from datetime import timedelta
import math

from . import training as t
from .model_methods import configs, fit, features, cutoff, NAMES, capabilities, FEATURE_VERSION, FEATURE_NAMES
from .sources import parse_date
from .store import now_iso, fingerprint


def feature_metadata(kind):
    vector = kind in ('elo_logistic', 'catboost', 'lightgbm_goals')
    return {'version': FEATURE_VERSION if vector else 'competition-goals-history-v1',
            'policy': 'elo_recent_results_weekly' if vector else
                'discounted_gamma_team_goals' if kind == 'dynamic_gamma' else 'time_decayed_team_goals',
            'history_fields': ['competition_id','home_id','away_id','kickoff','result_available_at','home_goals','away_goals'],
            'features': FEATURE_NAMES if vector else ['team_identity','home_away','historical_goals','time_decay'],
            'optional_half_time_fields': ['home_half_goals','away_half_goals'] if not vector or kind=='lightgbm_goals' else [],
            'excluded_inputs': ['lineups','injuries','xg','external_odds','llm_prose']}


def market_coverage(records):
    counts = {code:sum(code in r['markets'] for r in records) for code in ('had','hhad','ttg','crs','hafu')}
    return {code:count for code,count in counts.items() if count}


def stage_walk(stage, rows, targets, config, vectors, sink=None, exclusions=None, **kwargs):
    checkpoint = getattr(sink, 'checkpoint', None)
    cached = checkpoint(stage,config,rows,targets) if checkpoint else None
    if cached is not None:
        if exclusions is not None:
            exclusions.extend(cached['exclusions'])
        return cached['records'], cached['task_id']
    records = walk(rows,targets,config,vectors,artifact_sink=sink,exclusions=exclusions,**kwargs)
    if checkpoint:
        checkpoint(stage,config,rows,targets,records=records,exclusions=exclusions or [])
    return records, None


def walk(rows, targets, config, vectors, fixed_model=None, exclusions=None, artifact_sink=None, fixed_artifact=None,
         minimum_team_samples=5, record_extra=None):
    models, records, artifacts = {}, [], {}
    for row in targets:
        at = parse_date(fixed_model.trained_before) if fixed_model else cutoff(row)
        cid = row['competition_id']
        if fixed_model:
            models[at] = fixed_model
        elif at not in models:
            replay = getattr(artifact_sink, 'restore_fold', None)
            cached = replay(cid,at,config,rows) if replay else None
            if cached:
                models[at], artifacts[at] = cached
            else:
                models[at] = fit(config,rows,at,cid,vectors)
        if artifact_sink and not fixed_model and at not in artifacts:
            inputs = [r for r in rows if r['competition_id'] == cid
                      and parse_date(r['result_available_at']) < at and parse_date(r['kickoff']) < at]
            model = models[at].to_dict()
            artifacts[at] = artifact_sink({'competition_id': cid, 'cutoff': at.isoformat(),
                'configuration': {k:config[k] for k in ('kind', 'mode', 'half_life')},
                'model': model, 'model_digest': fingerprint(model), 'feature_metadata': feature_metadata(config['kind']),
                'input_receipt': {'row_ids': [r['id'] for r in inputs], 'rows': len(inputs),
                                  'digest': fingerprint(inputs), 'availability': 'result_available_at < cutoff'}})
        if exclusions is not None and any(models[at].team_counts.get(cid+':'+row[side+'_id'], 0) < minimum_team_samples for side in ('home', 'away')):
            exclusions.append({'id': row['id'], 'reason': '球队截止时点前样本不足5场'})
            continue
        forecast = models[at].forecast(row,row.get('handicap'))
        probabilities = [forecast['markets']['had'][key] for key in 'hda']
        asof = parse_date(row['kickoff'])-timedelta(minutes=60)
        odds, market = row.get('odds'), None
        if odds and 0 <= (asof-parse_date(row['odds_observed_at'])).total_seconds() <= 10800:
            mass = sum(1/v for v in odds.values())
            market = [1/odds[k]/mass for k in 'hda']
        h,a = row['home_goals'],row['away_goals']
        records.append({'id':row['id'],'competition_id':cid,'season':row['season'],'stage':row['stage'],
            'team_samples':[models[at].team_counts.get(cid+':'+row[side+'_id'], 0) for side in ('home','away')],
            **(record_extra(models[at],row) if record_extra else {}),
            **({'artifact_ref':fixed_artifact or artifacts[at]} if fixed_artifact or at in artifacts else {}),
            'rules_era':row.get('rules_era', 'unknown'),
            'asof':asof.isoformat(),'trained_before':at.isoformat(),'feature_cutoff':at.isoformat(),
            'probabilities':probabilities,'outcome':0 if h>a else 2 if a>h else 1,
            'market':market,'markets':forecast['markets'],
            'actual_markets':{'had':t.outcome(h,a),'ttg':f's{min(h+a,7)}','crs':t.score_key(h,a),
                **({'hhad':t.outcome(h+row['handicap'],a)} if row.get('handicap') is not None else {}),
                **({'hafu':t.outcome(row['home_half_goals'],row['away_half_goals'])+t.outcome(h,a)} if row.get('home_half_goals') is not None else {})}})
    return records


def evaluate_task(data, config, *, fixed_model=None, date_from=None, date_to=None, progress=None,
                  artifact_sink=None, fixed_artifact=None):
    """Evaluate a frozen artifact or a predeclared rolling strategy, never select/refit."""
    rows = t.validate_dataset(data)
    cid = data['manifest']['competition_id']
    if {row['competition_id'] for row in rows} != {cid}:
        raise ValueError('评估数据必须属于单一赛事')
    if fixed_model:
        trained = parse_date(fixed_model.trained_before)
        if date_from is None or date_from - timedelta(minutes=60) <= trained:
            raise ValueError('固定模型评估须指定晚于训练截止的测试区间')
    elif date_from is None:
        _, _, start, _, _ = t.split_dataset(rows)
        date_from = parse_date(str(start) + 'T00:00:00+00:00')
    targets = [row for row in rows if parse_date(row['kickoff']) >= date_from
               and (date_to is None or parse_date(row['kickoff']) < date_to)]
    if len(targets) < t.MIN_TEST:
        raise ValueError(f'评估样本不足{t.MIN_TEST}场')
    if fixed_model and any(parse_date(row['kickoff']) - timedelta(minutes=60) <= trained for row in targets):
        raise ValueError('评估样本早于模型训练截止')
    if progress:
        progress({'phase': 'evaluate', 'completed': 0, 'total': len(targets)})
    exclusions = []
    records, reused = stage_walk('evaluate',rows,targets,config,None,sink=artifact_sink,exclusions=exclusions,
                                 fixed_model=fixed_model,fixed_artifact=fixed_artifact)
    if len(records) < t.MIN_TEST:
        raise ValueError(f'模型可评估样本不足{t.MIN_TEST}场')
    return {'created_at': now_iso(), 'competition_id': cid, 'dataset_digest': fingerprint(data),
            'model_schema': t.MODEL_SCHEMA, 'status': 'research_only',
            'evaluation_type': 'fixed_model' if fixed_model else 'rolling_strategy',
            'evaluation_protocol': 'fixed-parameters-v1' if fixed_model else 'weekly-cutoff-v1',
            'date_from': date_from.isoformat(), 'date_to': date_to.isoformat() if date_to else None,
            'configuration': config, 'test': t.metrics(records), 'records': records,
            'feature_metadata':feature_metadata(config['kind']), 'market_coverage':market_coverage(records),
            'supported_markets':list(market_coverage(records)), 'capabilities_verified':True,
            'reused_stage_from':reused,
            'total_targets': len(targets), 'excluded_count': len(exclusions), 'exclusions': exclusions,
            'by_competition': {cid: t.metrics(records)}, 'markets': t.market_metrics(records),
            'by_rules_era': {era:t.metrics([r for r in records if r.get('rules_era', 'unknown') == era])
                             for era in sorted({r.get('rules_era', 'unknown') for r in records})},
            'production_enabled': False}


def evaluate(data, progress=None, candidates=None, target='full_time', test_selected_only=False, artifact_sink=None):
    if target not in ('full_time','had'):
        raise ValueError('训练目标无效')
    rows = t.validate_dataset(data)
    leagues, vs, ts, validation, test = t.split_dataset(rows)
    if len(leagues) != 1:
        raise ValueError('多方法训练必须指定单一赛事')
    cid = leagues[0]
    candidates = candidates or configs()
    vectors = {r['id']:features(rows,r,cutoff(r)) for r in rows}
    comparisons, validation_records, validation_exclusions = [], {}, {}
    strict = artifact_sink is not None
    # Freeze validation winner before evaluating any test outcomes.
    for i,config in enumerate(candidates):
        stage = 'validation:'+config['kind']+':'+str(config['half_life'])
        if progress: progress({'phase':'validation','algorithm':NAMES[config['kind']], 'completed':i,'total':len(candidates),
                               'stage_key':stage,'stage_status':'running','configuration':config})
        excluded = []
        if strict:
            records,reused = stage_walk(stage,rows,validation,config,vectors,sink=artifact_sink,exclusions=excluded)
        else:
            records,reused = walk(rows,validation,config,vectors),None
        key = config['kind']+':'+str(config['half_life'])
        validation_records[key], validation_exclusions[key] = records, excluded
        scores = t.metrics(records)
        artifacts = list({r['artifact_ref']['digest']:r['artifact_ref'] for r in records if r.get('artifact_ref')}.values())
        if progress: progress({'phase':'validation','stage_key':stage,'stage_status':'completed','metrics':scores,'artifacts':artifacts,'reused_from':reused})
        comparisons.append({**config,'key':config['kind']+':'+str(config['half_life']),
                            'name':NAMES[config['kind']],
                            'supported_markets':list(market_coverage(records)) if strict else capabilities(config['kind']),
                            **({'market_coverage':market_coverage(records),'capabilities_verified':True,
                                'feature_metadata':feature_metadata(config['kind'])} if strict else {}),
                            'validation':scores,'by_competition':{cid:scores},
                            **({'validation_records':records,'validation_artifacts':artifacts} if artifact_sink else {}),
                            'validation_by_rules_era':{era:t.metrics([r for r in records if r.get('rules_era', 'unknown') == era])
                                for era in sorted({r.get('rules_era', 'unknown') for r in rows})}})
    common_ids = None
    if strict:
        if progress: progress({'phase':'validation_alignment','stage_key':'validation_alignment','stage_status':'running'})
        common_ids = set.intersection(*(set(r['id'] for r in records) for records in validation_records.values()))
        if len(common_ids) < t.MIN_TEST:
            raise ValueError(f'候选共同验证样本不足{t.MIN_TEST}场')
        for candidate in comparisons:
            records = validation_records[candidate['key']]
            common = [r for r in records if r['id'] in common_ids]
            candidate['validation_raw'] = candidate['validation']
            candidate['validation'] = t.metrics(common)
            candidate['by_competition'] = {cid:candidate['validation']}
            candidate['validation_by_rules_era'] = {era:t.metrics([r for r in common if r.get('rules_era','unknown') == era])
                for era in sorted({r.get('rules_era','unknown') for r in rows})}
            candidate['validation_coverage'] = {'target_count':len(validation),'forecast_count':len(records),
                'common_count':len(common),'excluded_count':len(validation_exclusions[candidate['key']]),
                'exclusions':validation_exclusions[candidate['key']]}
            if progress: progress({'phase':'validation','stage_key':'validation:'+candidate['key'],
                'stage_status':'completed','metrics':candidate['validation'],'coverage':candidate['validation_coverage']})
        if progress: progress({'phase':'validation_alignment','stage_key':'validation_alignment','stage_status':'completed',
                               'metrics':{'common_count':len(common_ids),'target_count':len(validation)}})
    required = {'had','ttg','crs'} if target == 'full_time' else {'had'}
    for c in comparisons:
        c['eligible_for_target'] = required.issubset(c['supported_markets'])
    eligible = [c for c in comparisons if c['eligible_for_target']]
    if not eligible:
        raise ValueError('候选模型没有足够的目标玩法输出')
    winner = min(eligible,key=lambda c:c['validation']['log_loss'])
    if progress: progress({'phase':'validation_selection','stage_key':'validation_selection',
                           'stage_status':'completed','selected_key':winner['key']})
    all_models, selected_records = [], None
    test_candidates = [winner] if test_selected_only else comparisons
    for i,config in enumerate(test_candidates):
        stage = 'test:'+config['key']
        if progress: progress({'phase':'test','algorithm':NAMES[config['kind']], 'completed':i,'total':len(test_candidates),
                               'stage_key':stage,'stage_status':'running','configuration':{k:config[k] for k in ('kind','mode','half_life')}})
        excluded = []
        if strict:
            records,reused = stage_walk(stage,rows,test,config,vectors,sink=artifact_sink,exclusions=excluded)
        else:
            records,reused = walk(rows,test,config,vectors),None
        if strict and len(records) < t.MIN_TEST:
            raise ValueError(f'胜出配置可用测试样本不足{t.MIN_TEST}场')
        artifacts = list({r['artifact_ref']['digest']:r['artifact_ref'] for r in records if r.get('artifact_ref')}.values())
        if progress: progress({'phase':'test','stage_key':stage,'stage_status':'completed',
                               'metrics':t.metrics(records),'artifacts':artifacts,'reused_from':reused})
        all_models.append({**config,'test':t.metrics(records),'markets':t.market_metrics(records),'records':records,
                           **({'test_coverage':{'target_count':len(test),'forecast_count':len(records),'exclusions':excluded},
                               'test_market_coverage':market_coverage(records)} if strict else {})})
        if config['key'] == winner['key']: selected_records = records
    paired = [r for r in selected_records if r['market']]
    by_season = {s:t.metrics([r for r in selected_records if r['season']==s]) for s in sorted({r['season'] for r in test})}
    report = {'created_at':now_iso(),'competition_id':cid,'dataset_digest':fingerprint(data),
        'model_schema':t.MODEL_SCHEMA,'status':'research_only','production_enabled':False,'target':target,
        'horizon_minutes':60,'validation_start':str(vs),'test_start':str(ts),
        'feature_version':'candidate-specific-v2' if strict else FEATURE_VERSION,
        'feature_names':{c['key']:c['feature_metadata']['features'] for c in comparisons} if strict else FEATURE_NAMES,
        **({'feature_metadata_by_candidate':{c['key']:c['feature_metadata'] for c in comparisons},
            'validation_sample':{'target_count':len(validation),'common_count':len(common_ids),'fixture_ids':sorted(common_ids)},
            'capabilities_verified':True,'market_coverage':market_coverage(selected_records),
            'supported_markets':list(market_coverage(selected_records))} if strict else {}),
        'evaluation_protocol':'weekly-cutoff-common-validation-v2' if strict else 'weekly-cutoff-exploration-v1','calibration':'none',
        'candidates':comparisons,'selected':winner,'selected_by_competition':{cid:winner},
        'test':t.metrics(selected_records),'by_competition':{cid:t.metrics(selected_records)},
        'by_stage':{s:t.metrics([r for r in selected_records if r['stage']==s]) for s in sorted({r['stage'] for r in test})},
        'by_season':by_season,'records':selected_records,'markets':t.market_metrics(selected_records),
        'by_rules_era':{era:t.metrics([r for r in selected_records if r.get('rules_era', 'unknown') == era])
                       for era in sorted({r.get('rules_era', 'unknown') for r in rows})},
        'market_metrics_by_competition':{cid:t.market_metrics(selected_records)},
        'paired_model':t.metrics(paired),'market_baseline':t.metrics(paired,'market'),
        'paired_by_competition':{cid:{'model':t.metrics(paired),'market':t.metrics(paired,'market')}},
        'models':all_models,'selected_on_validation':winner['kind'],'selected_key':winner['key'],
        'test_selected_only':test_selected_only,
        'uniform_baseline':{'log_loss':math.log(3),'brier':2/3},'market_comparison_count':len(paired),
        'data_audit':{'rows':len(rows),'half_rows':sum(r.get('home_half_goals') is not None for r in rows),
                      'rules_era_rows':{era:sum(r.get('rules_era', 'unknown') == era for r in rows)
                                        for era in sorted({r.get('rules_era', 'unknown') for r in rows})},
                      'historical_odds_rows':sum(bool(r.get('odds')) for r in rows),
                      'availability_policy':data['manifest'].get('availability_policy'),
                      'missing_features':['xG','伤停','阵容','外盘']},
        'limitations':['已查看的历史测试段仅用于探索；需要独立前瞻验证',
            '赛果可用时间沿用历史模拟假设；不等同当年采集快照',
            '未使用 xG、伤停、阵容或外盘；分类器只输出胜平负',
            '半全场仍为条件二项分配；动态 Gamma 使用近似共轭滤波，非联合 MCMC'],
        'blockers':['仅生成研究候选；无独立前瞻与市场优势证明']}
    return report
