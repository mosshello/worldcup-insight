"""Attach immutable club evidence references to each competition's research version."""
from pathlib import Path
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pyarrow.parquet as pq
from scripts.audit_big_five_clubs import CODES, OUT, ROOT, digest, read
from worldcup_mvp.console.training import atomic_json


def run():
    outputs={};parents={};refs={}
    for slug in CODES:
        folder=OUT/slug;catalog_pointer=read(folder/'latest.json')
        catalog_body=(folder/catalog_pointer['file']).read_bytes()
        if digest(catalog_body)!=catalog_pointer['sha256']:raise ValueError('catalog_integrity')
        m=json.loads(catalog_body);p=parents[slug]=m['source_snapshot']
        body=(ROOT/slug/p['file']).read_bytes()
        if digest(body)!=p['sha256']:raise ValueError('source_integrity')
        d=json.loads(body)
        if d['manifest']['competition_id']!=m['competition_id']:raise ValueError('foreign_catalog')
        eligible={r['id']:r for r in d['matches'] if r['training_eligible']}
        for key in ('club_file','minutes_file','player_stat_file'):
            file=folder/m[key]['file']
            if digest(file.read_bytes())!=m[key]['sha256']:raise ValueError('catalog_resource_integrity')
            if key=='club_file':continue
            expected='minute_ledger_rows' if key=='minutes_file' else 'player_stat_rows'
            table=pq.read_table(file,columns=['fixture_id','side','competition_id','espn_team_id' if key=='minutes_file' else 'team_id'])
            if table.num_rows!=m['counts'][expected]:raise ValueError('ledger_count_mismatch')
            sides=set()
            for r in table.to_pylist():
                match=eligible[r['fixture_id']]
                if (r['competition_id']!=m['competition_id'] or r['side'] not in ('home','away')
                        or match[r['side']+'_id']!=r.get('espn_team_id',r.get('team_id'))):
                    raise ValueError('foreign_personnel_evidence')
                sides.add((r['fixture_id'],r['side']))
            expected_sides=m['counts']['minutes_linked_sides' if key=='minutes_file' else 'sides_with_player_stats']
            if len(sides)!=expected_sides:raise ValueError('ledger_side_count_mismatch')
        refs[slug]={'competition_id':m['competition_id'],'parent_snapshot_sha256':p['sha256'],
            'audit_file':(folder/catalog_pointer['file']).as_posix(),'audit_sha256':catalog_pointer['sha256'],
            'counts':m['counts'],'evidence_timing':'retrospectively collected; historical pregame eligibility unknown',
            'approved_as_training_feature':False}
        d['manifest']['club_personnel_catalog']=refs[slug]
        body=json.dumps(d,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        sha=digest(body);dest=ROOT/slug/'snapshots'/(sha+'.json')
        if dest.exists() and dest.read_bytes()!=body:raise ValueError('snapshot_collision')
        if not dest.exists():dest.write_bytes(body)
        outputs[slug]={'file':str(dest.relative_to(ROOT/slug)),'sha256':sha,'rows':len(d['matches'])}
    for slug,p in outputs.items():
        if read(ROOT/slug/'latest.json') not in (parents[slug],p):raise ValueError('newer_parent')
    atomic_json(OUT/'catalog-bindings.json',refs)
    for slug,p in outputs.items():atomic_json(ROOT/slug/'latest.json',p)
    print('verified competition catalogs:',len(refs))


if __name__=='__main__':run()
