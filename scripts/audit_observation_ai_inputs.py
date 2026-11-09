"""Offline point-in-time input reconstruction. Never calls AI or changes jobs."""
import json,sqlite3,sys
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console.store import ROOT,encode,fingerprint
from worldcup_mvp.console import observation_models as models
from worldcup_mvp.console.observation_evidence import evidence_packet,merge_packets
from worldcup_mvp.console.observation_ai import prepare_input,restore_input


def snapshots():
    db=sqlite3.connect('file:'+(ROOT/'data/console/football.sqlite3').as_posix()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    try:
        db.execute('BEGIN')
        experiments={r['id']:json.loads(r['config']) for r in db.execute('SELECT id,config FROM observation_experiments')}
        enrolled={}
        for r in db.execute('SELECT * FROM observation_enrollments ORDER BY created_at'):
            config=experiments[r['experiment_id']]
            if config.get('ai_prompt_version')=='observation-ai-v3':enrolled[r['fixture_id']]={'config':config,'revision':r['revision']}
        rows=[]
        for fid,value in sorted(enrolled.items()):
            row=db.execute('SELECT * FROM observation_packets WHERE fixture_id=? AND revision=? ORDER BY captured_at DESC LIMIT 1',(fid,value['revision'])).fetchone()
            if not row:rows.append({'fixture_id':fid,'error':'no_latest_packet'});continue
            packet=json.loads(row['payload']);raw=packet.get('raw')
            if raw is None:
                receipt=db.execute('SELECT payload FROM observation_raw_receipts WHERE digest=?',(packet['raw_digest'],)).fetchone()
                raw=json.loads(receipt[0]) if receipt else None
            rows.append({'fixture_id':fid,'config':value['config'],'packet':packet,'raw':raw})
        return rows
    finally:db.close()


def reconstruct(row):
    packet=row['packet'];fixture=packet['fixture'];at=packet['captured_at'];cid=fixture['competition_id']
    artifact=models.load(models.ROOT/cid/(row['config']['enhanced_digest']+'.json'),cid)
    model=models.feature_packet(artifact,fixture,at)
    context=packet['context'];context={**context,'evidence':context.get('evidence',[])+packet.get('supplemental',{}).get('context',{}).get('evidence',[])}
    news=evidence_packet(fixture,context,at,summary=row['raw'],captured_at=packet.get('summary_captured_at',at))
    result=merge_packets(model,news)
    latest={}
    for quote in packet.get('quotes',[]):
        if quote['market'] not in latest or quote['source']=='sporttery':latest[quote['market']]=quote
    if latest.get('had'):
        result['market']={'fixture':fixture,'captured_at':latest['had']['captured_at'],'markets':latest}
    return result


def main():
    out=ROOT/'.ai/tmp/observation-input-audit';out.mkdir(parents=True,exist_ok=True)
    rows=snapshots();records=[];packets={};v4=[]
    for row in rows:
        if row.get('error'):records.append(row);continue
        try:
            packet=reconstruct(row);packets[row['fixture_id']]=packet
            data={k:v for k,v in packet.items() if k not in ('digest','excluded','market')}
            catalog={};facts=[]
            for f in data['evidence']:
                f=dict(f)
                if 'history_refs' in f:
                    refs=f.pop('history_refs');key=fingerprint(refs);catalog[key]=refs;f['history_ref_id']=key
                facts.append(f)
            v3={**data,'evidence':facts,'history_reference_catalog':catalog,'source_packet_digest':fingerprint(data)}
            records.append({'fixture_id':row['fixture_id'],'facts':len(facts),'original_chars':len(encode(data)),
                'v3_chars':len(encode(v3)),'kinds':dict(Counter(f.get('kind') for f in facts))})
            for mode in ('extract','blind','market'):
                if mode=='market' and 'market' not in packet:continue
                prepared=prepare_input(packet,mode,'observation-ai-v4')
                canonical={k:v for k,v in packet.items() if k not in ('digest','excluded') and (k!='market' or mode=='market')}
                if restore_input(prepared)!=canonical:raise ValueError('无损反解与原始输入不一致')
                if [f['id'] for f in prepared['evidence']]!=[f['id'] for f in packet['evidence']]:raise ValueError('证据索引丢失')
                v4.append({'fixture_id':row['fixture_id'],'mode':mode,'chars':len(encode(prepared)),
                    'utf8_bytes':len(encode(prepared).encode()),'facts':len(facts),
                    'request_digest':fingerprint(prepared),'source_input_digest':fingerprint(canonical)})
        except Exception as exc:records.append({'fixture_id':row['fixture_id'],'error':str(exc)})
    report={'created_at':datetime.now(timezone.utc).isoformat(),'target_count':len(rows),'records':records,
            'v4_inputs':v4,'max_cases_v4':sorted(v4,key=lambda r:r['chars'],reverse=True)[:8],
            'roundtrip_verified_inputs':len(v4),'source_packets_digest':fingerprint(packets)}
    (out/'frozen-packets.json').write_text(json.dumps(packets,ensure_ascii=False),encoding='utf-8')
    (out/'sizes-before.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    published=ROOT/'data/research/observation-models'/('input-v4-audit-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'.json')
    published.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'targets':len(rows),'constructed':len(packets),'max_cases':sorted([r for r in records if 'v3_chars' in r],key=lambda r:r['v3_chars'],reverse=True)[:5],
        'v4_checked':len(v4),'max_cases_v4':report['max_cases_v4'][:3],
        'report':str(published.relative_to(ROOT)),'errors':[r for r in records if 'error'in r]},ensure_ascii=False))


if __name__=='__main__':main()
