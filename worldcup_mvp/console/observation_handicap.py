"""Public MoviOdds Asian handicap observations, isolated from sporttery hhad."""
import gzip
import json
import math
import threading
import time
import unicodedata
import urllib.request
from collections import defaultdict
from .observations import stamp, iso

URL = 'https://moviodds.com/odds?sport=soccer&groups=938'
LEAGUES = {'England Premier League':'eng.premier','Spain La Liga':'esp.laliga',
           'Germany Bundesliga I':'ger.bundesliga','Italy Serie A':'ita.serie_a','France Ligue 1':'fra.ligue1'}
INFER_LEAGUES={'lg_T9268DXK':'England Premier League','lg_PYBWSDEA':'Spain La Liga',
               'lg_PGYWSDEA':'Germany Bundesliga I','lg_QXCSBRME':'Italy Serie A','lg_CNSP3BPA':'France Ligue 1'}
_lock=threading.Lock()
_cache=None
_cached_at=0.0

def fetch_board(timeout=15, cache_seconds=60):
    """One filtered, gzip request per process per minute; failures do not renew cache."""
    global _cache, _cached_at
    with _lock:
        if _cache is not None and time.monotonic()-_cached_at<max(60,cache_seconds):
            return json.loads(json.dumps(_cache))
        req=urllib.request.Request(URL,headers={'Accept-Encoding':'gzip','User-Agent':'FootballConsoleObservation/1.0'})
        with urllib.request.urlopen(req,timeout=timeout) as response:
            raw=response.read(8_000_001)
            if len(raw)>8_000_000: raise ValueError('盘口响应超过限制')
            if response.headers.get('Content-Encoding')=='gzip': raw=gzip.decompress(raw)
        if len(raw)>16_000_000: raise ValueError('盘口响应超过限制')
        board=json.loads(raw)
        if not isinstance(board.get('matches'),list): raise ValueError('盘口响应格式错误')
        board['_received_at']=iso()
        _cache=board; _cached_at=time.monotonic()
        return json.loads(json.dumps(board))

def norm(value):
    return ' '.join(unicodedata.normalize('NFKC',str(value)).casefold().split())

def bind_fixture(match, fixtures, aliases=None, id_map=None):
    """Aliases: {canonical team id: [audited provider names]}; no fuzzy matching."""
    cid=LEAGUES.get(match.get('league'))
    if not cid: return None
    candidates=[]
    mapped=(id_map or {}).get(str(match.get('id')))
    for fixture in fixtures:
        if fixture.get('competition_id')!=cid or stamp(fixture['kickoff'])!=stamp(match['kickoff_utc']): continue
        if mapped and fixture['id']!=mapped: continue
        home={norm(fixture.get('home','')),*[norm(x) for x in (aliases or {}).get(str(fixture.get('home_id')),[])]}
        away={norm(fixture.get('away','')),*[norm(x) for x in (aliases or {}).get(str(fixture.get('away_id')),[])]}
        if mapped or (norm(match['home']) in home and norm(match['away']) in away): candidates.append(fixture)
    if len(candidates)!=1: return None
    return candidates[0], {'method':'audited_id' if mapped else 'exact_alias_and_kickoff',
        'provider_fixture_id':str(match['id']),'competition_id':cid,'kickoff':match['kickoff_utc'],
        'provider_home':match['home'],'provider_away':match['away'],'canonical_fixture_id':candidates[0]['id']}

def adapt_board(board, fixtures, now=None, aliases=None, id_map=None, max_age_seconds=180):
    """Return bindings and rejected counts. Keep source change/read/receipt times separate."""
    now=stamp(now); rejected=defaultdict(int); bindings=[]
    captured=stamp(board.get('_received_at',now))
    generated=stamp(board.get('generated_at',board.get('server_time')))
    if not 0<=(now-generated).total_seconds()<=max_age_seconds or captured>now:
        return {'bindings':[],'rejected':{'stale_board':1},'provider':'moviodds'}
    for match in board['matches']:
        if match.get('in_play') or stamp(match['kickoff_utc'])<=now: rejected['started']+=1; continue
        bound=bind_fixture(match,fixtures,aliases,id_map)
        if not bound: rejected['unmapped_or_ambiguous']+=1; continue
        fixture,evidence=bound; groups=defaultdict(dict)
        for q in match.get('quotes',[]):
            if str(q.get('group_id'))!='938' or q.get('group_name')!='Asian Handicap' or q.get('period') not in (None,'0','full_time'): continue
            status=match.get('group_status',{}).get('938',{})
            if status.get('stale') or status.get('suspended') or q.get('suspended') or q.get('stale'): rejected['suspended_or_stale']+=1; continue
            try:
                polled=stamp(status['polled_at']); changed=stamp(q['ts']); price=float(q['odds_dec']); line=float(q['handicap'])
                if not 0<=(now-polled).total_seconds()<=max_age_seconds or changed>polled or polled>captured: raise ValueError('time')
                if not math.isfinite(price) or price<=1 or not math.isfinite(line) or abs(line*4-round(line*4))>1e-8: raise ValueError('odds')
                side='h' if norm(q['side'])==norm(match['home']) else 'a' if norm(q['side'])==norm(match['away']) else None
                if side is None: raise ValueError('side')
            except (KeyError,TypeError,ValueError): rejected['invalid_quote']+=1; continue
            home_line=line if side=='h' else -line
            groups[(str(q.get('market_id','938')),home_line)][side]={'key':side,'odds':price,'source_changed_at':iso(changed),'selection_id':q.get('sel_id'),'polled_at':iso(polled)}
        quotes=[]
        for (market_id,line),sides in groups.items():
            if set(sides)!= {'h','a'}: rejected['incomplete_pair']+=1; continue
            quotes.append({'source':'moviodds:bet365','market':'asian_handicap','period':'regular_time','line':line,
                'options':[sides['h'],sides['a']],'captured_at':iso(captured),'published_at':None,
                'source_checked_at':min(sides['h']['polled_at'],sides['a']['polled_at']),
                'time_basis':'provider_polled','is_opening':False,'opening_verified':False,
                'provider_market_id':market_id,'identity_evidence':evidence,
                'rules_status':'asian_quarter_line_observation_only','settlement_enabled':False})
        if quotes: bindings.append({'fixture_id':fixture['id'],'quotes':quotes,'evidence':evidence})
        else: rejected['no_usable_handicap']+=1
    return {'bindings':bindings,'rejected':dict(rejected),'provider':'moviodds','captured_at':iso(captured)}

def fetch_infer_odds(event_id,timeout=15):
    """Documented free GET, one event; caller owns polling budget/cache."""
    import re
    if not re.fullmatch(r'evt_[A-Za-z0-9]+',event_id): raise ValueError('来源比赛ID无效')
    url=f'https://api.infersports.dev/v1/events/{event_id}/odds?period=full_time&format=decimal'
    with urllib.request.urlopen(url,timeout=timeout) as response:
        raw=response.read(2_000_001)
        if len(raw)>2_000_000: raise ValueError('盘口响应超过限制')
    packet=json.loads(raw);packet['_received_at']=iso()
    return packet

def adapt_infer(event,packet,fixtures,now=None,aliases=None,id_map=None,max_age_seconds=180):
    """Require quote-level source timestamps; top-level stale=false is insufficient.

    as_of is preserved as provider-reported time, not proven last-polled time.
    Alternate lines have separate market IDs to avoid inventing line movements.
    """
    now=stamp(now);rejected=defaultdict(int)
    if packet.get('event_id')!=event.get('id'):return {'bindings':[],'rejected':{'identity_mismatch':1},'provider':'infersports'}
    league=INFER_LEAGUES.get(event.get('league',{}).get('id'))
    if not league:return {'bindings':[],'rejected':{'unverified_league':1},'provider':'infersports'}
    match={'id':event['id'],'league':league,'home':event['home_team']['name'],'away':event['away_team']['name'],
           'kickoff_utc':event['scheduled_at']}
    bound=bind_fixture(match,fixtures,aliases,id_map)
    if not bound:return {'bindings':[],'rejected':{'unmapped_or_ambiguous':1},'provider':'infersports'}
    if event.get('status')!='scheduled' or stamp(event['scheduled_at'])<=now or packet.get('stale'):
        return {'bindings':[],'rejected':{'started_or_stale':1},'provider':'infersports'}
    captured=stamp(packet.get('_received_at',now));quotes=[]
    for row in packet.get('odds',[]):
        if row.get('market_type')!='asian_handicap' or row.get('period')!='full_time':continue
        try:
            source_time=stamp(row['as_of']);line=float(row['line']);prices=row['prices'];components=row.get('handicap_components')
            if row.get('status')!='open' or row.get('format')!='decimal' or not row.get('bookmaker'):raise ValueError('state')
            if not 0<=(captured-source_time).total_seconds()<=max_age_seconds or not 0<=(now-captured).total_seconds()<=max_age_seconds:raise ValueError('stale')
            if not math.isfinite(line) or abs(line*4-round(line*4))>1e-8:raise ValueError('line')
            if int(round(line*4))%2 and (not components or len(components)!=2 or abs(sum(components)/2-line)>1e-8 or abs(components[0]-components[1])!=.5):raise ValueError('split')
            if set(prices)!={'home','away'} or any(not math.isfinite(v) or v<=1 for v in prices.values()):raise ValueError('prices')
        except (KeyError,TypeError,ValueError):rejected['invalid_or_stale_quote']+=1;continue
        quotes.append({'source':'infersports:'+row['bookmaker'],'market':'asian_handicap','period':'regular_time','line':line,
            'options':[{'key':'h','odds':prices['home']},{'key':'a','odds':prices['away']}],
            'captured_at':iso(captured),'published_at':None,'source_reported_at':iso(source_time),
            'time_basis':'provider_asof_unverified_semantics','provider_market_id':f"asian_handicap:{line}",
            'is_opening':False,'opening_verified':False,'identity_evidence':bound[1],
            'rules_status':'asian_quarter_line_observation_only','settlement_enabled':False})
    return {'bindings':[{'fixture_id':bound[0]['id'],'quotes':quotes,'evidence':bound[1]}] if quotes else [],'rejected':dict(rejected),'provider':'infersports'}
