"""Opt-in cold-cohort research. Existing five-match admission is never changed."""
import numpy as np
from . import training as t
from .model_evaluation import walk, feature_metadata, market_coverage
from .sources import parse_date
from .store import fingerprint, now_iso
from .model_benchmark import cutoff

POLICY = 'gamma_cold_start_v1'
CONFIG = {'kind':'dynamic_gamma','mode':'separate','half_life':180}
MIN_COHORT = 30


def stratum(counts):
    count = min(counts)
    return '0' if count == 0 else '1-2' if count <= 2 else '3-4' if count <= 4 else '5+'


def uncertainty(model, row):
    rng = np.random.default_rng(model.payload['seed'])
    intervals = []
    for side, other in (('home','away'),('away','home')):
        attack = model.payload['states'].get(row[side+'_id'],{}).get('attack',[8,8])
        defence = model.payload['states'].get(row[other+'_id'],{}).get('defence',[8,8])
        base = model.payload['league'][side]
        rates = rng.gamma(attack[0],1/attack[1],512)*rng.gamma(defence[0],1/defence[1],512)*base[0]/base[1]
        intervals.append([float(v) for v in np.quantile(rates,[.05,.95])])
    return {'method':'gamma-mean-field-rate-posterior','level':.9,'goal_rate_intervals':intervals,
            'interpretation':'parameter_uncertainty_not_match_score_interval','draws':512}


def extra(model, row):
    rates = [model.payload['league'][s][0]/model.payload['league'][s][1] for s in ('home','away')]
    baseline = t.market_probabilities(t.score_grid(*rates))['had']
    return {'league_prior':[baseline[k] for k in 'hda'], 'uncertainty':uncertainty(model,row)}


def comparison(records):
    measured, baseline = t.metrics(records), t.metrics(records,'league_prior')
    passed = (measured['count'] >= MIN_COHORT and measured['log_loss'] <= baseline['log_loss']
              and measured['brier'] <= baseline['brier'])
    return {'model':measured,'league_prior':baseline,'passed':passed,
            'reason':None if passed else 'insufficient_cohort' if measured['count'] < MIN_COHORT else 'baseline_not_beaten'}


def evaluate(data, progress=None, artifact_sink=None):
    rows = t.validate_dataset(data)
    cid = data['manifest']['competition_id']
    if {r['competition_id'] for r in rows} != {cid}:
        raise ValueError('冷启动评估必须属于单一赛事')
    t.split_dataset(rows)
    from .catalog import catalog
    spec = next(c for c in catalog() if c['id']==cid)
    prediction_stages = {stage for values in spec.get('prediction_stage_policy',{}).values() for stage in values}
    if not prediction_stages:
        raise ValueError('冷启动策略需要明确预测阶段')
    # Fixed predeclared strategy; every eligible fixture after minimum training history.
    targets=[]
    for row in rows:
        if row['stage'] not in prediction_stages:continue
        at=cutoff(row)
        available=[r for r in rows if parse_date(r['result_available_at'])<at and parse_date(r['kickoff'])<at]
        if len(available)>=t.MIN_ROWS and len({r['season'] for r in available})>=t.MIN_SEASONS:
            targets.append(row)
    test_start=targets[0]['kickoff'] if targets else None
    if progress:progress({'phase':'cold_cohort','stage_key':'cold_cohort','stage_status':'running','configuration':CONFIG})
    checkpoint = getattr(artifact_sink,'checkpoint',None)
    cached = checkpoint(POLICY,CONFIG,rows,targets) if checkpoint else None
    records = cached['records'] if cached else walk(rows,targets,CONFIG,None,exclusions=[],artifact_sink=artifact_sink,
                                                   minimum_team_samples=0,record_extra=extra)
    if checkpoint and not cached:checkpoint(POLICY,CONFIG,rows,targets,records=records,exclusions=[])
    cold = [r for r in records if min(r['team_samples']) < 5]
    warm = [r for r in records if min(r['team_samples']) >= 5]
    strata = {key:comparison([r for r in cold if stratum(r['team_samples']) == key]) for key in ('0','1-2','3-4')}
    overall = comparison(cold)
    warm_comparison = comparison(warm)
    allowed = [key for key,value in strata.items() if value['passed']] if overall['passed'] and warm_comparison['passed'] else []
    report = {'created_at':now_iso(),'competition_id':cid,'dataset_digest':fingerprint(data),
        'model_schema':t.MODEL_SCHEMA,'status':'research_only','production_enabled':False,
        'evaluation_type':'cold_start_strategy','evaluation_protocol':POLICY,'configuration':CONFIG,
        'test_start':str(test_start),'test':t.metrics(cold),'records':cold,'baseline':overall['league_prior'],
        'warm_comparison':warm_comparison,'warm_records':warm,
        'cohort':{'definition':'minimum of both team sample counts at weekly fit cutoff is 0 through 4',
                  'minimum':MIN_COHORT,'target_count':len(targets),'cold_count':len(cold),'warm_count':len(records)-len(cold)},
        'strata':strata,'admission':{'policy':POLICY,'passed':bool(allowed),'allowed_strata':allowed,
                                  'identity_policy':'exact-espn-id-v1','target_stages':sorted(prediction_stages),
                                  'overall':overall,'warm':warm_comparison,
                                  'criteria':'cold overall, admitted stratum and warm each count>=30 and log_loss<=same-fixture league prior and brier<=same-fixture league prior'},
        'by_rules_era':{era:comparison([r for r in cold if r.get('rules_era')==era]) for era in sorted({r.get('rules_era','unknown') for r in cold})},
        'feature_metadata':feature_metadata('dynamic_gamma'),'supported_markets':list(market_coverage(cold)),
        'market_coverage':market_coverage(cold),'capabilities_verified':True,
        'limitations':['历史探索评估，非未查看过的独立检验','固定候选模型尚无前瞻成绩','低样本队伍依赖同赛事先验；参数区间不代表比分区间','不使用其他赛事或球队名推断实力']}
    if progress:progress({'phase':'cold_cohort','stage_key':'cold_cohort','stage_status':'completed',
                          'metrics':report['test'],'coverage':report['cohort'],
                          'artifacts':list({r['artifact_ref']['digest']:r['artifact_ref'] for r in records if r.get('artifact_ref')}.values())})
    return report


def allowed(policy, counts):
    return bool(policy and policy.get('policy') == POLICY and policy.get('passed') is True
                and len(counts)==2 and all(isinstance(n,int) and n>=0 for n in counts)
                and stratum(counts) in policy.get('allowed_strata',[]))


def validate_binding(bundle, report):
    policy = bundle.get('cold_start_policy')
    if not policy:
        return
    if (policy.get('policy') != POLICY or policy.get('passed') is not True
        or policy.get('dataset_digest') != bundle['dataset_digest']
        or not policy.get('report_id') or not policy.get('report_digest')
        or report.get('cold_start_policy') != policy
        or any(m.get('kind') != 'dynamic_gamma' or m.get('half_life') != 180 for m in bundle['models'].values())):
        raise ValueError('冷启动策略与模型绑定校验失败')
