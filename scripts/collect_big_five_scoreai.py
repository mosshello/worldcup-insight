"""Collect only attributed post-match measurements, never the site's AI outputs."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
import math
import re
import subprocess
from datetime import datetime, timezone, timedelta
from scripts.supplement_big_five import read, digest
from worldcup_mvp.console.big_five_history import SPECS, date

SOURCE='scoreai_apifootball_display'
COMPETITIONS={'PL':'eng.1','PD':'esp.1'}
# Reviewed provider IDs -> ESPN names; do not normalize away arbitrary suffixes.
NAMES={
 'arsenal':'Arsenal','man-city':'Manchester City','brighton':'Brighton & Hove Albion','chelsea':'Chelsea',
 'liverpool':'Liverpool','brentford':'Brentford','everton':'Everton','bournemouth':'AFC Bournemouth',
 'tottenham':'Tottenham Hotspur','aston-villa':'Aston Villa','hull':'Hull City','leeds':'Leeds United',
 'newcastle':'Newcastle United','ipswich':'Ipswich Town','fulham':'Fulham','coventry':'Coventry City',
 'man-united':'Manchester United','crystal-palace':'Crystal Palace','nottingham':'Nottingham Forest','sunderland':'Sunderland',
 'fd-team-86':'Real Madrid','fd-team-94':'Villarreal','fd-team-90':'Real Betis','fd-team-81':'Barcelona',
 'fd-team-78':'Atlético Madrid','fd-team-263':'Alavés','fd-team-82':'Getafe','fd-team-559':'Sevilla',
 'fd-team-87':'Rayo Vallecano','fd-team-5335':'Racing Santander','fd-team-80':'Espanyol','fd-team-88':'Levante',
 'fd-team-560':'Deportivo','fd-team-285':'Elche','fd-team-84':'Málaga','fd-team-92':'Real Sociedad',
 'fd-team-77':'Athletic Club','fd-team-95':'Valencia','fd-team-558':'Celta Vigo','fd-team-79':'Osasuna',
}

def fixture(match,rows):
    if match['competition'] not in COMPETITIONS or match['season']!='2026/27' or match['state']!='finished' or match.get('kickoffTentative') is not False:
        raise ValueError('source_fixture_not_confirmed')
    names=[NAMES.get(match[s]) for s in ('home','away')]
    if None in names:raise ValueError('unknown_team')
    candidates=[r for r in rows if r['season']=='2026' and [r['home_name'],r['away_name']]==names]
    if len(candidates)!=1:raise ValueError('fixture_not_unique')
    target=candidates[0]
    if target['competition_id']!=SPECS[COMPETITIONS[match['competition']]][0]:raise ValueError('competition_conflict')
    if target['training_eligible'] is not True or target['period']!='regular_time':raise ValueError('ineligible_target')
    if date(target['kickoff'])!=date(match['utcDate']):raise ValueError('kickoff_conflict')
    for fields,key in [(('home_goals','away_goals'),'score'),(('home_half_goals','away_half_goals'),'halfTime')]:
        if key=='halfTime' and match.get(key) is None:continue
        if [target[f] for f in fields]!=match[key]:raise ValueError(key+'_conflict')
    return target

def measurement(match,detail):
    if detail.get('matchId')!=match['id']:raise ValueError('detail_match_id_conflict')
    view=detail.get('views',{}).get('stats',{})
    if view.get('provider')!='API-Football' or view.get('status')!='ok':raise ValueError('attributed_statistics_unavailable')
    if date(view['fetchedAt'])<date(match['utcDate'])+timedelta(minutes=90):raise ValueError('not_postmatch_statistics')
    groups=view.get('statistics',[])
    if len(groups)!=2 or {g['teamId'] for g in groups}!={match['home'],match['away']}:raise ValueError('statistics_team_identity_conflict')
    xg=[];precision=[]
    for side in ('home','away'):
        group=next(g for g in groups if g['teamId']==match[side])
        values=[item['value'] for item in group['items'] if re.search(r'\bxG\b',item['label'],re.I)]
        if len(values)!=1 or not re.fullmatch(r'\d+(?:\.\d{1,2})?',str(values[0])):raise ValueError('xg_missing_or_ambiguous')
        value=float(values[0])
        if not math.isfinite(value) or not 0<=value<=30:raise ValueError('invalid_xg')
        xg.append(value)
        precision.append(len(str(values[0]).partition('.')[2]))
    return {'source_id':'scoreai:'+match['id'],'xg':xg,'source_kind':'third_party_display_attributed_to_API-Football',
        'precision_decimal_places':precision,'upstream_collection_time_verified':False,'display_reported_fetched_at':view['fetchedAt'],
        'identity_policy':'explicit_provider_team_ids_season_exact_kickoff_fulltime_optional_halftime_v1',
        'approved_as_training_feature':False}

def cached_json(path,url,cached_only):
    meta=path.with_suffix('.receipt.json')
    if not path.exists() or not meta.exists():
        if cached_only:raise ValueError('not_cached')
        r=subprocess.run(['curl.exe','-sS','-L','--max-time','30','-o',str(path),'-w','%{http_code}',url],capture_output=True,timeout=35)
        receipt={'url':url,'file':path.name,'http_status':r.stdout.decode().strip(),'transport_exit':r.returncode,
                 'observed_at':datetime.now(timezone.utc).isoformat(),'sha256':digest(path.read_bytes()) if path.exists() else None}
        meta.write_text(json.dumps(receipt,indent=2))
    receipt=read(meta)
    if receipt['http_status'] in ('403','429'):raise PermissionError('collection_stopped_after_access_denial')
    if receipt['http_status']!='200' or receipt['transport_exit']!=0:raise ValueError('source_request_failed')
    body=path.read_bytes()
    if digest(body)!=receipt['sha256'] or receipt['url']!=url:raise ValueError('source_integrity_failure')
    return json.loads(body),receipt

def run(root,cached_only=False):
    work=root/'final-gaps';raw=work/'scoreai';raw.mkdir(exist_ok=True)
    parentpath=work/'scoreai-parents.json'
    if not parentpath.exists():parentpath.write_text(json.dumps({s:read(root/s/'latest.json') for s in SPECS}))
    parents=read(parentpath);data={}
    for slug,p in parents.items():
        body=(root/slug/p['file']).read_bytes()
        if digest(body)!=p['sha256']:raise ValueError('parent_integrity_failure')
        data[slug]=json.loads(body)
    feed,receipt=cached_json(work/'scoreai-fixture-feed.json','https://scoreai.my/api/football?scope=all&view=page&lang=en',cached_only)
    report={'added':[],'errors':[],'competitions':{},'provider_integration':False,'not_trained':True}
    seen=set()
    for match in feed['data']['matches']:
        if match.get('competition') not in COMPETITIONS or match.get('state')!='finished':continue
        slug=COMPETITIONS[match['competition']]
        try:
            target=fixture(match,data[slug]['matches'])
            if target['id'] in seen:raise ValueError('duplicate_source_fixture')
            seen.add(target['id'])
            if target.get('supplemental_postmatch_facts') or target.get('external_postmatch_xg'):continue
            if not re.fullmatch(r'fd-(?:PD-)?\d+',match['id']):raise ValueError('invalid_source_id')
            payload,detail_receipt=cached_json(raw/(match['id']+'.json'),'https://scoreai.my/api/football/match-details?match='+match['id'],cached_only)
            fact=measurement(match,payload['data']);fact.update(source_receipt=detail_receipt,index_receipt=receipt,
                identity_exception={'source_fixture':match,'half_time_verified':match.get('halfTime') is not None,
                    'provider_names':{t['id']:t.get('en') for t in feed['data']['teams'] if t['id'] in (match['home'],match['away'])}})
            target.setdefault('supplemental_postmatch_facts',{})[SOURCE]=fact
            report['added'].append({'slug':slug,'id':target['id'],'source_id':fact['source_id']})
            print(json.dumps({'added':len(report['added']),'id':target['id']}),flush=True)
        except (ValueError,KeyError) as exc:report['errors'].append({'id':match['id'],'reason':str(exc)})
    for slug,d in data.items():
        added=sum(f['slug']==slug for f in report['added'])
        for c in d['coverage']:
            group=[r for r in d['matches'] if r['season']==c['season'] and r['training_eligible']]
            c['any_source_xg_both']=sum(bool(r.get('supplemental_postmatch_facts')) or bool(r.get('external_postmatch_xg')) for r in group)
            c['scoreai_apifootball_xg_both']=sum(SOURCE in r.get('supplemental_postmatch_facts',{}) for r in group)
        if added:
            d['manifest']['scoreai_supplement']={'parent_sha256':parents[slug]['sha256'],'index_receipt':receipt,'code_sha256':digest(Path(__file__).read_bytes())}
            body=json.dumps(d,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode();sha=digest(body);p=root/slug/'snapshots'/(sha+'.json')
            if p.exists() and p.read_bytes()!=body:raise ValueError('snapshot_collision')
            if not p.exists():p.write_bytes(body)
            (root/slug/'latest.json').write_text(json.dumps({'file':str(p.relative_to(root/slug)),'sha256':sha,'rows':len(d['matches'])}))
        report['competitions'][slug]={'added':added,'xg':sum(c['any_source_xg_both'] for c in d['coverage'])}
    (work/'scoreai-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=True),flush=True)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--cached-only',action='store_true');a=p.parse_args();run(a.root,a.cached_only)
