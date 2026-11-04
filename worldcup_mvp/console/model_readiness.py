"""Model input gate, independent from optional personnel facts and market prices."""
from datetime import datetime, timezone
from .catalog import catalog, classify
from .quality import age_seconds
from .readiness import fixture_key
from .sources import parse_date


def check(match, detail, football, now=None):
    now = now or datetime.now(timezone.utc)
    checks = []
    def add(code, ok, reason):
        checks.append({'code':code,'status':'ready' if ok else 'blocked','reason':None if ok else reason})
    cid = classify(match)
    spec = next((s for s in catalog() if s['id']==cid['id']), {})
    add('competition', bool(spec and cid.get('season')), '赛事或赛季未登记')
    add('fixture_identity', all(match.get(k) for k in ('id','provider','home_id','away_id','kickoff')) and match.get('home_id') != match.get('away_id'), '比赛身份不完整')
    add('fixture_freshness', -30 <= age_seconds(match.get('captured_at'),now) <= 180, '场次状态核验过期')
    try:
        pregame = parse_date(match['kickoff']) > now
    except (KeyError, ValueError, TypeError):
        pregame = False
    live = detail.get('live_score') or {}
    context = detail.get('context') or {}
    state = live.get('state')
    context_state = context.get('state')
    context_verified = (context.get('available') is True and context.get('fixture_identity') == fixture_key(match)
        and context_state == 'upcoming' and not context.get('stale')
        and -30 <= age_seconds(context.get('captured_at'),now) <= 180)
    live_verified = (state == 'upcoming' and bool(live.get('provider_event_id')) and not live.get('stale') and not live.get('error')
        and -30 <= age_seconds(live.get('observed_at') or live.get('captured_at'),now) <= 180)
    add('verified_state', context_verified or live_verified, '未取得有效的赛前比赛状态')
    add('fixture_state', pregame and not detail.get('cancelled') and not detail.get('official_score')
        and not any(v in ('live','completed','cancelled','postponed','suspended') for v in (state,context_state)), '比赛已开赛或状态异常')
    policy = spec.get('prediction_stage_policy')
    if policy is not None:
        identity = context.get('espn_identity') or {}
        add('rules', identity.get('fixture') == fixture_key(match) and identity.get('competition_slug') == spec.get('providers',{}).get('espn')
            and str(identity.get('season')) == str(cid.get('season'))
            and -30 <= age_seconds(identity.get('captured_at'),now) <= 180 and not context.get('stale')
            and identity.get('stage') in policy.get(str(cid.get('season')), []) and identity.get('neutral') is False,
            '本场阶段或场地规则未通过核验')
    elif spec and spec['id'] not in ('jp.j1','kr.k1'):
        add('rules', False, '本赛事模型适用规则未登记')
    add('model', (football or {}).get('status') == 'shadow', (football or {}).get('reason') or '未选用可用赛事模型')
    if (football or {}).get('status') == 'shadow':
        add('model_scope', football.get('competition_id') == cid['id'] and football.get('training_scope') == 'competition_only', '模型赛事范围不一致')
        from .cold_start import allowed
        counts = football.get('team_samples',[])
        add('team_history', len(counts) == 2 and (all(v >= 5 for v in counts) or allowed(football.get('cold_start_policy'),counts)), '球队历史样本不足5场，冷启动策略未通过本组评估')
        add('markets', any(v.get('code')=='had' and v.get('options') for v in football.get('markets',[])), '模型没有可用胜平负输出')
    reasons = [c['reason'] for c in checks if c['status']=='blocked']
    return {'policy':'model-inputs-v2','can_predict':not reasons,'checks':checks,'reasons':reasons,
            'verified_at':now.isoformat(),'required_inputs':['competition','team_identity','home_away','historical_regular_time_results','frozen_model'],
            'auxiliary_inputs':['lineups','injuries','xg','external_odds']}
