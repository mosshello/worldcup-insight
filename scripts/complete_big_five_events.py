"""Append reviewed shot events, red-card displays and dated lineup reports."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collections import Counter
from datetime import datetime, timezone
from html import unescape
import json
import re
from zoneinfo import ZoneInfo
import pyarrow.parquet as pq

from scripts.big_five_shot_archive import (
    EXCEPTIONS, index_shots, reviewed_archive_fact, shot_evidence, verified_parquet,
)
from scripts.collect_big_five_gallery import blob_sha, parse_page
from scripts.reconcile_big_five import (
    STATS_REVIEWS, STATS_URLS, STATS_SOURCE, verified_html, parse_club_page, field_coverage,
)
from scripts.supplement_big_five import read, digest, deduplicate, LEAGUES
from worldcup_mvp.console.training import atomic_json

LIVE = {
    'espn:643971': ('madrid-live', 'real-madrid-elche-en-directo-laliga-santander-hoy-en-vivo-n',
        'Real Madrid - Elche', {
            'home': ('8d544cef78273ea0ab3882d2bde6aa7b', 'ONCE DEL REAL MADRID',
                ['Lunin','Carvajal','Militao','Nacho','Alaba','Valverde','Camavinga','Ceballos','Asensio','Benzema','Rodrygo'],
                [7194,2260,7829,2247,391,6241,7359,2446,2117,2370,8015]),
            'away': ('06195aec5618324182d7f754671cf6fe', 'ONCE DEL ELCHE',
                ['Edgar Badía','Enzo Roco','Diego González','Magallán','Carmona','Guti','Gumbau','Clerc','Eze Ponce','Nteka','Fidel'],
                [8890,2109,2071,8029,9809,8894,2609,5101,2057,9813,4146])}),
    'espn:643974': ('valencia-live', 'valencia-athletic-en-directo-laliga-santander-en-vivo-n',
        'Valencia - Athletic', {
            'home': ('1db630e03fde30d2be7dca1db69249c5','Saldrán de inicio los siguientes:',
                ['Mamardashvili','Foulquier','Cömert','Diakhaby','Gayà','Ilaix Moriba','Musah','Almeida','Samu Castillejo','Cavani','Samuel Lino'],
                [9693,2509,10382,5666,2505,9243,8771,11057,2303,3294,10943]),
            'away': ('35b2d71cacc83151a16b21b08a4221d3','El Athletic saldrá con los siguientes de inicio:',
                ['Unai Simón','De Marcos','Yeray','Vivian','Lekue','Sancet','Vesga','Nico Williams','Muniaín','Berenguer','Raúl García'],
                [5185,2452,5062,6766,2403,7779,5077,9211,2401,5091,2400])}),
}
JUV = 'https://www.juventus.com/it/news/articoli/'


def body_receipt(work, name, url):
    path = work / (name + '.html')
    receipt = read(path.with_suffix('.html.receipt.json'))
    body = path.read_bytes()
    if (receipt['url'] != url or receipt['http_status'] != '200' or receipt['transport_exit'] != 0
            or digest(body) != receipt['sha256']):
        raise ValueError('unverified_response:' + name)
    return body, receipt


def visible(body):
    html = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', ' ', body.decode('utf8'), flags=re.I|re.S)
    return re.sub(r'\s+', ' ', unescape(re.sub('<[^>]+>', ' ', html))).strip()


def displayed_red_cards(html, home, away):
    html = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', '', html, flags=re.S|re.I)
    pattern = (r'MkFootballModuleContentMatchStatsTeamStatsRowStats"><div[^>]*>Tarjetas rojas:'
               + re.escape(home) + r' (\d+) versus ' + re.escape(away) + r' (\d+)</div>')
    values = re.findall(pattern, unescape(html))
    if len(values) != 1:
        raise ValueError('missing_or_ambiguous_displayed_red_cards')
    return [int(v) for v in values[0]]


def admit_cards(row, review, old):
    eid, name, source_id, home, away = review
    html, receipt = verified_html(old / 'reconciliation', name, STATS_URLS[name])
    prior = row['additional_postmatch_statistics'][STATS_SOURCE]
    game = parse_club_page(html)['game']
    if (row['id'] != eid or row['competition_id'] != 'esp.laliga' or row['season'] != '2022'
            or prior['source_receipt'] != receipt or game['id'] != source_id
            or prior['source_id'] != 'llt:' + source_id or game['period']['type'] != 'FullTime'
            or datetime.fromisoformat(game['time']) != datetime.fromisoformat(row['kickoff'])
            or [game['homeScore'], game['awayScore']] != [row['home_goals'], row['away_goals']]):
        raise ValueError('official_card_identity_conflict')
    pair = displayed_red_cards(html, home, away)
    for side, count in zip(('home', 'away'), pair):
        existing = prior['statistics'][side].get('redCards')
        if existing is not None and existing != count:
            raise ValueError('display_conflicts_with_reported_total')
    row['additional_postmatch_statistics']['club_official_rendered_cards'] = {
        'source_id': prior['source_id'], 'source_receipt': receipt,
        'statistics': {s: {'redCards': v} for s, v in zip(('home','away'), pair)},
        'basis': 'explicit_both_team_values_in_match_statistics_display',
        'approved_as_training_feature': False}


def parse_lineup_update(entry, spec, url, kickoff, players):
    link_id, intro, names, player_ids = spec
    if (entry['@type'] != 'BlogPosting' or entry['url'] != url + '#' + link_id
            or len(names) != 11 or len(set(player_ids)) != 11):
        raise ValueError('lineup_update_identity_conflict')
    start = datetime.fromisoformat(kickoff)
    published, modified = (datetime.fromisoformat(entry[k]) for k in ('datePublished','dateModified'))
    if (any(d.tzinfo is None for d in (start, published, modified))
            or not published <= modified < start or (start - published).total_seconds() > 6 * 3600):
        raise ValueError('pregame_update_time_unverified')
    body = unescape(entry['articleBody']).replace('\\xa0',' ').replace('\xa0',' ')
    if body.count(intro) != 1:
        raise ValueError('announced_lineup_missing')
    listed = body.split(intro, 1)[1].split('.', 1)[0].strip()
    actual = [v.strip() for v in re.split(r'\s*[,;]\s*|\s+y\s+', listed)]
    if actual != names:
        raise ValueError('announced_players_conflict')
    ids = ['understat:' + str(v) for v in player_ids]
    if set(ids) != {p['id'] for p in players} or len(players) != 11:
        raise ValueError('starter_roster_identity_conflict')
    return {'source_update_url': entry['url'], 'published_at': published.isoformat(),
            'modified_at': modified.isoformat(), 'usable_no_earlier_than': modified.astimezone(timezone.utc).isoformat(),
            'announced_players': [{'name': name, 'mapped_id': pid} for name,pid in zip(names,ids)]}


def admit_lineups(work, row):
    file, slug, teams, sides = LIVE[row['id']]
    url = 'https://as.com/futbol/primera/' + slug + '/'
    body, receipt = body_receipt(work, file, url)
    blogs = []
    for raw in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', body.decode(), re.S):
        doc = json.loads(raw)
        if isinstance(doc, dict) and doc.get('@type') == 'LiveBlogPosting':
            blogs.append(doc)
    if len(blogs) != 1 or teams not in blogs[0]['about']['name'] or blogs[0]['url'] != url:
        raise ValueError('liveblog_identity_conflict')
    if row['competition_id'] != 'esp.laliga' or row['season'] != '2022' or row['period'] != 'regular_time':
        raise ValueError('unexpected_lineup_target')
    records = {}
    for side, spec in sides.items():
        entries = [e for e in blogs[0]['liveBlogUpdate'] if e['url'] == url + '#' + spec[0]]
        if len(entries) != 1:
            raise ValueError('liveblog_update_not_unique')
        records[side] = parse_lineup_update(entries[0], spec, url, row['kickoff'], row['supplemental_basics']['lineups'][side])
    return {'sides': records, 'both_available_no_earlier_than': max(v['usable_no_earlier_than'] for v in records.values()),
            'basis': 'retrospective_source_reported_update_times', 'source_receipt': receipt,
            'contemporaneous_local_snapshot': False, 'approved_as_training_feature': False}


def torino_xg(data, old, work):
    row = next(r for r in data['matches'] if r['id'] == 'espn:737158')
    n = old / 'next'; tree = read(n / 'iyush-tree.json')
    pinned = '3a6f1469364adc98ce3ed204bc9001f5f36030cc'
    index_path = 'football/data/gallery/2025.json'
    index = n / 'football_data_gallery_2025.json'; ir = read(index.with_suffix('.json.receipt.json'))
    entries = {e['path']: e['sha'] for e in tree['tree']}
    if (tree['sha'] != pinned or ir['commit'] != pinned or ir['http_status'] != '200'
            or digest(index.read_bytes()) != ir['sha256'] or blob_sha(index.read_bytes()) != entries[index_path]):
        raise ValueError('gallery_index_integrity')
    matches = [m for m in read(index)['matches'] if m['k'] == 'Serie_A-2025-us:30222']
    if len(matches) != 1:
        raise ValueError('gallery_match_not_unique')
    match = matches[0]; path = 'football/gallery/match/' + match['u'] + '.html'
    body, receipt = body_receipt(work, 'torino-gallery', 'https://raw.githubusercontent.com/Spyyy004/iyush.dev/' + pinned + '/' + path)
    if receipt['commit'] != pinned or blob_sha(body) != entries[path]:
        raise ValueError('gallery_blob_integrity')
    preview, pr = body_receipt(work, 'torino-preview', JUV + 'matchday-torino-juventus-precedenti-numeri-e-stato-di-forma')
    report, rr = body_receipt(work, 'torino-report', JUV + 'serie-a-torino-juventus-il-tabellino')
    if ('24 maggio 2026, alle ore 20:45' not in visible(preview) or '38ª giornata della Serie A 2025/2026' not in visible(preview)
            or '24 maggio 2026' not in visible(report) or '24’, 54’ Vlahovic (J), 60’ Casadei (T), 84’ Adams (T)' not in visible(report)):
        raise ValueError('official_schedule_or_scorers_conflict')
    local = datetime(2026, 5, 24, 20, 45, tzinfo=ZoneInfo('Europe/Rome'))
    if (row['competition_id'] != 'ita.serie_a' or row['season'] != '2025' or row['period'] != 'regular_time'
            or not row['training_eligible'] or row.get('supplemental_postmatch_facts') or row.get('external_postmatch_xg')
            or [row['home_name'],row['away_name']] != [match['h'],match['a']] or match['c'] != 'Serie_A'
            or [row['home_goals'],row['away_goals']] != [match['hg'],match['ag']] or [match['hg'],match['ag']] != [2,2]
            or [match['hc'],match['ac']] != ['us:113','us:98'] or match['aet'] is not None or match['so'] is not None
            or any(data['understat_mapping']['understat:' + match[k+'c'][3:]]['espn_id'] != row[side+'_id']
                   for k,side in [('h','home'),('a','away')])
            or sum(r['season']=='2025' and r['home_id']==row['home_id'] and r['away_id']==row['away_id'] for r in data['matches'])!=1
            or row['kickoff'] != '2026-05-24T19:45:00+00:00' or datetime.fromisoformat(match['t']) != local
            or match['g'] != 'a24.1 a54.2 h60.2 h84.2'):
        raise ValueError('torino_fixture_conflict')
    row.setdefault('supplemental_postmatch_facts', {})['understat_gallery_archive'] = {
        'source_id': 'understat:30222', 'source_receipt': receipt, 'index_receipt': ir, 'xg': parse_page(body,match),
        'source_kind': 'third_party_display_archive_of_understat', 'precision_decimal_places': 2,
        'identity_policy': 'reviewed_torino_schedule_and_goal_sequence_v1',
        'identity_exception': {'canonical_kickoff':row['kickoff'],'source_kickoff':match['t'],
           'official_scheduled_local':'2026-05-24 20:45 Europe/Rome','preview_receipt':pr,'report_receipt':rr,
           'canonical_kickoff_conflict_preserved':True, 'actual_kickoff_instant_verified':False},
        'approved_as_training_feature': False}


def quarantine_empty_box(row):
    stats = row['postmatch_statistics']
    if not all(stats.get(side, {}).get(key) == 0 for side in ('home', 'away')
               for key in ('possessionPct', 'totalShots')):
        return False
    row.setdefault('supplemental_quality', {})['espn_boxscore'] = {
        'review_required': True, 'usable': False, 'scope': 'postmatch_statistics',
        'reason': 'both_possession_and_shots_zero_for_both_teams'}
    shots = row.get('postmatch_shot_evidence')
    if shots:
        meta = row['supplemental_postmatch_facts']['understat_archive']['facts']
        counts = {}
        for side in ('home', 'away'):
            total, on_target = meta[side + '_shots'], meta[side + '_shots_on_target']
            if type(total) is not int or type(on_target) is not int or not 0 <= on_target <= total:
                return True
            counts[side] = {'totalShots': total, 'shotsOnTarget': on_target}
        row.setdefault('additional_postmatch_statistics', {})['understat_archive_shot_counts'] = {
            'source_id': shots['source_id'], 'statistics': counts, 'shot_asset_sha256': shots['archive_sha256'],
            'source_receipt': row['supplemental_postmatch_facts']['understat_archive']['source_receipt'],
            'approved_as_training_feature': False}
    return True


def run(old, work):
    parents = read(work / 'parents.json')
    releases = read(old / 'continuation/pannadata-releases.json')
    path = work / 'understat_shots.parquet'
    sr = verified_parquet(path, releases)
    metadata_path = old / 'continuation/understat_metadata.parquet'
    mr = verified_parquet(metadata_path, releases)
    records, conflicts, _ = deduplicate(pq.read_table(metadata_path).to_pylist())
    if conflicts:
        raise ValueError('metadata_conflicts')
    meta = {(s['league_code'],str(s['understat_id'])):s for s in records}
    snapshots = {}; targets = {}
    for slug, parent in parents.items():
        body = (old / slug / parent['file']).read_bytes()
        if digest(body) != parent['sha256']:
            raise ValueError('parent_digest_mismatch')
        data = snapshots[slug] = json.loads(body)
        for row in data['matches']:
            if not row['training_eligible']:
                continue
            archive = row.get('supplemental_postmatch_facts', {}).get('understat_archive')
            review = EXCEPTIONS.get(row['id'].removeprefix('espn:'))
            if archive or review:
                uid = archive['source_id'].split(':')[1] if archive else review[2]
                key = (LEAGUES[slug][0], uid)
                if key in targets:
                    raise ValueError('duplicate_archive_target')
                targets[key] = (slug, row, bool(review))
    groups, conflicts = index_shots(path, targets)
    audit = {'shot_events': {}, 'shot_exclusions': [], 'added_xg': [], 'stat_disagreements': [],
             'lineup_times': {}, 'red_cards': {}, 'shot_asset':sr['sha256'], 'not_trained':True}
    for key,(slug,row,exception) in targets.items():
        try:
            if key in conflicts:
                raise ValueError('conflicting_shot_ids')
            shots = shot_evidence(meta[key], groups.get(key, {}))
            if exception:
                fact = reviewed_archive_fact(row, snapshots[slug], meta[key], shots, mr, sr)
                row.setdefault('supplemental_postmatch_facts', {})['understat_archive'] = fact
                audit['added_xg'].append(row['id'])
        except (KeyError,TypeError,ValueError) as exc:
            audit['shot_exclusions'].append({'id':row['id'],'reason':str(exc)})
            continue
        shots['archive_sha256'] = sr['sha256']
        row['postmatch_shot_evidence'] = shots
        counts = [row['postmatch_statistics'].get(s,{}).get('totalShots') for s in ('home','away')]
        if counts == [0,0] and row['home_goals'] + row['away_goals'] > 0:
            audit['stat_disagreements'].append({'id':row['id'],'espn_total_shots':counts,'archive_event_count':shots['event_count']})
            row.setdefault('supplemental_quality', {})['zero_shot_positive_score'] = {
                'espn_total_shots':counts,'archive_event_count':shots['event_count'],'review_required':True}
    torino_xg(snapshots['ita.1'],old,work)
    audit['added_xg'].append('espn:737158')
    esp = {r['id']:r for r in snapshots['esp.1']['matches']}
    for review in STATS_REVIEWS:
        row = esp[review[0]]
        admit_cards(row,review,old)
        audit['red_cards'][row['id']] = row['additional_postmatch_statistics']['club_official_rendered_cards']['statistics']
    for eid in LIVE:
        lineup = admit_lineups(work,esp[eid])
        esp[eid]['reported_pregame_lineup'] = lineup
        audit['lineup_times'][eid] = lineup['both_available_no_earlier_than']
    usable = {}
    audit['quarantined_boxscores'] = {}
    for slug, data in snapshots.items():
        masked = []; blocked = []; recovered = []
        for row in data['matches']:
            if row['training_eligible'] and quarantine_empty_box(row):
                blocked.append(row['id'])
                if 'understat_archive_shot_counts' in row.get('additional_postmatch_statistics', {}):
                    recovered.append(row['id'])
                masked.append({**row, 'postmatch_statistics': {}})
            else:
                masked.append(row)
        usable[slug] = {'matches': masked}
        audit['quarantined_boxscores'][slug] = {'ids':blocked, 'shot_counts_recovered':recovered}
    audit['usable_coverage'], _ = field_coverage(usable)
    audit['coverage'], audit['missing_xg'] = field_coverage(snapshots, include_quarantined=True)
    outputs = {}
    for slug, data in snapshots.items():
        for cov in data['coverage']:
            rows = [r for r in data['matches'] if r['training_eligible'] and r['season']==cov['season']]
            cov['shot_archive_matches'] = sum('postmatch_shot_evidence' in r for r in rows)
            cov['reported_pregame_lineup_both'] = sum('reported_pregame_lineup' in r for r in rows)
            for key,provider in [('understat_archive_xg_both','understat_archive'),('gallery_archive_xg_both','understat_gallery_archive')]:
                cov[key] = sum(provider in r.get('supplemental_postmatch_facts',{}) for r in rows)
            cov['any_source_xg_both'] = sum(bool(r.get('external_postmatch_xg') or r.get('supplemental_postmatch_facts')) for r in rows)
        shots = [r['postmatch_shot_evidence'] for r in data['matches'] if 'postmatch_shot_evidence' in r]
        audit['shot_events'][slug] = {'matches':len(shots),'shots':sum(len(v['shot_ids']) for v in shots)}
        data['manifest']['event_evidence_update'] = {'parent_sha256':parents[slug]['sha256'],
            'shot_asset':sr, 'shot_file':str(path), 'shot_coverage':audit['shot_events'][slug],
            'usable_field_coverage':audit['usable_coverage'][slug],
            'quarantined_espn_boxscores':len(audit['quarantined_boxscores'][slug]['ids']),
            'code_sha256':digest(Path(__file__).read_bytes()),'shot_validation_sha256':digest(Path(__file__).with_name('big_five_shot_archive.py').read_bytes()),
            'enhanced_features_trained':False}
        body = json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        sha = digest(body); dest = old / slug / 'snapshots' / (sha+'.json')
        outputs[slug] = {'sha256':sha,'file':str(dest.relative_to(old/slug)),'rows':len(data['matches'])}
        if dest.exists() and digest(dest.read_bytes()) != sha:
            raise ValueError('snapshot_collision')
        if not dest.exists():
            dest.write_bytes(body)
    for slug,pointer in outputs.items():
        if read(old/slug/'latest.json') not in (parents[slug],pointer):
            raise ValueError('newer_snapshot_present:'+slug)
    atomic_json(work/'audit.json',audit)
    for slug,pointer in outputs.items():
        atomic_json(old/slug/'latest.json',pointer)
    print(json.dumps({k:audit[k] for k in ['shot_events','added_xg','lineup_times']}))
    print('missing_xg',len(audit['missing_xg']),'excluded',Counter(e['reason'] for e in audit['shot_exclusions']))


if __name__=='__main__':
    root=Path('data/research/big-five')
    run(root/'2026-11-05',root/'2026-11-06')
