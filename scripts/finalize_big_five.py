"""Freeze audited research snapshots without touching production datasets.

Replays saved source receipts offline. Explicit corrections are additive and
retain conflicting source evidence. No fuzzy identity match or zero imputation.
"""
import argparse
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console.big_five_history import SPECS, BASE, coverage, date, enrich, fetch_url, teams

SPANISH_REPAIRS = [
    ('643979','6832','244','UD Almería','Real Betis Balompié',[2,3],[1,2],
     'https://www.realbetisbalompie.es/noticias/primer-equipo/cronica-el-real-betis-vence-en-almeria-2-3-28705'),
    ('643973','243','84','Sevilla FC','RCD Mallorca',[2,0],[2,0],
     'https://www.rcdmallorca.es/partidos/temporada-2022-2023-laliga-santander-21-sevilla-fc-vs-rcd-mallorca'),
    ('643974','94','93','Valencia CF','Athletic Club',[1,2],[1,1],
     'https://www.valenciacf.com/es-cronica-derrota-del-valencia-cf-ante-el-athletic-club-1-2-2023-02-11'),
    ('643976','88','89','RCD Espanyol de Barcelona','Real Sociedad de Fútbol',[2,3],[0,1],
     'https://www.realsociedad.eus/es/noticias/detalle/mas-y-mas'),
    ('643971','86','3751','Real Madrid CF','Elche CF',[4,0],[3,0],
     'https://www.realmadrid.com/es-ES/noticias/futbol/primer-equipo/cronicas/4-0-el-madrid-vence-con-doblete-de-benzema'),
]


def read_json(path):
    return json.loads(path.read_text(encoding='utf8'))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')


def verified_body(folder, receipt):
    body = gzip.decompress((folder / receipt['file']).read_bytes())
    if hashlib.sha256(body).hexdigest() != receipt['sha256']:
        raise ValueError('raw_receipt_integrity_failure')
    return json.loads(body)


def supplement_receipt(evidence, key):
    receipt=read_json(evidence/'receipts.json')[key]
    if receipt['http_status']!='200' or hashlib.sha256((evidence/receipt['file']).read_bytes()).hexdigest()!=receipt['sha256']:
        raise ValueError('supplement_evidence_invalid')
    return receipt


def spanish_repairs(root, evidence):
    wrapper = read_json(evidence / 'openfootball-es-2022.json')
    source = json.loads(base64.b64decode(wrapper['content']))
    if source['name'] != 'Primera División de España 2022/23':
        raise ValueError('openfootball_season_mismatch')
    provenance = read_json(evidence / 'receipts.json')['openfootball-es-2022']
    if hashlib.sha256((evidence/'openfootball-es-2022.json').read_bytes()).hexdigest() != provenance['sha256']:
        raise ValueError('supplement_integrity_failure')
    board, receipt = fetch_url(BASE+'esp.1/scoreboard?dates=2023&limit=1000', root/'esp.1/raw', allow_network=False)
    events = {str(e['id']):e for e in board['events']}
    result = []
    for eid, hid, aid, hn, an, ft, ht, official in SPANISH_REPAIRS:
        event = events[eid]; comp = event['competitions'][0]; sides = teams(comp)
        if (event['uid'] != 's:600~l:740~e:'+eid or event['season']['year'] != 2022
                or str(sides['home']['team']['id']) != hid or str(sides['away']['team']['id']) != aid):
            raise ValueError('supplement_identity_mismatch')
        matches = [m for m in source['matches'] if m['team1']==hn and m['team2']==an]
        if len(matches)!=1: raise ValueError('supplement_pairing_ambiguous')
        match = matches[0]
        # These five fixtures are in February: explicit CET offset, not inferred current timezone.
        source_kickoff = date(match['date']+'T'+match['time']+':00+01:00')
        if (source_kickoff != date(event['date']) or match['score']['ft']!=ft or match['score']['ht']!=ht):
            raise ValueError('supplement_score_or_time_conflict')
        row = {'id':'espn:'+eid,'competition_id':SPECS['esp.1'][0], 'source_competition':'esp.1',
            'season':'2022','stage':'regular-season','kickoff':date(event['date']).isoformat(),
            'period':'regular_time','source_url':provenance['url'],'collected_at':provenance['observed_at'],
            'result_available_at':(date(event['date'])+timedelta(days=7)).isoformat(),
            'availability_basis':'historical_7_day_lag_assumption',
            'neutral_site':None,'training_eligible':False,'summary_verified':False,
            'exclusion_reason':'source_correction_requires_training_policy_review',
            'extra_time':None,'shootout':None,'qualification':None,
            'postmatch_statistics':{},'postmatch_lineups':{},'postmatch_players':{},'postmatch_events':[],
            'feature_availability':'Post-match facts; target-match fields forbidden as pre-match inputs',
            'correction':{'reason':'ESPN incorrectly retains scheduled state',
                'original_status':comp['status'],'identity_receipt':receipt,'result_receipt':provenance,
                'git_blob_sha':wrapper['sha'],'official_fulltime_confirmation':official,
                'half_time_source':'Openfootball only','explicit_identity_mapping':{hn:'espn:'+hid,an:'espn:'+aid}}}
        row['correction']['official_receipt']=supplement_receipt(evidence,'valencia-athletic' if eid=='643974' else 'official-'+eid)
        for i, side in enumerate(('home','away')):
            row.update({side+'_id':'espn:'+str(sides[side]['team']['id']),
                side+'_name':sides[side]['team']['displayName'],side+'_goals':ft[i],side+'_half_goals':ht[i]})
        result.append(row)
    return result


def link_xg(espn_rows, fotmob_rows):
    by_name = {}
    for r in espn_rows:
        for s in ('home','away'): by_name.setdefault(r[s+'_name'],set()).add(r[s+'_id'])
    mapping = {}; errors = []; joined = {}
    fixtures = {(r['home_id'],r['away_id'],r['season'],date(r['kickoff'])):r for r in espn_rows}
    for f in fotmob_rows:
        ids = []
        for s in ('home','away'):
            options = by_name.get(f[s+'_name'],set())
            if len(options)!=1: break
            target = next(iter(options)); prior = mapping.get(f[s+'_id'])
            if prior and prior['espn_id']!=target: raise ValueError('identity_mapping_conflict')
            mapping[f[s+'_id']] = {'espn_id':target,'exact_name':f[s+'_name'],'basis':'exact name plus fixture identity and score checks'}
            ids.append(target)
        if len(ids)!=2:
            errors.append({'id':f['id'],'reason':'unresolved_team_identity'});continue
        r = fixtures.get((*ids,f['season'],date(f['kickoff'])))
        if r is None or any(r[s+'_goals']!=f[s+'_goals'] for s in ('home','away')):
            errors.append({'id':f['id'],'reason':'fixture_or_score_mismatch'});continue
        if r['id'] in joined: raise ValueError('duplicate_xg_match')
        joined[r['id']] = {'source_match_id':f['id'],'source':'FotMob','xg':f['xg'],
            'source_receipt':f['source_receipt'],'availability':'postmatch; no historical pre-match snapshot',
            'source_native_path':'../fotmob/eng.1/research.json'}
    if len({v['espn_id'] for v in mapping.values()})!=len(mapping):raise ValueError('nonbijective_mapping')
    return joined,mapping,errors


def finalize(root):
    evidence = root/'supplements'; all_audits = {}; snapshots = {}
    fdata = read_json(root/'fotmob/eng.1/research.json')
    access_denials = []
    for p in (root/'fotmob').rglob('*.receipt.json'):
        r = read_json(p)
        if r.get('http_status') in ('403','429'):access_denials.append({'file':str(p.relative_to(root)),'observed_at':r['observed_at'],'http_status':r['http_status']})
    for slug in SPECS:
        data = read_json(root/slug/'research.json'); rows = []
        for original in data['matches']:
            row = deepcopy(original)
            if row.get('summary_verified'):
                summary = verified_body(root/slug/'raw',row['summary_receipt'])
                row = enrich(row,summary,row['summary_receipt'])
            if slug=='ita.1' and row['id']=='espn:712475':
                if (row['home_id'],row['away_id'],row['home_goals'],row['away_goals'],row['kickoff']) != ('espn:104','espn:103',3,1,'2025-05-18T18:45:00+00:00'):
                    raise ValueError('roma_correction_identity_conflict')
                row['half_time_correction']={'original':[row['home_half_goals'],row['away_half_goals']],
                    'corrected':[1,1],'source_receipt':supplement_receipt(evidence,'roma-milan')}
                row['home_half_goals']=1;row['away_half_goals']=1
                # Corrected labels require a separate eligibility policy, as with the Spanish supplements.
                row['training_eligible']=False
                row['exclusion_reason']='source_correction_requires_training_policy_review'
            rows.append(row)
        excluded_fixtures=[]
        if slug=='fra.1':
            board,receipt=fetch_url(BASE+'fra.1/scoreboard?dates=2026&limit=1000',root/slug/'raw',allow_network=False)
            event=next(e for e in board['events'] if str(e['id'])=='746714');comp=event['competitions'][0];sides=teams(comp)
            if (event['uid']!='s:600~l:710~e:746714' or event['season']['year']!=2025
                    or comp['status']['type']['name']!='STATUS_ABANDONED'
                    or str(sides['home']['team']['id'])!='165' or str(sides['away']['team']['id'])!='179'):
                raise ValueError('abandoned_fixture_identity_conflict')
            excluded_fixtures=[{'id':'espn:746714','season':'2025','home_id':'espn:165','away_id':'espn:179',
                'kickoff':event['date'],'status':'abandoned','training_eligible':False,'regular_time_outcome':None,
                'administrative_outcome':[0,0],'source_event':event,'source_receipt':receipt,
                'decision_receipt':supplement_receipt(evidence,'nantes-toulouse')}]
        if slug == 'esp.1':
            additions = spanish_repairs(root,evidence)
            if {r['id'] for r in additions}&{r['id'] for r in rows}:raise ValueError('correction_already_present')
            rows.extend(additions)
        xg = {}; mapping = {}; conflicts = []
        if slug == 'eng.1':xg,mapping,conflicts = link_xg(rows,fdata['matches'])
        for r in rows:
            if r['id'] in xg:r['external_postmatch_xg'] = xg[r['id']]
        rows.sort(key=lambda r:(r['kickoff'],r['id']))
        if len({r['id'] for r in rows})!=len(rows):raise ValueError('duplicate_ids')
        audits = coverage(slug,rows)
        for audit in audits:
            season = [r for r in rows if r['season']==audit['season']]
            audit['external_xg_both'] = sum(r.get('external_postmatch_xg',{}).get('xg') is not None for r in season)
            audit['administrative_awards'] = sum(r['period']=='administrative_award' for r in season)
            audit['corrected_labels'] = sum('correction' in r for r in season)
            audit['half_time_corrections'] = sum('half_time_correction' in r for r in season)
            audit['statistics_fields_both'] = {key:sum(all(key in r['postmatch_statistics'].get(s,{}) for s in ('home','away')) for r in season)
                for key in sorted({key for r in season for stats in r['postmatch_statistics'].values() for key in stats})}
            accounted={(r['home_id'],r['away_id']) for r in season}|{(r['home_id'],r['away_id']) for r in excluded_fixtures if r['season']==audit['season']}
            audit['known_excluded_fixtures']=sum(r['season']==audit['season'] for r in excluded_fixtures)
            audit['fixtures_accounted_complete']=len(accounted)==audit['expected_full_season'] and not audit['duplicate_pairings']
            if int(audit['season'])<2026 and not audit['fixtures_accounted_complete']:
                raise ValueError(slug+': incomplete completed season '+audit['season'])
        data.update(matches=rows,coverage=audits,identity_mapping=mapping,xg_conflicts=conflicts,excluded_fixtures=excluded_fixtures)
        data['manifest'].update(finalized_from_sha256=hashlib.sha256((root/slug/'research.json').read_bytes()).hexdigest(),
            dataset_kind='research_snapshot_not_production',commercial_use_permitted=False,
            outcome_policy='Administrative awards retained separately and excluded from regular-time fitting',
            xg_access_denials=len(access_denials) if slug=='eng.1' else None)
        payload=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        digest=hashlib.sha256(payload).hexdigest();target=root/slug/'snapshots'/(digest+'.json')
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and target.read_bytes()!=payload:raise ValueError('snapshot_integrity_failure')
        if not target.exists():target.write_bytes(payload)
        pointer={'file':str(target.relative_to(root/slug)),'sha256':digest,'rows':len(rows)}
        write_json(root/slug/'latest.json',pointer);snapshots[slug]=pointer;all_audits[slug]=audits
        print(json.dumps({'source':slug,'rows':len(rows),'eligible':sum(r['training_eligible'] for r in rows),'xg':len(xg),'conflicts':len(conflicts)}),flush=True)
    write_json(root/'audit.json',{'snapshots':snapshots,'coverage':all_audits,'xg_access_denials':access_denials,
        'training_performed':False,'production_import_performed':False})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args();finalize(a.root)
