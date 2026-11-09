"""EPL-only, timestamped FPL reports as numerical research inputs.

Source report counts are not confirmed absences. Snapshot minutes are a usage
proxy, never replacement quality or expected minutes. No production selection.
"""
from datetime import timedelta
import math

from .observation_features import instant
from .observation_models import FIELDS, vectors
from .observation_personnel_models import immutable, load
from .football_markets import score_grid, market_probabilities
from .store import fingerprint

CID = 'eng.premier'
SCHEMA = 'epl-pregame-fpl-poisson-v1'
NEWS_FIELDS = ['reported_injured', 'reported_doubtful', 'reported_suspended',
               'flagged_minutes_share', 'news_status_coverage', 'minutes_unknown', 'snapshot_age_days']
ALL_FIELDS = FIELDS + NEWS_FIELDS + ['opp_' + k for k in NEWS_FIELDS]


def fpl_vectors(payload, fixture, as_of, captured_at, team_mapping):
    at, captured = instant(as_of), instant(captured_at)
    if fixture['competition_id'] != CID:
        raise ValueError('FPL增强仅支持英超')
    if at >= instant(fixture['kickoff']) or not at - timedelta(hours=72) <= captured <= at:
        raise ValueError('FPL快照晚到或超过72小时')
    deadlines = [instant(e['deadline_time']) for e in payload.get('events', []) if e.get('deadline_time')]
    if not deadlines or str(min(deadlines).year) != str(fixture['season']):
        raise ValueError('FPL快照赛季不匹配')
    teams = payload.get('teams', [])
    ids = [str(t['id']) for t in teams]
    if len(ids) != 20 or len(set(ids)) != 20:
        raise ValueError('FPL球队表不完整或重复')
    mapped = [team_mapping.get(t) for t in ids if team_mapping.get(t)]
    if len(mapped) != len(set(mapped)):
        raise ValueError('FPL球队映射冲突')
    target_ids = [fixture['home_id'], fixture['away_id']]
    if len(set(target_ids)) != 2 or any(t not in mapped for t in target_ids):
        raise ValueError('FPL缺少本场双方身份')
    players = payload.get('elements', [])
    seen = set()
    for p in players:
        if p['id'] in seen or str(p['team']) not in ids:
            raise ValueError('FPL球员或球队身份冲突')
        seen.add(p['id'])
    values, refs = [], []
    for team in target_ids:
        group = [p for p in players if team_mapping.get(str(p['team'])) == team]
        if len(group) < 11:
            raise ValueError('FPL球队列名不足11人')
        eligible = []
        unknown = []
        for p in group:
            if p.get('status') == 'a':
                continue
            pub = p.get('news_added')
            if p.get('status') in ('i', 'd', 's') and p.get('news') and (not pub or instant(pub) <= captured):
                eligible.append(p)
            else:
                unknown.append(p['id'])
        minutes = [p.get('minutes') for p in group]
        minutes_known = (captured > min(deadlines) + timedelta(hours=3)
                         and all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in minutes)
                         and sum(minutes) > 0)
        value = {'reported_injured': sum(p['status'] == 'i' for p in eligible),
                 'reported_doubtful': sum(p['status'] == 'd' for p in eligible),
                 'reported_suspended': sum(p['status'] == 's' for p in eligible),
                 'flagged_minutes_share': sum(p['minutes'] for p in eligible) / sum(minutes) if minutes_known else 0.,
                 'news_status_coverage': 1 - len(unknown) / len(group),
                 'minutes_unknown': float(not minutes_known),
                 'snapshot_age_days': (at - captured).total_seconds() / 86400}
        values.append(value)
        refs.append({'team_id': team, 'listed_players': len(group), 'unknown_status_ids': unknown,
                     'reported_players': [{'id': f"fpl:{fixture['season']}:{p['id']}",
                         'status': p['status'], 'news': p['news'], 'news_added': p.get('news_added'),
                         'minutes_used': p['minutes'] if minutes_known else None} for p in eligible],
                     'captured_at': captured.isoformat(), 'payload_digest': fingerprint(payload),
                     'coverage': 'partial_FPL_reports_not_complete_match_eligibility'})
    return [dict(values[i], **{'opp_' + k: v for k, v in values[1-i].items()}) for i in (0, 1)], refs


def fit(samples, cutoff, alpha, with_news, input_reference):
    import numpy as np
    from sklearn.linear_model import PoissonRegressor
    from threadpoolctl import threadpool_limits
    if any(r['competition_id'] != CID for r in samples):
        raise ValueError('人员训练不能跨赛事')
    eligible = [r for r in samples if instant(r['available_at']) < instant(cutoff)]
    if len(eligible) < 100:
        raise ValueError('英超人员训练不足100场')
    if any(instant(r['as_of']) >= instant(r['kickoff']) or instant(r['kickoff']) >= instant(r['available_at'])
           for r in eligible):
        raise ValueError('训练样本时间顺序错误')
    fields = ALL_FIELDS if with_news else FIELDS
    x = np.array([[v[k] for k in fields] for r in eligible for v in r['vectors']], dtype=float)
    y = np.array([g for r in eligible for g in r['goals']], dtype=float)
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError('训练数值无效')
    mean, scale = x.mean(0), x.std(0)
    scale[scale < 1e-9] = 1
    with threadpool_limits(limits=1):
        reg = PoissonRegressor(alpha=alpha, max_iter=500).fit((x-mean)/scale, y)
    halves = [r for r in eligible if all(v is not None for v in r['half_goals'])]
    shares = [(1+sum(r['half_goals'][i] for r in halves))/(2+sum(r['goals'][i] for r in halves))
              for i in (0, 1)] if len(halves) >= 100 else None
    return {'schema': SCHEMA, 'competition_id': CID, 'train_before': instant(cutoff).isoformat(),
            'fields': fields, 'alpha': alpha, 'with_news': with_news, 'mean': mean.tolist(),
            'scale': scale.tolist(), 'coef': reg.coef_.tolist(), 'intercept': float(reg.intercept_),
            'half_shares': shares, 'train_ids': [r['id'] for r in eligible], 'train_count': len(eligible),
            'input_reference': input_reference, 'research_only': True, 'automatic_selection': False,
            'not_trained': ['confirmed_target_lineup', 'expected_minutes', 'replacement_quality',
                            'complete_eligibility', 'national_team_player_load', 'free_text_causal_effect']}


def probabilities(artifact, feature_vectors, handicap=None):
    if artifact['schema'] != SCHEMA or artifact['competition_id'] != CID:
        raise ValueError('英超人员模型身份错误')
    mu = []
    for v in feature_vectors:
        score = artifact['intercept'] + sum((v[k]-artifact['mean'][j])/artifact['scale'][j]*artifact['coef'][j]
                                            for j, k in enumerate(artifact['fields']))
        if not math.isfinite(score):
            raise ValueError('人员预测数值无效')
        mu.append(math.exp(max(math.log(.05), min(math.log(8), score))))
    return market_probabilities(score_grid(*mu), handicap, artifact['half_shares'])


def blend(base, extra, weight):
    if not 0 <= weight <= 1:
        raise ValueError('融合权重越界')
    return {m: {k: (1-weight)*p + weight*extra[m][k] for k, p in dist.items()}
            for m, dist in base.items() if m in extra and set(dist) == set(extra[m])}


def feature_packet(artifact, history, fixture, as_of, payload, captured_at, team_mapping):
    if artifact['competition_id'] != fixture['competition_id'] or instant(artifact['train_before']) > instant(as_of):
        raise ValueError('人员模型赛事或训练截止不符')
    if any(r['competition_id'] != CID for r in history):
        raise ValueError('人员历史跨赛事')
    base, base_refs = vectors(history, fixture, as_of, historical=False)
    news, news_refs = fpl_vectors(payload, fixture, as_of, captured_at, team_mapping)
    evidence = []
    for i, side in enumerate(('home', 'away')):
        val = dict(base[i], **news[i])
        fact = {'kind': 'model_feature', 'side': side, 'field': 'team_vector',
                'value': {k: val[k] for k in artifact['fields']},
                'source': 'verified_history_and_timestamped_FPL_reports',
                'source_url': 'https://fantasy.premierleague.com/api/bootstrap-static/',
                'published_at': None, 'captured_at': instant(as_of).isoformat(),
                'time_basis': 'observed_only', 'history_refs': base_refs[i], 'personnel_refs': news_refs[i],
                'text': f'{side}: 已核验赛前来源的模型向量，人员状态为来源报告，不是已确认缺阵。'}
        fact['id'] = 'fpl-vector-' + fingerprint(fact)[:20]
        evidence.append(fact)
    return {'schema': 'observation-evidence-v1', 'fixture': fixture, 'as_of': instant(as_of).isoformat(),
            'evidence': evidence, 'missing': artifact['not_trained'], 'excluded': [],
            'artifact_digest': artifact['digest'], 'personnel_scope': 'timestamped_partial_FPL_reports'}


def forecast_packet(artifact, packet, extracted=None, handicap=None):
    if (packet['fixture']['competition_id'] != CID or artifact['competition_id'] != CID
            or packet['artifact_digest'] != artifact['digest']
            or instant(artifact['train_before']) > instant(packet['as_of'])
            or instant(packet['as_of']) >= instant(packet['fixture']['kickoff'])):
        raise ValueError('人员输入与模型或时点不匹配')
    expected = {r['id']: r for r in packet['evidence'] if r.get('kind') == 'model_feature'}
    if extracted is not None and any({r['id']: r for r in extracted['facts']}.get(k) != v for k, v in expected.items()):
        raise ValueError('AI未完整保留人员模型的已核验输入')
    vs = [next(r['value'] for r in expected.values() if r['side'] == s) for s in ('home', 'away')]
    probs = probabilities(artifact, vs, handicap)
    baseline_reference = artifact.get('blend_baseline_reference')
    if baseline_reference:
        from .training import GoalModel
        baseline = load(baseline_reference, CID)
        model = GoalModel.from_dict(baseline['model'])
        if instant(model.trained_before) > instant(packet['as_of']):
            raise ValueError('融合基准晚于预测截止')
        fixture = packet['fixture']
        if any(model.team_counts.get(CID+':'+fixture[s+'_id'], 0) < 5 for s in ('home', 'away')):
            raise ValueError('融合基准球队样本不足，不回退单模型')
        probs = blend(model.forecast(fixture, handicap)['markets'], probs, artifact['blend_weight'])
    return {'probabilities': probs, 'supported_markets': list(probs), 'model_id': artifact['digest'],
            'input_snapshot': packet, 'input_metadata': {'actually_used_fields': artifact['fields'],
                'not_trained': artifact['not_trained'], 'enhancement_kind': 'timestamped_FPL_numeric_features',
                'blend_baseline_reference': baseline_reference, 'blend_weight': artifact.get('blend_weight'),
                'ai_role': 'evidence_extraction_validation' if extracted else 'not_used'}, 'research_only': True}


def runtime_packet(config, fixture, cutoff, collected):
    """Explicit experiment opt-in, using only the collector's frozen references."""
    if config.get('enhanced_kind') != SCHEMA or fixture['competition_id'] != CID:
        raise ValueError('人员实验未显式配置或赛事不符')
    artifact = load(config['enhanced_reference'], CID)
    if artifact['digest'] != config.get('enhanced_digest') or artifact['schema'] != SCHEMA:
        raise ValueError('人员实验产物版本不符')
    prepared = load(artifact['input_reference'], CID)
    ref = collected.get('supplemental', {}).get('numeric_sources', {}).get('fpl')
    if not ref:
        raise ValueError('冻结时点缺少原始FPL快照，不回退旧增强模型')
    snapshot = load(ref, CID)
    packet = feature_packet(artifact, prepared['history'], fixture, cutoff, snapshot['payload'],
                            snapshot['captured_at'], snapshot['team_mapping'])
    packet['personnel_snapshot_reference'] = ref
    return artifact, packet
