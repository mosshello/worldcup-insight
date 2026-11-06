"""Competition-scoped club, player and historical match-personnel inventory."""
from collections import Counter, defaultdict
from datetime import datetime
import gzip
import json
import math
from pathlib import Path
import sys

import pyarrow as pa
import pyarrow.parquet as pq
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.restore_big_five_boxscores import CODES, MAPS, ROOT, WORK, checked_file, fixture_index, fixture_for
from scripts.supplement_big_five import digest, read
from scripts.big_five_shot_archive import verified_parquet
from scripts.supplement_big_five import LEAGUES
from scripts.big_five_club_sources import load_player_sources, mallorca_minutes, player_data
from worldcup_mvp.console.big_five_history import SPECS
from worldcup_mvp.console.training import atomic_json

OUT=WORK.parent/'club-personnel'
SIDES=('home','away')


def response(name, slug, endpoint):
    p=OUT/(name+'.json'); receipt=read(OUT/(name+'.receipt.json'))
    body=p.read_bytes()
    expected='https://site.api.espn.com/apis/site/v2/sports/soccer/'+slug+'/'+endpoint
    if (receipt['url']!=expected or receipt['http_status']!='200' or receipt['transport_exit']!=0
            or digest(body)!=receipt['sha256']):
        raise ValueError('roster_or_directory_receipt_invalid:'+name)
    return json.loads(gzip.decompress(body) if body.startswith(b'\x1f\x8b') else body),receipt


def roster(data, year, tid):
    if (data.get('status')!='success' or str(data['season']['year'])!=year
            or str(data['team']['id'])!=tid.split(':')[1] or not data.get('athletes')):
        raise ValueError('roster_identity_or_season_conflict')
    result={}
    for a in data['athletes']:
        pid=str(a['id'])
        if not pid.isdecimal() or a.get('uid')!='s:600~a:'+pid or not a.get('displayName') or 'espn:'+pid in result:
            raise ValueError('invalid_or_duplicate_roster_player')
        result['espn:'+pid]={'name':a['displayName'],'listed_position':(a.get('position') or {}).get('abbreviation'),
                             'listed':True,'starts':0,'sub_appearances':0,'sheet_matches':0}
    return result


def minutes_for_side(row, side, fixture, group):
    if not group or sum(p['is_starter'] is True for p in group)!=11:
        raise ValueError('missing_eleven')
    if len({p['player_id'] for p in group})!=len(group):
        raise ValueError('duplicate_player')
    for p in group:
        if (not p['player_id'] or p['team_position']!=side or p['team_id']!=fixture[side+'_team_id']
                or p['match_id']!=fixture['match_id'] or p['season']!=fixture['season']
                or p['competition']!=fixture['competition'] or p['match_date']!=fixture['match_date']
                or type(p['is_starter']) is not bool):
            raise ValueError('player_fixture_or_team_conflict')
        value=p['minutes_played']
        if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=130:
            raise ValueError('unknown_player_minutes')
    total=sum(p['minutes_played'] for p in group)
    if not 750<=total<=1250:raise ValueError('implausible_team_minutes')
    return [{'competition_id':row['competition_id'],'fixture_id':row['id'],'season':row['season'],
             'kickoff':row['kickoff'],'side':side,'espn_team_id':row[side+'_id'],
             'source_fixture_id':p['match_id'],'source_team_id':p['team_id'],
             'source':'opta_archive','source_player_id':p['player_id'],'source_name':p['player_name'],
             'position':p['position'],'starter':p['is_starter'],'minutes':p['minutes_played']} for p in group]


def managers(group):
    names=set()
    for p in group:
        if not p.get('team_official_json'):continue
        for person in json.loads(p['team_official_json']):
            if person.get('type')=='manager' and person.get('id') and person.get('lastName'):
                names.add((person['id'],(person.get('firstName','')+' '+person['lastName']).strip()))
    return list(names)[0] if len(names)==1 else None


def alternate_minutes(row,side,groups):
    fact=row.get('supplemental_postmatch_facts',{}).get('understat_archive')
    if not fact:return None
    m=fact['facts'];uid=str(m['understat_id']);team=str(m[side+'_team_id'])
    if (fact['source_id']!='understat:'+uid or m['season']!=row['season']
            or any(m[s+'_score']!=row[s+'_goals'] for s in SIDES)):
        raise ValueError('archive_match_conflict')
    g=groups.get(uid,[])
    out={};other_ids=set()
    for p in g:
        pid=str(p['player_id'])
        if p['h_a'] not in ('h','a') or p['is_home'] is not (p['h_a']=='h') or p['league_code']!=m['league_code']:
            raise ValueError('archive_side_conflict')
        if p['h_a']!=side[0]:
            other_ids.add(pid);continue
        if str(p['team_id'])!=team or not pid.isdecimal() or not p['position']:raise ValueError('archive_player_identity')
        val=float(p['time'])
        if not math.isfinite(val) or not 0<=val<=130:raise ValueError('unknown_archive_minutes')
        v={'competition_id':row['competition_id'],'fixture_id':row['id'],'season':row['season'],
            'kickoff':row['kickoff'],'side':side,'espn_team_id':row[side+'_id'],'source_fixture_id':uid,
            'source_team_id':team,'source':'understat_archive','source_player_id':pid,
            'source_name':p['player'],'position':p['position'],'starter':p['position']!='Sub','minutes':val}
        if pid in out and out[pid]!=v:raise ValueError('conflicting_archive_player')
        out[pid]=v
    if (other_ids.intersection(out) or sum(p['starter'] for p in out.values())!=11
            or not 750<=sum(p['minutes'] for p in out.values())<=1250):
        raise ValueError('incomplete_archive_side')
    return list(out.values())


def put_json(folder, name, data):
    body=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    sha=digest(body);p=folder/(name+'-'+sha+'.json')
    if p.exists() and digest(p.read_bytes())!=sha:raise ValueError('content_collision')
    if not p.exists():p.write_bytes(body)
    return {'file':p.name,'sha256':sha}


def opta_data():
    url='https://api.github.com/repos/peteowen1/pannadata/releases/tags/opta-latest'
    release=read(WORK.parent/'opta-release.json')
    checked_file(WORK.parent,'opta-release.json',url,'opta-release.receipt.json')
    refs={}
    for n,folder in [('opta_lineups.parquet',WORK),('opta_fixtures.parquet',WORK.parent)]:
        asset=next(a for a in release['assets'] if a['name']==n)
        rec=checked_file(folder,n,asset['browser_download_url'])
        if asset['digest']!='sha256:'+rec['sha256'] or (folder/n).stat().st_size!=asset['size']:
            raise ValueError('asset_checksum_conflict:'+n)
        refs[n]=rec
    fixtures=pq.read_table(WORK.parent/'opta_fixtures.parquet',filters=[
        ('competition','in',[v[1] for v in CODES.values()]),('match_status','=','Played')]).to_pylist()
    refs['understat_roster.parquet']=verified_parquet(ROOT/'continuation/understat_roster.parquet',
        read(ROOT/'continuation/pannadata-releases.json'))
    fb=read(WORK/'fbref-release.json')
    checked_file(WORK,'fbref-release.json','https://api.github.com/repos/peteowen1/pannadata/releases/tags/fbref-latest')
    a=next(a for a in fb['assets'] if a['name']=='metadata.parquet')
    rec=checked_file(WORK,'fbref-metadata.parquet',a['browser_download_url'])
    if 'sha256:'+rec['sha256']!=a['digest']:raise ValueError('fbref_asset_conflict')
    refs['fbref-metadata.parquet']=rec
    fb_rows=pq.read_table(WORK/'fbref-metadata.parquet',filters=[('league','in',[v[2] for v in CODES.values()])]).to_pylist()
    return fixtures,fb_rows,refs


def audit(slug,fixtures,fb_rows,refs,hf):
    pointer=read(ROOT/slug/'latest.json')
    body=(ROOT/slug/pointer['file']).read_bytes()
    if digest(body)!=pointer['sha256']:raise ValueError('snapshot_integrity')
    snap=json.loads(body);cid=SPECS[slug][0]
    rows=sorted((r for r in snap['matches'] if r['training_eligible']),key=lambda r:(r['kickoff'],r['id']))
    if any(r['competition_id']!=cid for r in rows):raise ValueError('competition_conflict')
    directory,dr=response(slug+'-directory',slug,'teams?limit=150')
    leagues=[l for sport in directory['sports'] for l in sport['leagues']
             if l['slug']==slug and str(l['id'])==SPECS[slug][2]]
    if len(leagues)!=1:raise ValueError('directory_league_conflict')
    members=[t['team'] for t in leagues[0]['teams']]
    if len({t['id'] for t in members})!=len(members) or any(t['uid']!='s:600~t:'+t['id'] for t in members):
        raise ValueError('directory_identity_conflict')
    active={'espn:'+str(t['id']) for t in members}
    schedule={r[s+'_id'] for r in rows if r['season']=='2026' for s in SIDES}
    clubs={};roster_errors=[];records=defaultdict(list)
    for row in rows:
        for s in SIDES:records[(row['season'],row[s+'_id'])].append((row,s))
    maps=read(MAPS)
    player_stats,team_staff,player_issues=player_data(slug,rows,maps[slug+'-hf'],hf,OUT)
    stat_sides={(p['fixture_id'],p['side']) for p in player_stats}
    index=fixture_index('opta',slug,fixtures,maps[slug+'-opta'],{})
    fb_index=fixture_index('fbref',slug,fb_rows,maps[slug+'-fbref'],{})
    linked={}
    for r in rows:
        f=fixture_for(r,index,'opta')
        if f and all(f[s+'_score_ht'] is not None and int(f[s+'_score_ht'])==r[s+'_half_goals'] for s in SIDES):
            if f['match_id'] in linked:raise ValueError('duplicate_source_fixture')
            linked[f['match_id']]=(r,f)
    cols=['match_id','match_date','team_position','team_id','player_id','player_name','season','competition',
          'is_starter','minutes_played','position','team_official_json']
    groups=defaultdict(list)
    for batch in pq.read_table(WORK/'opta_lineups.parquet',columns=cols,
            filters=[('match_id','in',list(linked))]).to_batches(max_chunksize=12000):
        for p in batch.to_pylist():groups[(p['match_id'],p['team_position'])].append(p)
    by_match={r['id']:f for r,f in linked.values()};minutes=[];minute_issues=[]
    alternates=defaultdict(list)
    archive_ids=[str(f['facts']['understat_id']) for r in rows if (f:=r.get('supplemental_postmatch_facts',{}).get('understat_archive'))]
    for p in pq.read_table(ROOT/'continuation/understat_roster.parquet',columns=['understat_id','player_id','team_id','player','h_a','is_home','time','position','league_code'],
            filters=[('understat_id','in',archive_ids),('league_code','=',LEAGUES[slug][0])]).to_pylist():
        alternates[str(p['understat_id'])].append(p)
    for (season,tid),games in sorted(records.items()):
        club={'season':season,'id':tid,'name':games[-1][0][games[-1][1]+'_name'],'counts':Counter(),
              'players':{},'fixtures':[],'comparison':{},'coach_names_from_match_reports':{}}
        clubs.setdefault(tid,[]).append(club)
        try:
            name=f'{slug}-{season}-{tid.split(":")[1]}'
            data,sr=response(name,slug,f'teams/{tid.split(":")[1]}/roster?season={season}')
            club['players']=roster(data,season,tid);club['season_roster_receipt']=sr
            club['unverified_roster_coach_field']=data.get('coach')
        except (ValueError,KeyError,OSError) as exc:roster_errors.append([season,tid,str(exc)])
        listed=set(club['players']);appeared=set();prev=None
        for r,s in games:
            cc=club['counts'];cc['matches']+=1;cc[s]+=1
            sheets=(r.get('supplemental_basics',{}).get('lineups') or r['postmatch_lineups']).get(s,[])
            cc['eleven_starters']+=len(sheets)==11 and len({p['id'] for p in sheets})==11
            cc['xg']+=bool(r.get('supplemental_postmatch_facts') or r.get('external_postmatch_xg'))
            cc['dated_lineups']+='reported_pregame_lineup' in r
            cc['partial_fpl']+='prematch_fpl_observation' in r
            cc['dated_odds']+='prematch_market_archive' in r
            cc['player_stats_reported']+=(r['id'],s) in stat_sides
            cc['trusted_replacement_box']+='archived_team_box' in r.get('additional_postmatch_statistics',{})
            participants=r.get('postmatch_players',{}).get(s,[])
            starter_ids={('espn:'+p['id'] if p['id'].isdigit() else p['id']):p['name'] for p in sheets}
            game_players=dict(starter_ids);subs=set()
            for p in participants:
                pid='espn:'+p['id'];game_players[pid]=p['name']
                if p.get('subbed_in') is True:subs.add(pid)
            for pid,display in game_players.items():
                v=club['players'].setdefault(pid,{'listed':False,'name':display,'listed_position':None,
                    'starts':0,'sub_appearances':0,'sheet_matches':0})
                v['sheet_matches']+=1;v['starts']+=pid in starter_ids;v['sub_appearances']+=pid in subs
            appeared.update(starter_ids);appeared.update(subs)
            start=datetime.fromisoformat(r['kickoff']);gap=None if prev is None else (start-prev).total_seconds()/86400;prev=start
            match={'id':r['id'],'side':s,'kickoff':r['kickoff'],'league_only_interval_days':gap,
                   'postmatch_starter_ids':list(starter_ids),'subbed_in_ids':sorted(subs)}
            f=by_match.get(r['id']);mg=None;accepted=False;reason='fixture_not_independently_linked'
            if f:
                group=groups[(f['match_id'],s)]
                try:
                    ledger=minutes_for_side(r,s,f,group)
                    other={p['player_id'] for p in groups[(f['match_id'],'away' if s=='home' else 'home')]}
                    if other.intersection(p['source_player_id'] for p in ledger):raise ValueError('player_on_both_teams')
                    minutes.extend(ledger);accepted=True;mg=managers(group);cc['opta_minutes']+=1
                except (ValueError,TypeError,KeyError) as exc:reason=str(exc)
            if not accepted:
                try:
                    alternate=alternate_minutes(r,s,alternates)
                    if alternate:minutes.extend(alternate);accepted=True;cc['understat_minutes']+=1
                except (ValueError,KeyError,TypeError) as exc:reason+='; '+str(exc)
            if not accepted and r['id']=='espn:748490' and s=='home':
                extra,receipt=mallorca_minutes(r,OUT)
                minutes.extend(extra);accepted=True;cc['club_derived_minutes']+=1
                match['derived_minute_evidence']=receipt
            if not accepted:minute_issues.append([r['id'],s,reason])
            fb=fixture_for(r,fb_index,'fbref')
            staff=team_staff.get((r['id'],s))
            if staff:
                match['compiled_lineup_coach']=staff
                club['coach_names_from_match_reports']['hf_display:'+staff['reported_name']]=staff['reported_name']
            if fb:
                match['match_report_context']={k:fb[k] for k in ['fbref_id','match_url','venue','referee',s+'_manager',s+'_formation']}
                if fb[s+'_manager']:
                    name=' '.join(fb[s+'_manager'].split())
                    club['coach_names_from_match_reports']['fbref_display:'+name]=name
                cc['venue_in_match_report']+=bool(fb['venue'])
                cc['referee_in_match_report']+=bool(fb['referee'])
            cc['validated_minutes']+=accepted;cc['match_manager']+=bool(mg or staff or (fb and fb[s+'_manager']))
            if mg:
                club['coach_names_from_match_reports'][mg[0]]=mg[1]
                match['postmatch_reported_manager']={'opta_id':mg[0],'name':mg[1]}
            match['validated_minutes']=accepted;club['fixtures'].append(match)
        comparable={pid for pid in appeared if pid.startswith('espn:')}
        club['comparison']={'espn_players_seen_playing':len(comparable),'listed_players':len(listed),
            'played_but_not_listed_ids':sorted(comparable-listed),'listed_without_observed_appearance_ids':sorted(listed-comparable),
            'other_namespace_starters':sorted(appeared-comparable)}
    folder=OUT/slug;folder.mkdir(exist_ok=True)
    roster_file=put_json(folder,'clubs',clubs)
    # A single parquet ledger per competition, never merge IDs from separate providers by name.
    tmp=folder/'minutes.tmp.parquet';pq.write_table(pa.Table.from_pylist(minutes),tmp,compression='zstd')
    checksum=digest(tmp.read_bytes());dest=folder/('minutes-'+checksum+'.parquet')
    if dest.exists() and digest(dest.read_bytes())!=checksum:raise ValueError('minute_ledger_collision')
    tmp.replace(dest)
    st_tmp=folder/'player-statistics.tmp.parquet';pq.write_table(pa.Table.from_pylist(player_stats),st_tmp,compression='zstd')
    published=pq.read_table(st_tmp,columns=['fixture_id','side'])
    if {(v['fixture_id'],v['side']) for v in published.to_pylist()}!=stat_sides:
        raise ValueError('stat_ledger_coverage_mismatch')
    st_sha=digest(st_tmp.read_bytes());st_dest=folder/('player-statistics-'+st_sha+'.parquet')
    if st_dest.exists() and digest(st_dest.read_bytes())!=st_sha:raise ValueError('stat_ledger_collision')
    st_tmp.replace(st_dest)
    all_groups=[c for years in clubs.values() for c in years]
    aggregate=sum((c['counts'] for c in all_groups),Counter())
    gaps=Counter(x[-1] for x in minute_issues)
    counts={'historical_clubs':len(clubs),'club_seasons':len(records),'current_directory':len(active),
            'eligible_matches':len(rows),'nonempty_identity_checked_season_rosters':len(records)-len(roster_errors),
            'rosters_below_eleven':sum(c['comparison']['listed_players']<11 for c in all_groups),
            'rosters_listing_every_seen_espn_participant':sum(not c['comparison']['played_but_not_listed_ids'] for c in all_groups),
            'listed_club_season_player_records':sum(c['comparison']['listed_players'] for c in all_groups),
            'unique_espn_roster_ids':len({pid for c in all_groups for pid,v in c['players'].items() if v['listed']}),
            'played_but_not_listed_club_season_records':sum(len(c['comparison']['played_but_not_listed_ids']) for c in all_groups),
            'club_match_sides':aggregate['matches'],'minutes_linked_sides':aggregate['validated_minutes'],
            'match_manager_sides':aggregate['match_manager'],'minute_ledger_rows':len(minutes),
            'venue_report_sides':aggregate['venue_in_match_report'],'referee_report_sides':aggregate['referee_in_match_report'],
            'minutes_from_opta':aggregate['opta_minutes'],'minutes_from_understat':aggregate['understat_minutes'],
            'derived_minutes_sides':aggregate['club_derived_minutes'],
            'player_stat_rows':len(player_stats),'sides_with_player_stats':len(stat_sides),
            'minutes_issues':dict(gaps),'directory_not_in_current_sample':sorted(active-schedule),
            'sample_not_in_directory':sorted(schedule-active)}
    manifest={'competition_id':cid,'source_snapshot':pointer,'directory_receipt':dr,'source_assets':{**refs,**hf[2]},
        'counts':counts,'club_file':roster_file,'minutes_file':{'file':dest.name,'sha256':checksum},
        'player_stat_file':{'file':st_dest.name,'sha256':st_sha},'player_stats_exclusions':player_issues,
        'roster_errors':roster_errors,'minutes_exclusions':minute_issues,
        'timing':'retrospective season membership and postmatch personnel; historical registration and publication times unknown',
        'approved_as_training_feature':False}
    result=put_json(folder,'audit',manifest);atomic_json(folder/'latest.json',result)
    return counts


def main():
    fixtures,fb,refs=opta_data()
    hf=load_player_sources(OUT)
    summary={slug:audit(slug,fixtures,fb,refs,hf) for slug in CODES}
    atomic_json(OUT/'summary.json',summary)
    print(json.dumps(summary))


if __name__=='__main__':main()
