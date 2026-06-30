"""欧赔与亚盘快照的读取、校验与历史持久化。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_european(odds: dict[str, Any]) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for key in ("home", "draw", "away"):
        value = odds.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"欧赔 {key} 必须是数字")
        if value <= 1:
            raise ValueError(f"欧赔 {key} 必须大于 1.00")
        normalized[key] = float(value)
    return normalized


def _validate_asian_handicap(market: dict[str, Any]) -> dict[str, float]:
    line = market.get("line")
    if isinstance(line, bool) or not isinstance(line, (int, float)):
        raise ValueError("亚盘 line 必须是数字")
    normalized: dict[str, float] = {"line": float(line)}
    for key in ("home", "away"):
        value = market.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"亚盘 {key} 水位必须是数字")
        if not 0 < value < 2:
            raise ValueError(f"亚盘 {key} 水位通常应在 0 到 2 之间")
        normalized[key] = float(value)
    return normalized


def validate_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """校验单条快照，至少包含欧赔或亚盘之一。"""
    european = snapshot.get("european")
    asian = snapshot.get("asian_handicap")
    if european is None and asian is None:
        raise ValueError("快照必须包含 european 或 asian_handicap")

    normalized: dict[str, Any] = {
        "recorded_at": snapshot.get("recorded_at") or _utc_now_iso(),
    }
    if european is not None:
        normalized["european"] = _validate_european(european)
    if asian is not None:
        normalized["asian_handicap"] = _validate_asian_handicap(asian)
    if snapshot.get("source"):
        normalized["source"] = snapshot["source"]
    return normalized


def load_history(path: str | Path) -> dict[str, Any]:
    """读取盘口历史文件。"""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"盘口历史文件不存在：{file_path}")

    with file_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    for field in ("match_id", "home", "away", "snapshots"):
        if field not in payload:
            raise ValueError(f"盘口历史缺少字段：{field}")

    snapshots = payload["snapshots"]
    if not isinstance(snapshots, list):
        raise ValueError("snapshots 必须是数组")

    payload["snapshots"] = [validate_snapshot(item) for item in snapshots]
    return payload


def append_snapshot(
    path: str | Path,
    snapshot: dict[str, Any],
    *,
    match_id: str | None = None,
    home: str | None = None,
    away: str | None = None,
) -> dict[str, Any]:
    """追加一条快照；若文件不存在则创建。"""
    file_path = Path(path)
    normalized = validate_snapshot(snapshot)

    if file_path.exists():
        payload = load_history(file_path)
        last = payload["snapshots"][-1] if payload["snapshots"] else None
        if last and _snapshots_equal(last, normalized):
            return payload
        payload["snapshots"].append(normalized)
    else:
        if not all([match_id, home, away]):
            raise ValueError("新建历史文件需要提供 match_id、home、away")
        payload = {
            "match_id": match_id,
            "home": home,
            "away": away,
            "snapshots": [normalized],
        }

    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return payload


def _snapshots_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """忽略时间戳，比较盘口数值是否相同。"""
    for key in ("european", "asian_handicap"):
        if left.get(key) != right.get(key):
            return False
    return True
