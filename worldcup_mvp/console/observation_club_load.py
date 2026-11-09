"""Cross-competition CLUB fixture counts from verified Opta IDs and date-only facts."""
from collections import defaultdict,Counter
from datetime import datetime,timedelta,timezone
import hashlib,json
from .store import ROOT
from .observation_features import instant
from .observation_personnel import SLUGS,_verified

FIELDS=['observed_club_games_14d','observed_club_games_30d','observed_nonleague_games_30d',
        'days_since_known_club_game_upper_bound','club_load_missing']
OWN_NAMES={'eng.premier':'EPL','esp.laliga':'La_Liga','ger.bundesliga':'Bundesliga','ita.serie_a':'Serie_A','fra.ligue1':'Ligue_1'}


def load_club_history(competition_id):
    import pyarrow.parquet as pq
    base=ROOT/'data/research/big-five/2026-11-06'
    directory=base/'club-personnel'/SLUGS[competition_id]
    audit=json.loads(_verified(directory,json.loads((directory/'latest.json').read_text(encoding='utf-8'))).read_text(encoding='utf-8'))
    minutes=pq.read_table(_verified(directory,audit['minutes_file'])).to_pylist()
    reverse=defaultdict(set);mapping=defaultdict(set)
    for row in minutes:
        if row['source']=='opta_archive':
            reverse[row['source_team_id']].add(row['espn_team_id']);mapping[row['espn_team_id']].add(row['source_team_id'])
    ambiguous={key:sorted(vals) for key,vals in reverse.items() if len(vals)!=1}
    if ambiguous:raise ValueError('Opta球队ID映射多义，不能汇总负荷')
    ref=audit['source_assets']['opta_fixtures.parquet'];path=base/ref['file']
    if hashlib.sha256(path.read_bytes()).hexdigest()!=ref['sha256']:raise ValueError('跨赛事球队比赛存档摘要不符')
    all_rows=pq.read_table(path).to_pylist();rows=[];seen={};excluded=Counter();precision=Counter()
    for row in all_rows:
        teams=[s for s in ('home_team_id','away_team_id') if row[s] in reverse]
        if not teams:continue
        if row['match_status']!='Played':excluded['not_played']+=1;continue
        raw=row['match_date']
        try:
            # The archive exposes many dates like 2026-11-04Z, not kickoff.
            day=datetime.fromisoformat(raw[:10]).replace(tzinfo=timezone.utc)
            if not raw.endswith('Z'):raise ValueError('unknown date basis')
        except (ValueError,TypeError):excluded['invalid_or_unknown_day']+=1;continue
        if day<datetime(2020,1,1,tzinfo=timezone.utc):continue
        end=day+timedelta(days=1)
        for source_team in teams:
            team=next(iter(reverse[row[source_team]]));key=(row['match_id'],team)
            fact={'fixture_id':'opta:'+row['match_id'],'team_id':team,'source_team_id':row[source_team],
                'competition_name':row['competition'],'day_start':day.isoformat(),'day_end':end.isoformat(),
                'date_precision':'day','source_match_date':raw,'kickoff':None,
                'historical_available_at':(end+timedelta(days=7)).isoformat(),'captured_at':ref['observed_at'],
                'player_minutes':None,'extra_time_minutes':None,'source_url':ref['url']}
            if key in seen:
                if seen[key]!=fact:raise ValueError('同场跨赛事负荷事实冲突')
                continue
            seen[key]=fact;rows.append(fact);precision['day']+=1
    return {'schema':'observed-club-load-history-v1','competition_id':competition_id,'rows':sorted(rows,key=lambda r:(r['day_start'],r['fixture_id'],r['team_id'])),
        'source_digest':ref['sha256'],'source_archive_rows':len(all_rows),'source_captured_at':ref['observed_at'],
        'verified_teams':len(mapping),'source_competitions':dict(Counter(r['competition_name'] for r in rows)),
        'excluded':dict(excluded),'date_precision':dict(precision),
        'coverage':'observed_club_fixtures_in_archive_not_certified_complete_and_not_national_team_player_load'}


def club_features(history,fixture,as_of,historical=False):
    if history['competition_id']!=fixture['competition_id']:raise ValueError('负荷研究赛事身份不符')
    at=instant(as_of)
    if at>=instant(fixture['kickoff']):raise ValueError('负荷截止不是赛前')
    own=OWN_NAMES[fixture['competition_id']];values=[];refs=[]
    for side in ('home','away'):
        prior=[r for r in history['rows'] if r['team_id']==fixture[side+'_id'] and instant(r['day_end'])<=at
               and instant(r['historical_available_at'] if historical else r['captured_at'])<=at]
        past=[r for r in prior if (at-instant(r['day_end'])).total_seconds()<=30*86400]
        values.append({'observed_club_games_14d':sum((at-instant(r['day_end'])).total_seconds()<=14*86400 for r in past),
            'observed_club_games_30d':len(past),'observed_nonleague_games_30d':sum(r['competition_name']!=own for r in past),
            'days_since_known_club_game_upper_bound':min(60,(at-max(instant(r['day_start']) for r in prior)).total_seconds()/86400) if prior else 60,
            'club_load_missing':int(not prior)})
        refs.append({'side':side,'fixture_ids':[r['fixture_id'] for r in past],'source_competitions':sorted({r['competition_name'] for r in past}),
            'date_precision':'day','availability_basis':'day_end_plus_7_days_assumption' if historical else 'actual_capture',
            'coverage':history['coverage'],'individual_minutes':None,'national_team_minutes':None})
    return [dict(values[i],**{'opp_'+k:v for k,v in values[1-i].items()}) for i in (0,1)],refs
