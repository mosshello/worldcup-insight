"""Read-only source probes for a saved coverage audit; never writes product records."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from worldcup_mvp.console.history_audit import Archive, Document
from worldcup_mvp.console.sources import ESPN, LEAGUES, parse_date
from worldcup_mvp.console.public_enrichment import parse_page


def run(audit_path, output):
    audit = json.loads(Path(audit_path).read_text(encoding='utf-8'))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect((ROOT / 'data/console/football.sqlite3').as_uri() + '?mode=ro', uri=True)
    jobs = []
    for row in audit['matches']:
        match = json.loads(db.execute('select payload from matches where id=?', (row['id'],)).fetchone()[0])
        detail = json.loads(db.execute('select payload from detail_cache where match_id=?', (row['id'],)).fetchone()[0])
        context = detail['context']
        prior = None
        for payload, in db.execute('select payload from detail_snapshots where match_id=? order by id desc', (row['id'],)):
            candidate = json.loads(payload).get('context') or {}
            if (candidate.get('supplementary_source') or {}).get('source_url'):
                prior = candidate
                break
        jobs.append((row, match, context, prior))
    db.close()

    def probe(job):
        row, match, context, prior = job
        result = {'id': match['id'], 'fixture': row['fixture'], 'phase': row['lifecycle'],
                  'venue': [], 'errors': {}}
        archive = Archive(output / 'raw')
        for side in ('home', 'away'):
            try:
                tid = str(context['provider_ids'][side])
                url = ESPN + f'all/teams/{tid}/schedule?season=2026'
                data = json.loads(archive.fetch(url))
                if str(data.get('team', {}).get('id')) != tid:
                    raise ValueError('schedule team identity mismatch')
                events = []
                for event in data.get('events', []):
                    if parse_date(event['date']) >= min(parse_date(match['kickoff']), datetime.now(timezone.utc)):
                        continue
                    comp = event['competitions'][0]
                    own = [p for p in comp['competitors'] if str(p['team']['id']) == tid]
                    if len(own) != 1 or own[0]['homeAway'] != side or comp.get('neutralSite'):
                        continue
                    status = comp.get('status', {}).get('type', {})
                    if not status.get('completed') or status.get('name') != 'STATUS_FULL_TIME':
                        continue  # Do not infer regulation scores from extra time or shootouts.
                    scores = [p.get('score', {}).get('displayValue') for p in comp['competitors']]
                    if len(scores) != 2 or not all(re.fullmatch(r'\d{1,2}', s or '') for s in scores):
                        continue
                    events.append({'id': event['id'], 'date': event['date'],
                                   'league': event.get('league', {}).get('slug'), 'scores': scores})
                result['venue'].append({'side': side, 'url': url, 'returned_events': len(data.get('events', [])),
                    'same_competition': sum(e['league'] == LEAGUES[match['league_code']] for e in events),
                    'all_competitions': len(events), 'events': events})
            except Exception as exc:
                result['errors'][side + '_schedule'] = str(exc)
        if row['lifecycle'] == 'completed':
            try:
                url = ESPN + LEAGUES[match['league_code']] + '/summary?event=' + context['event_id']
                summary = json.loads(archive.fetch(url))
                if str(summary['header']['id']) != str(context['event_id']):
                    raise ValueError('summary event mismatch')
                teams = summary.get('boxscore', {}).get('teams', [])
                if {str(t['team']['id']) for t in teams} != {str(x) for x in context['provider_ids'].values()}:
                    raise ValueError('summary teams mismatch')
                result['raw_statistics'] = [{'team_id': t['team']['id'],
                    'fields': [s.get('name') for s in t.get('statistics', [])],
                    'xg_fields': [s for s in t.get('statistics', []) if re.search('expected|xg', s.get('name', ''), re.I)]}
                    for t in teams]
            except Exception as exc:
                result['errors']['raw_statistics'] = str(exc)
        if prior:
            try:
                src = prior['supplementary_source']
                url = src['source_url']
                html = archive.fetch(url)
                header = Document(html).root.find('div', **{'class': 'odds_header'})[0]
                team_names = {}
                for anchor in header.find('a'):
                    found = re.search(r'/team/(\d+)/', anchor.attrs.get('href', ''))
                    if found and anchor.text.strip():
                        team_names.setdefault(found[1], anchor.text.strip())
                ids = list(team_names)[:2]
                expected_league = {'UCL': '欧冠', 'ACL': '亚冠'}.get(match['league_code'])
                if not expected_league or expected_league not in header.text[:180]:
                    raise ValueError('500 competition header mismatch')
                local_kickoff = parse_date(match['kickoff']).astimezone(timezone(timedelta(hours=8)))
                if local_kickoff.strftime('%Y-%m-%d %H:%M') not in header.text[:180]:
                    raise ValueError('500 kickoff header mismatch')
                identity = {'id': src['provider_event_id'], 'team_ids': ids[:2]}
                # Explicitly reviewed aliases for this research sample, not a production identity registry.
                aliases = {'比利亚雷': '比利亚雷亚尔', '巴黎圣曼': '巴黎圣日耳曼',
                           '布里兰': '武里南联', '莱红牛': '莱比锡红牛', '曼城': '曼彻斯特城',
                           '加拉塔萨': '加拉塔萨雷', '马竞': '马德里竞技', '拜仁': '拜仁慕尼黑',
                           '布拉迪斯': '布拉迪斯拉发', '曼联': '曼彻斯特联', '巴萨': '巴塞罗那',
                           '维拉': '阿斯顿维拉', '埃因霍温': 'PSV埃因霍温', '贝蒂斯': '皇家贝蒂斯',
                           '萨巴赫': '沙巴巴库', '斯拉维亚': '布拉格斯拉维亚'}
                if [aliases.get(match[s], match[s]) for s in ('home', 'away')] != [team_names[i] for i in ids]:
                    raise ValueError('500 current header names require explicit alias review')
                parsed = parse_page(match, identity, html, datetime.now(timezone.utc).isoformat())
                injury_rows, injury_sections = [], 0
                for box in Document(html).root.find('div', **{'class': 'M_box starting'}):
                    for side_class in ('team_a', 'team_b'):
                        for block in box.find('div', **{'class': side_class}):
                            in_injuries = False
                            for tr in block.find('tr'):
                                if tr.find('th'):
                                    in_injuries = '伤病' in tr.text
                                    injury_sections += int(in_injuries)
                                elif in_injuries and tr.text:
                                    injury_rows.append(tr.text)
                result['stable_500'] = {'url': url, 'team_ids': ids[:2], 'team_names': [team_names[i] for i in ids], 'header': header.text[:180],
                    'estimated': [len(r['players']) for r in parsed['estimated_lineups']],
                    'injuries_parsed': len(parsed['injuries']),
                    'nonempty_injury_rows': injury_rows, 'injury_section_count': injury_sections,
                    'injury_sections': [tr.text for box in Document(html).root.find('div', **{'class':'M_box starting'})
                        for tr in box.find('tr') if tr.find('th') or '伤病' in tr.text or '停赛' in tr.text]}
            except Exception as exc:
                result['errors']['stable_500'] = str(exc)
        else:
            result['errors']['stable_500'] = 'No saved source identity'
        print(result['id'], 'venue', [(v['same_competition'], v['all_competitions']) for v in result['venue']],
              '500', result.get('stable_500', {}).get('estimated'), 'errors', list(result['errors']), flush=True)
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(probe, jobs))
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'source_audit': str(audit_path),
              'scope': 'research only; no product database changes', 'matches': rows}
    (output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--audit', required=True)
    parser.add_argument('--output', default='.ai/tmp/fundamental-source-research')
    args = parser.parse_args()
    run(args.audit, args.output)
