"""Read-only sweep accounting. Never manufacture or rewrite observations."""
import math

from .sources import EXPECTED_OPTIONS, parse_date

CODES = ('had', 'hhad', 'hafu', 'crs', 'ttg')


def count(rows, kind):
    result = {}
    for code in CODES:
        stats = {'single': {'hits': 0, 'settled': 0}, 'multiple': {'hits': 0, 'settled': 0},
                 'missing': 0, 'abstained': 0, 'pending': 0, 'excluded': 0}
        for row in rows:
            observation = row.get('observations', {}).get(kind, {})
            forecast = observation.get('forecast') or {}
            market = next((m for m in forecast.get('market_assessments', []) if m['code'] == code), None)
            settled = next((m for m in observation.get('markets', []) if m['code'] == code), {})
            if not market or not market.get('selected_keys') and not market.get('options'):
                stats['missing'] += 1
            elif not market.get('selected_keys'):
                stats['abstained'] += 1
            elif type(settled.get('hit')) is bool:
                group = stats['single' if len(market['selected_keys']) == 1 else 'multiple']
                group['settled'] += 1
                group['hits'] += int(settled['hit'])
            elif observation.get('conflict') or observation.get('cancelled'):
                stats['excluded'] += 1
            else:
                stats['pending'] += 1
        result[code] = stats
    return result


def probabilities(market, code):
    options = market.get('options', [])
    if len(options) != len(EXPECTED_OPTIONS[code]):
        return None
    values = {o.get('key'): o.get('probability') for o in options}
    if set(values) != set(EXPECTED_OPTIONS[code]) or any(
        type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 100 for p in values.values()
    ) or abs(sum(values.values()) - 100) > .2:
        return None
    total = sum(values.values())
    return {k: v / total for k, v in values.items()}


def paired(rows):
    """Only compare reference probabilities embedded in the same model archive.

    Independent first-market records can precede a model by hours. They are not
    controls. Require the embedded quote receipt within 180s of the prediction.
    Losses use the complete distribution, regardless of whether a pick was made.
    """
    output = {}
    for code in CODES:
        stats = {'eligible': 0, 'settled': 0, 'model': {'brier': 0., 'log_loss': 0.},
                 'market': {'brier': 0., 'log_loss': 0.}, 'matches': []}
        for row in rows:
            observation = row.get('observations', {}).get('model', {})
            p = observation.get('forecast') or {}
            reference = p.get('market_reference') or {}
            try:
                cutoff = parse_date(p['captured_at'])
                verified = parse_date(reference['source_times']['markets'])
                if cutoff >= parse_date(row['kickoff']) or not 0 <= (cutoff-verified).total_seconds() <= 180:
                    continue
                if any(p.get('fixture', {}).get(k) != row.get(k) for k in ('id', 'home', 'away', 'kickoff')):
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            model = next((m for m in p.get('market_assessments', []) if m['code'] == code), {})
            market = next((m for m in reference.get('market_assessments', []) if m['code'] == code), {})
            a, b = probabilities(model, code), probabilities(market, code)
            if a is None or b is None or code == 'hhad' and (model.get('line') is None or model.get('line') != market.get('line')):
                continue
            if observation.get('conflict') or observation.get('cancelled'):
                continue
            stats['eligible'] += 1
            settled = next((m for m in observation.get('markets', []) if m['code'] == code), {})
            actual = settled.get('actual_key')
            if actual not in a:
                continue
            stats['settled'] += 1
            stats['matches'].append({'id': row['id'], 'captured_at': p['captured_at'],
                                     'quotes_at': reference['source_times']['markets']})
            for name, values in (('model', a), ('market', b)):
                stats[name]['brier'] += sum((v - int(k == actual)) ** 2 for k, v in values.items())
                stats[name]['log_loss'] -= math.log(max(values[actual], 1e-15))
        for name in ('model', 'market'):
            stats[name] = {key: round(value/stats['settled'], 6) if stats['settled'] else None
                           for key, value in stats[name].items()}
        output[code] = stats
    return output
