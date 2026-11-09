"""Fresh EPL future-fixture forecasts and one real AI extraction, research only."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console.store import ROOT, Store, fingerprint
from worldcup_mvp.console.sources import get_json
from worldcup_mvp.console import model_registry
from worldcup_mvp.console.observation_models import history_rows
from worldcup_mvp.console.observation_personnel_models import load, immutable
from worldcup_mvp.console.observation_personnel_sources import fpl_team_mapping
from worldcup_mvp.console.observation_epl_personnel import CID, SCHEMA, runtime_packet, forecast_packet
from worldcup_mvp.console.observation_ai import ObservationAI
from worldcup_mvp.console.observation_features import instant

OUT = ROOT/'data/research/epl-pregame-personnel'


def archive_state(store):
    result = {}
    with store.connect() as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('analyses', 'daily_forecasts', 'daily_outcomes', 'settlement_versions'):
            if table not in names:
                continue
            result[table] = {str(r[0]): fingerprint(dict(r)) for r in db.execute('SELECT rowid AS _rowid_, * FROM '+table)}
    return result


def main():
    index = json.loads((OUT/'latest.json').read_text(encoding='utf-8'))
    model = load(index['candidate'], CID)
    store = Store(); before = archive_state(store)
    data = model_registry.dataset(CID, admission=False)
    rows = data['matches']; season = max(r['season'] for r in rows)
    teams = {r[s+'_id']: r[s+'_name'] for r in rows if r['season'] == season for s in ('home', 'away')}
    url = 'https://fantasy.premierleague.com/api/bootstrap-static/'
    payload = get_json(url, timeout=20)
    capture = datetime.now(timezone.utc).isoformat()
    mapping, unmapped = fpl_team_mapping(payload, teams)
    source_ref = immutable({'competition_id': CID, 'schema': 'fpl-snapshot-v1', 'payload': payload,
        'captured_at': capture, 'team_mapping': mapping, 'source_url': url}, OUT/'live-inputs')
    at = datetime.now(timezone.utc).isoformat()
    with store.connect() as db:
        fixtures = [json.loads(r[0]) for r in db.execute('SELECT payload FROM observation_fixtures')]
    fixtures = sorted([f for f in fixtures if f['competition_id'] == CID and instant(f['kickoff']) > instant(at)
                       and f['status'] == 'upcoming'], key=lambda f: f['kickoff'])
    config = {'competition_id': CID, 'enhanced_kind': SCHEMA, 'enhanced_reference': index['candidate'],
              'enhanced_digest': model['digest'], 'ai_prompt_version': 'observation-ai-v4'}
    report = {'competition_id': CID, 'purpose': 'manual_current_time_smoke_not_T60_or_historical_backfill',
        'as_of': at, 'model_reference': index['candidate'], 'source_reference': source_ref,
        'target_count': len(fixtures), 'unmapped_teams': unmapped, 'predictions': [], 'failures': []}
    for f in fixtures:
        try:
            _, packet = runtime_packet(config, f, at, {'supplemental': {'numeric_sources': {'fpl': source_ref}}})
            prediction = forecast_packet(model, packet)
            report['predictions'].append({'fixture': f, 'prediction': prediction})
        except ValueError as exc:
            report['failures'].append({'fixture_id': f['id'], 'reason': str(exc)})
    if report['predictions']:
        packet = report['predictions'][0]['prediction']['input_snapshot']
        ai = ObservationAI(store)
        report['ai_budget_before'] = ai.budget_status()
        try:
            extraction = ai.extract(packet, prompt_version='observation-ai-v4')
            report['ai_extraction'] = extraction
            report['ai_validated_prediction'] = forecast_packet(model, packet, extraction)
            report['deterministic_parity'] = (report['ai_validated_prediction']['probabilities']
                                               == report['predictions'][0]['prediction']['probabilities'])
        except ValueError as exc:
            report['ai_error'] = {'reason': str(exc), 'code': getattr(exc, 'code', None)}
        report['ai_budget_after'] = ai.budget_status()
    after = archive_state(store)
    report['protected_counts'] = {t: len(v) for t, v in before.items()}
    report['protected_changes'] = {t: [k for k, v in old.items() if after.get(t, {}).get(k) != v]
                                   for t, old in before.items()}
    ref = immutable(report, OUT/'live-reports')
    print(json.dumps({'report': ref, 'targets': len(fixtures), 'completed': len(report['predictions']),
        'failures': report['failures'], 'ai_error': report.get('ai_error'), 'ai_parity': report.get('deterministic_parity'),
        'ai_usage': report.get('ai_extraction', {}).get('usage'), 'protected_changes': report['protected_changes']}, ensure_ascii=True))


if __name__ == '__main__':
    main()
