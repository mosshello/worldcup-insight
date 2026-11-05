"""Explicit lower-probability observations, separate from primary selections.

Heuristic thresholds are a display/observation policy, not a calibrated edge.
Only frozen, complete distributions may supply candidates; never use results.
"""
import math
import re
from copy import deepcopy
from .sources import EXPECTED_OPTIONS


def candidates(markets):
    output = []
    for market in markets:
        code = market.get('code')
        if code not in ('crs', 'ttg', 'hafu') or not market.get('selected_keys'):
            continue
        options = market.get('options', [])
        if ({o.get('key') for o in options} != set(EXPECTED_OPTIONS[code]) or
            len(options) != len(EXPECTED_OPTIONS[code]) or
            any(type(o.get('probability')) not in (int,float) or not math.isfinite(o['probability']) or not 0 <= o['probability'] <= 100 for o in options) or
            abs(sum(o['probability'] for o in options)-100) > .2):
            continue
        ranked = sorted(options, key=lambda o:(-o['probability'],o['key']))
        floor = max(3 if code == 'crs' else 8, ranked[0]['probability']*.3)
        def eligible(o):
            if o['key'] in market['selected_keys'] or o['probability'] < floor:
                return False
            if code == 'crs':
                pair = re.fullmatch(r's(\d{2})s(\d{2})',o['key'])
                return bool(pair and sum(map(int,pair.groups())) >= 4)
            if code == 'ttg':
                return int(o['key'][1:]) >= 4
            return o['key'] in ('dh','da','ha','ah')
        picked = next((o for o in ranked if eligible(o)),None)
        if not picked:
            continue
        output.append({**deepcopy(market), 'selected_keys':[picked['key']],
            'selection':picked['label'], 'selection_count':1, 'coverage':picked['probability'],
            'policy':'exploration-v1', 'status':'进取观察',
            'reason':{'crs':'总进球至少4球的非主选比分','ttg':'至少4球的非主选进球数','hafu':'半场平局后分胜负或胜负逆转的非主选情景'}[code],
            'notice':'实验观察，非收益优势判断；与主预测分别核对'})
    return output
