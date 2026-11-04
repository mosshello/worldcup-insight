"""Match-specific reviewed official bulletins and source conflict annotations."""
import json
from .store import ROOT
from .sources import parse_date


def official_bulletins(match):
    try:
        entries = json.loads((ROOT/'config/football_verified_bulletins.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    result = []
    for entry in entries:
        if (entry['provider_match_id'],entry['home'],entry['away']) != (str(match['provider_id']),match['home'],match['away']): continue
        if parse_date(entry['kickoff']) != parse_date(match['kickoff']): continue
        for p in entry['players']:
            result.append({**p, **{k:entry[k] for k in ('side','source','source_url','published_at','verified_at','mode')},
                           'captured_at':entry['verified_at'], 'scope':'仅适用本场', 'official':True})
    return result


def annotate(context, match):
    official = official_bulletins(match) + context.get('official_personnel', [])
    context['injuries'] = [r for r in context.get('injuries',[]) if not r.get('official')] + official
    context['injury_coverage'] = 'partial' if context['injuries'] else 'unavailable'
    # A few verified absences never prove the entire squad has been checked.
    context['injuries_verified'] = False
    for lineup in context.get('estimated_lineups',[]):
        conflicts = []
        for p in lineup.get('players',[]):
            reports = [r for r in official if r['side']==lineup['side'] and p['name'] in [r['name'],*r.get('aliases',[])]]
            p['availability'] = reports[0]['status'] if reports else '待核实'
            p['conflict'] = bool(reports and reports[0]['status'] in ('官方确认缺阵', '官方确认停赛'))
            if p['conflict']: conflicts.append(p['name'])
        lineup['conflicts'] = conflicts
    return context
