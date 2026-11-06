"""Admit traceable archive facts to NEW research snapshots, never to forecasts.

Requires the public pannadata Understat metadata release and its download receipt.
Naive archive times remain naive; identity is explicit teams + season + calendar
date + final score. No fuzzy matching, inferred timezone, or pre-match claim.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console.big_five_history import SPECS, BASE, date, fetch_url, teams
from scripts.finalize_big_five import supplement_receipt, SPANISH_REPAIRS

LEAGUES={'eng.1':('ENG','EPL'),'esp.1':('ESP','La liga'),'ger.1':('GER','Bundesliga'),
         'ita.1':('ITA','Serie A'),'fra.1':('FRA','Ligue 1')}
ALIASES={
 'eng.1':{'Bournemouth':'AFC Bournemouth','Brighton':'Brighton & Hove Albion','Ipswich':'Ipswich Town',
   'Leeds':'Leeds United','Leicester':'Leicester City','Luton':'Luton Town','Norwich':'Norwich City',
   'Tottenham':'Tottenham Hotspur','West Ham':'West Ham United'},
 'esp.1':{'Alaves':'Alavés','Almeria':'Almería','Atletico Madrid':'Atlético Madrid','Cadiz':'Cádiz','Leganes':'Leganés'},
 'ger.1':{'Augsburg':'FC Augsburg','Bochum':'VfL Bochum','Borussia M.Gladbach':'Borussia Mönchengladbach',
   'Darmstadt':'SV Darmstadt 98','FC Heidenheim':'1. FC Heidenheim 1846','Freiburg':'SC Freiburg',
   'Greuther Fuerth':'SpVgg Greuther Fürth','Hamburger SV':'Hamburg SV','Hoffenheim':'TSG Hoffenheim',
   'Mainz 05':'Mainz','RasenBallsport Leipzig':'RB Leipzig','Union Berlin':'1. FC Union Berlin','Wolfsburg':'VfL Wolfsburg'},
 'ita.1':{'Inter':'Internazionale','Parma Calcio 1913':'Parma','Roma':'AS Roma','Verona':'Hellas Verona'},
 'fra.1':{'Ajaccio':'AC Ajaccio','Auxerre':'AJ Auxerre','Le Havre':'Le Havre AC','Monaco':'AS Monaco',
   'Paris Saint Germain':'Paris Saint-Germain','Reims':'Stade de Reims','Rennes':'Stade Rennais','Saint-Etienne':'Saint-Étienne'},
}

def digest(body):return hashlib.sha256(body).hexdigest()
def read(path):return json.loads(path.read_text(encoding='utf8'))


def evidence(root,name):
    receipt=read(root/'continuation'/(name+'.receipt.json'))
    if receipt['http_status']!='200' or digest((root/'continuation'/receipt['file']).read_bytes())!=receipt['sha256']:
        raise ValueError('official_evidence_integrity_failure')
    return receipt


def official_corrections(root,slug,data):
    rows=data['matches']
    if slug=='esp.1':
        for eid,hid,aid,_,_,ft,ht,_ in SPANISH_REPAIRS:
            target=next(r for r in rows if r['id']=='espn:'+eid)
            summary,receipt=fetch_url(BASE+'esp.1/summary?event='+eid,root/slug/'raw',allow_network=False)
            header=summary['header'];comp=header['competitions'][0];sides=teams(comp)
            if (header['uid']!='s:600~l:740~e:'+eid or str(header['season']['year'])!=target['season']
                    or date(comp['date'])!=date(target['kickoff']) or comp.get('neutralSite') is not False
                    or [str(sides[s]['team']['id']) for s in ('home','away')]!=[hid,aid]
                    or [target[s+'_goals'] for s in ('home','away')]!=ft
                    or [target[s+'_half_goals'] for s in ('home','away')]!=ht):
                raise ValueError('corrected_result_identity_or_venue_conflict')
            supplement_receipt(root/'supplements','valencia-athletic' if eid=='643974' else 'official-'+eid)
            target.update(neutral_site=False,training_eligible=True,
                eligibility_policy='explicit_official_and_openfootball_score_with_espn_identity_and_venue_v1')
            target.pop('exclusion_reason',None)
            target['correction']['venue_receipt']=receipt
            # The scheduled ESPN status and unverified summary result remain visible as conflicts.
    if slug=='ita.1':
        target=next(r for r in rows if r['id']=='espn:712475')
        if (target['neutral_site'] is not False or target['summary_verified'] is not True
                or [target[s+'_goals'] for s in ('home','away')]!=[3,1]
                or target.get('half_time_correction',{}).get('corrected')!=[1,1]):
            raise ValueError('half_time_correction_not_verified')
        supplement_receipt(root/'supplements','roma-milan')
        target['training_eligible']=True;target['eligibility_policy']='explicit_official_half_time_correction_v1'
        target.pop('exclusion_reason',None)
    if slug=='ita.1':
        target=next(r for r in rows if r['id']=='espn:712218')
        if (target['home_name'],target['away_name'],target['kickoff'])!=('Monza','AC Milan','2024-11-02T19:45:00+00:00'):
            raise ValueError('lineup_correction_identity_conflict')
        expected={'261399','169219','301278','165490','251776','305672','224188','116477','290545','235453'}
        original=[p for p in target['postmatch_players']['home'] if p['starter'] is True]
        if {p['id'] for p in original}!=expected:raise ValueError('lineup_source_changed_requires_review')
        # Explicit manual reconciliation with the club's official eleven, no guessed surname lookup.
        identity=[(r['id'],p['id'],p['name']) for r in rows for side,ps in r['postmatch_players'].items()
                  if r[side+'_name']=='Monza' for p in ps if p['id']=='213207']
        if not identity or {p[2] for p in identity}!={'Georgios Kyriakopoulos'}:raise ValueError('player_mapping_unconfirmed')
        target['postmatch_lineups']['home']=[{'id':p['id'],'name':p['name']} for p in original]+[{'id':'213207','name':'Georgios Kyriakopoulos'}]
        target['lineup_correction']={'source_receipt':evidence(root,'monza-lineups'),'added_player_id':'213207',
            'original_starters':sorted(expected),'identity_evidence_matches':sorted({i[0] for i in identity})[:3],
            'availability':'collected_postmatch; historical pre-match publication time not verified'}
    if slug=='eng.1':
        target=next(r for r in rows if r['id']=='espn:638153')
        source=next(r for r in read(root/'fotmob/eng.1/research.json')['matches'] if r['id']=='fotmob:3901266')
        if (target['home_name'],target['away_name'],target['home_goals'],target['away_goals'],target['kickoff'])!=('Crystal Palace','West Ham United',4,3,'2023-04-29T11:30:00+00:00'):
            raise ValueError('kickoff_correction_identity_conflict')
        if (source['kickoff'],source['home_name'],source['away_name'],source['home_goals'],source['away_goals'])!=('2023-04-29T11:45:00Z','Crystal Palace','West Ham United',4,3):
            raise ValueError('kickoff_supplement_conflict')
        target['external_postmatch_xg']={'source_match_id':source['id'],'source':'FotMob','xg':source['xg'],
            'source_receipt':source['source_receipt'],'availability':'postmatch; no historical pre-match snapshot',
            'identity_exception':{'policy':'explicit_match_delay_v1','scheduled_kickoff':target['kickoff'],
                'actual_start':source['kickoff'],'delay_minutes':15,'official_receipt':evidence(root,'palace-delay')}}
        data['resolved_xg_conflicts']=data.get('xg_conflicts',[]);data['xg_conflicts']=[]


def deduplicate(records):
    groups=defaultdict(list)
    for r in records:groups[(r['league_code'],str(r['understat_id']))].append(r)
    clean=[];conflicts=[];duplicates=0
    for key,group in groups.items():
        # season_input is the collector's requested season, NOT the fixture's season.
        signatures={json.dumps({k:v for k,v in r.items() if k!='season_input'},sort_keys=True) for r in group}
        if len(signatures)!=1:
            conflicts.append({'source_id':key,'reason':'archive_duplicate_fact_conflict'});continue
        row=deepcopy(group[0]);row['archive_requested_seasons']=sorted({r['season_input'] for r in group})
        clean.append(row);duplicates+=len(group)-1
    return clean,conflicts,duplicates


def associate(slug,canonical,source,receipt):
    code,league=LEAGUES[slug];names=defaultdict(set);mapping={};reverse={}
    for r in canonical:
        for side in ('home','away'):names[r[side+'_name']].add(r[side+'_id'])
    fixtures={}
    for r in canonical:
        key=(r['season'],r['home_id'],r['away_id'],r['kickoff'][:10])
        if key in fixtures:raise ValueError('ambiguous_canonical_fixture')
        fixtures[key]=r
    joined={};errors=[]
    for original in source:
        if original['league_code']!=code or int(original['season'])<2021:continue
        if original['league']!=league:raise ValueError('archive_league_mismatch:'+str(original['league']))
        if original['season']!=str(int(original['season'])):raise ValueError('archive_season_invalid')
        srcid='understat:'+str(original['understat_id']);ids=[]
        for side in ('home','away'):
            name=original[side+'_team'];target=ALIASES[slug].get(name,name);options=names.get(target,set())
            if len(options)!=1:break
            espnid=next(iter(options));uid='understat:'+str(original[side+'_team_id'])
            if uid in mapping and mapping[uid]['espn_id']!=espnid:raise ValueError('source_team_mapping_conflict')
            if espnid in reverse and reverse[espnid]!=uid:raise ValueError('nonbijective_team_mapping')
            mapping[uid]={'source_name':name,'espn_name':target,'espn_id':espnid,
                'policy':'explicit_reviewed_alias' if name in ALIASES[slug] else 'exact_name'}
            reverse[espnid]=uid;ids.append(espnid)
        if len(ids)!=2:
            errors.append({'id':srcid,'reason':'unmapped_team','record':original});continue
        raw_date=datetime.strptime(original['match_date'],'%Y-%m-%d %H:%M:%S')
        target=fixtures.get((original['season'],*ids,raw_date.date().isoformat()))
        if target is None:
            errors.append({'id':srcid,'reason':'fixture_date_or_season_unmatched','record':original});continue
        if target['period']!='regular_time':
            errors.append({'id':srcid,'reason':'non_regulation_target','target':target['id']});continue
        if any(original[s+'_score']!=target[s+'_goals'] for s in ('home','away')):
            errors.append({'id':srcid,'reason':'score_conflict','target':target['id'],'record':original});continue
        xg=[original[s+'_xg'] for s in ('home','away')]
        if not all(type(v) in (int,float) and math.isfinite(v) and v>=0 for v in xg):
            errors.append({'id':srcid,'reason':'invalid_or_missing_xg'});continue
        if target['id'] in joined:raise ValueError('multiple_archive_matches_for_fixture')
        joined[target['id']]={'source_id':srcid,'source_url':original['match_url'],
            'source_kind':'third_party_archive_of_understat','source_receipt':receipt,
            'observed_at':receipt['observed_at'],'upstream_collection_time_verified':False,
            'archive_timestamp':original['match_date'],'archive_timezone':None,
            'identity_policy':'explicit_teams_season_calendar_date_and_fulltime_score_v1',
            'historical_feature_availability':'unknown; retrospective assumption required',
            'approved_as_training_feature':False,'xg':xg,'facts':original}
    return joined,mapping,errors


def fill_missing_basics(root,rows,joined):
    import pyarrow.parquet as pq
    missing={joined[r['id']]['source_id'].split(':')[1]:r for r in rows if r['id'] in joined and len(r['postmatch_lineups'])!=2}
    if not missing:return
    path=root/'continuation/understat_roster.parquet';receipt=read(path.with_suffix('.parquet.receipt.json'))
    sha=digest(path.read_bytes())
    if receipt['http_status']!='200' or receipt['sha256']!=sha or receipt['asset']['digest']!='sha256:'+sha:
        raise ValueError('roster_archive_integrity_failure')
    records=pq.read_table(path,filters=[('understat_id','in',list(missing))]).to_pylist()
    for uid,row in missing.items():
        fact=joined[row['id']]['facts'];source=[p for p in records if str(p['understat_id'])==uid]
        lineups={};goals={};own={}
        for side,letter in (('home','h'),('away','a')):
            group=[p for p in source if p['h_a']==letter]
            if not group or any(str(p['team_id'])!=str(fact[side+'_team_id']) or p['league_code']!=fact['league_code'] for p in group):
                raise ValueError('roster_team_or_competition_conflict')
            if len({p['player_id'] for p in group})!=len(group):raise ValueError('duplicate_roster_player')
            starters=[p for p in group if p['position']!='Sub']
            if len(starters)!=11 or sum(p['position']=='GK' for p in starters)!=1:raise ValueError('incomplete_archive_starting_eleven')
            goals[side]=sum(int(p['goals']) for p in group);own[side]=sum(int(p['own_goals']) for p in group)
            lineups[side]=[{'id':'understat:'+str(p['player_id']),'name':p['player'],'position':p['position'],'minutes':int(p['time'])} for p in starters]
        if goals['home']+own['away']!=row['home_goals'] or goals['away']+own['home']!=row['away_goals']:
            raise ValueError('roster_goal_total_conflict')
        stats={side:{'totalShots':fact[side+'_shots'],'shotsOnTarget':fact[side+'_shots_on_target']} for side in ('home','away')}
        if any(type(v) is not int or v<0 for s in stats.values() for v in s.values()):raise ValueError('archive_shot_stat_invalid')
        row['supplemental_basics']={'source':'understat_archive','source_id':'understat:'+uid,
            'lineups':lineups,'statistics':stats,'roster_receipt':receipt,
            'statistics_receipt':joined[row['id']]['source_receipt'],
            'availability':'postmatch acquired facts; no historical pre-match lineup timestamp',
            'approved_as_training_feature':False}


def supplement(root):
    import pyarrow.parquet as pq
    sourcepath=root/'continuation/understat_metadata.parquet'
    receipt=read(sourcepath.with_suffix('.parquet.receipt.json'));body=sourcepath.read_bytes()
    if receipt['http_status']!='200' or digest(body)!=receipt['sha256'] or receipt['asset']['digest']!='sha256:'+digest(body):
        raise ValueError('archive_integrity_failure')
    records=[r for r in pq.read_table(sourcepath).to_pylist() if r['league_code'] in {x[0] for x in LEAGUES.values()} and int(r['season'])>=2021]
    records,conflicts,duplicates=deduplicate(records)
    report={'duplicate_records_removed':duplicates,'duplicate_conflicts':conflicts,'competitions':{},
        'source_receipt':receipt,'not_trained':True,'dataset_scope':'local_research'}
    for slug in SPECS:
        # Always derive from the first audit's fixed snapshot, never recursively from our own latest pointer.
        pointer=read(root/'audit.json')['snapshots'][slug];parent=root/slug/pointer['file'];payload=parent.read_bytes()
        if digest(payload)!=pointer['sha256']:raise ValueError('parent_snapshot_integrity_failure')
        data=json.loads(payload);official_corrections(root,slug,data);rows=data['matches'];joined,mappings,errors=associate(slug,rows,records,receipt)
        fill_missing_basics(root,rows,joined)
        for r in rows:
            if r['id'] in joined:r.setdefault('supplemental_postmatch_facts',{})['understat_archive']=joined[r['id']]
        data['understat_mapping']=mappings;data['understat_quarantine']=errors
        data['manifest'].update(parent_research_sha256=pointer['sha256'],supplement_policy='archive_identity_v1',
            source_archive_receipt=receipt,source_code_sha256=digest(Path(__file__).read_bytes()))
        for c in data['coverage']:
            c['eligible']=sum(r['season']==c['season'] and r['training_eligible'] for r in rows)
            c['lineups_both']=sum(r['season']==c['season'] and len(r['postmatch_lineups'])==2 for r in rows)
            c['external_xg_both']=sum(r['season']==c['season'] and r.get('external_postmatch_xg',{}).get('xg') is not None for r in rows)
            c['understat_archive_xg_both']=sum(r['season']==c['season'] and r['id'] in joined for r in rows)
            c['any_source_xg_both']=sum(r['season']==c['season'] and (r['id'] in joined or r.get('external_postmatch_xg',{}).get('xg') is not None) for r in rows)
            c['any_source_lineups_both']=sum(r['season']==c['season'] and (len(r['postmatch_lineups'])==2 or len(r.get('supplemental_basics',{}).get('lineups',{}))==2) for r in rows)
            c['any_source_shot_statistics_both']=sum(r['season']==c['season'] and (all('totalShots' in r['postmatch_statistics'].get(s,{}) for s in ('home','away')) or bool(r.get('supplemental_basics'))) for r in rows)
        payload=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode();sha=digest(payload)
        target=root/slug/'snapshots'/(sha+'.json')
        if not target.exists():target.write_bytes(payload)
        elif target.read_bytes()!=payload:raise ValueError('snapshot_collision')
        pointer={'file':str(target.relative_to(root/slug)),'sha256':sha,'rows':len(rows)}
        (root/slug/'latest.json').write_text(json.dumps(pointer,indent=2))
        report['competitions'][slug]={'snapshot':pointer,'xg':len(joined),'seasons':{c['season']:c['understat_archive_xg_both'] for c in data['coverage']},
            'quarantine_reasons':dict(Counter(e['reason'] for e in errors)),'quarantine':errors}
        print(json.dumps({k:v for k,v in report['competitions'][slug].items() if k not in ('snapshot','quarantine')},ensure_ascii=False),flush=True)
    out=root/'continuation/archive-audit.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);supplement(p.parse_args().root)
