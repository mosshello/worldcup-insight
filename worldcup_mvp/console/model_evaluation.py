"""Shared experiment for CLI and console. No selection side effects."""
from datetime import timedelta
import math

from . import training as t
from .model_methods import configs, fit, features, cutoff, NAMES, capabilities, FEATURE_VERSION, FEATURE_NAMES
from .sources import parse_date
from .store import now_iso, fingerprint


def walk(rows, targets, config, vectors):
    models, records = {}, []
    for row in targets:
        at = cutoff(row)
        cid = row['competition_id']
        if at not in models:
            models[at] = fit(config,rows,at,cid,vectors)
        forecast = models[at].forecast(row,row.get('handicap'))
        probabilities = [forecast['markets']['had'][key] for key in 'hda']
        asof = parse_date(row['kickoff'])-timedelta(minutes=60)
        odds, market = row.get('odds'), None
        if odds and 0 <= (asof-parse_date(row['odds_observed_at'])).total_seconds() <= 10800:
            mass = sum(1/v for v in odds.values())
            market = [1/odds[k]/mass for k in 'hda']
        h,a = row['home_goals'],row['away_goals']
        records.append({'id':row['id'],'competition_id':cid,'season':row['season'],'stage':row['stage'],
            'asof':asof.isoformat(),'trained_before':at.isoformat(),'feature_cutoff':at.isoformat(),
            'probabilities':probabilities,'outcome':0 if h>a else 2 if a>h else 1,
            'market':market,'markets':forecast['markets'],
            'actual_markets':{'had':t.outcome(h,a),'ttg':f's{min(h+a,7)}','crs':t.score_key(h,a),
                **({'hhad':t.outcome(h+row['handicap'],a)} if row.get('handicap') is not None else {}),
                **({'hafu':t.outcome(row['home_half_goals'],row['away_half_goals'])+t.outcome(h,a)} if row.get('home_half_goals') is not None else {})}})
    return records


def evaluate(data, progress=None, candidates=None, target='full_time'):
    if target not in ('full_time','had'):
        raise ValueError('训练目标无效')
    rows = t.validate_dataset(data)
    leagues, vs, ts, validation, test = t.split_dataset(rows)
    if len(leagues) != 1:
        raise ValueError('多方法训练必须指定单一赛事')
    cid = leagues[0]
    candidates = candidates or configs()
    vectors = {r['id']:features(rows,r,cutoff(r)) for r in rows}
    comparisons = []
    # Freeze validation winner before evaluating any test outcomes.
    for i,config in enumerate(candidates):
        if progress: progress({'phase':'validation','algorithm':NAMES[config['kind']], 'completed':i,'total':len(candidates)})
        records = walk(rows,validation,config,vectors)
        scores = t.metrics(records)
        comparisons.append({**config,'key':config['kind']+':'+str(config['half_life']),
                            'name':NAMES[config['kind']],'supported_markets':capabilities(config['kind']),
                            'validation':scores,'by_competition':{cid:scores}})
    required = {'had','ttg','crs'} if target == 'full_time' else {'had'}
    for c in comparisons:
        c['eligible_for_target'] = required.issubset(c['supported_markets'])
    winner = min((c for c in comparisons if c['eligible_for_target']),key=lambda c:c['validation']['log_loss'])
    all_models, selected_records = [], None
    for i,config in enumerate(comparisons):
        if progress: progress({'phase':'test','algorithm':NAMES[config['kind']], 'completed':i,'total':len(candidates)})
        records = walk(rows,test,config,vectors)
        all_models.append({**config,'test':t.metrics(records),'markets':t.market_metrics(records),'records':records})
        if config['key'] == winner['key']: selected_records = records
    paired = [r for r in selected_records if r['market']]
    by_season = {s:t.metrics([r for r in selected_records if r['season']==s]) for s in sorted({r['season'] for r in test})}
    report = {'created_at':now_iso(),'competition_id':cid,'dataset_digest':fingerprint(data),
        'model_schema':t.MODEL_SCHEMA,'status':'research_only','production_enabled':False,'target':target,
        'horizon_minutes':60,'validation_start':str(vs),'test_start':str(ts),
        'feature_version':FEATURE_VERSION,'feature_names':FEATURE_NAMES,
        'evaluation_protocol':'weekly-cutoff-exploration-v1','calibration':'none',
        'candidates':comparisons,'selected':winner,'selected_by_competition':{cid:winner},
        'test':t.metrics(selected_records),'by_competition':{cid:t.metrics(selected_records)},
        'by_stage':{s:t.metrics([r for r in selected_records if r['stage']==s]) for s in sorted({r['stage'] for r in test})},
        'by_season':by_season,'records':selected_records,'markets':t.market_metrics(selected_records),
        'market_metrics_by_competition':{cid:t.market_metrics(selected_records)},
        'paired_model':t.metrics(paired),'market_baseline':t.metrics(paired,'market'),
        'paired_by_competition':{cid:{'model':t.metrics(paired),'market':t.metrics(paired,'market')}},
        'models':all_models,'selected_on_validation':winner['kind'],'selected_key':winner['key'],
        'uniform_baseline':{'log_loss':math.log(3),'brier':2/3},'market_comparison_count':len(paired),
        'data_audit':{'rows':len(rows),'half_rows':sum(r.get('home_half_goals') is not None for r in rows),
                      'historical_odds_rows':sum(bool(r.get('odds')) for r in rows),
                      'availability_policy':data['manifest'].get('availability_policy'),
                      'missing_features':['xG','伤停','阵容','外盘']},
        'limitations':['已查看的历史测试段仅用于探索；需要独立前瞻验证',
            '赛果可用时间沿用历史模拟假设；不等同当年采集快照',
            '未使用 xG、伤停、阵容或外盘；分类器只输出胜平负',
            '半全场仍为条件二项分配；动态 Gamma 使用近似共轭滤波，非联合 MCMC'],
        'blockers':['仅生成研究候选；无独立前瞻与市场优势证明']}
    return report
