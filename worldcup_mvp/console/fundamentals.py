"""Competition-aware fundamental enrichment, separate from source transport and model fitting."""
def _base_context(match):
    from .sources import SourceError, fetch_espn_context
    if match.get("league_code") in ("JD1", "KD1"):
        from .league_data import historical_context
        try:
            context = historical_context(match)
        except SourceError as exc:
            if match.get("league_code") == "KD1":
                return {"available": False, "reason": str(exc), "recent": [[], []], "lineups": [], "source": "500"}
            context = None
        if match.get("league_code") == "JD1":
            try:
                additional = fetch_espn_context(match)
            except SourceError:
                additional = None
            if not context:
                return additional or {"available": False, "reason": "基本面暂不可用", "recent": [[], []], "lineups": [], "source": "500 / ESPN"}
            if additional and additional.get("available"):
                context.update({k: additional.get(k) for k in (
                    "lineups", "venue", "state", "score", "provider_ids", "event_id", "match_facts", "head_to_head")})
                context.update(lineup_source="ESPN", lineup_source_url=additional.get("source_url"),
                               lineup_captured_at=additional.get("captured_at"), schedule_recent=additional.get("recent"),
                               source="500 / ESPN")
        return context
    return fetch_espn_context(match)


def fetch_context(match):
    from concurrent.futures import ThreadPoolExecutor
    from . import public_enrichment, schedules, personnel
    from .sources import SourceError
    from .store import now_iso
    from .team_labels import localize_context
    from .competition_labels import label
    with ThreadPoolExecutor(max_workers=2) as pool:
        base_task = pool.submit(_base_context,match)
        extra_task = pool.submit(public_enrichment.fetch,match)
        try:
            context = base_task.result()
        except SourceError as exc:
            context = {'available':False,'reason':str(exc),'recent':[[],[]],'lineups':[]}
        try:
            extra = extra_task.result()
            context.update(estimated_lineups=extra['estimated_lineups'], injuries=extra['injuries'],
                           upcoming=extra['upcoming'], supplementary_source={
                               k:extra[k] for k in ('source','source_url','captured_at','provider_event_id')})
            context['external_odds'] = extra.get('external_odds') or {'rows': []}
            if not context.get('available') and any(extra['recent']):
                context.update(available=True, recent=extra['recent'], source='500', source_url=extra['source_url'],
                               captured_at=extra['captured_at'], reason=None, coverage='mixed_display_only',
                               score_period_notice='备用近期赛果未确认加时口径，仅作资料展示')
            elif context.get('available'):
                for i in range(2):
                    if len(context.get('recent',[[],[]])[i]) < len(extra['recent'][i]):
                        context['recent'][i] = extra['recent'][i]
                        context['source'] = ' / '.join(dict.fromkeys((context.get('source') or '历史资料', '500')))
                        context['coverage'] = 'mixed_display_only'
                        context['score_period_notice'] = '部分近期赛果来自500，未确认加时口径，仅作资料展示'
        except SourceError as exc:
            context['supplementary_error']=str(exc)
    context.setdefault('upcoming',[[],[]])
    def schedule(i):
        team_id = context.get('provider_ids',{}).get(('home','away')[i])
        if not team_id: return
        try:
            rows=schedules.fetch(team_id,match['kickoff'])
            if rows: context['upcoming'][i]=rows
        except SourceError:
            pass  # Retain the independently matched 500 schedule and its source/precision.
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(schedule,range(2)))
    for field in ('recent','schedule_recent','venue_recent'):
        for rows in context.get(field) or []:
            for row in rows:
                if not row.get('league') and context.get('coverage')=='league_only': row.update(label(match['league']))
    context['enriched_at']=now_iso()
    return localize_context(personnel.annotate(context,match))

