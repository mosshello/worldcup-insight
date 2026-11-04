"""Explicit ESPN source/competition adapter; no automatic import or selection.
UEFA qualifying is separate and opt-in. Stage and era remain evaluation strata.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import uuid
from .espn_history import _date, _teams
BASE = 'https://site.api.espn.com/apis/site/v2/sports/soccer/'
POLICY = 'espn-competition-regular-home-away-research-v1'
SOURCE_SPECS = {
    'uefa.champions': {'league_id':'775','competition_id':'uefa.ucl','stages':('group-stage','league-phase')},
    'uefa.europa': {'league_id':'776','competition_id':'uefa.uel','stages':('group-stage','league-phase')},
    'eng.2': {'league_id':'3914','competition_id':'eng.championship','stages':('regular-season',)},
    'uefa.champions_qual': {'league_id':'19874','competition_id':'uefa.ucl','qualifying':True,
        'stages':('first-round','second-round','third-round','playoff-round')},
}


def fetch(source_slug, endpoint, folder):
    """Retain source bytes and actual HTTP/transport outcome; safe bounded retry externally."""
    if source_slug not in SOURCE_SPECS or not endpoint.startswith(('scoreboard?dates=','summary?event=')):
        raise ValueError('unsupported_source_endpoint')
    url=BASE+source_slug+'/'+endpoint
    root=Path(folder);root.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256(url.encode()).hexdigest()
    ttl=180 if endpoint.startswith('scoreboard?dates='+str(datetime.now(timezone.utc).year)) else 86400
    for meta in sorted(root.glob(key+'*.receipt.json'),key=lambda p:p.stat().st_mtime,reverse=True):
        try:
            receipt=json.loads(meta.read_text(encoding='utf8'));raw=Path(receipt['raw_path']).resolve()
            age=(datetime.now(timezone.utc)-_date(receipt['observed_at'])).total_seconds()
            if (receipt.get('status')=='succeeded' and receipt.get('http_status')=='200'
                    and receipt.get('url')==url and raw.is_relative_to(root.resolve()) and 0<=age<=ttl
                    and hashlib.sha256(raw.read_bytes()).hexdigest()==receipt.get('sha256')):
                return json.loads(raw.read_bytes()),receipt
        except (ValueError,TypeError,KeyError,OSError):pass
    attempt=key+'.'+uuid.uuid4().hex
    raw=root/(attempt+'.raw');meta=root/(attempt+'.receipt.json')
    receipt={'url':url,'observed_at':datetime.now(timezone.utc).isoformat(),'raw_path':str(raw)}
    try:
        result=subprocess.run(['curl.exe','-sS','-L','--max-time','35','--write-out','%{http_code}',url,'-o',str(raw)],capture_output=True,timeout=40)
        receipt.update(http_status=result.stdout.decode().strip(),transport_exit=result.returncode)
        if raw.exists():receipt['sha256']=hashlib.sha256(raw.read_bytes()).hexdigest()
        if result.returncode or receipt['http_status']!='200':raise ValueError('source_request_failed')
        value=json.loads(raw.read_bytes())
        receipt['status']='succeeded'
        return value,receipt
    except Exception as exc:
        receipt.update(status='failed',error=str(exc));raise
    finally:
        meta.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf8')


def collect_competition(competition_id, years, directory, progress=None, fetcher=None, allow_qualifying=False, qualifying_years=None):
    """Runtime adapter. fetcher(source_slug, endpoint, raw_dir) -> (JSON, receipt).

    A failed request raises with its raw receipt left on disk; no partial success.
    The returned dataset is a candidate, never automatically imported or selected.
    """
    primary=next((s for s,v in SOURCE_SPECS.items() if v['competition_id']==competition_id and not v.get('qualifying')),None)
    if primary is None:raise ValueError('unsupported_competition')
    sources=[primary]
    if allow_qualifying:
        if competition_id!='uefa.ucl':raise ValueError('qualifying_source_not_verified')
        if (not isinstance(qualifying_years,list) or not qualifying_years
                or any(type(y) is not int or y not in years for y in qualifying_years)):
            raise ValueError('explicit_qualifying_years_required')
        sources.append('uefa.champions_qual')
    fetcher=fetcher or fetch
    root=Path(directory);root.mkdir(parents=True,exist_ok=True)
    prepared=[];receipts=[]
    for source in sources:
        boards=[];summaries={}
        source_years=qualifying_years if SOURCE_SPECS[source].get('qualifying') else years
        for year in sorted(set(source_years)):
            board,receipt=fetcher(source,f'scoreboard?dates={year}&limit=1000',root/'raw')
            verify_receipt_time(receipt)
            receipts.append(receipt)
            if not isinstance(board.get('events'),list) or not board['events']:
                raise ValueError('source_empty_or_invalid')
            if len(board['events'])>=1000:
                raise ValueError('source_response_possibly_truncated')
            boards.append(board)
            if progress:progress({'phase':'scoreboards','source':source,'completed_year':year,'receipt_count':len(receipts)})
        events={str(e['id']):e for b in boards for e in b['events']}
        for eid,event in events.items():
            if (event.get('season',{}).get('slug') not in SOURCE_SPECS[source]['stages']
                    or event.get('competitions',[{}])[0].get('status',{}).get('type',{}).get('name')!='STATUS_FULL_TIME'):
                continue
            summary,receipt=fetcher(source,'summary?event='+eid,root/'raw')
            verify_receipt_time(receipt)
            receipts.append(receipt);summaries[eid]={'header':summary.get('header',{})}
            if progress:progress({'phase':'summaries','source':source,'completed_event':eid,'receipt_count':len(receipts)})
        data=prepare(source,boards,summaries,datetime.now(timezone.utc).isoformat(),
                     'Explicit competition ingestion '+root.name,allow_qualifying)
        failures={'summary_identity_unverified','summary_identity_mismatch','summary_result_conflict',
                  'competition_identity_mismatch','fixture_identity_mismatch','score_invalid'}
        if any(e['reason'] in failures for e in data['manifest']['excluded']):
            (root/'quarantined.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
            raise ValueError('source_verification_incomplete')
        prepared.append(data)
    result=prepared[0]
    if len(prepared)>1:
        result['matches']+=prepared[1]['matches']
        result['manifest']['excluded']+=prepared[1]['manifest']['excluded']
        result['manifest']['unique_source_events']+=prepared[1]['manifest']['unique_source_events']
        result['manifest']['qualifying_enabled']=True
        result['team_mappings']=list({(m['provider'],m['provider_id']):m for d in prepared for m in d['team_mappings']}.values())
    rows=result['matches'];rows.sort(key=lambda r:(r['kickoff'],r['id']))
    if not rows:raise ValueError('no_eligible_history')
    if len({r['id'] for r in rows})!=len(rows):raise ValueError('cross_source_duplicate_fixture')
    result['manifest'].update(source_receipts=receipts,source_competitions=sources,collection_status='completed_requested_scope',
                              qualifying_years=sorted(set(qualifying_years or [])),
                              prepared_at=datetime.now(timezone.utc).isoformat())
    result['coverage']={'seasons':dict(Counter(r['season'] for r in rows)),
        'teams':dict(Counter(r[s] for r in rows for s in ('home_id','away_id'))),
        'excluded':dict(Counter(e['reason'] for e in result['manifest']['excluded']))}
    (root/'candidate.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return result


def verify_receipt_time(receipt):
    try:
        if _date(receipt['observed_at'])>datetime.now(timezone.utc):
            raise ValueError('future')
    except (KeyError,TypeError,ValueError,AttributeError):
        raise ValueError('source_receipt_time_invalid') from None


def prepare(source_slug, boards, summaries, observed_at, request_reference, allow_qualifying=False):
    spec=SOURCE_SPECS[source_slug]
    if not request_reference:raise ValueError('research_request_required')
    rows,issues,seen=[],[],{}
    for board in boards:
        if not any(str(l.get('id'))==spec['league_id'] and l.get('slug')==source_slug for l in board.get('leagues',[])):
            raise ValueError('scoreboard_competition_mismatch')
        for event in board.get('events',[]):
            eid=str(event.get('id'))
            digest=hashlib.sha256(json.dumps(event,sort_keys=True).encode()).hexdigest()
            if eid in seen:
                if seen[eid]!=digest:raise ValueError('duplicate_event_conflict')
                continue
            seen[eid]=digest
            summary=summaries.get(eid)
            reference={'event_sha256':digest,'summary_header_sha256':hashlib.sha256(json.dumps((summary or {}).get('header'),sort_keys=True).encode()).hexdigest()}
            row,issue=parse_event(event,summary,observed_at,source_slug,reference,allow_qualifying)
            if issue:issues.append(issue)
            else:rows.append(row)
    rows.sort(key=lambda r:(r['kickoff'],r['id']))
    fixtures=[(r['home_id'],r['away_id'],r['kickoff']) for r in rows]
    if len(set(fixtures))!=len(fixtures):raise ValueError('duplicate_fixture')
    teams={r[s] for r in rows for s in ('home_id','away_id')}
    return {'manifest':{'usage_mode':'local_research','local_training_authorized':True,
        'authorization_reference':request_reference,'rights_status':'unverified','commercial_use_permitted':False,
        'source_url':BASE+source_slug+'/scoreboard','prepared_at':observed_at,'sample_policy':POLICY,
        'source_competition':source_slug,'qualifying_enabled':bool(spec.get('qualifying') and allow_qualifying),
        'continuity_policy':'Same canonical competition only; stage and rules_era retained for stratified evaluation',
        'quality_basis':'Single ESPN provider; scoreboard/summary identity and score consistency, not independent dual-source validation',
        'availability_policy':'historical_7_day_lag_or_observed','excluded':issues,'unique_source_events':len(seen)},
        'matches':rows,'team_mappings':[{'competition_id':spec['competition_id'],'provider':'espn','provider_id':t.split(':',1)[1],'team_id':t} for t in sorted(teams)],
        'coverage':{'seasons':dict(Counter(r['season'] for r in rows)),
            'teams':dict(Counter(r[s] for r in rows for s in ('home_id','away_id'))),
            'excluded':dict(Counter(i['reason'] for i in issues))}}

def parse_event(event, summary, observed_at, source_slug, raw_reference=None, allow_qualifying=False):
    spec = SOURCE_SPECS.get(source_slug)
    if spec is None:
        return None, {"id": str(event.get("id")), "reason": "unsupported_source"}
    """Return (row, exclusion). Only a strict supported sample view is emitted."""
    eid = str(event.get('id', ''))
    try:
        if not eid or event.get('uid') != f"s:600~l:{spec['league_id']}~e:{eid}":
            raise ValueError('competition_identity_mismatch')
        comps = event.get('competitions', [])
        if len(comps) != 1 or str(comps[0].get('id')) != eid:
            raise ValueError('fixture_identity_mismatch')
        comp = comps[0]
        status = comp.get('status', {})
        if (status.get('type', {}).get('name') != 'STATUS_FULL_TIME'
                or status.get('type', {}).get('completed') is not True
                or status.get('period') != 2
                or comp.get('format', {}).get('regulation', {}).get('periods') != 2):
            raise ValueError('regular_time_result_unconfirmed')
        season = event.get('season', {})
        if type(season.get('year')) is not int or season.get('slug') not in spec['stages']:
            raise ValueError('stage_rules_unsupported')
        if spec.get('qualifying') and not allow_qualifying:
            raise ValueError('qualifying_policy_not_enabled')
        if source_slug.startswith('uefa.') and not spec.get('qualifying'):
            expected = 'group-stage' if season['year'] < 2024 else 'league-phase'
            if season['slug'] != expected:
                raise ValueError('era_stage_conflict')
        teams = _teams(comp)
        kickoff, observed = _date(event['date']), _date(observed_at)
        if kickoff != _date(comp['date']) or observed < kickoff + timedelta(minutes=90):
            raise ValueError('result_time_invalid')
        header = (summary or {}).get('header', {})
        detail = header.get('competitions', [])
        if header.get('uid') != event['uid'] or len(detail) != 1 or str(detail[0].get('id')) != eid:
            raise ValueError('summary_identity_unverified')
        sc = detail[0]
        st = _teams(sc)
        if (_date(sc['date']) != kickoff or header.get('season', {}).get('year') != season['year']
                or any(str(st[s]['team']['id']) != str(teams[s]['team']['id']) for s in teams)):
            raise ValueError('summary_identity_mismatch')
        if spec.get('qualifying'):
            leg=(comp.get('leg') or {}).get('value')
            if leg not in (1,2) or (sc.get('leg') or {}).get('value') != leg:
                raise ValueError('qualifying_leg_unverified')
        if sc.get('neutralSite') is not False:
            raise ValueError('neutral_site' if sc.get('neutralSite') is True else 'neutral_site_unknown')
        ss = sc.get('status', {})
        if (ss.get('type', {}).get('name') != 'STATUS_FULL_TIME'
                or ss.get('type', {}).get('completed') is not True
                or ('period' in ss and ss['period'] != 2)):
            raise ValueError('summary_result_conflict')
        goals = {}
        for side in teams:
            score = teams[side].get('score')
            if not isinstance(score, str) or not score.isdecimal() or not 0 <= int(score) <= 30:
                raise ValueError('score_invalid')
            if str(st[side].get('score')) != score:
                raise ValueError('summary_result_conflict')
            goals[side] = int(score)
        assumed = kickoff + timedelta(days=7)
        row = {'id': 'espn:'+eid, 'competition_id': spec['competition_id'],
               'season': str(season['year']), 'stage': ('qualifying:' if spec.get('qualifying') else '') + season['slug'],
               'kickoff': kickoff.isoformat(), 'period': 'regular_time', 'neutral_site': False,
               'source_url': BASE+source_slug+'/summary?event='+eid, 'collected_at': observed.isoformat(),
               'result_available_at': min(assumed, observed).isoformat(),
               'availability_basis': 'assumed_7_day_lag' if assumed < observed else 'observed_at_collection',
               'training_eligible': True, 'sample_policy': POLICY, 'source_competition':source_slug, 'source_stage':season['slug'], 'leg':comp.get('leg'),
               'rules_era': ('qualifying' if spec.get('qualifying') else ('league-phase' if season['year'] >= 2024 else 'group-stage')) if source_slug.startswith('uefa.') else 'regular-season',
               'raw_reference': raw_reference,
               'extra_time': None, 'shootout': None, 'qualification': None}
        for side in teams:
            row[side+'_id'] = 'espn:'+str(teams[side]['team']['id'])
            row[side+'_name'] = teams[side]['team'].get('displayName')
            row[side+'_goals'] = goals[side]
            row[side+'_half_goals'] = None
        return row, None
    except (ValueError, KeyError, TypeError) as exc:
        return None, {'id': 'espn:'+eid, 'reason': str(exc)}
