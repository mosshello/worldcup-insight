"""Import dated market observations and partial FPL availability reports."""
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from io import StringIO
from math import isfinite
from pathlib import Path
import csv
import gzip
import json
import sys
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.restore_big_five_boxscores import ROOT, WORK, CODES, checked_file
from scripts.supplement_big_five import read, digest
from worldcup_mvp.console.training import atomic_json

ARCHIVES={
    'odds':('ovignez-hash/timestamped-football-odds-pinnacle','3b48b3f76555b0f3759bf218fa21da372e14694d'),
    'fpl':('beeradb/FPL-Armband','d4e13036c4e9939299053946d59dfcf8d1a47387')}
MATCHES={
    '10856112':('eng.1','ENG_PL','espn:740971','West Ham','Leeds','espn:371','espn:357'),
    '11889805':('esp.1','ESP_LL','espn:748515','Villarreal','Atletico Madrid','espn:102','espn:1068'),
    '13087014':('esp.1','ESP_LL','espn:401882868','Rayo Vallecano','Espanyol','espn:101','espn:88'),
    '13086836':('esp.1','ESP_LL','espn:401882876','CD Alaves','Valencia','espn:96','espn:94'),
    '13086922':('esp.1','ESP_LL','espn:401882872','Elche','Real Madrid','espn:3751','espn:86'),
    '10909720':('ita.1','ITA_SA','espn:737158','Torino','Juventus','espn:239','espn:111'),
    '10702928':('ger.1','GER_BL','espn:747022','Werder Bremen','Borussia Dortmund','espn:137','espn:124'),
    '10176815':('fra.1','FRA_L1','espn:746717','Marseille','Rennes','espn:176','espn:169')}
MARKETS={'FT_1X2':('Home','Draw','Away'),'FT_Asian_Handicap':('Home','Away'),'FT_Total_Goals':('Over','Under')}
FPL_NAMES={'Bournemouth':'AFC Bournemouth','Brighton':'Brighton & Hove Albion',
    'Ipswich':'Ipswich Town','Leeds':'Leeds United','Leicester':'Leicester City','Luton':'Luton Town',
    'Man City':'Manchester City','Man Utd':'Manchester United','Newcastle':'Newcastle United',
    'Norwich':'Norwich City',"Nott'm Forest":'Nottingham Forest','Sheffield Utd':'Sheffield United',
    'Spurs':'Tottenham Hotspur','West Ham':'West Ham United','Wolves':'Wolverhampton Wanderers'}


def utc(value):
    d=datetime.fromisoformat(value)
    if d.tzinfo is None:raise ValueError('unverified_timezone')
    return d.astimezone(timezone.utc)


def archive(name):
    repo,commit=ARCHIVES[name];filename=name+'-pinned.zip'
    rec=checked_file(WORK,filename,'https://codeload.github.com/'+repo+'/zip/'+commit)
    z=zipfile.ZipFile(WORK/filename)
    if z.comment.decode()!=commit or len(set(z.namelist()))!=len(z.namelist()):
        z.close();raise ValueError('archive_commit_or_entries_conflict')
    return z,{**rec,'commit':commit}


def rows_in(z,path):
    body=z.read(path)
    if path.endswith('.gz'):body=gzip.decompress(body)
    return csv.DictReader(StringIO(body.decode('utf-8-sig')))


def quote(q,cutoff):
    market=q['Market']
    if market not in MARKETS or q['Phase']!='Pre-match' or q['Suspended']!='0':return None
    at=utc(q['Timestamp_UTC'])
    if int(at.timestamp())!=int(q['Epoch']):raise ValueError('quote_epoch_conflict')
    if at>=cutoff or any(q[k] for k in ('Minute','Second','Score_Home','Score_Away')):return None
    if q['Book'] not in {'Bet365','Pinnacle','Betfair','Betsson','Unibet'}:raise ValueError('unknown_book')
    try:
        prices={s:float(q['Price_'+s]) for s in MARKETS[market]}
        line=float(q['Line']) if market!='FT_1X2' else None
    except (ValueError,TypeError):return None
    if (not all(isfinite(v) and v>1 for v in prices.values())
            or (line is not None and not isfinite(line)) or (line is None and q['Line'])):return None
    return {'book':q['Book'],'market':market,'line':line,'decimal_prices':prices,'quoted_at':at.isoformat()}


def market_evidence(snapshots):
    z,rec=archive('odds');summary={}
    with z:
        root=z.namelist()[0]+'sample/'
        matches={m['Match_ID']:m for m in rows_in(z,root+'Big6_Matches.csv')}
        for mid,(slug,code,eid,home,away,hid,aid) in MATCHES.items():
            m=matches[mid];row=next(r for r in snapshots[slug]['matches'] if r['id']==eid)
            start=utc(row['kickoff'])
            if (m['Competition_Code']!=code or m['Division']!='1' or m['Stage']!='Regular Season' or m['Extra_Time']!='0'
                    or m['Season'][:4]!=row['season'] or not row['training_eligible'] or row['period']!='regular_time'
                    or row['competition_id']!=snapshots[slug]['manifest']['competition_id']
                    or [m['Home_Team'],m['Away_Team']]!=[home,away] or [row['home_id'],row['away_id']]!=[hid,aid]
                    or utc(m['Kickoff_UTC'])!=start or int(m['Kickoff_Epoch'])!=int(start.timestamp())
                    or any(int(m[side+'_Goals_'+phase])!=row[side.lower()+'_'+field] for side in ['Home','Away']
                           for phase,field in [('HT','half_goals'),('90','goals')])):
                raise ValueError('market_match_identity_conflict:'+mid)
            cutoff=start
            if mid=='10909720':
                earlier=row['supplemental_postmatch_facts']['understat_gallery_archive']['identity_exception']['source_kickoff']
                cutoff=min(start,utc(earlier))
            path=root+'odds_timestamped/Big6_Odds_Timestamped_'+code+'.csv.gz'
            groups=defaultdict(list)
            for raw in rows_in(z,path):
                if raw['Match_ID']!=mid:continue
                val=quote(raw,cutoff)
                if val:groups[(val['book'],val['market'],val['line'],val['quoted_at'])].append(val)
            # Ambiguous same-instant prices cannot be ordered by an invented sequence.
            kept=[v[0] for v in groups.values() if all(x==v[0] for x in v)]
            if not kept:raise ValueError('no_timestamped_pregame_quotes:'+mid)
            kept.sort(key=lambda x:(x['quoted_at'],x['book'],x['market'],str(x['line'])))
            row['prematch_market_archive']={'source':'Olivier Sports Data, free evaluation sample',
                'source_match_id':mid,'source_receipt':rec,'file_in_zip':path,'safe_cutoff_utc':cutoff.isoformat(),
                'purpose':'market_reference_only','quotes':kept,'ambiguous_keys_excluded':sum(len(set(str(v) for v in x))>1 for x in groups.values()),
                'approved_as_training_feature':False}
            summary[eid]={'quotes':len(kept),'markets':dict(Counter(q['market'] for q in kept))}
    return summary


def captures(z):
    observations=defaultdict(dict);rejected=[]
    for path in z.namelist():
        if '/data/captures/' not in path or not path.endswith('/manifest.json'):continue
        m=json.loads(z.read(path));bf=m.get('backfill');stamp=utc(m['captured_at'])
        if bf:
            season=bf['season'][:4]
            if season not in {'2021','2022','2023','2024','2025'}:continue
            ts=stamp.strftime('%Y%m%d%H%M%S')
            if bf['wayback_timestamp']!=ts or bf['source']!='https://web.archive.org/web/'+ts+'id_/https://fantasy.premierleague.com/api/bootstrap-static/':
                raise ValueError('archive_capture_identity')
        else:
            if stamp.year!=2026 or stamp.month<7:continue
            season='2026'
        entries=[f for f in m['files'] if f['endpoint']=='/bootstrap-static/' and f['name']=='bootstrap-static.json.gz']
        if len(entries)!=1:raise ValueError('capture_endpoint_missing')
        f=entries[0];blob=z.read(path.rsplit('/',1)[0]+'/'+f['name']);raw=gzip.decompress(blob)
        if len(blob)!=f['stored_bytes'] or len(raw)!=f['bytes'] or digest(raw)!=f['sha256']:raise ValueError('capture_integrity')
        payload=json.loads(raw);event=[e for e in payload['events'] if e['id']==m['event']]
        if len(event)!=1 or event[0]['finished'] or not stamp<min(utc(m['event_deadline']),utc(event[0]['deadline_time'])):
            rejected.append({'file':path,'reason':'payload_does_not_confirm_predeadline_capture'});continue
        by_id={t['id']:FPL_NAMES.get(t['name'],t['name']) for t in payload['teams']}
        if len(by_id)!=20 or len(set(by_id.values()))!=20:raise ValueError('team_identity')
        teams={name:{'listed_players':0,'reported_flags':[]} for name in by_id.values()}
        seen=set()
        for p in payload['elements']:
            if p['id'] in seen or p['team'] not in by_id:raise ValueError('player_identity_conflict')
            seen.add(p['id']);t=teams[by_id[p['team']]];t['listed_players']+=1
            if p['status'] not in ('i','d','s') or not p.get('news'):continue
            published=p.get('news_added')
            if published and utc(published)>stamp:continue
            t['reported_flags'].append({'id':f"fpl:{season}:{p['id']}",'name':p['web_name'],
                'fpl_status':p['status'],'reported_news_at':published,'news_sha256':digest(p['news'].encode())})
        if min(t['listed_players'] for t in teams.values())<11:raise ValueError('incomplete_team_list')
        rec={'teams':teams,'manifest_path':path,'payload_sha256':f['sha256'],'backfill':bf,
             'captured_at':stamp.isoformat(),'source':'FPL bootstrap-static archived snapshot'}
        prior=observations[season].get(stamp)
        if prior and prior['payload_sha256']!=rec['payload_sha256']:raise ValueError('capture_time_collision')
        observations[season][stamp]=rec
    return observations,rejected


def available_at(observations,start,max_age=timedelta(hours=72)):
    times=sorted(observations);idx=bisect_left(times,start)-1
    if idx<0 or start-times[idx]>max_age:return None
    return observations[times[idx]]


def fpl_evidence(rows):
    z,receipt=archive('fpl')
    with z:obs,rejected=captures(z)
    counts=Counter();flags=Counter()
    for row in rows:
        if row['competition_id']!='eng.premier' or not row['training_eligible'] or row['period']!='regular_time':continue
        rec=available_at(obs.get(row['season'],{}),utc(row['kickoff']))
        if not rec:continue
        sides={}
        for side in ('home','away'):
            name=row[side+'_name']
            if name not in rec['teams']:raise ValueError('missing_season_scoped_fpl_team:'+name)
            sides[side]={'team_id':row[side+'_id'], **rec['teams'][name]}
        row['prematch_fpl_observation']={**{k:v for k,v in rec.items() if k!='teams'},'sides':sides,
            'archive_receipt':receipt,'scope':'FPL-listed players, reported injury/doubt/suspension flags only',
            'matchday_availability_complete':False,'approved_as_training_feature':False}
        counts[row['season']]+=1;flags[row['season']]+=sum(len(x['reported_flags']) for x in sides.values())
    return {'matches_by_season':dict(counts),'flag_instances_by_season':dict(flags),
            'distinct_captures_by_season':{k:len(d) for k,d in obs.items()},'excluded_captures':rejected}


def run():
    parentpath=WORK/'prematch-parents.json'
    if not parentpath.exists():atomic_json(parentpath,{s:read(ROOT/s/'latest.json') for s in CODES})
    parents=read(parentpath);snapshots={};outputs={}
    for slug,p in parents.items():
        raw=(ROOT/slug/p['file']).read_bytes()
        if digest(raw)!=p['sha256']:raise ValueError('parent_integrity')
        snapshots[slug]=json.loads(raw)
    report={'market':market_evidence(snapshots),'fpl':fpl_evidence(snapshots['eng.1']['matches'])}
    for slug,d in snapshots.items():
        for c in d['coverage']:
            rows=[r for r in d['matches'] if r['training_eligible'] and r['season']==c['season']]
            c['timestamped_prematch_market_matches']=sum('prematch_market_archive' in r for r in rows)
            c['partial_fpl_availability_snapshots']=sum('prematch_fpl_observation' in r for r in rows)
        d['manifest']['event_evidence_update']['prematch_archives']={'parent_sha256':parents[slug]['sha256'],
            'code_sha256':digest(Path(__file__).read_bytes()),'max_fpl_age_hours':72,
            'market_reference_matches':sum(c['timestamped_prematch_market_matches'] for c in d['coverage']),
            'partial_fpl_availability_matches':sum(c['partial_fpl_availability_snapshots'] for c in d['coverage']),
            'verified_complete_medical_or_eligibility_snapshots':0}
        body=json.dumps(d,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        sha=digest(body);dest=ROOT/slug/'snapshots'/(sha+'.json')
        if dest.exists() and digest(dest.read_bytes())!=sha:raise ValueError('snapshot_collision')
        if not dest.exists():dest.write_bytes(body)
        outputs[slug]={'file':str(dest.relative_to(ROOT/slug)),'sha256':sha,'rows':len(d['matches'])}
    for slug,p in outputs.items():
        if read(ROOT/slug/'latest.json') not in (parents[slug],p):raise ValueError('newer_snapshot_present')
    atomic_json(WORK/'prematch-audit.json',report)
    for slug,p in outputs.items():atomic_json(ROOT/slug/'latest.json',p)
    print('market coverage',len(report['market']),sum(m['quotes'] for m in report['market'].values()))
    print('FPL',report['fpl']['matches_by_season'],'excluded captures',len(report['fpl']['excluded_captures']))


if __name__=='__main__':run()
