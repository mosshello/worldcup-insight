"""Past personnel usage proxies. Never predicts a starting XI or eligibility."""
from collections import Counter, defaultdict
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import math
from .store import ROOT, fingerprint
from .observation_features import instant

SLUGS={'eng.premier':'eng.1','esp.laliga':'esp.1','ger.bundesliga':'ger.1','ita.serie_a':'ita.1','fra.ligue1':'fra.1'}
PERSONNEL_FIELDS=['starter_retention','starter_retention_missing','last_rotation',
    'bench_minute_share','bench_players_per_match','distinct_starters_ratio',
    'minutes_top11_share','everpresent_starter_share','known_league_minutes_14d']
COACH_FIELDS=['observed_coach_change','coach_comparison_missing','observed_same_coach_games']
H2H_FIELDS=['h2h_win_streak','h2h_draw_share','h2h_count','h2h_same_venue_win_share']


def _verified(directory, ref):
    path=directory/ref['file']
    if hashlib.sha256(path.read_bytes()).hexdigest()!=ref['sha256']:
        raise ValueError('人员历史产物摘要不符')
    return path


def load_personnel_history(competition_id):
    """Verify frozen inventory and retain provider namespaces; no inferred transfer joins."""
    import pyarrow.parquet as pq
    from . import model_registry
    directory=ROOT/'data/research/big-five/2026-11-06/club-personnel'/SLUGS[competition_id]
    audit=json.loads(_verified(directory,json.loads((directory/'latest.json').read_text(encoding='utf-8'))).read_text(encoding='utf-8'))
    if audit['competition_id']!=competition_id:raise ValueError('人员历史跨赛事')
    minute_path=_verified(directory,audit['minutes_file'])
    rows=pq.read_table(minute_path).to_pylist()
    clubs=json.loads(_verified(directory,audit['club_file']).read_text(encoding='utf-8'))
    fixture_rows={r['id']:r for r in model_registry.dataset(competition_id,admission=False)['matches']}
    # Capture time comes from the actual archived inputs, not today's read.
    capture=max(r['observed_at'] for r in audit['source_assets'].values() if r.get('observed_at'))
    metadata={}
    for team,seasons in clubs.items():
        for season in seasons:
            for r in season.get('fixtures',[]):
                coach=r.get('compiled_lineup_coach') or {}
                context=r.get('match_report_context') or {}
                key=('hf:'+str(coach['source_coach_id'])) if coach.get('source_coach_id') is not None else None
                if not key and context.get(r['side']+'_manager'):
                    key='fbref_name:'+' '.join(context[r['side']+'_manager'].split()).casefold()
                metadata[(r['id'],team)]={'coach_id':key,'coach_name':coach.get('reported_name') or context.get(r['side']+'_manager')}
    groups=defaultdict(list)
    for r in rows:
        if r['competition_id']!=competition_id:raise ValueError('人员分钟跨赛事')
        groups[(r['fixture_id'],r['espn_team_id'])].append(r)
    result=[];excluded=[]
    for (fid,team),players in groups.items():
        row=fixture_rows.get(fid)
        reason=None
        if not row or team not in (row['home_id'],row['away_id']):reason='fixture_team_mismatch'
        elif any(instant(p['kickoff'])!=instant(row['kickoff']) for p in players):reason='kickoff_mismatch'
        elif len({(p['source'],str(p['source_player_id'])) for p in players})!=len(players):reason='duplicate_player'
        elif len({p['source'] for p in players})!=1:reason='mixed_player_namespace'
        elif sum(p['starter'] is True for p in players)!=11:reason='unverified_starting_eleven'
        elif any(not isinstance(p['minutes'],(int,float)) or not math.isfinite(p['minutes']) or not 0<=p['minutes']<=130 for p in players):reason='invalid_minutes'
        elif sum(p['minutes'] for p in players)<=0:reason='missing_team_minutes'
        if reason:excluded.append({'id':fid,'team_id':team,'reason':reason});continue
        source=players[0]['source']
        result.append({'fixture_id':fid,'competition_id':competition_id,'team_id':team,
            'kickoff':row['kickoff'],'historical_available_at':max(instant(row['result_available_at']),instant(row['kickoff'])+timedelta(days=7)).isoformat(),
            'captured_at':max(instant(capture),instant(row.get('collected_at') or capture)).isoformat(),
            'source':source,'players':[{'id':source+':'+str(p['source_player_id']),'starter':p['starter'],'minutes':p['minutes']} for p in players],
            **metadata.get((fid,team),{'coach_id':None,'coach_name':None})})
    return {'schema':'past-personnel-history-v1','competition_id':competition_id,'rows':sorted(result,key=lambda r:(r['kickoff'],r['fixture_id'],r['team_id'])),
            'source_digest':audit['minutes_file']['sha256'],'excluded':excluded,
            'scope':'same_league_postmatch_usage_only','timing':'historical_plus_7_days_assumption_or_actual_capture'}


def personnel_features(history, fixture, as_of, historical=False):
    at=instant(as_of)
    if history['competition_id']!=fixture['competition_id']:raise ValueError('人员特征跨赛事')
    if any(r['competition_id']!=history['competition_id'] for r in history['rows']):raise ValueError('人员行包含其他赛事')
    if at>=instant(fixture['kickoff']):raise ValueError('人员特征截止不是赛前')
    values=[];refs=[]
    for side in ('home','away'):
        past=[r for r in history['rows'] if r['team_id']==fixture[side+'_id'] and r['fixture_id']!=fixture['id']
              and instant(r['kickoff'])<at and instant(r['historical_available_at'] if historical else r['captured_at'])<=at]
        past=sorted(past,key=lambda r:(r['kickoff'],r['fixture_id']))[-10:]
        if len(past)<3:raise ValueError('缺少三场可用历史人员记录')
        # Compare identities only inside a single provider namespace.
        source=past[-1]['source']; chosen=[r for r in past if r['source']==source][-5:]
        if len(chosen)<3:raise ValueError('同来源人员历史不足')
        starters=[{p['id'] for p in r['players'] if p['starter']} for r in chosen]
        overlap=[len(a&b)/11 for a,b in zip(starters,starters[1:])]
        mins=Counter();starts=Counter();benchmins=benchcount=total=0
        for r in chosen:
            for p in r['players']:
                mins[p['id']]+=p['minutes'];total+=p['minutes']
                if p['starter']:starts[p['id']]+=1
                elif p['minutes']>0:benchmins+=p['minutes'];benchcount+=1
        coaches=[r for r in past[-5:] if r.get('coach_id')]
        comparable=len(coaches)>=2 and coaches[-1]['coach_id'].split(':')[0]==coaches[-2]['coach_id'].split(':')[0]
        tenure=0
        if coaches:
            for r in reversed(past[-5:]):
                if r.get('coach_id')!=coaches[-1]['coach_id']:break
                tenure+=1
        values.append({'starter_retention':sum(overlap)/len(overlap),'starter_retention_missing':0,
            'last_rotation':11-len(starters[-1]&starters[-2]),'bench_minute_share':benchmins/total,
            'bench_players_per_match':benchcount/len(chosen),'distinct_starters_ratio':len(starts)/11,
            'minutes_top11_share':sum(v for _,v in mins.most_common(11))/total,
            'everpresent_starter_share':sum(n==len(chosen) for n in starts.values())/11,
            'known_league_minutes_14d':sum(sum(p['minutes'] for p in r['players']) for r in past if (at-instant(r['kickoff'])).total_seconds()<=14*86400),
            'observed_coach_change':int(comparable and coaches[-1]['coach_id']!=coaches[-2]['coach_id']),
            'coach_comparison_missing':int(not comparable),'observed_same_coach_games':tenure})
        refs.append({'side':side,'personnel_fixture_ids':[r['fixture_id'] for r in chosen],
            'coach_fixture_ids':[r['fixture_id'] for r in coaches],'source':source,
            'availability_basis':'historical_plus_7_days_assumption' if historical else 'actual_capture',
            'not_inferred':['target_starting_xi','expected_minutes','replacement_quality','eligibility','full_cross_competition_load']})
    return [dict(values[i],**{'opp_'+k:v for k,v in values[1-i].items()}) for i in (0,1)],refs


def h2h_features(history,fixture,as_of,historical=False):
    """Past streak/draw hypotheses, controlled by the candidate's other features."""
    at=instant(as_of);pair={fixture['home_id'],fixture['away_id']};past=[]
    for r in history:
        if r['competition_id']!=fixture['competition_id']:raise ValueError('交锋历史跨赛事')
        available=(max(instant(r['result_available_at']),instant(r['kickoff'])+timedelta(days=7)) if historical
                   else instant(r['result_captured_at']) if r.get('result_captured_at') else None)
        if (r['id']!=fixture['id'] and {r['home_id'],r['away_id']}==pair and available and available<=at
                and 0<(at-instant(r['kickoff'])).total_seconds()<=3*366*86400):past.append(r)
    past=sorted(past,key=lambda r:r['kickoff'])[-5:];values=[]
    for side in ('home','away'):
        team=fixture[side+'_id']
        def wins(r):
            return (r['home_goals']>r['away_goals']) if r['home_id']==team else (r['away_goals']>r['home_goals'])
        streak=0
        for r in reversed(past):
            if not wins(r):break
            streak+=1
        same=[r for r in past if r[side+'_id']==team]
        values.append({'h2h_win_streak':streak,'h2h_draw_share':(1+sum(r['home_goals']==r['away_goals'] for r in past))/(3+len(past)),
            'h2h_count':len(past),'h2h_same_venue_win_share':(1+sum(wins(r) for r in same))/(3+len(same))})
    return [dict(values[i],**{'opp_'+k:v for k,v in values[1-i].items()}) for i in (0,1)], [r['id'] for r in past]
