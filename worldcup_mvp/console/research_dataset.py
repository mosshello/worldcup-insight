"""Explicit local-experiment admission; does not assert third-party permission."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from .store import fingerprint
from .training import atomic_json, validate_dataset


def prepare_local_research(staging_path, output, mappings, authorization_reference):
    if not authorization_reference:
        raise ValueError("缺少本地训练请求记录")
    data = json.loads(Path(staging_path).read_text(encoding="utf8"))
    if data.get("status") != "collected" or data.get("digest") != fingerprint({"matches": data["matches"], "coverage": data["coverage"]}):
        raise ValueError("历史数据未采集完成或摘要不符")
    for coverage in data["coverage"]:
        unresolved = [e for e in coverage.get("issues", []) if not e.endswith("场开赛时间差异待核查")]
        if coverage.get("conflicts") or unresolved:
            raise ValueError(f"{coverage['id']}：仍有采集或比分问题")
    rows, excluded = [], []
    for original in data["matches"]:
        if original.get("kickoff_review_required"):
            excluded.append({"id": original["id"], "reason": "开赛时间差异待核查"})
            continue
        if original.get("independent_verification") == "conflict":
            raise ValueError("历史比分冲突")
        row = dict(original)
        kickoff = datetime.fromisoformat(row["kickoff"])
        observed = datetime.fromisoformat(row["collected_at"])
        assumed = kickoff + timedelta(days=7)
        available = min(observed, assumed)
        row.update(result_available_at=available.isoformat(), training_eligible=True,
                   availability_basis="observed_at_collection" if observed < assumed else "assumed_7_day_lag")
        rows.append(row)
    candidate = {"manifest": {
        "usage_mode": "local_research", "local_training_authorized": True,
        "authorization_reference": authorization_reference,
        "rights_status": "unverified", "commercial_use_permitted": False,
        "source_url": "https://liansai.500.com/", "dataset_digest": data["digest"],
        "availability_policy": "historical_7_day_lag_or_observed",
        "quality_basis": "结构校验及官方抽查；未声称全量双源核验",
        "prepared_at": datetime.now(timezone.utc).isoformat(), "excluded": excluded,
    }, "matches": rows, "team_mappings": mappings}
    validate_dataset(candidate)
    atomic_json(Path(output), candidate)
    return candidate


if __name__ == "__main__":
    import argparse
    from .history_pipeline import competition_staging
    from .catalog import classify
    from .model_registry import import_competition
    from .league_data import load_history, team_mappings
    from .store import Store
    from .training import DATASET
    parser = argparse.ArgumentParser(description="建立显式请求的本地实验训练库，不声明商用授权")
    parser.add_argument("--request-reference", required=True, help="用户本地训练请求的记录")
    parser.add_argument("--competition", required=True)
    parser.add_argument("--import-prepared", action="store_true")
    parser.add_argument("--staging", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    args.staging = args.staging or competition_staging(args.competition)
    args.output = args.output or args.staging.with_name("research."+args.competition+".json")
    data = load_history(args.staging)
    matches = [m for m in Store().list_matches() if classify(m)["id"] == args.competition]
    mappings = team_mappings(matches, data)
    result = prepare_local_research(args.staging, args.output, mappings, args.request_reference)
    if args.import_prepared:
        import_competition(result,args.competition)
    print(json.dumps({"rows": len(result["matches"]), "team_mappings": len(mappings),
                      "excluded": result["manifest"]["excluded"], "usage_mode": "local_research"}, ensure_ascii=False))
