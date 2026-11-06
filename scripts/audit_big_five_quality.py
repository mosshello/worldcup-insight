"""Persist diagnostic flags without rewriting measurements or enabling features.

Team xG and summed player xG need not share an aggregation definition. A numeric
difference is a review item, NOT proof that either value is erroneous.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from collections import Counter,defaultdict
import json
import math
from scripts.supplement_big_five import read,digest
from worldcup_mvp.console.big_five_history import SPECS

TOLERANCE=0.0002

def roster_index(records):
    groups=defaultdict(dict);conflicts=set()
    for p in records:
        key=(p['league_code'],str(p['understat_id']));pid=str(p['player_id'])
        value={k:v for k,v in p.items() if k!='season_input'}
        if pid in groups[key] and groups[key][pid]!=value:conflicts.add(key)
        groups[key][pid]=value
    return groups,conflicts

def compare(fact,groups,conflicts):
    src=fact['facts'];key=(src['league_code'],str(src['understat_id']))
    base={'source_id':fact['source_id'],'comparison':'team_xg_vs_sum_of_player_xg',
          'aggregation_definitions_verified':False,'diagnostic_only':True,'tolerance':TOLERANCE}
    if key in conflicts:return {**base,'status':'conflicting_player_records'}
    players=list(groups.get(key,{}).values());deltas=[]
    for side,letter in [('home','h'),('away','a')]:
        group=[p for p in players if p['h_a']==letter]
        if not group or any(str(p['team_id'])!=str(src[side+'_team_id']) for p in group):
            return {**base,'status':'missing_or_conflicting_player_identity'}
        try:values=[float(p['x_g']) for p in group];total=math.fsum(values);match=float(src[side+'_xg'])
        except (KeyError,ValueError,TypeError):return {**base,'status':'invalid_numeric_value'}
        if not all(math.isfinite(v) and v>=0 for v in values+[match]):return {**base,'status':'invalid_numeric_value'}
        if abs(total-match)>TOLERANCE:deltas.append({'side':side,'match_xg':match,'player_xg_sum':total,'delta':total-match})
    return {**base,'status':'aggregate_difference_definition_unverified' if deltas else 'numerically_equal',
        'differences':deltas}

def run(root):
    import pyarrow.parquet as pq
    work=root/'depth';parents_path=work/'quality-parents.json'
    if not parents_path.exists():parents_path.write_text(json.dumps({s:read(root/s/'latest.json') for s in SPECS}))
    parents=read(parents_path);path=root/'continuation/understat_roster.parquet';receipt=read(path.with_suffix('.parquet.receipt.json'));body=path.read_bytes()
    if receipt['http_status']!='200' or digest(body)!=receipt['sha256'] or receipt['asset']['digest']!='sha256:'+digest(body):
        raise ValueError('roster_integrity_failure')
    groups,conflicts=roster_index(pq.read_table(path).to_pylist());report={'competitions':{},'source_receipt':receipt,
        'policy':'diagnostic_only; preserve measurements; aggregation definitions unverified; not a data-error count'}
    pending={}
    for slug,p in parents.items():
        body=(root/slug/p['file']).read_bytes()
        if digest(body)!=p['sha256']:raise ValueError('parent_integrity_failure')
        d=json.loads(body);counts=Counter();details=[]
        for row in d['matches']:
            if not row['training_eligible']:continue
            fact=row.get('supplemental_postmatch_facts',{}).get('understat_archive')
            if not fact:continue
            result=compare(fact,groups,conflicts);counts[result['status']]+=1
            row.setdefault('supplemental_quality',{})['understat_player_aggregation']=result
            if result['status']!='numerically_equal':details.append({'id':row['id'],**result})
        report['competitions'][slug]={'counts':dict(counts),'review_items':details};pending[slug]=d
    report_body=json.dumps(report,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode();report_sha=digest(report_body)
    reportpath=work/('quality-'+report_sha+'.json')
    if reportpath.exists() and reportpath.read_bytes()!=report_body:raise ValueError('report_collision')
    if not reportpath.exists():reportpath.write_bytes(report_body)
    for slug,d in pending.items():
        d['manifest']['source_quality_audit']={'report_file':str(reportpath.relative_to(root)),
            'sha256':report_sha,'parent_sha256':parents[slug]['sha256'],'counts':report['competitions'][slug]['counts'],
            'policy':report['policy'],'code_sha256':digest(Path(__file__).read_bytes())}
        body=json.dumps(d,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode();sha=digest(body);p=root/slug/'snapshots'/(sha+'.json')
        if p.exists() and p.read_bytes()!=body:raise ValueError('snapshot_collision')
        if not p.exists():p.write_bytes(body)
        (root/slug/'latest.json').write_text(json.dumps({'file':str(p.relative_to(root/slug)),'sha256':sha,'rows':len(d['matches'])}))
    (work/'quality-audit.json').write_text(json.dumps({'file':reportpath.name,'sha256':report_sha,'competitions':{s:v['counts'] for s,v in report['competitions'].items()}},indent=2))
    print(json.dumps({s:v['counts'] for s,v in report['competitions'].items()}))

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);run(p.parse_args().root)
