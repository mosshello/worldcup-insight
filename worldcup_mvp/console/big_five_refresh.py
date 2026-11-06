"""Incremental historical facts. Existing verified rows are immutable parents.

A changed old result is a review conflict, never an automatic label rewrite.
This collector creates research snapshots only; import remains explicit.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from .big_five_history import SPECS, fetch, parse_event, enrich, coverage, date, evidence_counts


def collect_increment(slug, base, folder, season, fetcher=None):
    cid,_,lid,_=SPECS[slug]
    if base['manifest']['competition_id']!=cid or any(r['competition_id']!=cid for r in base['matches']):
        raise ValueError('cross_competition_parent')
    if not base['matches'] or len({r['id'] for r in base['matches']})!=len(base['matches']):
        raise ValueError('invalid_parent_matches')
    fetcher=fetcher or fetch;folder=Path(folder)
    board,receipt=fetcher(slug,f'scoreboard?dates={season}&limit=1000',folder/'raw')
    if not any(str(l.get('id'))==lid and l.get('slug')==slug for l in board.get('leagues',[])):
        raise ValueError('scoreboard_competition_mismatch')
    if not isinstance(board.get('events'),list) or not 0<len(board['events'])<1000:
        raise ValueError('empty_or_truncated_scoreboard')
    observed=receipt['observed_at']
    if date(observed)>datetime.now(timezone.utc):raise ValueError('future_receipt')
    result=deepcopy(base);existing={r['id']:r for r in base['matches']};seen=set();added=[];checked=0;excluded=[];receipts=[receipt]
    for e in board['events']:
        eid='espn:'+str(e['id'])
        if eid in seen:raise ValueError('duplicate_source_event')
        seen.add(eid)
        if e.get('season',{}).get('year')!=season:continue
        comp=e['competitions'][0];state=comp.get('status',{}).get('type',{})
        old=existing.get(eid)
        if not state.get('completed'):
            if old and old['training_eligible']:raise ValueError('completed_result_regressed:'+eid)
            continue
        row=parse_event(slug,e,observed)
        if old:
            if not old['training_eligible']:
                excluded.append({'id':eid,'reason':'prior_quarantine_preserved'});continue
            keys=('competition_id','season','period','home_id','away_id','home_goals','away_goals','kickoff')
            if any(row[k]!=old[k] for k in keys):raise ValueError('existing_fixture_changed_requires_review:'+eid)
            checked+=1;continue
        detail,r=fetcher(slug,'summary?event='+str(e['id']),folder/'raw');receipts.append(r)
        row=enrich(row,detail,r)
        if not row['training_eligible'] or row['period']!='regular_time':
            excluded.append({'id':eid,'reason':'new_fixture_not_eligible','record':row});continue
        identity=('season','home_id','away_id','kickoff')
        if any(all(row[k]==other[k] for k in identity) for other in [*existing.values(),*added]):
            raise ValueError('duplicate_fixture_under_new_id')
        added.append(row)
    result['matches'].extend(added);result['matches'].sort(key=lambda r:(r['kickoff'],r['id']))
    # No facts or original receipt timestamps on retained rows are refreshed by this check.
    report={'competition_id':cid,'season':season,'source_observed_at':observed,'existing_checked':checked,
            'added':len(added),'retained':len(existing),'excluded':excluded,'receipts':receipts,
            'refresh_scope':'completed fixtures in requested season; existing supplementary facts retained'}
    if added:
        prior={c['season']:c for c in base.get('coverage',[])}
        result['coverage']=[{**prior.get(c['season'],{}),**c} for c in coverage(slug,result['matches'])]
        for c in result['coverage']:
            group=[r for r in result['matches'] if r['season']==c['season'] and r['training_eligible']]
            counts=evidence_counts(group)
            c['any_source_xg_both']=counts['xg_both']
            c['any_source_lineups_both']=counts['starting_eleven_both']
        evidence=result['manifest'].get('event_evidence_update')
        if evidence is not None:
            evidence['usable_field_coverage']=evidence_counts(result['matches'])
            evidence['quarantined_espn_boxscores']=sum(r.get('supplemental_quality',{}).get('espn_boxscore',{}).get('usable') is False for r in result['matches'] if r['training_eligible'])
        result['manifest']['incremental_refresh']=report
    return result,report


def refresh(root, season):
    root=Path(root);run=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output=root/'refresh'/run;output.mkdir(parents=True)
    prepared=[];reports=[]
    # Verify every parent and complete all collections before publishing any latest pointer.
    for slug in SPECS:
        parent=json.loads((root/slug/'latest.json').read_text());path=(root/slug/parent['file']).resolve()
        if not path.is_relative_to((root/slug/'snapshots').resolve()):raise ValueError('invalid_parent_path')
        body=path.read_bytes()
        if hashlib.sha256(body).hexdigest()!=parent['sha256']:raise ValueError('parent_integrity_failure')
        data,report=collect_increment(slug,json.loads(body),root/slug,season)
        report['parent_sha256']=parent['sha256'];reports.append(report)
        if report['added']:
            data['manifest']['refresh_parent_sha256']=parent['sha256']
            body=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
            sha=hashlib.sha256(body).hexdigest();target=root/slug/'snapshots'/(sha+'.json')
            if target.exists() and target.read_bytes()!=body:raise ValueError('snapshot_collision')
            if not target.exists():target.write_bytes(body)
            prepared.append((slug,{'sha256':sha,'file':str(target.relative_to(root/slug)),'rows':len(data['matches'])}))
    for slug,p in prepared:(root/slug/'latest.json').write_text(json.dumps(p,indent=2))
    (output/'audit.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf8')
    return output,reports
