"""Additional named, retrospective sources for match personnel."""
from collections import defaultdict
import math
import pyarrow.parquet as pq

from scripts.restore_big_five_boxscores import HF, WORK, checked_file, fixture_index, fixture_for, read
from scripts.reconcile_big_five import parse_club_page

MALLORCA='https://www.rcdmallorca.es/en/matches/season-2025-2026-laliga-ea-sports-35-rcd-mallorca-vs-villarreal-cf-346'


def verified_hf_files(folder):
    tree_url='https://huggingface.co/api/datasets/svgwvgs/soccer-dataset/tree/'+HF+'?recursive=true&expand=false'
    checked_file(WORK,'hf-tree.json',tree_url)
    entries={e['path']:e for e in read(WORK/'hf-tree.json')}
    receipts={}
    for name in ('fixtures','teams','fixture_players','players','fixture_players_stats_flat','fixture_lineups'):
        f=name+'.parquet';old=name in ('fixtures','teams')
        base=WORK if old else folder;local='hf-'+f if old else f
        rec=checked_file(base,local,'https://huggingface.co/datasets/svgwvgs/soccer-dataset/resolve/'+HF+'/'+f)
        if rec['sha256']!=entries[f]['lfs']['oid'] or (base/local).stat().st_size!=entries[f]['size']:
            raise ValueError('player_asset_integrity:'+name)
        receipts[name]=rec
    return receipts


def load_player_sources(folder):
    receipts=verified_hf_files(folder)
    fixtures=pq.read_table(WORK/'hf-fixtures.parquet',filters=[('league_id','in',[1,5,7,9,11]),('status_norm','=','FT')]).to_pylist()
    teams={t['id']:t['name'] for t in pq.read_table(WORK/'hf-teams.parquet').to_pylist()}
    return fixtures,teams,receipts


def player_data(slug, rows, mapping, sources, folder):
    fixtures,teams,_=sources
    idx=fixture_index('hf',slug,fixtures,mapping,teams)
    byid={}
    for r in rows:
        f=fixture_for(r,idx,'hf')
        if f:
            if f['id'] in byid:raise ValueError('duplicate_player_source_fixture')
            byid[f['id']]=(r,f)
    ids=list(byid)
    players=pq.read_table(folder/'fixture_players.parquet',filters=[('fixture_id','in',ids)]).to_pylist()
    roster={p['id']:p for p in players}
    if len(roster)!=len(players):raise ValueError('duplicate_player_match_row')
    # Publish named, after-match statistics only; fields can remain individually missing.
    fields=['goals_total','goals_assists','shots_total','shots_on','passes_total','fouls_committed',
            'cards_yellow','cards_red','goals_saves','duels_total','duels_won']
    stats=pq.read_table(folder/'fixture_players_stats_flat.parquet',columns=['fixture_player_id','fixture_id','player_id',*fields],
                        filters=[('fixture_id','in',ids)]).to_pylist()
    fact={};bad=[]
    for st in stats:
        p=roster.get(st['fixture_player_id'])
        if not p or p['fixture_id']!=st['fixture_id'] or p['player_id']!=st['player_id'] or p['id'] in fact:
            raise ValueError('player_stat_key_conflict')
        fact[p['id']]=st
    groups=defaultdict(list)
    for p in players:
        r,f=byid[p['fixture_id']]
        side=next((s for s in ('home','away') if f[s+'_team_id']==p['team_id']),None)
        if side is None:bad.append((r['id'],'foreign_source_team'));continue
        groups[(p['fixture_id'],side)].append(p)
    ledger=[]
    for (fid,side),g in groups.items():
        r,f=byid[fid]
        if (None in {p['player_id'] for p in g} or len(set(p['player_id'] for p in g))!=len(g)
                or sum(p['is_starter'] is True for p in g)!=11):
            bad.append((r['id'],side,'incomplete_starting_eleven_or_duplicate'));continue
        for p in g:
            st=fact.get(p['id'])
            if not st:continue
            values={k:st[k] for k in fields}
            if any(v is not None and (type(v) not in (int,float) or not math.isfinite(v) or v<0) for v in values.values()):
                bad.append((r['id'],side,'invalid_count',p['player_id']));continue
            if not any(v is not None for v in values.values()):continue
            ledger.append({'competition_id':r['competition_id'],'season':r['season'],'fixture_id':r['id'],
                           'source_fixture_id':fid,'source_player_id':p['player_id'],'source_name':p['player_name'],
                           'team_id':r[side+'_id'],'side':side,'starter':p['is_starter'],'source_position':p['position'],
                           **values})
    coaches={}
    for t in pq.read_table(folder/'fixture_lineups.parquet',filters=[('fixture_id','in',ids)]).to_pylist():
        r,f=byid[t['fixture_id']]
        side=next((s for s in ('home','away') if f[s+'_team_id']==t['team_id']),None)
        if not side or not t['coach_name']:continue
        key=(r['id'],side)
        if key in coaches:raise ValueError('ambiguous_team_coach')
        coaches[key]={'source_fixture_id':t['fixture_id'],'source_team_id':t['team_id'],
            'reported_name':t['coach_name'],'source_coach_id':t['coach_api_id'],'formation':t['formation']}
    return ledger,coaches,bad


def mallorca_minutes(row,folder):
    """Regulation minutes calculated from the official XI and paired substitutions."""
    receipt=checked_file(folder,'mallorca-minutes.html',MALLORCA)
    d=parse_club_page((folder/'mallorca-minutes.html').read_text(encoding='utf8'));g=d['game'];side='home'
    if (row['id']!='espn:748490' or row['competition_id']!='esp.laliga' or row['home_id']!='espn:84'
        or row['away_name']!='Villarreal' or g['id']!='b52856e2-74fe-4c21-a648-c837d62c714c'
        or g['homeTeam']['shortName']!='RCD Mallorca' or g['awayTeam']['shortName']!='Villarreal CF'
        or g['time']!='2026-05-10T12:00:00Z' or row['kickoff']!='2026-05-10T12:00:00+00:00'
        or g['period']['type']!='FullTime' or [row['home_goals'],row['away_goals'],g['homeScore'],g['awayScore']]!=[1]*4
        or row['postmatch_statistics']['home']['redCards']!=0):
        raise ValueError('unverified_official_fixture')
    events=d['events']['data']
    if len(events)!=d['events']['total'] or len(set(e['id'] for e in events))!=len(events):
        raise ValueError('incomplete_official_timeline')
    # The club page omits one starter. Use the independently identified ESPN XI
    # and require exact agreement between both complete substitution logs.
    people={p['id']:p for p in row['postmatch_players']['home']}
    names={'Antonio Latorre Grueso':'Toni Lato','Zito André Sebastião Luvumbo':'Zito Luvumbo'}
    by_name={p['name']:pid for pid,p in people.items()}
    def identify(p):
        name=p['nickname'] or p['name']
        return by_name.get(names.get(name,name))
    starts={p['id']:0 for p in row['postmatch_lineups']['home']};ends={}
    original=set(starts);subs=defaultdict(dict)
    partial={identify(p['player']) for p in d['lineups']['homeTeam'] if p['starting']}
    if len(original)!=11 or len(partial)!=10 or not partial.issubset(original) or original-partial!={'183466'}:
        raise ValueError('unconfirmed_starting_eleven')
    for e in events:
        if (e.get('team') or {}).get('id')!=g['homeTeam']['id']:continue
        typ=e['type']['type']
        if typ=='Card' and (e.get('qualificationType') or {}).get('type')!='YellowCard':
            raise ValueError('unsupported_card')
        if typ in ('PlayerOn','PlayerOff'):
            key=(e['minute'],e['second']);pid=identify(e['relatedPlayer'])
            if typ in subs[key] or pid not in people or not 0<=e['second']<60:raise ValueError('unmatched_substitution')
            subs[key][typ]=pid
    other=[e for e in row['postmatch_events'] if e['side']=='home' and e['type']=='substitution']
    if len(other)!=len(subs):raise ValueError('substitution_log_size_conflict')
    for (minute,second),pair in sorted(subs.items()):
        if set(pair)!={'PlayerOn','PlayerOff'}:raise ValueError('unpaired_substitution')
        on,off=pair['PlayerOn'],pair['PlayerOff'];boundary=min(90,minute+int(second>0))
        if not any(e['participants']==[on,off] and e['clock']['displayValue']==str(boundary)+"'" for e in other):
            raise ValueError('independent_substitution_clock_conflict')
        if on in starts or off not in starts or off in ends or starts[off]>boundary:raise ValueError('substitution_sequence_conflict')
        starts[on]=ends[off]=boundary
    total={pid:ends.get(pid,90)-start for pid,start in starts.items()}
    for entry in d['stats']['homeTeamPlayersStats']:
        val=(entry.get('stats') or {}).get('minsPlayed')
        if val is not None and total.get(identify(entry['player']))!=val:raise ValueError('published_minute_conflict')
    if sum(total.values())!=990:raise ValueError('regulation_minute_conflict')
    vals=[{'competition_id':row['competition_id'],'fixture_id':row['id'],'season':row['season'],'kickoff':row['kickoff'],
           'side':side,'espn_team_id':row['home_id'],'source_fixture_id':g['id'],'source_team_id':g['homeTeam']['id'],
           'source':'espn_xi_and_club_official_substitutions','source_player_id':'espn:'+pid,
           'source_name':people[pid]['name'],'position':(people[pid].get('position') or {}).get('abbreviation'),
           'starter':pid in original,'minutes':n}
          for pid,n in total.items()]
    return vals,{'club_report':receipt,'espn_summary':row['summary_receipt'],'official_listed_starters':len(partial),
                 'basis':'regulation substitution boundary, rounded up to minute, stoppage time excluded'}
