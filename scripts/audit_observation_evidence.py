"""Read existing personnel inventories and make one fresh, non-AI evidence check."""
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console.observation_sources import fetch_summary
from worldcup_mvp.console.observation_evidence import evidence_packet

ROOT=Path(__file__).resolve().parents[1]


def main():
    previous=json.loads((ROOT/'data/research/observation-models/live-ai-smoke-20261109T110737.json').read_text(encoding='utf-8'))
    fixture,context,quotes,raw=fetch_summary(previous['fixture'])
    as_of=datetime.now(timezone.utc).isoformat()
    packet=evidence_packet(fixture,context,as_of,summary=raw,captured_at=fixture['captured_at'])
    inventory_path=ROOT/'data/research/big-five/2026-11-06/club-personnel/summary.json'
    inventory=json.loads(inventory_path.read_text(encoding='utf-8'))
    keys=('eligible_matches','club_seasons','nonempty_identity_checked_season_rosters','rosters_below_eleven',
          'rosters_listing_every_seen_espn_participant','listed_club_season_player_records',
          'played_but_not_listed_club_season_records','minutes_linked_sides','match_manager_sides','minute_ledger_rows')
    result={'audited_at':as_of,'fixture':fixture,'source_receipt':{'captured_at':fixture['captured_at'],'source_url':fixture['source_url']},
            'packet':packet,'raw_summary':raw,'historical_inventory':{'path':str(inventory_path.relative_to(ROOT)),
            'by_league':{cid:{k:r.get(k) for k in keys} for cid,r in inventory.items()},
            'totals':{k:sum(r.get(k,0) for r in inventory.values()) for k in keys}},
            'training_eligibility':{
                'personnel_scenario':'not_eligible_missing_historical_pregame_lineups_and_full_availability',
                'replacement_ability':'not_fitted_no_validated_identity_time_aware_replacement_feature',
                'full_cross_competition_load':'not_eligible_last_five_team_matches_is_incomplete_and_has_no_player_minutes',
                'coach_registration':'not_eligible_season_rosters_do_not_establish_effective_intervals',
                'existing_candidate':'same_league_historical_xg_goals_rest_only'}}
    dest=ROOT/'data/research/observation-models'/('evidence-audit-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'.json')
    dest.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(dest.relative_to(ROOT)),'coverage':packet['coverage'],
                      'facts':len(packet['evidence']),'excluded':packet['excluded'],
                      'totals':result['historical_inventory']['totals']},ensure_ascii=False))


if __name__=='__main__':main()
