"""Competition-aware fundamental enrichment, separate from source transport and model fitting."""
def _base_context(match):
    from .sources import SourceError, fetch_espn_context
    if match.get("league_code") in ("JD1", "KD1"):
        from .league_data import historical_context
        from .official_context import fetch_jleague, fetch_kleague
        errors = []
        try:
            context = historical_context(match)
        except SourceError as exc:
            context = None
            errors.append(str(exc))
        additional = None
        if match.get("league_code") == "JD1":
            try:
                additional = fetch_espn_context(match)
            except SourceError as exc:
                errors.append(str(exc))
        if not additional or not additional.get("available"):
            try:
                additional = (fetch_jleague if match.get("league_code") == "JD1" else fetch_kleague)(match)
            except (SourceError, ValueError, KeyError, TypeError, OSError) as exc:
                errors.append(str(exc))
        if additional and additional.get("available"):
            source = additional.get("source") or "ESPN"
            if not context or not context.get("available"):
                context = additional
            else:
                context.update({k: additional[k] for k in (
                    "lineups", "venue", "state", "score", "provider_ids", "event_id", "match_facts", "head_to_head", "identity_evidence")
                    if k in additional})
                context.update(source=f"{context.get('source') or '500'} / {source}", schedule_recent=additional.get("recent", [[], []]))
            context.update(lineup_source=source, lineup_source_url=additional.get("source_url"),
                           lineup_captured_at=additional.get("captured_at"))
            errors.extend(additional.get("source_errors", []))
        if not context:
            context = {"available": False, "reason": "基本面暂不可用", "recent": [[], []], "lineups": []}
        context["source_errors"] = errors
        return context
    return fetch_espn_context(match)


def fetch_context(match):
    from concurrent.futures import ThreadPoolExecutor
    from . import public_enrichment, schedules, personnel, supplemental_facts
    from .sources import SourceError
    from .store import now_iso
    from .team_labels import localize_context
    from .competition_labels import label
    with ThreadPoolExecutor(max_workers=3) as pool:
        base_task = pool.submit(_base_context,match)
        extra_task = pool.submit(public_enrichment.fetch,match)
        facts_task = pool.submit(supplemental_facts.fetch,match)
        try:
            context = base_task.result()
            if not isinstance(context, dict):
                raise SourceError('基本面来源响应格式无效')
        except (SourceError, ValueError, KeyError, TypeError, IndexError, OSError) as exc:
            context = {'available':False,'reason':str(exc),'recent':[[],[]],'lineups':[]}
        context['recent_captured_at'] = [context.get('captured_at')] * 2
        try:
            extra = extra_task.result()
            context.update(estimated_lineups=extra['estimated_lineups'], injuries=extra['injuries'],
                           upcoming=extra['upcoming'], supplementary_source={
                               k:extra[k] for k in ('source','source_url','captured_at','provider_event_id')})
            if extra.get('source_identity'):
                context['source_identity'] = extra['source_identity']
            context['external_odds'] = extra.get('external_odds') or {'rows': []}
            if not context.get('available') and any(extra['recent']):
                context.update(available=True, recent=extra['recent'], source='500', source_url=extra['source_url'],
                               captured_at=extra['captured_at'], reason=None, coverage='mixed_display_only',
                               score_period_notice='备用近期赛果未确认加时口径，仅作资料展示')
                context['recent_captured_at'] = [extra['captured_at']] * 2
            elif context.get('available'):
                for i in range(2):
                    if len(context.get('recent',[[],[]])[i]) < len(extra['recent'][i]):
                        context['recent'][i] = extra['recent'][i]
                        context['recent_captured_at'][i] = extra['captured_at']
                        context['source'] = ' / '.join(dict.fromkeys((context.get('source') or '历史资料', '500')))
                        context['coverage'] = 'mixed_display_only'
                        context['score_period_notice'] = '部分近期赛果来自500，未确认加时口径，仅作资料展示'
        except (SourceError, ValueError, KeyError, TypeError, IndexError, OSError) as exc:
            context['supplementary_error']=str(exc)
        try:
            supplement = facts_task.result()
            context['official_personnel'] = supplement['rows']
            context['supplemental_fact_errors'] = supplement['errors']
            public = supplement.get('public_facts')
            if public:
                context.setdefault('injuries', []).extend(public['personnel'])
                context['personnel_sources'] = public['personnel_coverage']
                context['public_facts_source'] = public['source']
                existing_sides = {r.get('side') for r in context.get('estimated_lineups', [])}
                context.setdefault('estimated_lineups', []).extend(
                    r for r in public['estimated_lineups'] if r['side'] not in existing_sides)
                if not supplement['xg'] and public['xg']:
                    supplement['xg'] = public['xg']
            if supplement['xg']:
                xg = supplement['xg']
                facts = context.setdefault('match_facts', {k: xg[k] for k in ('source', 'source_url', 'captured_at')})
                if not any(r.get('code') == 'expectedGoals' for r in facts.get('statistics', [])):
                    facts.setdefault('statistics', []).append(xg)
                facts.setdefault('events', [])
        except (SourceError, ValueError, KeyError, TypeError, OSError) as exc:
            context['supplemental_fact_errors'] = [str(exc)]
    context.setdefault('upcoming',[[],[]])
    def schedule(i):
        team_id = context.get('provider_ids',{}).get(('home','away')[i])
        if not team_id or str(team_id).startswith('500:'): return
        try:
            rows=schedules.fetch(team_id,match['kickoff'])
            if rows: context['upcoming'][i]=rows
        except SourceError:
            pass  # Retain the independently matched 500 schedule and its source/precision.
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(schedule,range(2)))
    if 'venue_recent' not in context and all(str(context.get('provider_ids', {}).get(s, '')).isdigit() for s in ('home', 'away')):
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs = [pool.submit(schedules.fetch_venue, context['provider_ids'][s], s, match) for s in ('home', 'away')]
                venues = [job.result() for job in jobs]
            context['venue_recent'] = [v[0] for v in venues]
            context['venue_captured_at'] = min(v[1] for v in venues)
        except (SourceError, ValueError, KeyError, TypeError, OSError) as exc:
            context['venue_error'] = str(exc)
    elif 'venue_recent' in context:
        context['venue_captured_at'] = context.get('captured_at')
    for field in ('recent','schedule_recent','venue_recent'):
        for rows in context.get(field) or []:
            for row in rows:
                if not row.get('league') and context.get('coverage')=='league_only': row.update(label(match['league']))
    context['enriched_at']=now_iso()
    from .readiness import fixture_key
    context['fixture_identity'] = fixture_key(match)
    return localize_context(personnel.annotate(context,match))

