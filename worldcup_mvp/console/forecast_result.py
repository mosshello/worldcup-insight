"""Versioned football-first archive; legacy display fields are derived once."""
from copy import deepcopy
from .predictor import predict, OUTCOMES


def build(match, detail, football):
    if football.get('status') != 'shadow':
        raise ValueError(football.get('reason') or '赛事模型不可用')
    markets = deepcopy(football['markets'])
    had = next((m for m in markets if m['code']=='had'), None)
    if not had or len(had.get('options',[])) != 3:
        raise ValueError('模型胜平负输出不完整')
    probs = {o['key']:o['probability'] for o in had['options']}
    pick = max(probs,key=probs.get)
    selected = had['selected_keys']
    by_code = {m['code']:m for m in markets}
    market_reference = None
    if detail.get('quality',{}).get('quotes_ready'):
        market_detail = {**detail,'quality':{**detail['quality'],'can_predict':True,'reasons':[]}}
        try:
            market_reference = predict(match,market_detail)
        except (ValueError,KeyError):
            pass
    return {'schema_version':2,'prediction_type':'football', 'football_prediction':deepcopy(football),
        'market_reference':market_reference,'football_model':deepcopy(football),
        'model_version':'football-v2:'+football['model_id'], 'engine':'赛事足球模型',
        'title':had['selection'],'summary':'','direction':OUTCOMES[pick] if selected else '观望',
        'direction_key':pick if selected else None,'leading_outcome':pick,
        'probabilities':[probs[k] for k in ('h','d','a')], 'probLabel':'足球模型概率',
        'market_probabilities':(market_reference or {}).get('market_probabilities'),
        'goals':by_code.get('ttg',{}).get('selection','未支持'), 'scores':by_code.get('crs',{}).get('selection','未支持'),
        'score_probabilities':[], 'market_assessments':markets, 'evidence':[], 'risks':[],
        'narrative':None,'validation':'prospective_observation', 'quality':detail['quality'],
        'source_times':{'markets':detail.get('captured_at'),'context':detail.get('context',{}).get('captured_at')},
        'explanation_method':'deterministic'}


def read_archived(value):
    """Read-only bridge; never edits the stored payload or its input snapshot."""
    if value.get('schema_version') == 2:
        return {'football_prediction':value.get('football_prediction'),'market_reference':value.get('market_reference')}
    return {'football_prediction':value.get('football_model'),'market_reference':value}
