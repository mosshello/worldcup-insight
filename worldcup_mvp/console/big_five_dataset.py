"""Explicit admission of audited big-five snapshots; never fits or selects models."""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path

from .big_five_history import SPECS, empty_boxscore_sides
from . import catalog as competitions
from .training import atomic_json

PROVIDERS={
 'eng.1':('GB','EPL','https://www.premierleague.com/'),
 'esp.1':('ES','SFL','https://www.laliga.com/'),
 'ger.1':('DE','GSL','https://www.bundesliga.com/'),
 'ita.1':('IT','ISA','https://www.legaseriea.it/'),
 'fra.1':('FR','FFL','https://www.lfp.fr/'),
}
POLICY='big-five-audited-research-v1'


def specs():
    result=[]
    for slug,(cid,name,lid,suffix) in SPECS.items():
        country,code,url=PROVIDERS[slug]
        result.append({'id':cid,'name':name,'country':country,'region':'UEFA','official_url':url,
            'rule_source':'https://www.theifab.com/laws/latest/the-duration-of-the-match/',
            'providers':{'sporttery':code,'espn':slug,'espn_id':lid},'training_enabled':True,
            'target_period':'regular_time','training_scope':'competition_only','cross_competition_features':False,
            'training_stage_policy':{str(y):['regular'] for y in range(2021,2027)},
            'prediction_stage_policy':{},
            'research_policy':{'sample_policy':POLICY,'usage_mode':'local_research',
                'rules_scope':'Main league regular-time, non-neutral matches only; all playoffs, administrative awards and abandoned matches excluded',
                'data_admission_only':True,'enhanced_features_trained':False,'rules_verified_at':'2026-11-05',
                'limitations':['历史可用时间采用明确七天假设','增强资料按实际覆盖','新增赛事尚无选用模型']},
            'seasons':[{'id':str(y),'label':f'{y}/{str(y+1)[2:]}','start':f'{y}-07-01','end':f'{y+1}-07-01',
                'format':'league','stages':['regular'],'shootout_separate':True,
                'expected_teams':18 if slug=='ger.1' or (slug=='fra.1' and y>=2023) else 20} for y in range(2021,2027)]})
    return result


def register():
    """Append new specs, refusing to replace a pre-existing definition or provider mapping."""
    existing=competitions.catalog();by_id={c['id']:c for c in existing};additions=[]
    codes={c.get('providers',{}).get('sporttery') for c in existing}
    for spec in specs():
        if spec['id'] in by_id:
            if by_id[spec['id']]!=spec:raise ValueError('existing_competition_definition_conflict')
        elif spec['providers']['sporttery'] in codes:raise ValueError('provider_competition_mapping_conflict')
        else:additions.append(spec)
    if additions:atomic_json(competitions.REGISTRY_PATH,existing+additions)
    return [s['id'] for s in specs()]


def evidence_time(value):
    timestamp=datetime.fromisoformat(value)
    if timestamp.tzinfo is None:raise ValueError('evidence_time_requires_timezone')
    return timestamp


def validate_prematch_evidence(row):
    start=evidence_time(row['kickoff'])
    market=row.get('prematch_market_archive')
    if market:
        cutoff=evidence_time(market['safe_cutoff_utc'])
        if cutoff>start or not market['quotes'] or market['purpose']!='market_reference_only':
            raise ValueError('unverified_market_reference')
        legs={'FT_1X2':{'Home','Draw','Away'},'FT_Asian_Handicap':{'Home','Away'},'FT_Total_Goals':{'Over','Under'}}
        for q in market['quotes']:
            if (evidence_time(q['quoted_at'])>=cutoff or not q.get('book')
                    or legs.get(q['market'])!=set(q['decimal_prices'])
                    or not all(type(v) in (int,float) and math.isfinite(v) and v>1 for v in q['decimal_prices'].values())
                    or (q['market']=='FT_1X2' and q['line'] is not None)
                    or (q['market']!='FT_1X2' and (type(q['line']) not in (int,float) or not math.isfinite(q['line'])))):
                raise ValueError('invalid_prematch_quote')
    observation=row.get('prematch_fpl_observation')
    if observation:
        seen=evidence_time(observation['captured_at'])
        if (row['competition_id']!='eng.premier' or not start-timedelta(hours=72)<=seen<start
                or observation['matchday_availability_complete'] is not False):
            raise ValueError('unverified_fpl_observation')
        players=set()
        for side in ('home','away'):
            group=observation['sides'][side]
            if group['team_id']!=row[side+'_id'] or group['listed_players']<11:
                raise ValueError('fpl_team_scope_conflict')
            for p in group['reported_flags']:
                if (not p['id'].startswith('fpl:'+row['season']+':') or p['id'] in players
                        or p['fpl_status'] not in ('i','d','s')
                        or (p['reported_news_at'] and evidence_time(p['reported_news_at'])>seen)):
                    raise ValueError('fpl_player_or_publication_conflict')
                players.add(p['id'])


def prepare(data,source_digest,authorization_reference):
    if not authorization_reference:raise ValueError('missing_local_research_request')
    cid=data['manifest']['competition_id']
    if cid not in {s[0] for s in SPECS.values()}:raise ValueError('not_big_five')
    rows=[];excluded=[];mappings={}
    for original in data['matches']:
        if original.get('competition_id')!=cid:raise ValueError('cross_competition_row')
        if original.get('training_eligible') is not True or original.get('period')!='regular_time' or original.get('neutral_site') is not False:
            excluded.append({'id':original['id'],'reason':original.get('exclusion_reason','regular_time_non_neutral_eligibility_unconfirmed')});continue
        if original.get('stage')!='regular-season':raise ValueError('unknown_source_stage')
        row={k:deepcopy(original[k]) for k in ('id','competition_id','season','home_id','away_id','home_name','away_name',
            'kickoff','period','home_goals','away_goals','home_half_goals','away_half_goals','source_url',
            'collected_at','result_available_at','availability_basis','neutral_site','postmatch_statistics','postmatch_lineups')}
        row.update(stage='regular',source_stage=original['stage'],training_eligible=True,research_snapshot_sha256=source_digest)
        row['summary_receipt']=deepcopy(original.get('summary_receipt',{}))
        row['feature_availability']='Post-match facts only; target match statistics and lineups forbidden in pre-match input'
        # Preserve supplementary measurements as evidence. They are not automatically used by any model.
        row['postmatch_xg_sources']={}
        supplements=original.get('supplemental_postmatch_facts',{})
        for source,fact in [('fotmob',original.get('external_postmatch_xg')),('understat_archive',supplements.get('understat_archive')),
                            ('understat_gallery_archive',supplements.get('understat_gallery_archive')),
                            ('scoreai_apifootball_display',supplements.get('scoreai_apifootball_display')),
                            ('pitchradar_understat_display',supplements.get('pitchradar_understat_display')),
                            ('pannadata_shot_xg_sum',supplements.get('pannadata_shot_xg_sum'))]:
            if fact:
                row['postmatch_xg_sources'][source]={k:deepcopy(fact[k]) for k in ('xg','source_receipt','source_id','source_match_id','identity_policy','identity_exception','archive_timestamp','archive_timezone','index_receipt','precision_decimal_places','source_kind','upstream_collection_time_verified','display_reported_fetched_at') if k in fact}
                row['postmatch_xg_sources'][source].update(approved_as_training_feature=False,historical_availability='unknown; explicit retrospective policy and evaluation required')
        if original.get('lineup_correction'):row['lineup_correction']=deepcopy(original['lineup_correction'])
        for field in ('correction','half_time_correction','half_time_review','eligibility_policy','supplemental_basics','supplemental_quality'):
            if field in original:row[field]=deepcopy(original[field])
        empty=empty_boxscore_sides(row['postmatch_statistics'])
        if empty and row.get('supplemental_quality',{}).get('espn_boxscore',{}).get('usable') is not False:
            row.setdefault('supplemental_quality',{})['espn_boxscore']={
                'usable':False,'scope':'postmatch_statistics','reason':'empty_side_in_team_statistics','empty_sides':empty}
        if original.get('additional_postmatch_statistics'):
            row['additional_postmatch_statistics']=deepcopy(original['additional_postmatch_statistics'])
            for fact in row['additional_postmatch_statistics'].values():
                fact['approved_as_training_feature']=False
        for key in ('postmatch_shot_evidence','reported_pregame_lineup',
                    'prematch_market_archive','prematch_fpl_observation'):
            if key in original:
                row[key]=deepcopy(original[key])
                row[key]['approved_as_training_feature']=False
        if 'prematch_market_archive' in row or 'prematch_fpl_observation' in row:
            validate_prematch_evidence(row)
        for side in ('home','away'):
            provider_id=row[side+'_id'].removeprefix('espn:')
            mappings[provider_id]={'competition_id':cid,'provider':'espn','provider_id':provider_id,'team_id':row[side+'_id']}
        rows.append(row)
    return {'manifest':{'usage_mode':'local_research','local_training_authorized':True,
        'authorization_reference':authorization_reference,'rights_status':'unverified','commercial_use_permitted':False,
        'source_url':'https://site.api.espn.com/apis/site/v2/sports/soccer/',
        'source_digest':source_digest,'source_version':POLICY,'source_competition':data['manifest']['source'],
        'source_manifest':{'research_snapshot_sha256':source_digest,'coverage':data['coverage'],
            'enhanced_features_trained':False,'source_archive_receipt':data['manifest'].get('source_archive_receipt'),
            'gallery_commit':data['manifest'].get('gallery_commit'),
            'incremental_refresh':data['manifest'].get('incremental_refresh'),
            'date_exception_review':data['manifest'].get('date_exception_review'),
            'scoreai_supplement':data['manifest'].get('scoreai_supplement'),
            'pitchradar_supplement':data['manifest'].get('pitchradar_supplement'),
            'source_quality_audit':data['manifest'].get('source_quality_audit'),
            **({'official_reconciliation':deepcopy(data['manifest']['official_reconciliation'])}
               if 'official_reconciliation' in data['manifest'] else {}),
            **({'event_evidence_update':deepcopy(data['manifest']['event_evidence_update'])}
               if 'event_evidence_update' in data['manifest'] else {}),
            **({'half_time_label_audit':deepcopy(data['manifest']['half_time_label_audit'])}
               if 'half_time_label_audit' in data['manifest'] else {}),
            **({'club_personnel_catalog':deepcopy(data['manifest']['club_personnel_catalog'])}
               if 'club_personnel_catalog' in data['manifest'] else {}),
            'data_admission_only':True},'sample_policy':POLICY,
        'availability_policy':'historical_7_day_lag_or_observed_assumption; supplemental xG not point-in-time verified',
        'quality_basis':'Scoreboard-summary identity agreement; explicit source corrections and exclusions; xG archive independently namespaced',
        'collection_status':'collected','collection_errors':[],'excluded':excluded+data.get('excluded_fixtures',[]),
        'continuity_policy':'No cross-competition training, eligibility or inferred strength sharing'},
        'matches':rows,'team_mappings':list(mappings.values())}


def import_snapshots(root,authorization_reference):
    from .model_registry import import_competition, validate_scope
    register();prepared=[]
    for slug,(cid,*_) in SPECS.items():
        folder=Path(root)/slug;pointer=json.loads((folder/'latest.json').read_text())
        path=(folder/pointer['file']).resolve()
        if not path.is_relative_to((folder/'snapshots').resolve()):raise ValueError('invalid_snapshot_path')
        body=path.read_bytes()
        if hashlib.sha256(body).hexdigest()!=pointer['sha256']:raise ValueError('snapshot_digest_mismatch')
        value=prepare(json.loads(body),pointer['sha256'],authorization_reference)
        validate_scope({**value,'manifest':{**value['manifest'],'competition_id':cid,'training_scope':'competition_only'}},cid)
        prepared.append((cid,value))
    # All five validated before any dataset pointer is written. Existing other competitions are untouched.
    return [import_competition(value,cid) for cid,value in prepared]
