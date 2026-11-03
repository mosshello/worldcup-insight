"""Read-only bridge to the mutable legacy journal; not a verified frozen forecast."""

import json
from .sources import parse_date


def read_journal(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("entries", []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        return []


def for_match(match, entries):
    candidates = []
    for row in entries:
        if not isinstance(row, dict) or str(row.get("match_id")) != str(match.get("provider_id")):
            continue
        if (row.get("home"), row.get("away")) != (match.get("home"), match.get("away")):
            continue
        try:
            kickoff = parse_date(row["kickoff_beijing"])
            if abs((kickoff - parse_date(match["kickoff"])).total_seconds()) > 60:
                continue
            before = parse_date(row["recorded_at"]) < kickoff
        except (ValueError, KeyError, TypeError):
            continue
        candidates.append({
            "direction": row.get("initial_direction") or row.get("direction") or "未记录",
            "latest_direction": row.get("direction"),
            "score": row.get("initial_predicted_score") or row.get("predicted_score"),
            "recorded_at": row["recorded_at"], "recorded_before_kickoff": before,
            "source": "旧版预测日志", "verified_archive": False,
            "notice": "旧日志为可更新记录，缺少完整赛前输入快照；仅供查阅，不计入新版命中与模型评估。",
        })
    return max(candidates, key=lambda r: parse_date(r["recorded_at"]), default=None)
