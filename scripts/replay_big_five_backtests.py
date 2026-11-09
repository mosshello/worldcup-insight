"""Replay existing frozen league models. Never fit, select or rewrite an artifact."""
import argparse
from collections import Counter
from datetime import timedelta
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from worldcup_mvp.console import model_registry as registry, training
from worldcup_mvp.console.model_evaluation import evaluate_task
from worldcup_mvp.console.model_methods import restore
from worldcup_mvp.console.model_tasks import save_report
from worldcup_mvp.console.sources import parse_date
from worldcup_mvp.console.store import fingerprint, now_iso

LEAGUES = ('eng.premier', 'esp.laliga', 'ger.bundesliga', 'ita.serie_a', 'fra.ligue1')
MARKETS = ('had', 'hhad', 'ttg', 'crs', 'hafu')


def accuracy(hits, count):
    if not count:
        return {'count': 0, 'hits': 0, 'accuracy': None, 'wilson95': None}
    p, z = hits / count, 1.959963984540054
    centre = (p + z*z/(2*count)) / (1 + z*z/count)
    radius = z * math.sqrt(p*(1-p)/count + z*z/(4*count*count)) / (1 + z*z/count)
    return {'count': count, 'hits': hits, 'accuracy': p,
            'wilson95': [centre-radius, centre+radius]}


def summarize(records):
    result = {}
    for code in MARKETS:
        rows = [r for r in records if r['markets'].get(code) and code in r['actual_markets']]
        hits = covered = selected = selected_hits = 0
        for row in rows:
            probs, actual = row['markets'][code], row['actual_markets'][code]
            ranked = sorted(probs, key=probs.get, reverse=True)
            hits += ranked[0] == actual
            count = 2 if code == 'ttg' else 3 if code == 'crs' else 1
            covered += actual in ranked[:count]
            abstain = code in ('had', 'hhad', 'hafu') and probs[ranked[0]]-probs[ranked[1]] < .03
            if not abstain:
                selected += 1
                selected_hits += actual in ranked[:count]
        result[code] = {**accuracy(hits, len(rows)), 'top_k': 2 if code == 'ttg' else 3 if code == 'crs' else 1,
                        'top_k_hits': covered, 'top_k_coverage': covered/len(rows) if rows else None,
                        'policy_selected': selected, 'policy_hits': selected_hits,
                        'policy_hit_or_coverage': selected_hits/selected if selected else None,
                        'policy_abstained': len(rows)-selected}
    return result


def protected_hashes():
    # Reports may be appended; model bytes, datasets and selection pointers may not change.
    paths = []
    for cid in LEAGUES:
        base = registry.directory(cid)
        paths += list((base/'runs').rglob('*.json')) + list((base/'datasets').glob('*.json'))
        paths += [base/name for name in ('candidate.json', 'observation.json', 'dataset.json') if (base/name).exists()]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def replay(output):
    output.mkdir(parents=True, exist_ok=False)
    before = protected_hashes()
    summary = {'created_at': now_iso(), 'protocol': 'existing-frozen-artifacts-replay-v1',
               'limitations': ['Previously inspected historical periods, not an untouched prospective test.',
                              'Historical result availability follows the dataset policy.',
                              'No refit, parameter search, selection or synthetic handicap lines.'],
               'evaluations': [], 'current_models': {}}
    for cid in LEAGUES:
        data = registry.dataset(cid)
        rows = training.validate_dataset(data)
        by_id = {r['id']: r for r in rows}
        base = registry.directory(cid)
        current = {name: registry.read(base/(name+'.json')) if (base/(name+'.json')).exists() else None
                   for name in ('candidate', 'observation')}
        current['latest_dataset_kickoff'] = max(r['kickoff'] for r in rows)
        summary['current_models'][cid] = current
        seen = set()
        paired = {}
        for path in sorted((base/'runs').glob('*/manifest.json')):
            manifest, bundle, _, _ = registry.load_run(cid, path.parent.name)
            value = bundle['models'][cid]
            if value['kind'] not in ('dc', 'elo_logistic'):
                continue
            model = restore(value)
            at = parse_date(model.trained_before)
            # Evaluate each preseason artifact only on the following season.
            start = at + timedelta(days=1)
            end = parse_date(f'{at.year+1}-07-01T00:00:00+08:00')
            targets = [r for r in rows if start <= parse_date(r['kickoff']) < end]
            metadata = {'competition_id': cid, 'run_id': manifest['run_id'], 'model_id': manifest['model_id'],
                        'model_digest': manifest['model_digest'], 'training_dataset_digest': manifest['dataset_digest'],
                        'kind': value['kind'], 'trained_before': model.trained_before,
                        'training_samples': value['sample_count'], 'date_from': start.isoformat(),
                        'date_to': end.isoformat(), 'test_season': f'{at.year}/{str(at.year+1)[2:]}',
                        'is_current_candidate': bool(current['candidate'] and current['candidate']['run_id']==manifest['run_id']),
                        'is_observation': bool(current['observation'] and current['observation']['run_id']==manifest['run_id'])}
            if len(targets) < training.MIN_TEST:
                summary['evaluations'].append({**metadata, 'status': 'insufficient_holdout', 'targets': len(targets)})
                print(cid, value['kind'], at.isoformat(), 'insufficient_holdout', len(targets), flush=True)
                continue
            if (at.month, at.day) != (7, 1) or at.year not in (2025, 2026):
                raise ValueError(f'Unexpected artifact period: {cid} {manifest["run_id"]}')
            key = (at.year, value['kind'])
            if key in seen:
                raise ValueError(f'Ambiguous artifact: {cid} {key}')
            seen.add(key)
            ref = {k: manifest[k] for k in ('competition_id', 'run_id', 'model_id', 'model_digest', 'dataset_digest')}
            ref.update(kind='run', digest=manifest['model_digest'])
            report = evaluate_task(data, {'kind':value['kind'], 'mode':'separate', 'half_life':value['half_life']},
                                   fixed_model=model, date_from=start, date_to=end, fixed_artifact=ref)
            records = report['records']
            ids = {r['id'] for r in records}
            excluded = {r['id'] for r in report['exclusions']}
            assert len(ids)==len(records) and not ids & excluded
            assert ids | excluded == {r['id'] for r in targets}
            for record in records:
                assert record['competition_id']==cid and parse_date(record['asof']) > at
                assert record['artifact_ref']==ref and min(record['team_samples'])>=5
                for probs in record['markets'].values():
                    assert all(math.isfinite(p) and 0<=p<=1 for p in probs.values())
                    assert abs(sum(probs.values())-1)<1e-8
            if at.year in paired:
                assert ids == paired[at.year], 'Methods evaluated on different fixtures'
            paired[at.year] = ids
            history = [r for r in rows if parse_date(r['result_available_at']) < at and parse_date(r['kickoff']) < at]
            counts = Counter(training.outcome(r['home_goals'],r['away_goals']) for r in history)
            prior = [(counts[k]+1)/(len(history)+3) for k in 'hda']
            baseline = [{**r, 'probabilities':prior} for r in records]
            metrics = summarize(records)
            stat = {**metadata, 'status':'completed', 'targets':len(targets), 'evaluated':len(records),
                    'excluded':len(excluded), 'exclusion_reasons':dict(Counter(r['reason'] for r in report['exclusions'])),
                    'dataset_digest':fingerprint(data), 'metrics':metrics, 'probability_metrics':report['test'],
                    'always_home':accuracy(sum(r['outcome']==0 for r in records),len(records)),
                    'training_frequency_baseline':training.metrics(baseline),
                    'market_pairs':sum(bool(r.get('market')) for r in records),
                    'first_kickoff':min(by_id[r['id']]['kickoff'] for r in records),
                    'last_kickoff':max(by_id[r['id']]['kickoff'] for r in records),
                    'availability_policy':data['manifest'].get('availability_policy')}
            report.update({k: metadata[k] for k in ('run_id','model_id','model_digest','training_dataset_digest')})
            report['accuracy_audit'] = stat
            receipt = save_report(cid, report, None)
            stat.update(receipt)
            summary['evaluations'].append(stat)
            # Keep a standalone full, checksummed copy beside the batch summary.
            archived = registry.read(base/'reports'/(receipt['report_id']+'.json'))
            training.atomic_json(output/(cid+'-'+manifest['run_id']+'.json'), archived)
            print(cid, at.year, value['kind'], metrics['had']['hits'], '/', len(records),
                  f"{metrics['had']['accuracy']:.2%}", 'excluded', len(excluded), flush=True)
        assert seen == {(year,kind) for year in (2025,2026) for kind in ('dc','elo_logistic')}
    after = protected_hashes()
    assert before == after, 'Protected artifact or pointer changed during replay'
    summary['integrity'] = {'protected_files':len(before), 'unchanged':True, 'hashes':before}
    summary['digest'] = fingerprint(summary)
    training.atomic_json(output/'summary.json', summary)
    print('SUMMARY', output/'summary.json', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New directory; existing outputs are never replaced')
    args = parser.parse_args()
    replay(args.output.resolve())
