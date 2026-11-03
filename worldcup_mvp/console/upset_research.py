"""Read-only model/market upset audit and time-separated calibration research.

No prediction, selection or dataset pointer is modified. Calibrated probabilities
are HAD-only experiments, never silently mixed with archived score distributions.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import timedelta
import json
import hashlib
import math
from pathlib import Path
import sqlite3
from uuid import uuid4

import numpy as np
from scipy.optimize import minimize

from . import model_registry as registry, training as t
from .model_evaluation import walk
from .sources import parse_date
from .store import fingerprint, now_iso

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = ROOT / 'config' / 'football_upset_research.json'


def probabilities(values):
    p = np.asarray(values, dtype=float)
    if p.shape != (3,) or not np.isfinite(p).all() or (p < 0).any() or not np.isclose(p.sum(), 1, atol=1e-6):
        raise ValueError('胜平负概率必须为非负且合计为一的三项')
    return p


def _scope(records, competition):
    if any(r['competition_id'] != competition for r in records):
        raise ValueError('禁止混用不同赛事的校准记录')
    if len({r['id'] for r in records}) != len(records):
        raise ValueError('同一场比赛不能重复计入评估')
    for r in records:
        probabilities(r['probabilities'])
        if r['outcome'] not in (0, 1, 2):
            raise ValueError('胜平负结果无效')
        if any(parse_date(r[k]).tzinfo is None for k in ('trained_before', 'asof', 'result_available_at')):
            raise ValueError('校准时点必须包含时区')
        if parse_date(r['trained_before']) > parse_date(r['asof']):
            raise ValueError('模型训练晚于预测时点')
        if parse_date(r['result_available_at']) <= parse_date(r['asof']):
            raise ValueError('目标赛果已经在预测时点可用')


def transform(values, artifact, competition):
    if artifact['competition_id'] != competition:
        raise ValueError('禁止跨赛事校准')
    p = probabilities(values)
    if artifact['method'] == 'identity':
        return p.tolist()
    z = np.log(np.clip(p, 1e-12, 1)) / artifact['temperature'] + np.asarray(artifact['bias'])
    z -= z.max()
    exp = np.exp(z)
    return (exp / exp.sum()).tolist()


def fit_calibrator(records, competition, method, before, ridge):
    _scope(records, competition)
    if method not in ('identity', 'temperature', 'temperature_bias'):
        raise ValueError('未知校准方法')
    before = parse_date(before) if isinstance(before, str) else before
    if any(parse_date(r['asof']) >= before or parse_date(r['result_available_at']) >= before for r in records):
        raise ValueError('校准使用了截止时点之后的预测或结果')
    artifact = {'competition_id': competition, 'method': method, 'temperature': 1., 'bias': [0., 0., 0.],
                'trained_before': before.isoformat(), 'training_rows': len(records), 'input_digest': fingerprint(records),
                'supported_markets': ['had'], 'status': 'research_only'}
    if method == 'identity':
        return artifact
    if len(records) < 3 or len({r['outcome'] for r in records}) < 3:
        raise ValueError('校准样本未覆盖全部胜平负类别')
    x = np.log(np.clip([r['probabilities'] for r in records], 1e-12, 1))
    y = np.array([r['outcome'] for r in records])
    def unpack(theta):
        return math.exp(theta[0]), np.array([theta[1], theta[2], -theta[1]-theta[2]]) if len(theta) == 3 else np.zeros(3)
    def objective(theta):
        temperature, bias = unpack(theta)
        z = x / temperature + bias
        z -= z.max(axis=1, keepdims=True)
        log_probs = z - np.log(np.exp(z).sum(axis=1, keepdims=True))
        return float(-log_probs[np.arange(len(y)), y].mean() + ridge * (theta[0]**2 + np.sum(bias**2)))
    initial = np.zeros(3 if method == 'temperature_bias' else 1)
    bounds = [(math.log(.5), math.log(2.))] + ([(-1., 1.)]*2 if len(initial) == 3 else [])
    result = minimize(objective, initial, method='L-BFGS-B', bounds=bounds)
    if not result.success:
        raise ValueError('校准优化失败：' + str(result.message))
    temperature, bias = unpack(result.x)
    return {**artifact, 'temperature': temperature, 'bias': bias.tolist(), 'ridge': ridge}


def apply_calibrator(records, artifact):
    # Only return HAD research records. Other market grids deliberately omitted.
    if any(parse_date(r['asof']) < parse_date(artifact['trained_before']) for r in records):
        raise ValueError('校准参数在预测时点尚不可用')
    return [{k: r[k] for k in ('id', 'competition_id', 'asof', 'result_available_at', 'outcome')} |
            {'probabilities': transform(r['probabilities'], artifact, r['competition_id'])} for r in records]


def select_calibration(validation, competition, test_start, policy):
    """Does not accept test labels. All tuning uses earlier validation predictions."""
    _scope(validation, competition)
    if any(parse_date(r['asof']) >= test_start for r in validation):
        raise ValueError('校准选择不能包含测试时段的预测')
    validation = sorted(validation, key=lambda r: (parse_date(r['asof']), r['id']))
    days = sorted({parse_date(r['asof']).date() for r in validation})
    if len(days) < 2:
        raise ValueError('校准时序窗口不足')
    split_day = days[min(len(days)-1, max(1, int(len(days)*policy['calibration_fit_fraction'])))]
    selection = [r for r in validation if parse_date(r['asof']).date() >= split_day
                 and parse_date(r['result_available_at']) < test_start]
    if len(selection) < policy['calibration_min_select']:
        raise ValueError('校准选择样本不足')
    cutoff = min(parse_date(r['asof']) for r in selection)
    fitting = [r for r in validation if parse_date(r['asof']) < cutoff and parse_date(r['result_available_at']) < cutoff]
    if len(fitting) < policy['calibration_min_fit']:
        raise ValueError('校准拟合样本不足')
    artifacts = {method: fit_calibrator(fitting, competition, method, cutoff, policy['calibration_ridge'])
                 for method in policy['calibration_methods']}
    scores = {method: t.metrics(apply_calibrator(selection, a)) for method, a in artifacts.items()}
    base = scores['identity']
    eligible = [k for k, v in scores.items() if base['log_loss']-v['log_loss'] >= policy['minimum_selection_log_loss_gain']
                and v['brier'] <= base['brier']]
    selected = min(eligible, key=lambda k: scores[k]['log_loss']) if eligible else 'identity'
    return {'artifacts': artifacts, 'selection_scores': scores, 'selected': selected,
            'fit_rows': len(fitting), 'selection_rows': len(selection), 'fit_cutoff': cutoff.isoformat(),
            'selection_frozen_before': test_start.isoformat(), 'fit_ids': [r['id'] for r in fitting],
            'selection_ids': [r['id'] for r in selection]}


def market_reference(record, max_age):
    odds, observed = record.get('odds'), record.get('odds_observed_at')
    if not odds or not observed:
        return None, 'missing_odds'
    try:
        if set(odds) != set('hda'):
            return None, 'incomplete_odds'
        values = np.array([float(odds[k]) for k in 'hda'])
        if not np.isfinite(values).all() or (values <= 1).any():
            return None, 'invalid_odds'
        observed_at, asof = parse_date(observed), parse_date(record['asof'])
        if observed_at.tzinfo is None or asof.tzinfo is None:
            return None, 'unknown_timezone'
        age = (asof-observed_at).total_seconds()
        if age < 0:
            return None, 'future_odds'
        if age > max_age:
            return None, 'stale_odds'
        inverse = 1 / values
        return (inverse / inverse.sum()).tolist(), None
    except (ValueError, TypeError, OverflowError):
        return None, 'invalid_odds'


def favorite(reference, minimum):
    p = probabilities(reference)
    index = int(np.argmax(p))
    if index == 1 or p[index] < minimum or np.sum(np.isclose(p, p[index])) != 1:
        return None
    return index


def binary_metrics(cells):
    if not cells:
        return {'count': 0}
    p = np.asarray([v[0] for v in cells]); y = np.asarray([v[1] for v in cells], dtype=float)
    clipped = np.clip(p, 1e-12, 1-1e-12)
    return {'count': len(cells), 'events': int(y.sum()), 'predicted': float(p.mean()), 'observed': float(y.mean()),
            'brier': float(np.mean((p-y)**2)), 'log_loss': float(np.mean(-y*np.log(clipped)-(1-y)*np.log(1-clipped)))}


def risk_metrics(records, predictions, policy, reference='market'):
    if reference not in ('market', 'model'):
        raise ValueError('未知热门定义')
    cells = {k: [] for k in ('favorite_fails', 'underdog_win', 'draw', 'longshot_win')}
    skipped, selected, watched = Counter(), [], []
    for row in records:
        p = probabilities(predictions[row['id']])
        anchor, reason = market_reference(row, policy['max_odds_age_seconds']) if reference == 'market' else (row['probabilities'], None)
        if anchor is None:
            skipped[reason] += 1; continue
        fav = favorite(anchor, policy['favorite_min_probability'])
        if fav is None:
            skipped['no_clear_favorite'] += 1; continue
        other = 2 if fav == 0 else 0
        selected.append(row['id'])
        cells['favorite_fails'].append((1-p[fav], row['outcome'] != fav))
        cells['underdog_win'].append((p[other], row['outcome'] == other))
        cells['draw'].append((p[1], row['outcome'] == 1))
        if reference == 'market' and anchor[other] <= policy['longshot_max_market_probability']:
            cells['longshot_win'].append((p[other], row['outcome'] == other))
            if p[other] >= policy['watch_min_probability'] and p[other]-anchor[other] >= policy['watch_min_market_gap']:
                watched.append((p[other], row['outcome'] == other))
    return {'reference': reference, 'label': '市场冷门' if reference == 'market' else '模型反向结果（非市场冷门）',
            'cohort_ids': selected, 'skipped': dict(skipped), **{k: binary_metrics(v) for k, v in cells.items()},
            'watchlist': binary_metrics(watched), 'return_metrics': None}


def class_metrics(records):
    out = {}
    for index, name in enumerate(('home', 'draw', 'away')):
        cells = [(r['probabilities'][index], r['outcome'] == index) for r in records]
        bins = []
        for n in range(5):
            group = [v for v in cells if n/5 <= v[0] < (n+1)/5 or n == 4 and v[0] == 1]
            bins.append({'lower': n/5, 'upper': (n+1)/5, **binary_metrics(group)})
        out[name] = {**binary_metrics(cells), 'bins': bins}
    return out


def paired_interval(base, adjusted, policy):
    if [(r['id'], r['outcome']) for r in base] != [(r['id'], r['outcome']) for r in adjusted]:
        raise ValueError('比较必须使用相同比赛与结果')
    groups = defaultdict(list)
    for b, a in zip(base, adjusted):
        at = parse_date(b['asof']); week = (at-timedelta(days=at.weekday())).date().isoformat()
        difference = -math.log(max(1e-12, a['probabilities'][a['outcome']])) + math.log(max(1e-12, b['probabilities'][b['outcome']]))
        groups[week].append(difference)
    if len(groups) < 10:
        return {'blocks': len(groups), 'status': 'insufficient_blocks'}
    values = list(groups.values()); sums = np.array([sum(v) for v in values]); counts = np.array([len(v) for v in values])
    rng = np.random.default_rng(policy['bootstrap_seed'])
    indices = rng.integers(0, len(values), (policy['bootstrap_draws'], len(values)))
    means = sums[indices].sum(axis=1) / counts[indices].sum(axis=1)
    return {'blocks': len(groups), 'method': 'paired_week_cluster_bootstrap', 'draws': policy['bootstrap_draws'],
            'delta_log_loss': float(sums.sum()/counts.sum()), 'interval_95': np.quantile(means, [.025, .975]).tolist(),
            'interpretation': '负值表示校准误差较低；已查看测试段仅作探索'}


def archived_odds_inventory(competition, policy, path=None):
    """Count distinct eligible pre-match archives, not versions or current odds."""
    path = Path(path or ROOT/'data'/'console'/'football.sqlite3').resolve()
    if not path.exists():
        return {'unique_matches': 0, 'eligible_matches': 0, 'reason': 'database_missing'}
    eligible, seen, rejected = set(), set(), Counter()
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
        for match_id, created, raw_input, raw_prediction in db.execute('SELECT match_id,created_at,input_snapshot,payload FROM analyses'):
            source, prediction = json.loads(raw_input), json.loads(raw_prediction)
            match = source.get('match', {})
            if match.get('competition', {}).get('id') != competition:
                continue
            seen.add(match_id)
            if parse_date(created) >= parse_date(match['kickoff']):
                rejected['post_match_archive'] += 1; continue
            pool = source.get('markets', {}).get('had', {})
            options = pool.get('options', [])
            odds = {o['key']: o.get('odds') for o in options}
            if len(options) != len(odds):
                rejected['duplicate_options'] += 1; continue
            _, reason = market_reference({'odds': odds, 'odds_observed_at': prediction.get('source_times', {}).get('markets'),
                                          'asof': created}, policy['max_odds_age_seconds'])
            if reason:
                rejected[reason] += 1
            else:
                eligible.add(match_id)
    return {'unique_matches': len(seen), 'eligible_matches': len(eligible), 'eligible_ids': sorted(eligible),
            'rejected_versions': dict(rejected), 'horizon': '各归档实际创建时点；未与历史训练库自动拼接'}


def audit_run(competition, run_id, policy=None, progress=print):
    policy = policy or registry.read(PROTOCOL_PATH)
    manifest, bundle, old, data = registry.load_run(competition, run_id)
    rows = t.validate_dataset(data)
    if {r['competition_id'] for r in rows} != {competition}:
        raise ValueError('研究数据必须为单一赛事')
    _, _, _, validation, _ = t.split_dataset(rows)
    indexed = {r['id']: r for r in rows}
    config = old['selected_by_competition'][competition]
    progress(competition + ': 生成校准训练所需的历史滚动预测', flush=True)
    validation_records = walk(rows, validation, config, None)
    def enrich(records):
        return [{**r, **{k: indexed[r['id']].get(k) for k in ('result_available_at', 'odds', 'odds_observed_at')}} for r in records]
    validation_records, records = enrich(validation_records), enrich(old['records'])
    _scope(records, competition)
    frozen = select_calibration(validation_records, competition, min(parse_date(r['asof']) for r in records), policy)
    outputs = {}
    for method, artifact in frozen['artifacts'].items():
        changed = apply_calibrator(records, artifact)
        lookup = {r['id']: r['probabilities'] for r in changed}
        outputs[method] = {'overall': t.metrics(changed), 'by_class': class_metrics(changed),
                           'model_counter_outcomes': risk_metrics(records, lookup, policy, 'model'),
                           'market_upsets': risk_metrics(records, lookup, policy, 'market'),
                           'paired_vs_raw': paired_interval(records, changed, policy), 'records': changed}
    valid_markets = sum(market_reference(r, policy['max_odds_age_seconds'])[0] is not None for r in records)
    market_events = outputs['identity']['market_upsets']['underdog_win'].get('events', 0)
    inventory = archived_odds_inventory(competition, policy)
    challengers = {}
    for model in old.get('models', []):
        comparison = enrich(model['records'])
        _scope(comparison, competition)
        lookup = {r['id']: r['probabilities'] for r in comparison}
        if set(lookup) != {r['id'] for r in records}:
            raise ValueError('候选算法必须在同一比赛集上比较')
        challengers[model['key']] = {'overall': t.metrics(comparison),
            'model_counter_outcomes': risk_metrics(records, lookup, policy, 'model')}
    code_paths = ['worldcup_mvp/console/'+name+'.py' for name in
                  ('upset_research', 'model_evaluation', 'model_methods', 'model_benchmark', 'training')]
    report = {'created_at': now_iso(), 'competition_id': competition, 'reference_run_id': run_id,
              'dataset_digest': manifest['dataset_digest'], 'policy': policy, 'policy_digest': fingerprint(policy),
              'reference_manifest_digest': manifest['digest'],
              'code_digests': {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in code_paths},
              'base_method': config['key'], 'status': 'research_only', 'production_enabled': False,
              'calibration': frozen, 'experiments': outputs, 'challengers_same_cohort': challengers,
              'market_data': {'historical_rows_with_odds': sum(bool(r.get('odds')) for r in rows),
                              'test_usable_odds': valid_markets, 'archive_inventory': inventory,
                              'specialist_training': 'not_trained',
                              'reason': '历史赛前赔率与独立训练样本未达本协议门槛',
                              'minimum_rows': policy['market_training_min_rows'],
                              'minimum_underdog_wins': policy['market_training_min_underdog_wins'],
                              'test_underdog_wins': market_events},
              'data_freshness': {'latest_dataset_kickoff': max(r['kickoff'] for r in rows),
                                 'availability_policy': data['manifest'].get('availability_policy')},
              'limitations': ['测试段已被查看，仅作历史探索，不等同独立前瞻验证',
                  '基础算法已在原验证段选定；校准拟合与选择按时间再分开，边界按结果可用时间剔除',
                  '模型反向结果不能代替市场冷门；风险概率不等于可盈利的博冷策略',
                  '未新增外盘、xG、首发或伤停输入；校准实验仅胜平负，不写回其他玩法',
                  '市场模型训练门槛只是本次实验最低要求，满足也不代表通过验收']}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--competition', required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    # Freeze the protocol before any new fit. Keep all output within this competition.
    policy = registry.read(PROTOCOL_PATH)
    out = registry.directory(args.competition)/'research'/'upsets'/uuid4().hex
    out.mkdir(parents=True, exist_ok=False)
    (out/'protocol.json').write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding='utf8')
    report = audit_run(args.competition, args.run_id, policy)
    path = out/'report.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'report': str(path), 'selected_on_validation': report['calibration']['selected'],
                      'test': {k: v['overall']['log_loss'] for k, v in report['experiments'].items()},
                      'market_data': report['market_data']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
