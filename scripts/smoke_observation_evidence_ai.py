"""Two bounded paid calls on a genuinely future fixture; does not create forecast jobs."""
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console.observation_sources import fetch_summary
from worldcup_mvp.console.observation_evidence import evidence_packet, merge_packets
from worldcup_mvp.console.observation_models import load, feature_packet, forecast
from worldcup_mvp.console.observation_ai import ObservationAI
from worldcup_mvp.console.store import Store

ROOT=Path(__file__).resolve().parents[1]


def main():
    earlier=json.loads((ROOT/'data/research/observation-models/live-ai-smoke-20261109T110737.json').read_text(encoding='utf-8'))
    fixture,context,quotes,raw=fetch_summary(earlier['fixture'])
    from worldcup_mvp.console.observation_personnel_sources import cached_fixture_evidence
    supplemental=cached_fixture_evidence(fixture)
    context={**context,'evidence':context.get('evidence',[])+supplemental['context']['evidence']}
    as_of=datetime.now(timezone.utc).isoformat()
    aux=evidence_packet(fixture,context,as_of,summary=raw,captured_at=fixture['captured_at'])
    index=json.loads((ROOT/'data/research/observation-models/candidate-index.json').read_text(encoding='utf-8'))
    artifact=load(index[fixture['competition_id']]['path'],fixture['competition_id'])
    packet=merge_packets(feature_packet(artifact,fixture,as_of),aux)
    ai=ObservationAI(Store())
    output={'fixture':fixture,'as_of':as_of,'packet':packet,'raw_summary':raw,'supplemental':supplemental,'budget_before':ai.budget_status(),
            'purpose':'manual_current_snapshot_smoke_not_backfilled_node','evidence_version':'v3'}
    try:
        output['extraction']=ai.extract(packet)
        output['enhanced']=forecast(artifact,fixture,as_of,extracted=output['extraction'])
        output['independent']=ai.predict(packet)
    except ValueError as exc:
        output['error']=str(exc)
    output['budget_after']=ai.budget_status()
    dest=ROOT/'data/research/observation-models'/('live-evidence-ai-smoke-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'.json')
    dest.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(dest.relative_to(ROOT)),'evidence_count':len(packet['evidence']),
        'coverage':packet['auxiliary_coverage'],'extracted_count':len(output.get('extraction',{}).get('facts',[])),
        'enhanced_markets':output.get('enhanced',{}).get('supported_markets'),
        'independent':output.get('independent',{}).get('probabilities'),
        'usage':{k:output.get(k,{}).get('usage') for k in ('extraction','independent')},'error':output.get('error')},ensure_ascii=False))


if __name__=='__main__':main()
