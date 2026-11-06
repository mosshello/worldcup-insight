"""Admit attributed Understat measurements from a public display snapshot.

Source timestamps are naive. Join by explicit teams, season, calendar date and
score; never claim a verified UTC kickoff or ingest the display's predictions.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
import math
from datetime import datetime
from scripts.supplement_big_five import read,digest,ALIASES
from scripts.collect_big_five_gallery import NAMES
from worldcup_mvp.console.big_five_history import SPECS

SOURCE='pitchradar_understat_display'
LEAGUES={'epl':'eng.1','laliga':'esp.1','serie_a':'ita.1','bundesliga':'ger.1','ligue_1':'fra.1'}

def load_verified(work,name,url):
    path=work/name;receipt=read(path.with_suffix(path.suffix+'.receipt.json'))
    body=path.read_bytes()
    if receipt['url']!=url or receipt['http_status']!='200' or receipt['transport_exit']!=0 or digest(body)!=receipt['sha256']:
        raise ValueError('source_integrity_failure')
    data=json.loads(body)
    if data['meta']['source']!='understat.com' or data['meta']['season']!='2026/27':raise ValueError('unconfirmed_source_or_season')
    return data,receipt

def associate(slug,source,rows):
    if source.get('src','understat')!='understat':raise ValueError('non_understat_fallback_unverified')
    if not str(source['id']).isdecimal():raise ValueError('invalid_understat_id')
    names=[NAMES.get(slug,{}).get(source[s],ALIASES[slug].get(source[s],source[s])) for s in ('home','away')]
    candidates=[r for r in rows if r['season']=='2026' and [r['home_name'],r['away_name']]==names]
    if len(candidates)!=1:raise ValueError('fixture_identity_not_unique')
    row=candidates[0]
    if row['competition_id']!=SPECS[slug][0] or row['period']!='regular_time' or row['training_eligible'] is not True:
        raise ValueError('competition_or_period_conflict')
    source_date=datetime.strptime(source['date'],'%Y-%m-%d %H:%M').date().isoformat()
    if row['kickoff'][:10]!=source_date:raise ValueError('calendar_date_conflict')
    if [row['home_goals'],row['away_goals']]!=[source['gh'],source['ga']]:raise ValueError('fulltime_score_conflict')
    values=[source['xgh'],source['xga']]
    if not all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=30 and abs(v-round(v,2))<1e-9 for v in values):
        raise ValueError('invalid_display_xg')
    return row

def verify_history(source,history):
    for side,home,gf,ga,xg,xga in [('home',True,'gh','ga','xgh','xga'),('away',False,'ga','gh','xga','xgh')]:
        records=[r for r in history.get(source[side],[]) if r['date']==source['date'][:10] and r['home'] is home]
        if len(records)!=1:raise ValueError('club_history_identity_conflict')
        record=records[0]
        if any(record[k]!=source[v] for k,v in [('gf',gf),('ga',ga),('xg',xg),('xga',xga)]):
            raise ValueError('club_history_fact_conflict')

def run(root):
    work=root/'depth';parents_path=work/'pitchradar-parents.json'
    if not parents_path.exists():parents_path.write_text(json.dumps({s:read(root/s/'latest.json') for s in SPECS}))
    parents=read(parents_path);data={}
    for slug,p in parents.items():
        body=(root/slug/p['file']).read_bytes()
        if digest(body)!=p['sha256']:raise ValueError('parent_integrity_failure')
        data[slug]=json.loads(body)
    source,receipt=load_verified(work,'pitchradar-matches.json','https://pitchradar.live/matches.json')
    history,history_receipt=load_verified(work,'pitchradar-history.json','https://pitchradar.live/history.json')
    if source['meta']!=history['meta']:raise ValueError('source_generation_mismatch')
    generated=datetime.fromisoformat(source['meta']['generated_at'])
    if generated.tzinfo is None or generated>datetime.fromisoformat(receipt['observed_at']):raise ValueError('invalid_generation_time')
    report={'competitions':{},'errors':[],'not_trained':True,'source_receipt':receipt,'history_receipt':history_receipt}
    pending=[];seen=set()
    for code,slug in LEAGUES.items():
        overlap=0;added=0;new_coverage=0;targets=set()
        for m in source[code]['matches']:
            uid='understat:'+str(m['id'])
            if uid in seen:raise ValueError('duplicate_source_id')
            seen.add(uid)
            try:
                row=associate(slug,m,data[slug]['matches'])
                if row['id'] in targets:raise ValueError('multiple_source_ids_for_target')
                targets.add(row['id']);verify_history(m,history[code]['clubs'])
                if row['kickoff'][:10]>generated.date().isoformat():raise ValueError('future_fixture_in_postmatch_source')
            except (ValueError,KeyError) as exc:
                report['errors'].append({'slug':slug,'source_id':uid,'reason':str(exc)});continue
            known=row.get('supplemental_postmatch_facts',{})
            prior=known.get('understat_archive') or known.get('understat_gallery_archive')
            if prior:
                if prior['source_id']!=uid or any(abs(a-b)>0.005001 for a,b in zip(prior['xg'],[m['xgh'],m['xga']])):
                    raise ValueError('overlapping_understat_fact_conflict:'+uid)
                overlap+=1;continue
            new_coverage+=int(not known and not row.get('external_postmatch_xg'));added+=1
            pending.append((row,{'source_id':uid,'xg':[m['xgh'],m['xga']],
                'source_kind':'third_party_display_attributed_to_Understat','source_receipt':receipt,'index_receipt':history_receipt,
                'precision_decimal_places':2,'archive_timestamp':m['date'],'archive_timezone':None,
                'display_reported_fetched_at':source['meta']['generated_at'],'upstream_collection_time_verified':False,
                'identity_policy':'explicit_teams_season_calendar_date_fulltime_and_club_history_v1',
                'identity_exception':{'source_home_name':m['home'],'source_away_name':m['away'],
                    'canonical_team_ids':[row['home_id'],row['away_id']],
                    'exact_kickoff_verified':False,'club_history_verified':True},
                'approved_as_training_feature':False}))
        if overlap<5:raise ValueError('insufficient_overlap_validation:'+slug)
        report['competitions'][slug]={'overlap_checks':overlap,'added_source_measurements':added,'new_xg_coverage':new_coverage}
    # All overlap checks finish before any match or latest pointer is changed.
    for row,fact in pending:row.setdefault('supplemental_postmatch_facts',{})[SOURCE]=fact
    for slug,d in data.items():
        for c in d['coverage']:
            rows=[r for r in d['matches'] if r['season']==c['season'] and r['training_eligible']]
            c['pitchradar_xg_both']=sum(SOURCE in r.get('supplemental_postmatch_facts',{}) for r in rows)
            c['any_source_xg_both']=sum(bool(r.get('supplemental_postmatch_facts')) or bool(r.get('external_postmatch_xg')) for r in rows)
        d['manifest']['pitchradar_supplement']={'parent_sha256':parents[slug]['sha256'],'receipt':receipt,
            'code_sha256':digest(Path(__file__).read_bytes()),'overlap_checks':report['competitions'][slug]['overlap_checks']}
        body=json.dumps(d,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode();sha=digest(body);p=root/slug/'snapshots'/(sha+'.json')
        if p.exists() and p.read_bytes()!=body:raise ValueError('snapshot_collision')
        if not p.exists():p.write_bytes(body)
        pointer={'file':str(p.relative_to(root/slug)),'sha256':sha,'rows':len(d['matches'])}
        (root/slug/'latest.json').write_text(json.dumps(pointer))
        report['competitions'][slug].update(xg=sum(c['any_source_xg_both'] for c in d['coverage']),snapshot=pointer)
    (work/'pitchradar-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('source_receipt','history_receipt')},ensure_ascii=True))

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);run(p.parse_args().root)
