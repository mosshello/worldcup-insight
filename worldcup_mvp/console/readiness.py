"""Fail-closed, point-in-time preflight for production console predictions.

Coverage requirements are operational checks, not claims about trained features.
"""
from datetime import datetime, timezone
import math

from .sources import parse_date


def fixture_key(match):
    return {k: match.get(k) for k in ('id', 'league_code', 'home_id', 'away_id', 'home', 'away', 'kickoff')}


def check(match, context, now=None):
    now = now or datetime.now(timezone.utc)
    checks = []
    def add(code, label, ok, reason):
        checks.append(dict(code=code, label=label, status='ready' if ok else 'blocked', reason=None if ok else reason))
    def stamp(value, max_age):
        try:
            when = parse_date(value)
            return when < kickoff and 0 <= (now-when).total_seconds() <= max_age
        except (ValueError, TypeError, AttributeError):
            return False
    try:
        kickoff = parse_date(match.get('kickoff'))
    except (ValueError, TypeError, AttributeError):
        kickoff = now
    add('phase', '比赛阶段', now < kickoff and context.get('state') not in
        ('live', 'completed', 'postponed', 'cancelled', 'suspended'), '比赛已开始或状态待核实')
    add('identity', '比赛身份', context.get('fixture_identity') == fixture_key(match), '基本面比赛身份未核实或已变更')
    add('freshness', '基本面时间', context.get('available') and not context.get('stale') and
        stamp(context.get('captured_at'), 1800), '基本面缺失、超过30分钟或时间异常')
    def valid_rows(rows, venue=None):
        seen = set()
        for row in rows or []:
            try:
                start = parse_date(row.get('date'))
                values = [row.get(k) for k in ('gf', 'ga')]
                valid = all(not isinstance(v, bool) and isinstance(v, (int, float)) and
                            math.isfinite(v) and v >= 0 and int(v) == v for v in values)
                key = row.get('id') or (row.get('date'), row.get('opponent'))
                if start >= min(now, kickoff) or not valid or (venue and row.get('venue') != venue):
                    continue
                seen.add(key)
            except (ValueError, TypeError, AttributeError):
                continue
        return len(seen)
    counts = {}
    for field, label in (('recent', '近期战绩'), ('venue_recent', '主客场战绩')):
        groups = context.get(field) or []
        counts[field] = [valid_rows(groups[i] if len(groups) > i else [], ('主', '客')[i] if field == 'venue_recent' else None)
                         for i in range(2)]
        freshness = [context.get('venue_captured_at')] if field == 'venue_recent' else context.get('recent_captured_at', [context.get('captured_at')]*2)
        add(field, label, min(counts[field]) >= 5 and bool(freshness) and all(stamp(t, 1800) for t in freshness) and not context.get('stale_fields', {}).get(field),
            f'{label}未达双方各5场有效样本或来源已过期（{counts[field][0]}/{counts[field][1]}）')
    add('h2h', '交锋核查', context.get('h2h_checked') is True and stamp(context.get('captured_at'), 1800), '交锋尚未完成核查')
    # Require confirmed starters only inside the final hour; earlier estimates stay estimates.
    confirmed_required = (kickoff-now).total_seconds() <= 3600
    complete_sides = set()
    for field, status in (('lineups', 'confirmed'), ('estimated_lineups', 'estimated')):
        if confirmed_required and status != 'confirmed':
            continue
        for roster in context.get(field) or []:
            players = roster.get('players') or []
            ids = [p.get('id') or p.get('name') for p in players]
            captured = roster.get('captured_at') or (context.get('lineup_captured_at') or context.get('captured_at') if field == 'lineups' else None)
            if (roster.get('side') in ('home', 'away') and roster.get('status') == status and
                len(ids) == 11 and all(ids) and len(set(ids)) == 11 and not roster.get('conflicts') and
                roster.get('observed_before_kickoff') is not False and stamp(captured, 300 if confirmed_required else 1800) and
                not context.get('stale_fields', {}).get(field)):
                complete_sides.add(roster['side'])
    add('lineups', '双方阵容', len(complete_sides) == 2,
        '临场一小时内需双方完整正式首发及5分钟内核验' if confirmed_required else '双方阵容缺失、不完整、冲突或已过期')
    personnel = context.get('personnel_verification') or {}
    add('personnel', '伤停与资格', personnel.get('complete') is True and
        personnel.get('fixture_identity') == fixture_key(match) and
        set(personnel.get('sides') or []) == {'home', 'away'} and bool(personnel.get('source_url')) and
        stamp(personnel.get('captured_at'), 300 if confirmed_required else 1800), '双方伤停与本赛事参赛资格未完整核查')
    return {'policy': 'pregame-completeness-v1', 'ready': all(c['status'] == 'ready' for c in checks),
            'checked_at': now.isoformat(), 'checks': checks, 'sample_counts': counts,
            'reasons': [c['reason'] for c in checks if c['status'] != 'ready'],
            'not_required': ['本场赛后统计、事件及xG', '未启用的跨赛事模型特征'],
            'training_features_changed': False}
