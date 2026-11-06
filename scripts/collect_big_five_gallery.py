"""Pinned public Understat display values; no ratings, odds or inferred xG imported."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from html import unescape
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from threading import Event
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.supplement_big_five import ALIASES, read, digest
from worldcup_mvp.console.big_five_history import date

CODES = {'EPL':'eng.1','La_Liga':'esp.1','Bundesliga':'ger.1','Serie_A':'ita.1','Ligue_1':'fra.1'}
NAMES = {'eng.1':{'Coventry':'Coventry City','Hull':'Hull City'},
         'esp.1':{'Deportivo La Coruna':'Deportivo','Malaga':'Málaga'},
         'ger.1':{'Elversberg':'SV Elversberg','Paderborn':'SC Paderborn 07'}}
SOURCE = 'understat_gallery_archive'


def blob_sha(body):
    return hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()


def parse_page(body, match):
    text = body.decode('utf8')
    events = [json.loads(x) for x in re.findall(r'<script type="application/ld\+json">(.*?)</script>',text,re.S)]
    events = [e for e in events if e.get('@type')=='SportsEvent']
    if len(events)!=1: raise ValueError('page_event_ambiguous')
    e=events[0]
    if (e['homeTeam']['name']!=match['h'] or e['awayTeam']['name']!=match['a']
            or date(e['startDate'])!=date(match['t'])
            or e['url']!='https://iyush.dev/football/gallery/match/'+match['u']):
        raise ValueError('page_identity_conflict')
    expected=match['k']
    if f'<dt>Match ID</dt><dd>{expected}</dd>' not in text:raise ValueError('page_source_id_conflict')
    score=re.findall(r'<span class="sr-only">(\d+) to (\d+)</span>',text)
    if score!=[(str(match['hg']),str(match['ag']))]:raise ValueError('page_score_conflict')
    rows=re.findall(r'<tr>(.*?)</tr>',text,re.S)
    xrows=[r for r in rows if re.search(r'<th[^>]*>xG<small>Understat · context only, not in the rating</small></th>',r)]
    if len(xrows)!=1:raise ValueError('understat_xg_row_missing_or_ambiguous')
    values=re.findall(r'<td class="num">([0-9]+\.[0-9]{2})</td>',xrows[0])
    if len(values)!=2:raise ValueError('xg_values_missing')
    xg=[float(v) for v in values]
    if not all(math.isfinite(v) and 0<=v<=30 for v in xg):raise ValueError('xg_values_invalid')
    return xg


def match_fixture(slug, match, rows, mapping):
    if CODES.get(match['c'])!=slug or match.get('aet') is not None or match.get('so') is not None:
        raise ValueError('source_competition_or_period_conflict')
    season=match['k'].split('-')[1]
    uid=match['k'].split('-')[-1]
    if not uid.startswith('us:') or not uid[3:].isdecimal():raise ValueError('not_understat_id')
    names=[NAMES.get(slug,{}).get(match[s],ALIASES[slug].get(match[s],match[s])) for s in ('h','a')]
    candidates=[r for r in rows if r['season']==season and [r['home_name'],r['away_name']]==names]
    if len(candidates)!=1:raise ValueError('fixture_identity_unmatched')
    r=candidates[0]
    if r.get('training_eligible') is not True or r['period']!='regular_time':raise ValueError('fixture_not_eligible')
    if date(r['kickoff'])!=date(match['t']):raise ValueError('fixture_kickoff_conflict')
    if r['kickoff'][:10]!=match['d']:raise ValueError('fixture_calendar_conflict')
    if [r['home_goals'],r['away_goals']]!=[match['hg'],match['ag']]:raise ValueError('fixture_score_conflict')
    for side,short in [('home','h'),('away','a')]:
        source_id=match[short+'c'].replace('us:','understat:')
        if not source_id.startswith('understat:') or not source_id[10:].isdecimal():raise ValueError('invalid_team_id')
        prior=mapping.get(source_id)
        if prior and prior['espn_id']!=r[side+'_id']:raise ValueError('team_mapping_conflict')
        if any(v['espn_id']==r[side+'_id'] and k!=source_id for k,v in mapping.items()):
            raise ValueError('nonbijective_team_mapping')
    return r


def collect(root, cached_only=False):
    work=root/'next';raw=work/'gallery';raw.mkdir(exist_ok=True)
    tree=read(work/'iyush-tree.json');commit=tree['sha'];entries={t['path']:t for t in tree['tree']}
    if not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('invalid_commit')
    # Freeze parents on first execution, so retries never recursively use their own output.
    parents_path=work/'gallery-parents.json'
    if not parents_path.exists():parents_path.write_text(json.dumps({s:read(root/s/'latest.json') for s in CODES.values()}))
    parents=read(parents_path);data={}
    for slug,p in parents.items():
        body=(root/slug/p['file']).read_bytes()
        if digest(body)!=p['sha256']:raise ValueError('parent_integrity_failure')
        data[slug]=json.loads(body)
    jobs=[];errors=[];overlap=Counter();seen=set();source_targets={}
    for year in (2025,2026):
        path=f'football/data/gallery/{year}.json';file=work/path.replace('/','_');body=file.read_bytes()
        receipt=read(file.with_suffix('.json.receipt.json'))
        if digest(body)!=receipt['sha256'] or blob_sha(body)!=entries[path]['sha'] or receipt['commit']!=commit:
            raise ValueError('gallery_index_integrity_failure')
        index=json.loads(body)
        if index['meta']['season']!=str(year):raise ValueError('index_season_mismatch')
        for m in index['matches']:
            if m['c'] not in CODES:continue
            slug=CODES[m['c']]
            try:
                if m['k'] in seen:raise ValueError('duplicate_source_match')
                seen.add(m['k'])
                target=match_fixture(slug,m,data[slug]['matches'],data[slug]['understat_mapping'])
                source_id=m['k'].split('-')[-1]
                if source_id in source_targets and source_targets[source_id]!=target['id']:
                    raise ValueError('source_match_id_reassigned')
                source_targets[source_id]=target['id']
                for side,short in [('home','h'),('away','a')]:
                    data[slug]['understat_mapping'].setdefault(m[short+'c'].replace('us:','understat:'),
                        {'source_name':m[short],'espn_name':target[side+'_name'],'espn_id':target[side+'_id'],
                         'policy':'explicit_reviewed_gallery_alias','index_receipt':receipt})
                known=target.get('supplemental_postmatch_facts',{}).get('understat_archive')
                if known:
                    if known['source_id']!=m['k'].split('-')[-1].replace('us:','understat:'):
                        raise ValueError('understat_match_id_conflict')
                    if overlap[slug]>=5:continue
                    overlap[slug]+=1
                path='football/gallery/match/'+m['u']+'.html'
                if path not in entries:raise ValueError('page_missing_from_commit')
                jobs.append((slug,m,target,path,receipt))
            except (ValueError,KeyError,TypeError) as exc:errors.append({'source_id':m['k'],'reason':str(exc)})
    stopped=Event()
    def fetch(job):
        slug,m,target,path,index_receipt=job;entry=entries[path];p=raw/(entry['sha']+'.html');meta=p.with_suffix('.receipt.json')
        url=f'https://raw.githubusercontent.com/Spyyy004/iyush.dev/{commit}/{path}'
        try:
            if not p.exists() or not meta.exists():
                if cached_only:raise ValueError('not_cached')
                if stopped.is_set():raise ValueError('collection_stopped_after_access_denial')
                response=subprocess.run(['curl.exe','-sS','-L','--max-time','35','-o',str(p),'-w','%{http_code}',url],capture_output=True,timeout=40)
                receipt={'url':url,'commit':commit,'file':p.name,'observed_at':datetime.now(timezone.utc).isoformat(),
                         'http_status':response.stdout.decode().strip(),'transport_exit':response.returncode}
                if p.exists():receipt['sha256']=digest(p.read_bytes())
                meta.write_text(json.dumps(receipt,indent=2))
                if receipt['http_status'] in ('403','429'):stopped.set()
            receipt=read(meta);body=p.read_bytes()
            if receipt['http_status']!='200' or receipt.get('transport_exit')!=0:raise ValueError('source_request_failed')
            if digest(body)!=receipt['sha256'] or blob_sha(body)!=entry['sha'] or receipt['url']!=url:
                raise ValueError('page_integrity_failure')
            xg=parse_page(body,m)
            known=target.get('supplemental_postmatch_facts',{}).get('understat_archive')
            if known and any(abs(a-b)>0.005001 for a,b in zip(xg,known['xg'])):raise ValueError('overlap_xg_conflict')
            return {'slug':slug,'target':target['id'],'source_id':m['k'].split('-')[-1].replace('us:','understat:'),
                'xg':xg,'source_kind':'third_party_display_archive_of_understat','precision_decimal_places':2,
                'source_receipt':receipt,'index_receipt':index_receipt,'identity_policy':'explicit_teams_ids_season_exact_kickoff_fulltime_v1',
                'upstream_collection_time_verified':False,'approved_as_training_feature':False,'overlap_check':bool(known)},None
        except Exception as exc:return None,{'source_id':m['k'],'reason':str(exc)}
    facts=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for i,f in enumerate(as_completed([pool.submit(fetch,j) for j in jobs]),1):
            fact,error=f.result()
            if fact:facts.append(fact)
            if error:errors.append(error)
            if i%50==0:print(json.dumps({'processed':i,'requested':len(jobs),'facts':len(facts),'errors':len(errors)}),flush=True)
    if any(e['reason']=='overlap_xg_conflict' for e in errors):
        (work/'gallery-errors.json').write_text(json.dumps(errors,indent=2));raise ValueError('overlap_validation_failed')
    report={'commit':commit,'requested':len(jobs),'source_not_official_integration':True,'errors':errors,'competitions':{}}
    for slug,d in data.items():
        new={f['target']:f for f in facts if f['slug']==slug and not f['overlap_check']}
        for row in d['matches']:
            if row['id'] in new:row.setdefault('supplemental_postmatch_facts',{})[SOURCE]=new[row['id']]
        for c in d['coverage']:
            group=[r for r in d['matches'] if r['season']==c['season'] and r['training_eligible']]
            c['gallery_archive_xg_both']=sum(SOURCE in r.get('supplemental_postmatch_facts',{}) for r in group)
            c['any_source_xg_both']=sum(bool(r.get('supplemental_postmatch_facts')) or bool(r.get('external_postmatch_xg')) for r in group)
        d['manifest'].update(gallery_commit=commit,gallery_parent_sha256=parents[slug]['sha256'],gallery_source_code_sha256=digest(Path(__file__).read_bytes()))
        body=json.dumps(d,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode();sha=digest(body)
        p=root/slug/'snapshots'/(sha+'.json')
        if p.exists() and p.read_bytes()!=body:raise ValueError('snapshot_collision')
        if not p.exists():p.write_bytes(body)
        pointer={'file':str(p.relative_to(root/slug)),'sha256':sha,'rows':len(d['matches'])}
        (root/slug/'latest.json').write_text(json.dumps(pointer,indent=2))
        report['competitions'][slug]={'added':len(new),'overlap_checks':sum(f['slug']==slug and f['overlap_check'] for f in facts),
            'xg':sum(c['any_source_xg_both'] for c in d['coverage']),'snapshot':pointer}
    (work/'gallery-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({s:{k:v for k,v in r.items() if k!='snapshot'} for s,r in report['competitions'].items()},ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--cached-only',action='store_true')
    a=p.parse_args();collect(a.root,a.cached_only)
