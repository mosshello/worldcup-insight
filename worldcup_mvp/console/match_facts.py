"""Identity-checked match events and statistics, separate from prediction inputs."""
import math
import re
from .sources import parse_date, BEIJING
from .competition_labels import label

STAT_LABELS = {
    'possessionPct': '控球率', 'totalShots': '射门', 'shotsOnTarget': '射正',
    'wonCorners': '角球', 'foulsCommitted': '犯规', 'yellowCards': '黄牌',
    'redCards': '红牌', 'offsides': '越位', 'saves': '扑救',
    'totalPasses': '传球', 'accuratePasses': '成功传球', 'expectedGoals': '预期进球 xG',
}
EVENT_LABELS = {'goal': '进球', 'own-goal': '乌龙球', 'penalty': '点球进球',
                'penalty---scored': '点球进球', 'penalty---missed': '罚失点球',
                'yellow-card': '黄牌', 'red-card': '红牌', 'yellow-red-card': '两黄变红',
                'substitution': '换人', 'var': '视频判罚'}


def extract(summary, match, competitors, captured_at):
    identities = {str(competitors[s]['id']): s for s in ('home', 'away')}
    source_url = f"https://www.espn.com/soccer/match/_/gameId/{summary['header']['id']}"
    meta = {'source': 'ESPN', 'source_url': source_url, 'captured_at': captured_at,
            'provider_event_id': str(summary['header']['id'])}
    stats = {}
    for team in (summary.get('boxscore') or {}).get('teams', []):
        side = identities.get(str(team.get('team', {}).get('id')))
        if not side: continue
        for item in team.get('statistics', []):
            code = item.get('name')
            if code not in STAT_LABELS: continue
            try:
                raw = str(item['displayValue']).rstrip('%')
                value = float(raw)
                if not math.isfinite(value) or value < 0: continue
                if code == 'possessionPct' and value > 100: continue
            except (ValueError, TypeError, KeyError): continue
            stats.setdefault(code, {'code': code, 'label': STAT_LABELS[code],
                                    'home': None, 'away': None})[side] = raw + ('%' if code == 'possessionPct' else '')
    events = []
    seen = set()
    for event in summary.get('keyEvents', []):
        kind = event.get('type', {}).get('type')
        side = identities.get(str(event.get('team', {}).get('id')))
        period = event.get('period', {}).get('number')
        event_id = event.get('id')
        if kind not in EVENT_LABELS or not side or period not in (1, 2, 3, 4) or event.get('shootout'): continue
        if not event_id or event_id in seen: continue
        seen.add(event_id)
        participants = [p.get('athlete', {}).get('displayName') for p in event.get('participants', [])]
        events.append({'id': str(event_id), 'type': kind, 'label': EVENT_LABELS[kind],
                       'side': side, 'team': match[side], 'period': period,
                       'period_label': '常规时间' if period <= 2 else '加时',
                       'clock': event.get('clock', {}).get('displayValue') or '时间待核实',
                       'seconds': event.get('clock', {}).get('value', 0),
                       'participants': ' / '.join(p for p in participants if p),
                       'occurred_at': event.get('wallclock')})
    events.sort(key=lambda e: (e['period'], e['seconds']))
    # Statistics are provider match totals, not inferred to be 90-minute totals.
    return {**meta, 'statistics': list(stats.values()), 'events': events,
            'statistics_scope': '来源比赛累计统计，可能含加时；不自动用于90分钟训练',
            'events_scope': '来源已列出的关键事件，不代表完整逐秒记录'}


def head_to_head(summary, match, competitors):
    ids = {str(competitors[s]['id']): match[s] for s in ('home', 'away')}
    rows, seen = [], set()
    for group in summary.get('seasonseries', []):
        if group.get('type') != 'head-to-head': continue
        for event in group.get('events', []):
            try:
                teams = {p['homeAway']: p for p in event['competitors']}
                if {str(p['team']['id']) for p in teams.values()} != set(ids): continue
                if not event.get('statusType', {}).get('completed'): continue
                start = parse_date(event['date'])
                if start >= parse_date(match['kickoff']) or str(event['id']) in seen: continue
                scores = [str(teams[s]['score']) for s in ('home', 'away')]
                if not all(re.fullmatch(r'\d{1,2}', x) for x in scores): continue
                seen.add(str(event['id']))
                competition = re.sub(r'^\d{4}(?:-\d{2,4})?\s+', '', event.get('competitionName', ''))
                rows.append({'id': str(event['id']), 'date': start.astimezone(BEIJING).isoformat(),
                             'home': ids[str(teams['home']['team']['id'])],
                             'away': ids[str(teams['away']['team']['id'])],
                             'score': '-'.join(scores), **label(competition), 'source': 'ESPN',
                             'score_period': 'regular_time' if event['statusType'].get('name') == 'STATUS_FULL_TIME' else 'unknown'})
            except (KeyError, ValueError, TypeError): continue
    return sorted(rows, key=lambda r: r['date'], reverse=True)[:5]
