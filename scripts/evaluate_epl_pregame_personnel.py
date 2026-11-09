"""Reproducible EPL-only rolling personnel ablation; never selects production."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.import_big_five_prematch_archives import archive, captures, available_at, FPL_NAMES
from scripts.evaluate_observation_personnel import metrics
from scripts.research_big_five_context import checked_lines
from worldcup_mvp.console import model_registry, training
from worldcup_mvp.console.observation_models import history_rows, vectors
from worldcup_mvp.console.observation_epl_personnel import CID, fit, fpl_vectors, probabilities, blend
from worldcup_mvp.console.observation_personnel_models import immutable
from worldcup_mvp.console.observation_features import instant
from worldcup_mvp.console.football_markets import outcome, score_key
from worldcup_mvp.console.store import ROOT, fingerprint

OUT = ROOT/'data/research/epl-pregame-personnel'
MARKETS = ('had', 'hhad', 'ttg', 'crs', 'hafu')
ALPHAS = (.08, .5)
WEIGHTS = (0., .25, .5, .75, 1.)


def protected_state():
    """Production artifact trees and historical row hashes, excluding research."""
    paths = list(model_registry.REGISTRY.rglob('*'))
    files = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in paths if p.is_file()}
    return files


def prepare(data):
    rows = data['matches']
    history = history_rows(rows, CID)
    names = {}
    for r in rows:
        for side in ('home', 'away'):
            key = (r['season'], r[side+'_name'])
            if key in names and names[key] != r[side+'_id']:
                raise ValueError('赛季球队名称冲突')
            names[key] = r[side+'_id']
    z, receipt = archive('fpl')
    samples, excluded, payloads = [], [], {}
    with z:
        observations, rejected = captures(z)
        for season, obs in observations.items():
            for rec in obs.values():
                path = rec['manifest_path'].rsplit('/', 1)[0]+'/bootstrap-static.json.gz'
                payload = json.loads(gzip.decompress(z.read(path)))
                mapping = {str(t['id']): names.get((season, FPL_NAMES.get(t['name'], t['name'])))
                           for t in payload['teams']}
                ref = immutable({'competition_id': CID, 'schema': 'fpl-snapshot-v1',
                    'season': season, 'captured_at': rec['captured_at'], 'payload': payload,
                    'source_receipt': receipt, 'manifest_path': rec['manifest_path'],
                    'raw_sha256': rec['payload_sha256'], 'team_mapping': mapping}, OUT/'inputs')
                payloads[rec['payload_sha256']] = (payload, mapping, ref)
        for r in rows:
            at = instant(r['kickoff']) - timedelta(hours=1)
            try:
                rec = available_at(observations.get(r['season'], {}), at)
                if not rec:
                    raise ValueError('没有T-60前72小时内FPL快照')
                payload, mapping, ref = payloads[rec['payload_sha256']]
                nv, nr = fpl_vectors(payload, r, at, rec['captured_at'], mapping)
                xv, xr = vectors(history, r, at, historical=True)
                samples.append({'id': r['id'], 'competition_id': CID, 'season': r['season'],
                    'kickoff': r['kickoff'], 'as_of': at.isoformat(),
                    'available_at': max(instant(r['result_available_at']), instant(r['kickoff'])+timedelta(days=7)).isoformat(),
                    'vectors': [dict(xv[i], **nv[i]) for i in (0, 1)],
                    'goals': [r['home_goals'], r['away_goals']],
                    'half_goals': [r.get('home_half_goals'), r.get('away_half_goals')],
                    'history_refs': xr, 'personnel_refs': nr, 'snapshot_reference': ref})
            except ValueError as exc:
                excluded.append({'id': r['id'], 'season': r['season'], 'reason': str(exc)})
    return samples, excluded, history, rejected


def actuals(sample, line):
    h, a = sample['goals']
    actual = {'had': outcome(h, a), 'ttg': f's{min(h+a,7)}', 'crs': score_key(h, a)}
    if line is not None:
        actual['hhad'] = outcome(h+line, a)
    if all(v is not None for v in sample['half_goals']):
        actual['hafu'] = outcome(*sample['half_goals']) + outcome(h, a)
    return actual


def market_reference(row, as_of):
    quotes = row.get('prematch_market_archive', {}).get('quotes', [])
    valid = [q for q in quotes if q['market'] == 'FT_1X2'
             and instant(as_of)-timedelta(seconds=180) <= instant(q['quoted_at']) <= instant(as_of)]
    if not valid:
        return None
    # Fixed company priority, then latest; never choose the best-looking quote.
    priority = ['Pinnacle', 'Bet365', 'Betfair', 'Betsson', 'Unibet']
    book = min({q['book'] for q in valid}, key=lambda b: priority.index(b))
    q = max([q for q in valid if q['book'] == book], key=lambda q: q['quoted_at'])
    vals = {k: 1/q['decimal_prices'][v] for k, v in [('h', 'Home'), ('d', 'Draw'), ('a', 'Away')]}
    total = sum(vals.values())
    return {'had': {k: v/total for k, v in vals.items()}}


def run_period(rows, samples, start, end, input_ref, alphas, fixed_weight=None):
    fixtures = {r['id']: r for r in rows}
    official = {r['id']: r for r in checked_lines(CID)[0]['official_rows']}
    targets = [r for r in rows if start <= instant(r['kickoff']) < end]
    by_id = {r['id']: r for r in samples}
    months = sorted({r['kickoff'][:7] for r in targets})
    records, exclusions, folds, base_all = [], [], {}, []
    for month in months:
        cutoff = instant(month+'-01T00:00:00+00:00')
        dc = training.GoalModel('separate', 180, 'dc').fit(rows, cutoff, CID)
        dc_ref = immutable({'competition_id': CID, 'model': dc.to_dict(),
                            'train_ids': [r['id'] for r in rows if instant(r['result_available_at']) < cutoff]}, OUT/'folds')
        fitted = {'xg': fit(samples, cutoff, .08, False, input_ref)}
        fitted.update({f'personnel_{alpha}': fit(samples, cutoff, alpha, True, input_ref) for alpha in alphas})
        refs = {k: immutable(v, OUT/'folds') for k, v in fitted.items()}
        folds[month] = {'dc': dc_ref, **refs}
        for r in targets:
            if r['kickoff'][:7] != month:
                continue
            at = instant(r['kickoff'])-timedelta(hours=1)
            if cutoff > at:
                exclusions.append({'id': r['id'], 'reason': 'monthly_fit_after_forecast_cutoff'})
                continue
            if any(dc.team_counts.get(CID+':'+r[s+'_id'], 0) < 5 for s in ('home', 'away')):
                exclusions.append({'id': r['id'], 'reason': 'baseline_prior_support'})
                continue
            q = official.get(r['id'], {}).get('hh')
            line = q['line'] if q and instant(q['time']) <= at else None
            base = dc.forecast(r, line)['markets']
            pseudo = {'goals': [r['home_goals'], r['away_goals']],
                      'half_goals': [r.get('home_half_goals'), r.get('away_half_goals')]}
            base_all.append({'id': r['id'], 'actual': actuals(pseudo, line), 'predictions': {'dc': base}})
            sample = by_id.get(r['id'])
            if sample is None:
                exclusions.append({'id': r['id'], 'reason': 'personnel_or_xg_input_unavailable'})
                continue
            if any(sample['id'] in model['train_ids'] for model in fitted.values()):
                raise ValueError('测试目标进入本折拟合')
            preds = {'dc': base, **{k: probabilities(v, sample['vectors'], line) for k, v in fitted.items()}}
            market = market_reference(r, sample['as_of'])
            if market:
                preds['market'] = market
            for alpha in alphas:
                for weight in (WEIGHTS if fixed_weight is None else (fixed_weight,)):
                    preds[f'blend_{alpha}_{weight}'] = blend(base, preds[f'personnel_{alpha}'], weight)
            records.append({'id': r['id'], 'season': r['season'], 'kickoff': r['kickoff'], 'as_of': sample['as_of'],
                'actual': actuals(sample, line), 'line': line, 'predictions': preds, 'fold': month,
                'snapshot_reference': sample['snapshot_reference'], 'sample_digest': fingerprint(sample)})
        print('fold', month, 'common_so_far', len(records), flush=True)
    return {'targets': len(targets), 'common_count': len(records), 'base_count': len(base_all),
            'records': records, 'exclusions': exclusions, 'folds': folds,
            'base_all_metrics': {m: metrics(base_all, 'dc', m) for m in MARKETS}}


def score(records, source, market):
    selected = [r for r in records if source in r['predictions']]
    result = metrics(selected, source, market)
    if result['n']:
        k = 3 if market == 'crs' else 2 if market == 'ttg' else 1
        pairs = [(r['predictions'][source][market], r['actual'][market]) for r in selected
                 if market in r['actual'] and market in r['predictions'][source]]
        result['selection_count'] = k
        result['selection_coverage'] = sum(a in sorted(p, key=p.get, reverse=True)[:k] for p, a in pairs)/len(pairs)
    return result


def interval(records, source):
    import numpy as np
    grouped = defaultdict(list)
    for r in records:
        iso = instant(r['kickoff']).isocalendar()
        actual = r['actual']['had']
        p, b = r['predictions'][source]['had'], r['predictions']['dc']['had']
        grouped[(iso.year, iso.week)].append([-__import__('math').log(max(p[actual], 1e-15))
            + __import__('math').log(max(b[actual], 1e-15)), int(max(p, key=p.get) == actual)-int(max(b, key=b.get) == actual)])
    if len(grouped) < 2:
        return {'weeks': len(grouped)}
    totals = np.array([np.sum(v, axis=0) for v in grouped.values()]); counts = np.array([len(v) for v in grouped.values()])
    rng = np.random.default_rng(20261109); idx = rng.integers(0, len(counts), (5000, len(counts)))
    draws = totals[idx].sum(1)/counts[idx].sum(1)[:, None]
    return {'weeks': len(counts), 'replicates': 5000,
            'log_loss_delta_ci95': np.quantile(draws[:, 0], [.025, .975]).tolist(),
            'accuracy_delta_ci95': np.quantile(draws[:, 1], [.025, .975]).tolist(),
            'scope': 'descriptive_reused_historical_period_not_promotion_certificate'}


def final_blend(final, rows, cutoff, weight, decision_reference):
    dc = training.GoalModel('separate', 180, 'dc').fit(rows, instant(cutoff), CID)
    base_ref = immutable({'competition_id': CID, 'model': dc.to_dict(),
        'train_ids': [r['id'] for r in rows if instant(r['result_available_at']) < instant(cutoff)]}, OUT/'models')
    candidate = {k: v for k, v in final.items() if k != 'digest'}
    candidate.update(blend_baseline_reference=base_ref, blend_weight=weight,
                     validation_decision_reference=decision_reference)
    return immutable(candidate, OUT/'models')


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--prepared', help='Reuse verified input artifact')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    before = protected_state()
    data = model_registry.dataset(CID, admission=False); rows = training.validate_dataset(data)
    if args.prepared:
        from worldcup_mvp.console.observation_personnel_models import load
        prepared = load(args.prepared, CID)
        if prepared['dataset_digest'] != fingerprint(data):
            raise ValueError('研究输入与当前数据版本不一致')
        input_ref = args.prepared
    else:
        samples, excluded, history, rejected = prepare(data)
        prepared = {'competition_id': CID, 'samples': samples, 'excluded': excluded, 'history': history,
                    'dataset_digest': fingerprint(data), 'rejected_captures': rejected}
        input_ref = immutable(prepared, OUT/'prepared')
    samples = prepared['samples']
    print('prepared', input_ref, 'samples', len(samples), 'excluded', len(prepared['excluded']), flush=True)
    validation = run_period(data['matches'], samples, instant('2024-07-01T00:00:00Z'),
                            instant('2025-07-01T00:00:00Z'), input_ref, ALPHAS)
    records = validation['records']
    if len(records) < 100:
        raise ValueError('验证共同样本不足100场')
    chosen_alpha = min(ALPHAS, key=lambda a: score(records, f'personnel_{a}', 'had')['log_loss'])
    chosen_weight = min(WEIGHTS, key=lambda w: score(records, f'blend_{chosen_alpha}_{w}', 'had')['log_loss'])
    decision = {'competition_id': CID, 'schema': 'epl-validation-decision-v1', 'alpha': chosen_alpha,
                'weight': chosen_weight, 'validation_reference': immutable({'competition_id': CID, **validation}, OUT/'validation'),
                'validation_metrics': {s: score(records, s, 'had') for s in records[0]['predictions']},
                'protocol_digest': hashlib.sha256((ROOT/'docs/reports/2026-11-09-epl-personnel-protocol.md').read_bytes()).hexdigest()}
    decision_ref = immutable(decision, OUT/'decisions')
    print('frozen_selection', chosen_alpha, chosen_weight, decision_ref, flush=True)
    now = datetime.now(timezone.utc)
    test = run_period(data['matches'], samples, instant('2025-07-01T00:00:00Z'), now, input_ref, (chosen_alpha,), chosen_weight)
    sources = ['dc', 'xg', f'personnel_{chosen_alpha}', f'blend_{chosen_alpha}_{chosen_weight}', 'market']
    test['metrics'] = {m: {s: score(test['records'], s, m) for s in sources} for m in MARKETS}
    market_common = [r for r in test['records'] if 'market' in r['predictions']]
    test['market_common_metrics'] = {s: score(market_common, s, 'had') for s in sources}
    test['intervals'] = {s: interval(test['records'], s) for s in sources if s != 'market'}
    final = fit(samples, now, chosen_alpha, True, input_ref)
    pure_ref = immutable(final, OUT/'models')
    final_ref = final_blend(final, rows, now, chosen_weight, decision_ref)
    after = protected_state()
    result = {'competition_id': CID, 'schema': 'epl-pregame-validation-v1', 'generated_at': now.isoformat(),
        'input_reference': input_ref, 'decision_reference': decision_ref, 'selected_alpha': chosen_alpha, 'pure_personnel_candidate': pure_ref,
        'selected_blend_weight': chosen_weight, 'final_candidate': final_ref, 'final_train_count': final['train_count'],
        'coverage_by_season': dict(Counter(r['season'] for r in samples)), 'excluded': prepared['excluded'], 'test': test,
        'protected_file_count': len(before), 'protected_changed': [p for p in before if after.get(p) != before[p]],
        'limitations': ['retrospective_xg_7day_assumption', 'previously_explored_test_period',
            'partial_FPL_reports_not_confirmed_lineup', 'snapshot_minutes_not_replacement_strength',
            'historical_AI_not_replayed', 'no_automatic_production_selection']}
    result_ref = immutable(result, OUT/'reports')
    (OUT/'latest.json').write_text(json.dumps({'report': result_ref, 'candidate': final_ref,
        'inputs': input_ref, 'decision': decision_ref}, indent=2), encoding='utf-8')
    print(json.dumps({'report': result_ref, 'candidate': final_ref, 'targets': test['targets'],
        'common': test['common_count'], 'final_train': final['train_count'], 'metrics': test['metrics']['had'],
        'protected_changed': result['protected_changed']}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
