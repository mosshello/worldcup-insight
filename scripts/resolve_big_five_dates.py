"""Narrow, evidence-reviewed date exceptions. No generic date tolerance or training."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime, timezone
import hashlib
import json
import math
import subprocess
from scripts.supplement_big_five import LEAGUES, deduplicate, read, digest
from worldcup_mvp.console.big_five_history import SPECS

# Explicit fixtures reviewed against original club/league reports. Source dates
# remain intact: these mappings do not claim that the archive time is correct.
REVIEWS = [
 ('esp.1','610409','17503','2022-05-14','2022-05-15 17:30:00','https://www.rcdespanyol.com/es/noticia/11-empate-en-el-ultimo-partido-de-la-temporada-en-el-rcde-stadium/14091', ['14/05/2022','Valencia']),
 ('esp.1','674166','22842','2023-12-11','2023-12-12 15:15:00','https://www.athletic-club.eus/en/teams/athletic-club/2023-24/matches/1_8394/', ['11/12/2023','Granada']),
 ('ger.1','711539','27849','2024-11-29','2024-11-30 14:30:00','https://www.bundesliga.com/en/bundesliga/news/st-pauli-holstein-kiel-match-report-highlights-matchday-12-29894', ['29.11.2024','Harres']),
 ('ita.1','679159','22621','2024-04-25','2024-04-14 16:00:00','https://www.asroma.com/it/notizie/71047/cristante-di-testa-roma-da-tre-punti-la-gallery-di-udinese-roma-1-2', ['Cristante','2024']),
 ('ita.1','679218','22676','2024-05-23','2024-05-24 16:00:00','https://cagliaricalcio.com/en/news/the-second-ranieri-era-comes-to-an-end/', ['23/05/2024','Fiorentina']),
 ('ita.1','712474','27724','2025-05-17','2025-05-18 13:00:00','https://www.atalanta.it/news/successo-al-ferraris-battuto-3-2-il-genoa', ['2025','Retegui']),
 ('fra.1','609395','17955','2022-02-01','2022-02-02 21:00:00','https://www.ol.fr/en/news/ol-om-un-rendez-vous-charniere-21h-sur-prime-video-2', ['2022','Marseille']),
 ('fra.1','639932','19973','2023-04-28','2023-04-30 19:00:00','https://www.lequipe.fr/Football/match-direct/ligue-1/2022-2023/strasbourg-lyon-live/538728', ['28 avril 2023','Caqueret']),
 ('fra.1','677197','23377','2023-08-11','2023-08-12 18:00:00','https://www.ogcnice.com/fr/resume/125136/un-gym-vaillant-rejoint-sur-le-fil-1-1.html', ['2023','Laborde']),
 ('fra.1','677185','23378','2023-08-13','2023-08-12 18:00:00','https://www.rclens.fr/fr/news/compte-rendu-brest-rclens-1ere-journee-ligue1-uber-eats-20230813', ['2023','Sotoca']),
]
SEGMENTS = {
 'espn:674166': {'original_play_date':'2023-12-10','completion_date':'2023-12-11','resumed_minute':17},
 'espn:679159': {'original_play_date':'2024-04-14','completion_date':'2024-04-25','resumed_minute':72},
}

def fetch_evidence(work, review, cached_only=False):
    slug,eid,uid,day,raw_date,url,markers=review
    path=work/(eid+'.html'); meta=path.with_suffix('.receipt.json')
    if not path.exists() or not meta.exists():
        if cached_only: raise ValueError('evidence_not_cached')
        response=subprocess.run(['curl.exe','-sS','-L','--max-time','30','-o',str(path),'-w','%{http_code}',url], capture_output=True, timeout=35)
        receipt={'url':url,'file':path.name,'http_status':response.stdout.decode().strip(),
                 'transport_exit':response.returncode,'observed_at':datetime.now(timezone.utc).isoformat(),
                 'sha256':digest(path.read_bytes()) if path.exists() else None}
        meta.write_text(json.dumps(receipt,indent=2))
    receipt=read(meta)
    if receipt['http_status']!='200' or receipt['transport_exit']!=0: raise ValueError('evidence_unavailable')
    body=path.read_bytes()
    if receipt['url']!=url or digest(body)!=receipt['sha256']: raise ValueError('evidence_integrity_failure')
    if not all(m.casefold() in body.decode('utf8').casefold() for m in markers): raise ValueError('evidence_content_unconfirmed')
    return receipt

def reconcile(review, data, source, roster, receipt, evidence, roster_receipt):
    slug,eid,uid,day,raw_date,*_=review
    candidates=[r for r in data['matches'] if r['id']=='espn:'+eid]
    if len(candidates)!=1: raise ValueError('target_identity_missing')
    target=candidates[0]
    if (source['league_code'],source['league'],str(source['understat_id']),source['match_date']) != (*LEAGUES[slug],uid,raw_date):
        raise ValueError('reviewed_source_changed')
    if target['competition_id']!=SPECS[slug][0] or target['kickoff'][:10]!=day or target['season']!=source['season']:
        raise ValueError('reviewed_target_changed')
    if target.get('training_eligible') is not True or target['period']!='regular_time': raise ValueError('ineligible_target')
    if target.get('supplemental_postmatch_facts') or target.get('external_postmatch_xg'): raise ValueError('target_already_has_xg')
    for side in ('home','away'):
        mapping=data['understat_mapping'].get('understat:'+str(source[side+'_team_id']))
        if not mapping or mapping['espn_id']!=target[side+'_id']: raise ValueError('team_identity_conflict')
        if source[side+'_score']!=target[side+'_goals']: raise ValueError('score_conflict')
    fixtures=[r for r in data['matches'] if (r['season'],r['home_id'],r['away_id'])==(target['season'],target['home_id'],target['away_id'])]
    if len(fixtures)!=1: raise ValueError('season_fixture_not_unique')
    players=[p for p in roster if str(p['understat_id'])==uid]
    if not players or len({p['player_id'] for p in players})!=len(players): raise ValueError('roster_missing_or_duplicate')
    goals={}; own={}; evidence_players={}
    for side,letter in [('home','h'),('away','a')]:
        group=[p for p in players if p['h_a']==letter]
        if not group or any(p['league_code']!=source['league_code'] or str(p['team_id'])!=str(source[side+'_team_id']) for p in group):
            raise ValueError('roster_identity_conflict')
        goals[side]=sum(int(p['goals']) for p in group); own[side]=sum(int(p['own_goals']) for p in group)
        evidence_players[side]=[{'source_player_id':p['player_id'],'name':p['player'],'goals':p['goals'],'own_goals':p['own_goals']} for p in group if int(p['goals']) or int(p['own_goals'])]
        if sum(int(p['shots']) for p in group)!=source[side+'_shots']: raise ValueError('roster_shots_conflict')
        if abs(sum(float(p['x_g']) for p in group)-source[side+'_xg'])>0.0002: raise ValueError('roster_xg_conflict')
    if [goals['home']+own['away'],goals['away']+own['home']]!=[target['home_goals'],target['away_goals']]: raise ValueError('roster_result_conflict')
    xg=[source[s+'_xg'] for s in ('home','away')]
    if not all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=30 for v in xg): raise ValueError('invalid_xg')
    exception={'policy':'explicit_reviewed_fixture_date_exception_v1','canonical_date':day,'archive_date':raw_date,
        'source_date_conflict_preserved':True,'evidence_receipt':evidence,'roster_receipt':roster_receipt,
        'scorers':evidence_players,'unique_competition_season_home_away':True}
    if target['id'] in SEGMENTS: exception['played_segments']=SEGMENTS[target['id']]
    return target,{'source_id':'understat:'+uid,'source_url':source['match_url'],'source_kind':'third_party_archive_of_understat',
        'source_receipt':receipt,'archive_timestamp':raw_date,'archive_timezone':None,'identity_exception':exception,
        'identity_policy':exception['policy'],'upstream_collection_time_verified':False,'approved_as_training_feature':False,
        'historical_feature_availability':'unknown; postmatch collection only','xg':xg,'facts':source}

def run(root,cached_only=False):
    import pyarrow.parquet as pq
    work=root/'final-gaps';work.mkdir(exist_ok=True)
    parentpath=work/'parents.json'
    if not parentpath.exists(): parentpath.write_text(json.dumps({s:read(root/s/'latest.json') for s in SPECS}))
    parents=read(parentpath);data={}
    for slug,p in parents.items():
        body=(root/slug/p['file']).read_bytes()
        if digest(body)!=p['sha256']:raise ValueError('parent_integrity_failure')
        data[slug]=json.loads(body)
    loaded=[]
    for filename in ('understat_metadata.parquet','understat_roster.parquet'):
        path=root/'continuation'/filename;receipt=read(path.with_suffix('.parquet.receipt.json'));sha=digest(path.read_bytes())
        if receipt['http_status']!='200' or receipt['sha256']!=sha or receipt['asset']['digest']!='sha256:'+sha:raise ValueError('archive_integrity_failure')
        loaded.append((pq.read_table(path).to_pylist(),receipt))
    (records,receipt),(roster,roster_receipt)=loaded
    records,conflicts,_=deduplicate(records)
    if conflicts:raise ValueError('archive_conflicting_duplicate')
    report={'resolved':[],'errors':[],'competitions':{},'not_trained':True}
    for review in REVIEWS:
        slug,eid,uid,*_=review
        try:
            evidence=fetch_evidence(work,review,cached_only)
            sources=[s for s in records if str(s['understat_id'])==uid and s['league_code']==LEAGUES[slug][0]]
            if len(sources)!=1:raise ValueError('source_not_unique')
            target,fact=reconcile(review,data[slug],sources[0],roster,receipt,evidence,roster_receipt)
            target.setdefault('supplemental_postmatch_facts',{})['understat_archive']=fact
            report['resolved'].append({'slug':slug,'id':target['id'],'source_id':fact['source_id']})
        except (ValueError,KeyError) as exc:report['errors'].append({'id':eid,'reason':str(exc)})
    for slug,d in data.items():
        added=sum(r['slug']==slug for r in report['resolved'])
        for c in d['coverage']:
            rows=[r for r in d['matches'] if r['season']==c['season'] and r['training_eligible']]
            c['any_source_xg_both']=sum(bool(r.get('supplemental_postmatch_facts')) or bool(r.get('external_postmatch_xg')) for r in rows)
            c['understat_archive_xg_both']=sum('understat_archive' in r.get('supplemental_postmatch_facts',{}) for r in rows)
        if added:
            d['manifest']['date_exception_review']={'parent_sha256':parents[slug]['sha256'],'resolved':added,'code_sha256':digest(Path(__file__).read_bytes())}
            body=json.dumps(d,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode();sha=digest(body)
            path=root/slug/'snapshots'/(sha+'.json')
            if path.exists() and path.read_bytes()!=body:raise ValueError('snapshot_collision')
            if not path.exists():path.write_bytes(body)
            (root/slug/'latest.json').write_text(json.dumps({'file':str(path.relative_to(root/slug)),'sha256':sha,'rows':len(d['matches'])}))
        report['competitions'][slug]={'added':added,'xg':sum(c['any_source_xg_both'] for c in d['coverage'])}
    (work/'date-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=True),flush=True)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--cached-only',action='store_true');a=p.parse_args();run(a.root,a.cached_only)
