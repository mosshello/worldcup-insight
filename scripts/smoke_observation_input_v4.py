"""Two explicit validation calls using the frozen max-size case; no job backfill."""
import json,sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console.store import ROOT,Store,encode
from worldcup_mvp.console.observation_ai import ObservationAI,prepare_input
from worldcup_mvp.console import observation_models as models


def main():
    packets=json.loads((ROOT/'.ai/tmp/observation-input-audit/frozen-packets.json').read_text(encoding='utf-8'))
    packet=packets['espn:401879234']
    ai=ObservationAI(Store());report={'purpose':'encoding_validation_only_not_observation_node_or_job_backfill',
        'executed_at':datetime.now(timezone.utc).isoformat(),'fixture':packet['fixture'],'input_cutoff':packet['as_of'],
        'facts':len(packet['evidence']),'model_feature_count':sum(r.get('kind')=='model_feature' for r in packet['evidence']),
        'request_chars':len(encode(prepare_input(packet,'extract','observation-ai-v4'))),'budget_before':ai.budget_status(),'results':{}}
    for mode in ('extract','blind'):
        try:report['results'][mode]=ai.run(packet,mode,prompt_version='observation-ai-v4')
        except ValueError as exc:report['results'][mode]={'error':str(exc),'code':getattr(exc,'code',None)}
    extraction=report['results']['extract']
    if 'facts'in extraction:
        cid=packet['fixture']['competition_id']
        artifact=models.load(models.ROOT/cid/(packet['artifact_digest']+'.json'),cid)
        try:report['enhanced_validation']=models.forecast(artifact,packet['fixture'],packet['as_of'],extracted=extraction)
        except ValueError as exc:report['enhanced_validation']={'error':str(exc)}
    report['budget_after']=ai.budget_status()
    out=ROOT/'data/research/observation-models'/('input-v4-smoke-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'.json')
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(out.relative_to(ROOT)),'facts':report['facts'],'request_chars':report['request_chars'],
        'results':{k:{'call_id':v.get('call_id'),'tokens':v.get('usage',{}).get('total_tokens'),'facts':len(v.get('facts',[])),
            'probabilities':v.get('probabilities'),'error':v.get('error')} for k,v in report['results'].items()},
        'enhanced_markets':report.get('enhanced_validation',{}).get('supported_markets'),'enhanced_error':report.get('enhanced_validation',{}).get('error')},ensure_ascii=False))


if __name__=='__main__':main()
