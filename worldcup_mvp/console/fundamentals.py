"""Competition-aware fundamental enrichment, separate from source transport and model fitting."""
def fetch_context(match):
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
                context.update({k: additional.get(k) for k in ("lineups", "venue", "state", "score")})
                context.update(lineup_source="ESPN", lineup_source_url=additional.get("source_url"),
                               lineup_captured_at=additional.get("captured_at"), schedule_recent=additional.get("recent"),
                               source="500 / ESPN")
        return context
    return fetch_espn_context(match)

