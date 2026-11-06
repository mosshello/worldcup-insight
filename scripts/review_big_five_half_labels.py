"""Confirm half labels against both the goal log and a separate fixture archive."""
from pathlib import Path
import gzip
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pyarrow.parquet as pq
from scripts.restore_big_five_boxscores import CODES, MAPS, ROOT, WORK, checked_file, fixture_index, fixture_for
from scripts.supplement_big_five import read,digest
from worldcup_mvp.console.big_five_history import event_half_score
from worldcup_mvp.console.training import atomic_json

OUT=WORK.parent/'club-personnel'
CHECKED={'espn:740675','espn:740876','espn:740882','espn:748260','espn:736853'}


def run():
    parent_file=OUT/'half-label-parents.json'
    if not parent_file.exists():atomic_json(parent_file,{s:read(ROOT/s/'latest.json') for s in CODES})
    parents=read(parent_file);fixes={};outputs={};audit={}
    release=read(WORK.parent/'opta-release.json')
    checked_file(WORK.parent,'opta-release.json','https://api.github.com/repos/peteowen1/pannadata/releases/tags/opta-latest','opta-release.receipt.json')
    a=next(a for a in release['assets'] if a['name']=='opta_fixtures.parquet')
    receipt=checked_file(WORK.parent,a['name'],a['browser_download_url'])
    if a['digest']!='sha256:'+receipt['sha256']:raise ValueError('fixture_release_integrity')
    fixtures=pq.read_table(WORK.parent/a['name'],filters=[('competition','in',[v[1] for v in CODES.values()]),('match_status','=','Played')]).to_pylist()
    for slug,p in parents.items():
        raw=(ROOT/slug/p['file']).read_bytes()
        if digest(raw)!=p['sha256']:raise ValueError('parent_integrity')
        d=json.loads(raw);idx=fixture_index('opta',slug,fixtures,read(MAPS)[slug+'-opta'],{})
        stats={'eligible':0,'full_score_reconstructed':0,'independent_half_checked':0,'corrected':0}
        for row in d['matches']:
            if not row['training_eligible']:continue
            stats['eligible']+=1
            trace=event_half_score(row);old=[row['home_half_goals'],row['away_half_goals']]
            stats['full_score_reconstructed']+=trace is not None
            f=fixture_for(row,idx,'opta')
            external=None
            if f and all(f[s+'_score_ht'] is not None for s in ('home','away')):
                external=[int(f[s+'_score_ht']) for s in ('home','away')];stats['independent_half_checked']+=1
            if not any(v is not None and v!=old for v in (trace,external)):continue
            if (row['id'] not in CHECKED or trace!=external or trace is None or 'half_time_correction' in row
                    or row['period']!='regular_time' or not row['summary_verified']):
                raise ValueError('unreviewed_half_time_conflict:'+row['id'])
            sr=row['summary_receipt'];body=gzip.decompress((ROOT/slug/'raw'/sr['file']).read_bytes())
            if sr['http_status']!='200' or digest(body)!=sr['sha256']:raise ValueError('summary_integrity')
            fact={'original':old,'corrected':trace,'source_receipt':receipt,'source_fixture_id':f['match_id'],
                'summary_receipt':sr,'policy':'reviewed_complete_regulation_goal_log_and_independent_fixture_half_v1'}
            row['half_time_correction']=fact
            fixes[row['id']]=fact;stats['corrected']+=1
            row['home_half_goals'],row['away_half_goals']=trace
        audit[slug]=stats
        if not stats['corrected']:continue
        for c in d['coverage']:
            c['half_time_corrections']=sum('half_time_correction' in r for r in d['matches'] if r['season']==c['season'])
        d['manifest']['half_time_label_audit']={**stats,'parent_sha256':p['sha256'],'code_sha256':digest(Path(__file__).read_bytes())}
        body=json.dumps(d,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        sha=digest(body);dest=ROOT/slug/'snapshots'/(sha+'.json')
        if dest.exists() and dest.read_bytes()!=body:raise ValueError('snapshot_collision')
        if not dest.exists():dest.write_bytes(body)
        outputs[slug]={'file':str(dest.relative_to(ROOT/slug)),'sha256':sha,'rows':len(d['matches'])}
    if set(fixes)!=CHECKED:raise ValueError('review_scope_changed')
    for slug,p in outputs.items():
        if read(ROOT/slug/'latest.json') not in (parents[slug],p):raise ValueError('newer_snapshot_present')
    atomic_json(OUT/'half-label-audit.json',{'coverage':audit,'corrections':fixes,'versions':outputs})
    for slug,p in outputs.items():atomic_json(ROOT/slug/'latest.json',p)
    print({eid:(v['original'],v['corrected']) for eid,v in fixes.items()})


if __name__=='__main__':run()
