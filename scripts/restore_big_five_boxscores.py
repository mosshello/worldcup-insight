"""Restore quarantined team statistics without changing the original source."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import file_digest
from math import isfinite
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pyarrow.parquet as pq
from scripts.supplement_big_five import read, digest
from scripts.reconcile_big_five import field_coverage
from scripts.complete_big_five_shot_sums import asset_receipt
from scripts.complete_big_five_events import visible
from worldcup_mvp.console.training import atomic_json
from worldcup_mvp.console.big_five_history import empty_boxscore_sides

ROOT = Path('data/research/big-five/2026-11-05')
WORK = ROOT.parent / '2026-11-06/completion'
MAPS = Path('config/big_five_boxscore_team_ids.json')
HF = 'e192852b3bb098d33d832165543b3363ae810061'
CODES = {'eng.1':(1,'EPL','ENG',39), 'esp.1':(5,'La_Liga','ESP',140),
         'ger.1':(11,'Bundesliga','GER',78), 'ita.1':(7,'Serie_A','ITA',135), 'fra.1':(9,'Ligue_1','FRA',61)}
FIELDS = {'possession':'possessionPct','corners':'wonCorners','fouls':'foulsCommitted',
          'shots_total':'totalShots','shots_on_goal':'shotsOnTarget',
          'yellow_cards':'yellowCards','red_cards':'redCards'}
SIDES = ('home','away')
EMPOLI_URL='https://www.gazzetta.it/Calcio/Serie-A/Empoli/03-04-2023/empoli-lecce-principio-d-incendio-stadio-castellani-inizio-posticipato-460969358074_amp.shtml'


def checked_file(folder, filename, url, receipt_name=None):
    path=folder/filename; rec=read(folder/(receipt_name or filename+'.receipt.json'))
    with path.open('rb') as handle:
        sha=file_digest(handle,'sha256').hexdigest()
    if rec['http_status']!='200' or rec['transport_exit']!=0 or rec['sha256']!=sha or rec['url']!=url:
        raise ValueError('unverified_response:'+filename)
    return rec


def provenance():
    url='https://huggingface.co/api/datasets/svgwvgs/soccer-dataset/tree/'+HF+'?recursive=true&expand=false'
    tree_receipt=checked_file(WORK,'hf-tree.json',url)
    entries={e['path']:e for e in read(WORK/'hf-tree.json')}
    evidence={}
    for n in ('leagues','teams','fixtures','match_stats'):
        filename=n+'.parquet'; local='hf-'+filename
        url='https://huggingface.co/datasets/svgwvgs/soccer-dataset/resolve/'+HF+'/'+filename
        rec=checked_file(WORK,local,url)
        if rec['sha256']!=entries[filename]['lfs']['oid'] or (WORK/local).stat().st_size!=entries[filename]['size']:
            raise ValueError('different_hf_object:'+n)
        evidence['hf-'+n]={**rec,'commit':HF,'tree_sha256':tree_receipt['sha256']}
    fb=read(WORK/'fbref-release.json')
    fr=checked_file(WORK,'fbref-release.json','https://api.github.com/repos/peteowen1/pannadata/releases/tags/fbref-latest')
    for n in ('metadata','summary'):
        filename=n+'.parquet';a=next(a for a in fb['assets'] if a['name']==filename)
        rec=checked_file(WORK,'fbref-'+filename,a['browser_download_url'])
        if fb['tag_name']!='fbref-latest' or 'sha256:'+rec['sha256']!=a['digest'] or (WORK/('fbref-'+filename)).stat().st_size!=a['size']:
            raise ValueError('different_fbref_object:'+n)
        evidence['fbref-'+n]={**rec,'release_sha256':fr['sha256'],'asset_id':a['id']}
    rel=read(WORK.parent/'opta-release.json')
    rr=checked_file(WORK.parent,'opta-release.json','https://api.github.com/repos/peteowen1/pannadata/releases/tags/opta-latest','opta-release.receipt.json')
    for n in ('fixtures','match_stats'):
        evidence['opta-'+n]=asset_receipt(WORK.parent,'opta_'+n+'.parquet',rel,rr)
    return evidence


def team_box(stats, row):
    # The archive selects a whole statistics row for each surviving fixture.
    # Require API-specific data on that row, never use a football-data-only row.
    if (stats.get('home_goals_ht')!=row['home_half_goals'] or stats.get('away_goals_ht')!=row['away_half_goals']
            or any(type(stats.get(s+'_'+k)) is not int for s in SIDES for k in
                   ('possession','shots_inside_box','shots_outside_box','shots_total','pass_accuracy'))
            or any(not 0 < stats[s+'_possession'] < 100 or not 0 < stats[s+'_pass_accuracy'] <= 100
                   for s in SIDES)
            or sum(stats[s+'_possession'] for s in SIDES)!=100
            or not any(stats[s+'_shots_inside_box']+stats[s+'_shots_outside_box']>0 for s in SIDES)):
        raise ValueError('incomplete_team_statistics_or_halftime_conflict')
    values={}
    for side in SIDES:
        values[side]={}
        for src,key in FIELDS.items():
            val=stats[side+'_'+src]
            if type(val) is not int or val<0:
                raise ValueError('missing_or_invalid_measurement:'+key)
            values[side][key]=val
        if values[side]['shotsOnTarget']>values[side]['totalShots']:
            raise ValueError('shots_on_target_exceed_attempts')
    return values


def aggregate_passes(players, provider, fixture):
    result={}; details={}
    for side in SIDES:
        if provider=='opta':
            entries=[p for p in players if p['team_position']==side]
            if any(p['team_id']!=fixture[side+'_team_id'] or p['match_id']!=fixture['match_id']
                   or p['competition']!=fixture['competition'] or p['season']!=fixture['season']
                   or p['home_score']!=int(fixture['home_score']) or p['away_score']!=int(fixture['away_score'])
                   for p in entries):
                raise ValueError('opta_player_identity')
            id_key,pass_key,minute_key='player_id','passes','total_minutes'
        else:
            entries=[p for p in players if p['is_home'] is (side=='home')]
            if any(p['team']!=fixture[side+'_team'] or p['fbref_id']!=fixture['fbref_id']
                   or p['match_url']!=fixture['match_url'] or p['season']!=fixture['season']
                   or p['league']!=fixture['league'] for p in entries):
                raise ValueError('fbref_player_identity')
            id_key,pass_key,minute_key='player','att','min'
        ids=[p[id_key] for p in entries]
        if not 11 <= len(ids) <= 18 or len(set(ids))!=len(ids) or not all(ids):
            raise ValueError('incomplete_or_duplicate_roster')
        try:
            pairs=[(float(p[pass_key]),float(p[minute_key])) for p in entries]
        except (TypeError,ValueError):
            raise ValueError('unknown_pass_count_or_minutes') from None
        if any(not isfinite(v) or v<0 or v!=int(v) for pair in pairs for v in pair):
            raise ValueError('invalid_pass_count_or_minutes')
        total=sum(int(p) for p,_ in pairs); minutes=sum(m for _,m in pairs)
        if not 750<=minutes<=1250 or not 40<=total<=1600:
            raise ValueError('incomplete_team_minutes_or_pass_count')
        result[side]={'totalPasses':total}
        details[side]={'player_passes':dict(zip(ids,[int(p) for p,_ in pairs])),'player_minutes_sum':minutes}
    return result,details


def fixture_index(provider, slug, source, mapping, team_names):
    hf,opta,fb,_=CODES[slug];idx=defaultdict(list)
    for f in source:
        if provider=='hf':
            if f['league_id']!=hf or not f['api_football_id'] or f['status_norm']!='FT':continue
            time=f['date_utc'].isoformat(timespec='seconds')
            score=(f['goals_home'],f['goals_away']); season=None
        elif provider=='opta':
            if f['competition']!=opta or f['match_status']!='Played':continue
            time=f['match_date'][:10];score=(f['home_score'],f['away_score']);season=f['season'][:4]
        else:
            if f['league']!=fb or not f['kickoff_epoch']:continue
            time=datetime.fromtimestamp(f['kickoff_epoch'],timezone.utc).isoformat(timespec='seconds')[:19]
            score=(f['home_score'],f['away_score']);season=f['season'][:4]
        if None in score:continue
        teams=[]
        for s in SIDES:
            sid=f[s+'_team_id'];name=team_names.get(sid) if provider=='hf' else f[s+'_team']
            link=mapping.get(str(sid))
            if link and name==link[1]:teams.append(link[0])
        if len(teams)==2 and teams[0]!=teams[1]:
            idx[(*teams,*map(int,score),time)].append((season,f))
    return idx


def fixture_for(row, index, provider):
    when=row['kickoff'][:10 if provider=='opta' else 19]
    prefix=(row['home_id'],row['away_id'],row['home_goals'],row['away_goals'])
    matches=[f for season,f in index.get((*prefix,when),[]) if season in (None,row['season'])]
    if not matches and provider=='hf' and row['id']=='espn:679159':
        # The independently reconciled suspended match has two recorded times.
        fact=row['supplemental_postmatch_facts']['understat_archive']['identity_exception']
        seg=fact['played_segments']
        if (row['competition_id']=='ita.serie_a' and seg['original_play_date']=='2024-04-14'
                and seg['completion_date']=='2024-04-25' and row['kickoff']=='2024-04-25T18:00:00+00:00'):
            matches=[f for _,f in index.get((*prefix,'2024-04-25T16:00:00'),[]) if f['id']==14455]
    if not matches and provider=='hf' and row['id']=='espn:644888' and row['kickoff']=='2023-04-03T17:30:00+00:00':
        checked_file(WORK,'empoli-notice.html',EMPOLI_URL)
        report=visible((WORK/'empoli-notice.html').read_bytes())
        if all(t in report for t in ('Empoli-Lecce','incendio','19.30','3 aprile','La Lega di Serie A ha ufficializzato')):
            matches=[f for _,f in index.get((*prefix,'2023-04-03T16:30:00'),[]) if f['id']==210633 and f['api_football_id']==882051]
    if len(matches)>1:raise ValueError('multiple_provider_matches_for:'+row['id'])
    return matches[0] if matches else None


def run(extended=False):
    receipts=provenance()
    maps=read(MAPS); tids={t['id']:t['name'] for t in pq.read_table(WORK/'hf-teams.parquet').to_pylist()}
    leagues={l['id']:l for l in pq.read_table(WORK/'hf-leagues.parquet').to_pylist()}
    source={'hf':pq.read_table(WORK/'hf-fixtures.parquet',filters=[('league_id','in',[a[0] for a in CODES.values()])]).to_pylist(),
        'opta':pq.read_table(WORK.parent/'opta_fixtures.parquet',filters=[('competition','in',[a[1] for a in CODES.values()])]).to_pylist(),
        'fbref':pq.read_table(WORK/'fbref-metadata.parquet',filters=[('league','in',[a[2] for a in CODES.values()])]).to_pylist()}
    parentpath=WORK/('single-side-parents.json' if extended else 'boxscore-parents.json')
    if not parentpath.exists():atomic_json(parentpath,{s:read(ROOT/s/'latest.json') for s in CODES})
    parents=read(parentpath);snapshots={};targets={};fixtures=defaultdict(dict)
    for slug,p in parents.items():
        raw=(ROOT/slug/p['file']).read_bytes()
        if digest(raw)!=p['sha256']:raise ValueError('parent_hash_conflict')
        snapshot=snapshots[slug]=json.loads(raw)
        if leagues[CODES[slug][0]]['api_football_id']!=CODES[slug][3]:raise ValueError('provider_league_conflict')
        if extended:
            for r in snapshot['matches']:
                sides=empty_boxscore_sides(r.get('postmatch_statistics',{}))
                if r['training_eligible'] and sides and 'espn_boxscore' not in r.get('supplemental_quality',{}):
                    r.setdefault('supplemental_quality',{})['espn_boxscore']={
                        'usable':False,'review_required':True,'scope':'postmatch_statistics',
                        'reason':'empty_side_in_team_statistics','empty_sides':sides}
        targets[slug]=[r for r in snapshot['matches'] if r['training_eligible'] and
            r.get('supplemental_quality',{}).get('espn_boxscore',{}).get('usable') is False
            and 'archived_team_box' not in r.get('additional_postmatch_statistics',{})]
        for provider,rows in source.items():
            mapping=maps[slug+'-'+provider]
            if len({v[0] for v in mapping.values()})!=len(mapping):raise ValueError('nonbijective_mapping')
            idx=fixture_index(provider,slug,rows,mapping,tids)
            for r in targets[slug]:
                if r['period']!='regular_time' or r['competition_id']!=snapshot['manifest']['competition_id']:
                    raise ValueError('competition_or_period_conflict')
                f=fixture_for(r,idx,provider)
                if f:fixtures[provider][r['id']]=f
    def selected_table(path,key,provider,columns=None):
        ids=[f[key if provider!='hf' else 'id'] for f in fixtures[provider].values()]
        if len(set(ids))!=len(ids):raise ValueError('duplicate_provider_match')
        result=defaultdict(list)
        for record in pq.read_table(path,columns=columns,filters=[(key,'in',ids)]).to_pylist():
            result[record[key]].append(record)
        return result
    hf=selected_table(WORK/'hf-match_stats.parquet','fixture_id','hf')
    op=selected_table(WORK.parent/'opta_match_stats.parquet','match_id','opta',
        ['match_id','team_id','team_position','player_id','passes','total_minutes','home_score','away_score','competition','season'])
    fb=selected_table(WORK/'fbref-summary.parquet','fbref_id','fbref',['fbref_id','player','team','att','min','is_home','season','league','match_url'])
    audit={'competitions':{},'excluded_pass_ledgers':[],'pass_disagreements':[],'restored':{},'source_receipts':receipts}
    for slug,rows in targets.items():
        for r in rows:
            eid=r['id'];f=fixtures['hf'].get(eid)
            if f is None or len(hf[f['id']])!=1:raise ValueError('team_box_missing:'+eid)
            stats=hf[f['id']][0];values=team_box(stats,r)
            if eid=='espn:679159' and [stats[s+'_shots_total'] for s in SIDES]!=r['postmatch_shot_evidence']['event_count']:
                raise ValueError('resumed_match_shots_do_not_cover_full_game')
            sources=r.setdefault('additional_postmatch_statistics',{})
            sources['archived_team_box']={'statistics':values,'archive_fixture_id':f['id'],'api_football_id':f['api_football_id'],
                'source_receipt':receipts['hf-match_stats'],'fixture_receipt':receipts['hf-fixtures'],
                'source_kind':'published_compilation_whole_statistics_row_with_api_specific_fields',
                'fixture_has_merged_duplicate':f['merged_football_data'],'archive_kickoff_utc':f['date_utc'].isoformat(),
                'canonical_kickoff':r['kickoff'],'statistics_published_at':None,'approved_as_training_feature':False}
            if eid=='espn:644888':
                sources['archived_team_box']['time_exception_receipt']=read(WORK/'empoli-notice.html.receipt.json')
            pass_totals={}
            for provider,key,group,rec in [('opta','match_id',op,'opta-match_stats'),('fbref','fbref_id',fb,'fbref-summary')]:
                f=fixtures[provider].get(eid)
                if f is None:continue
                try:vals,detail=aggregate_passes(group[f[key]],provider,f)
                except ValueError as exc:
                    audit['excluded_pass_ledgers'].append({'id':eid,'provider':provider,'reason':str(exc)});continue
                pass_totals[provider]=[vals[s]['totalPasses'] for s in SIDES]
                sources[provider+'_player_pass_sums']={'statistics':vals,'aggregation':'sum_of_explicit_player_pass_attempts',
                    'source_id':f[key],'source_receipt':receipts[rec], 'players':detail,'approved_as_training_feature':False}
            if not pass_totals:raise ValueError('no_complete_pass_ledger:'+eid)
            if len(pass_totals)==2 and pass_totals['fbref']!=pass_totals['opta']:
                audit['pass_disagreements'].append({'id':eid,**pass_totals})
            audit['restored'][eid]={'team_stats':sources['archived_team_box']['archive_fixture_id'],'pass_totals':pass_totals}
    usable={slug:{'matches':[{**r,'postmatch_statistics':{}} if r.get('supplemental_quality',{}).get('espn_boxscore',{}).get('usable') is False
              else r for r in d['matches']]} for slug,d in snapshots.items()}
    audit['competitions'],missing=field_coverage(usable)
    if missing:raise ValueError('existing_xg_missing')
    outputs={}
    for slug,d in snapshots.items():
        d['manifest']['event_evidence_update']['usable_field_coverage']=audit['competitions'][slug]
        d['manifest']['event_evidence_update']['quarantined_espn_boxscores']=sum(
            r.get('supplemental_quality',{}).get('espn_boxscore',{}).get('usable') is False for r in d['matches'])
        key='single_side_boxscore_restoration' if extended else 'boxscore_restoration'
        d['manifest']['event_evidence_update'][key]={'parent_sha256':parents[slug]['sha256'],
            'restored':len(targets[slug]),'receipts':receipts,'team_mapping_sha256':digest(MAPS.read_bytes()),
            'code_sha256':digest(Path(__file__).read_bytes()),'usage':'retrospective_local_research'}
        for c in d['coverage']:
            c['restored_team_box_count']=sum(r['season']==c['season'] and 'archived_team_box' in
                r.get('additional_postmatch_statistics',{}) for r in d['matches'])
        body=json.dumps(d,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        sha=digest(body);dest=ROOT/slug/'snapshots'/(sha+'.json')
        if dest.exists() and digest(dest.read_bytes())!=sha:raise ValueError('snapshot_collision')
        if not dest.exists():dest.write_bytes(body)
        outputs[slug]={'file':str(dest.relative_to(ROOT/slug)),'sha256':sha,'rows':len(d['matches'])}
    for slug,p in outputs.items():
        if read(ROOT/slug/'latest.json') not in (parents[slug],p):raise ValueError('newer_snapshot_present')
    atomic_json(WORK/('single-side-restoration.json' if extended else 'restoration.json'),audit)
    for slug,p in outputs.items():atomic_json(ROOT/slug/'latest.json',p)
    print('restored',len(audit['restored']),'nonmatching pass totals',len(audit['pass_disagreements']),'excluded',audit['excluded_pass_ledgers'])


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--single-side',action='store_true')
    run(parser.parse_args().single_side)
