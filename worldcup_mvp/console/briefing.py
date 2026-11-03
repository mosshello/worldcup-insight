"""Automatic, read-only match briefing. Never creates or rewrites a prediction archive."""
import math
from datetime import datetime, timezone
from .intelligence import valid_time
from .quality import complete, age_seconds
from .predictor import predict

LABELS = {'h': '主胜', 'd': '平局', 'a': '客胜'}


def rows_before(rows, cutoff):
    result, seen = [], set()
    for row in rows:
        stamp = valid_time(row.get('date'))
        # Date-only sources are display evidence, never given invented intraday precision.
        if not stamp and row.get('date_precision') == 'date':
            try: stamp = datetime.fromisoformat(row['date'][:10]).replace(tzinfo=timezone.utc)
            except (ValueError, TypeError): continue
        if not stamp or stamp >= cutoff: continue
        values = [row.get(k) for k in ('gf', 'ga')]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in values): continue
        key = row.get('id') or (row.get('date'), row.get('opponent'))
        if key in seen: continue
        seen.add(key); result.append(row)
    return sorted(result, key=lambda r: r['date'], reverse=True)[:5]


def build(match, detail, model=None, as_of=None):
    now = as_of or datetime.now(timezone.utc)
    kickoff = valid_time(match.get('kickoff'))
    context = detail.get('context') or {}
    live = detail.get('live_score') or {}
    state = live.get('state') if live.get('state') not in (None, 'unknown', 'unavailable') else context.get('state')
    phase = ('completed' if state == 'completed' or detail.get('official_score') else
             'unavailable' if state in ('cancelled', 'postponed', 'suspended') or detail.get('cancelled') else
             'live' if state == 'live' else 'pregame' if kickoff and now < kickoff else 'awaiting_result')
    data = {'version': 'briefing-v1', 'phase': phase, 'created_at': now.isoformat(),
            'title': {'pregame': '赛前自动研判', 'live': '比赛进行中', 'completed': '赛后事实概览',
                      'awaiting_result': '开赛时间已过，等待状态核实', 'unavailable': '比赛状态待处理'}[phase],
            'coverage': [], 'findings': [], 'actions': [], 'preview': None,
            'football_model': model, 'facts': context.get('match_facts'),
            'review_url': '#/reviews/' + match['id'], 'is_archived': False}
    def coverage(key, title, status, summary, source, stamp, tab):
        data['coverage'].append({'key': key, 'title': title, 'status': status, 'summary': summary,
                                 'source': source or '暂无', 'captured_at': stamp, 'tab': tab})
    fetched = context.get('captured_at')
    age = age_seconds(fetched, now)
    stale = bool(context.get('stale')) or age > 1800 or age < -30
    cutoff = min(now, kickoff) if kickoff else now
    counts = []
    groups = context.get('recent') or [[], []]
    for i, side in enumerate(('home', 'away')):
        rows = rows_before(groups[i] if len(groups) > i else [], cutoff)
        counts.append(len(rows))
        if not rows: continue
        wins = sum(r['gf'] > r['ga'] for r in rows)
        draws = sum(r['gf'] == r['ga'] for r in rows)
        gf, ga = sum(r['gf'] for r in rows), sum(r['ga'] for r in rows)
        leagues = list(dict.fromkeys(r.get('league') for r in rows if r.get('league')))
        venue = [r for r in rows if r.get('venue') == ('主' if i == 0 else '客')]
        evidence = f"{match[side]}近{len(rows)}场{wins}胜{draws}平{len(rows)-wins-draws}负，进{gf}球、失{ga}球；场均{gf/len(rows):.2f} / {ga/len(rows):.2f}。"
        if leagues: evidence += '样本赛事：' + '、'.join(leagues) + '。'
        if venue: evidence += f"其中{'主' if i == 0 else '客'}场{len(venue)}场，进{sum(r['gf'] for r in venue)}球、失{sum(r['ga'] for r in venue)}球。"
        data['findings'].append({'title': match[side] + '近期表现', 'text': evidence, 'tab': 'basic'})
        future = (context.get('upcoming') or [[], []])[i]
        if future:
            next_game = future[0]
            data['findings'].append({'title': match[side] + '赛程', 'tab': 'basic',
                'text': f"下一场已知赛程：{next_game['date'][:10]}，{next_game.get('league','赛事待核实')}，对阵{next_game.get('opponent','待核实')}；赛程可能调整。"})
        if len(rows) < 5: data['actions'].append(f"{match[side]}近期仅有{len(rows)}场可核实样本，继续补采，避免小样本外推。")
    coverage('recent', '近期表现', 'stale' if stale and any(counts) else 'available' if min(counts) >= 5 else 'partial' if any(counts) else 'missing',
             f'主队{counts[0]}场 / 客队{counts[1]}场；不同赛事仅作事实对照', context.get('source'), fetched, 'basic')
    rosters = context.get('lineups') or []
    confirmed = sum(r.get('status') == 'confirmed' and len(r.get('players', [])) == 11 for r in rosters)
    estimated = len(context.get('estimated_lineups') or [])
    lineup_sources = list(dict.fromkeys(r.get('source') or context.get('lineup_source') or context.get('source') for r in rosters + (context.get('estimated_lineups') or [])))
    coverage('lineups', '正式阵容', 'available' if confirmed == 2 else 'partial' if confirmed or estimated else 'pending',
             f'正式确认{confirmed}/2队；预计阵容{estimated}/2队', ' / '.join(s for s in lineup_sources if s),
             context.get('lineup_captured_at') or fetched, 'lineups')
    injuries = context.get('injuries') or []
    coverage('injuries', '伤停与资格', 'available' if context.get('injuries_verified') else 'partial' if injuries else 'missing',
             f'{len(injuries)}条人员消息；' + ('已完整核查' if context.get('injuries_verified') else '尚未取得完整缺阵和停赛名单'),
             ' / '.join(dict.fromkeys(r.get('source', '') for r in injuries)),
             max((r.get('captured_at') or '' for r in injuries), default=None), 'lineups')
    for roster in context.get('estimated_lineups', []):
        if roster.get('conflicts'):
            data['actions'].append(f"{match[roster['side']]}预计阵容与缺阵消息冲突：{'、'.join(roster['conflicts'])}；以正式名单重新核查。")
    future_counts = [len(r) for r in context.get('upcoming', [[], []])]
    coverage('schedule', '后续赛程', 'available' if min(future_counts) else 'partial' if any(future_counts) else 'missing',
             f'主队{future_counts[0]}场 / 客队{future_counts[1]}场', context.get('source'), context.get('schedule_captured_at') or fetched, 'basic')
    valid_markets = [code for code, value in detail.get('markets', {}).items() if complete(value)]
    fresh_odds = detail.get('odds_fresh') and -30 <= age_seconds(detail.get('captured_at'), now) <= 180
    coverage('markets', '体彩五种玩法', 'available' if len(valid_markets) == 5 and fresh_odds else 'partial' if valid_markets and fresh_odds else 'stale' if valid_markets else 'missing',
             f'{len(valid_markets)}/5种报价完整；仅为市场参考', '体彩', detail.get('captured_at'), 'odds')
    external = context.get('external_odds') or {}
    coverage('external', '欧赔参考', 'available' if external.get('rows') else 'missing',
             f"{len(external.get('rows', []))}家来源报价；不含亚洲让球盘口或资金热度", external.get('source'), external.get('captured_at'), 'odds')
    available_model = (model or {}).get('status') == 'shadow'
    coverage('model', '赛事足球模型', 'available' if available_model else 'missing',
             '本赛事前瞻观察，市场参考单独列示' if available_model else (model or {}).get('reason', '该赛事尚无可用观察模型'),
             '本地赛事模型' if available_model else None, (model or {}).get('trained_before'), 'analysis')
    facts = context.get('match_facts') or {}
    if phase != 'pregame':
        coverage('process', '比赛过程', 'available' if facts.get('statistics') and facts.get('events') else 'partial' if facts.get('statistics') or facts.get('events') else 'missing',
                 f"{len(facts.get('statistics', []))}项统计 / {len(facts.get('events', []))}条关键事件", facts.get('source'), facts.get('captured_at'), 'analysis')
    if phase == 'pregame' and detail.get('quality', {}).get('can_predict') and fresh_odds:
        try:
            preview = predict(match, detail, now=now)
            data['preview'] = {k: preview.get(k) for k in ('probabilities', 'title', 'direction', 'direction_key', 'market_assessments', 'goals', 'scores', 'risks', 'probLabel')}
            data['findings'].insert(0, {'title': '当前市场定价', 'tab': 'odds',
                'text': preview['title'] + ' 概率由完整SP去除返还率后归一化，不等于独立足球模型预测。'})
            p = preview['probabilities']
            ranked = sorted(range(3), key=lambda i:p[i], reverse=True)
            gap = p[ranked[0]] - p[ranked[1]]
            data['findings'].append({'title':'方向分歧与判断条件', 'tab':'odds',
                'text':f"市场前两项相差{gap:.1f}个百分点。" + ('差距较小，应同时检查两种结果情景。' if gap < 5 else '领先仅表示相对定价，不表示结果确定。') +
                       (' 所有单项均低于50%，其他结果合计仍占多数。' if max(p) < 50 else '')})
        except (ValueError, KeyError):
            data['actions'].append('市场参考暂不能生成，请重新核验完整报价。')
    elif phase == 'pregame':
        data['actions'].extend(detail.get('quality', {}).get('reasons') or ['等待最新完整报价后再生成市场参考。'])
    if phase == 'pregame':
        data['actions'].append('正式首发公布后复核人员与盘口；需要赛后评估时，在开赛前保存预测版本。')
    else:
        data['actions'].append('查看关联复盘，按已归档赛前判断对照结果；没有归档的比赛仅作事实分析。')
    if stale and any(counts): data['actions'].append('基本面采集时间已超过30分钟或时间异常，请刷新核实。')
    data['actions'].append('近期战绩、人员缺失和调价是核查依据；单场结果与赔率变化不能证明操控意图。')
    data['available_count'] = sum(r['status'] == 'available' for r in data['coverage'])
    data['coverage_count'] = len(data['coverage'])
    return data
