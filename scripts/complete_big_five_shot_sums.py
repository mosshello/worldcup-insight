"""Derive separately labelled postmatch xG from complete, verified shot ledgers."""
from collections import Counter
from datetime import date
from hashlib import file_digest
from math import fsum, isfinite
from pathlib import Path
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import pyarrow.parquet as pq
from scripts.reconcile_big_five import field_coverage
from scripts.supplement_big_five import read, digest
from worldcup_mvp.console.training import atomic_json

SOURCE = 'pannadata_shot_xg_sum'
LEAGUES = {'esp.1':'La_Liga','ita.1':'Serie_A','fra.1':'Ligue_1'}
FIXTURES = [
    ('esp.1','espn:401882824','7w6snqarunvcbuxoefo96ffh0','espn:90','espn:97','RC Deportivo de A Coruña','CA Osasuna'),
    ('ita.1','espn:401874963','7oast51248g14j4g2jf5kd2j8','espn:3997','espn:109','US Sassuolo Calcio','ACF Fiorentina'),
    ('ita.1','espn:401874971','7lt1urbbtoymx5x93yigph3pw','espn:105','espn:115','Atalanta Bergamasca Calcio','Parma Calcio 1913'),
    ('fra.1','espn:401876487','9clop68rwzdf3xoxa6lwnmgwk','espn:169','espn:160','Stade Rennais FC','Paris Saint-Germain FC'),
    ('fra.1','espn:401876419','9zp0wfqgvi0r0qil1yymyvuhg','espn:273','espn:6997','FC Lorient','Stade Brestois 29'),
]


def asset_receipt(work, filename, release, release_receipt):
    path = work / filename
    receipt = read(path.with_suffix('.parquet.receipt.json'))
    assets = [a for a in release['assets'] if a['name'] == filename]
    if len(assets) != 1:
        raise ValueError('ambiguous_release_asset')
    asset = assets[0]
    with path.open('rb') as f:
        sha = file_digest(f, 'sha256').hexdigest()
    if (receipt['http_status'] != '200' or receipt['transport_exit'] != 0 or sha != receipt['sha256']
            or receipt['url'] != asset['browser_download_url'] or path.stat().st_size != asset['size']
            or asset['digest'] != 'sha256:' + sha):
        raise ValueError('release_file_changed:' + filename)
    return {'sha256':sha, 'asset_id':asset['id'], 'asset_updated_at':asset['updated_at'],
            'url':receipt['url'], 'observed_at':receipt['observed_at'], 'http_status':'200',
            'transport_exit':0, 'release_receipt':release_receipt}


def shot_sums(fixture, events, players, canonical_counts):
    teams = [fixture['home_team_id'], fixture['away_team_id']]
    if len(set(teams)) != 2 or not all(teams):
        raise ValueError('team_identity_conflict')
    seen = set(); goals = Counter(); attempts = Counter(); reported = {}
    values = {t:[] for t in teams}; own = []; details = []
    for e in events:
        team = e['team_id']; key = (team,e['event_id'])
        if (key in seen or e['event_id'] is None or team not in teams or not e['player_id']
                or any(e[k] != fixture[k] for k in ('match_id','season','competition'))
                or e['thin_feed'] is True or e['type_id'] not in (13,14,15,16)
                or type(e['is_own_goal']) is not bool or e['is_goal'] is not (e['type_id']==16)):
            raise ValueError('invalid_or_duplicate_shot_event')
        seen.add(key)
        if e['is_own_goal']:
            if not e['is_goal'] or e['xg'] not in (None,0):
                raise ValueError('unresolved_own_goal')
            goals[teams[1-teams.index(team)]] += 1
            own.append(key)
        else:
            v=e['xg']
            if type(v) not in (int,float) or not isfinite(v) or not 0 <= v <= 1:
                raise ValueError('unpriced_attempt')
            values[team].append(v); attempts[(team,e['player_id'])] += 1
            goals[team] += int(e['is_goal'])
        details.append({k:e[k] for k in ('event_id','team_id','player_id','minute','second','type_id','is_goal','is_own_goal','thin_feed','xg')})
    for p in players:
        k = (p['team_id'],p['player_id']); n=p['total_shots']
        if (k in reported or p['team_id'] not in teams or not p['player_id']
                or any(p[k] != fixture[k] for k in ('match_id','season','competition'))
                or type(n) not in (int,float) or n < 0 or n != int(n)):
            raise ValueError('invalid_player_shot_ledger')
        reported[k]=int(n)
    if (attempts != Counter(reported) or [len(values[t]) for t in teams] != canonical_counts
            or [goals[t] for t in teams] != [int(fixture['home_score']),int(fixture['away_score'])]
            or not all(values.values())):
        raise ValueError('independent_shot_count_or_result_conflict')
    return [fsum(values[t]) for t in teams], {
        'priced_attempts':canonical_counts,'excluded_own_goal_ids':own,'events':details,
        'feed_flag_missing':any(e['thin_feed'] is None for e in events),'approved_as_training_feature':False}


def run(old, work):
    release_path = work / 'opta-release.json'; rel_receipt = read(work/'opta-release.receipt.json')
    release = read(release_path)
    if (rel_receipt['sha256'] != digest(release_path.read_bytes()) or rel_receipt['http_status'] != '200'
            or rel_receipt['transport_exit'] != 0 or release['tag_name'] != 'opta-latest'
            or rel_receipt['url'] != 'https://api.github.com/repos/peteowen1/pannadata/releases/tags/opta-latest'):
        raise ValueError('release_integrity')
    names = ['opta_fixtures.parquet','opta_shot_events.parquet','opta_shots.parquet']
    receipts = {n:asset_receipt(work,n,release,rel_receipt) for n in names}
    parent_file=work/'shot-sum-parents.json'
    if not parent_file.exists():
        atomic_json(parent_file,{s:read(old/s/'latest.json') for s in LEAGUES})
    parents=read(parent_file); snapshots={}; outputs={}; added={}
    for slug,p in parents.items():
        body=(old/slug/p['file']).read_bytes()
        if digest(body)!=p['sha256']:
            raise ValueError('parent_integrity')
        snapshots[slug]=json.loads(body)
    for review in FIXTURES:
        slug,eid,mid,hid,aid,hname,aname=review
        rows=snapshots[slug]['matches']; targets=[r for r in rows if r['id']==eid]
        if len(targets)!=1:
            raise ValueError('fixture_identity')
        row=targets[0]; tables=[pq.read_table(work/n,filters=[('match_id','=',mid)]).to_pylist() for n in names]
        if len(tables[0])!=1:
            raise ValueError('archive_fixture_not_unique')
        fx=tables[0][0]
        if (fx['match_status']!='Played' or fx['competition']!=LEAGUES[slug] or fx['season']!='2026-2027'
                or row['season']!='2026' or row['period']!='regular_time' or not row['training_eligible']
                or row['competition_id']!=snapshots[slug]['manifest']['competition_id']
                or row['home_id']!=hid or row['away_id']!=aid or [fx['home_team'],fx['away_team']]!=[hname,aname]
                or fx['match_date']!=row['kickoff'][:10]+'Z' or row.get('supplemental_postmatch_facts') or row.get('external_postmatch_xg')
                or sum(r['season']=='2026' and r['home_id']==hid and r['away_id']==aid for r in rows)!=1
                or any(int(fx[s+'_score'])!=row[s+'_goals'] or int(fx[s+'_score_ht'])!=row[s+'_half_goals'] for s in ('home','away'))):
            raise ValueError('archive_fixture_scope_or_score_conflict')
        counts=[row['postmatch_statistics'][s]['totalShots'] for s in ('home','away')]
        xg,evidence=shot_sums(fx,tables[1],tables[2],counts)
        row.setdefault('supplemental_postmatch_facts',{})[SOURCE]={
            'source_id':'opta-archive:'+mid,'xg':xg,'source_kind':'local_sum_of_pannadata_archived_shot_xg',
            'source_receipt':receipts[names[1]],'index_receipt':receipts[names[0]],
            'identity_policy':'reviewed_teams_season_date_full_and_half_scores_per_player_attempts_and_finals_v1',
            'identity_exception':{'archive_team_ids':[fx['home_team_id'],fx['away_team_id']],
                'canonical_team_ids':[hid,aid], 'archive_date':fx['match_date'],'exact_kickoff_verified':False,
                'player_count_receipt':receipts[names[2]],'measurement':'sum_of_recorded_non_own_goal_shot_xg',
                'algorithm_version_verified':False,'shot_evidence':evidence},
            'approved_as_training_feature':False,'upstream_collection_time_verified':False}
        added[eid]={'xg':xg,'priced_attempts':counts,'excluded_own_goals':len(evidence['excluded_own_goal_ids'])}
    for slug,d in snapshots.items():
        for cov in d['coverage']:
            rows=[r for r in d['matches'] if r['training_eligible'] and r['season']==cov['season']]
            cov['shot_sum_xg_both']=sum(SOURCE in r.get('supplemental_postmatch_facts',{}) for r in rows)
            cov['any_source_xg_both']=sum(bool(r.get('supplemental_postmatch_facts') or r.get('external_postmatch_xg')) for r in rows)
        manifest=d['manifest']['event_evidence_update']
        manifest['shot_sum_parent_sha256']=parents[slug]['sha256']
        manifest['shot_sum_code_sha256']=digest(Path(__file__).read_bytes())
        manifest['usable_field_coverage']['xg_both']=sum(c['any_source_xg_both'] for c in d['coverage'])
        body=json.dumps(d,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        sha=digest(body);dest=old/slug/'snapshots'/(sha+'.json')
        if dest.exists() and digest(dest.read_bytes())!=sha:
            raise ValueError('snapshot_collision')
        if not dest.exists():dest.write_bytes(body)
        outputs[slug]={'file':str(dest.relative_to(old/slug)),'sha256':sha,'rows':len(d['matches'])}
    for slug,p in outputs.items():
        if read(old/slug/'latest.json') not in (parents[slug],p):
            raise ValueError('newer_snapshot_present')
    atomic_json(work/'shot-sum-audit.json',{'added':added,'asset_receipts':receipts,'versions':outputs})
    for slug,p in outputs.items():atomic_json(old/slug/'latest.json',p)
    print(json.dumps(added))


if __name__=='__main__':
    root=Path('data/research/big-five')
    run(root/'2026-11-05',root/'2026-11-06')
