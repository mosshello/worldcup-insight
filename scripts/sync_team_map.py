#!/usr/bin/env python3
"""从体彩可售赛事同步 team_name_map.json 新队名。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldcup_mvp.sporttery_api import SportteryApiError, fetch_matches
from worldcup_mvp.team_names import load_team_map, save_team_map, sync_team_map_entries


def main() -> int:
    parser = argparse.ArgumentParser(description="同步体彩队名到 team_name_map.json")
    parser.add_argument("--dry-run", action="store_true", help="仅打印将新增的队名，不写文件")
    parser.add_argument("--map", dest="map_path", default=None, help="自定义 map 路径")
    args = parser.parse_args()

    try:
        matches = fetch_matches()
    except SportteryApiError as exc:
        print(f"拉取失败：{exc}", file=sys.stderr)
        return 1

    names: list[str] = []
    for match in matches:
        names.append(match.get("home", ""))
        names.append(match.get("away", ""))

    mapping, added = sync_team_map_entries(names, team_map=load_team_map(args.map_path))
    if not added:
        print("无新队名需要同步。")
        return 0

    print(f"将新增 {len(added)} 个队名：")
    for name in added:
        print(f"  - {name}")

    if args.dry_run:
        return 0

    path = save_team_map(mapping, args.map_path)
    print(f"已写入 {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
