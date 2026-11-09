"""Replay frozen EPL outputs and add a clearly post-hoc xG-only blend control."""
import json
from collections import Counter
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console.store import ROOT, fingerprint
from worldcup_mvp.console.observation_personnel_models import load, immutable
from worldcup_mvp.console.observation_epl_personnel import CID, probabilities, blend, fpl_vectors
from worldcup_mvp.console.observation_features import instant
from worldcup_mvp.console import training
from scripts.evaluate_epl_pregame_personnel import OUT, MARKETS, score, interval


def main():
    index = json.loads((OUT/'latest.json').read_text(encoding='utf-8'))
    report = load(index['report'], CID)
    inputs = load(report['input_reference'], CID)
    samples = {r['id']: r for r in inputs['samples']}
    records = report['test']['records']
    models, snapshots = {}, {}
    checks = Counter()
    for rec in records:
        sample = samples[rec['id']]
        if fingerprint(sample) != rec['sample_digest']:
            raise ValueError('测试输入摘要改变')
        ref = rec['snapshot_reference']
        if ref not in snapshots:
            snapshots[ref] = load(ref, CID)
        snapshot = snapshots[ref]
        # Fixture names are not needed: exact IDs are already pinned in snapshot mappings.
        fixture = {'id': rec['id'], 'competition_id': CID, 'season': rec['season'], 'kickoff': rec['kickoff'],
                   'home_id': sample['personnel_refs'][0]['team_id'], 'away_id': sample['personnel_refs'][1]['team_id']}
        nv, _ = fpl_vectors(snapshot['payload'], fixture, rec['as_of'], snapshot['captured_at'], snapshot['team_mapping'])
        for i in (0, 1):
            if any(sample['vectors'][i][k] != v for k, v in nv[i].items()):
                raise ValueError('人员特征无法从原始快照复算')
        checks['raw_personnel_replayed'] += 1
        for name, path in report['test']['folds'][rec['fold']].items():
            if path not in models:
                models[path] = load(path, CID)
            artifact = models[path]
            if rec['id'] in artifact['train_ids']:
                raise ValueError('目标进入本折训练')
            if name == 'dc':
                value = training.GoalModel.from_dict(artifact['model']).forecast(fixture, rec['line'])['markets']
            else:
                if instant(artifact['train_before']) > instant(rec['as_of']):
                    raise ValueError('模型晚于截止')
                if any(instant(samples[i]['available_at']) >= instant(artifact['train_before']) for i in artifact['train_ids']):
                    raise ValueError('未来标签进入训练')
                value = probabilities(artifact, sample['vectors'], rec['line'])
            expected = rec['predictions'][name]
            if any(abs(p-expected[m][k]) > 1e-12 for m, dist in value.items() for k, p in dist.items()):
                raise ValueError('冻结预测无法重放')
            checks['model_predictions_replayed'] += 1
        rec['predictions']['xg_blend_same_weight_control'] = blend(
            rec['predictions']['dc'], rec['predictions']['xg'], report['selected_blend_weight'])
    control = {'competition_id': CID, 'schema': 'epl-personnel-replay-audit-v1', 'parent_report': index['report'],
        'checks': dict(checks), 'model_artifacts_verified': len(models), 'source_snapshots_verified': len(snapshots),
        'scope': 'post_hoc_diagnostic_same_frozen_weight_no_new_selection',
        'control_metrics': {m: score(records, 'xg_blend_same_weight_control', m) for m in MARKETS},
        'control_interval': interval(records, 'xg_blend_same_weight_control'),
        'control_predictions': [{'id': r['id'], 'probabilities': r['predictions']['xg_blend_same_weight_control']} for r in records]}
    path = immutable(control, OUT/'audits')
    print(json.dumps({'audit': path, 'checks': dict(checks), 'control_had': control['control_metrics']['had']}, ensure_ascii=True))


if __name__ == '__main__':
    main()
