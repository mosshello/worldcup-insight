"""实时盘口采集与变动分析 CLI。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from worldcup_mvp.collector import create_collector
from worldcup_mvp.env_config import get_odds_api_key, load_dotenv
from worldcup_mvp.movement_analyzer import analyze_movement
from worldcup_mvp.odds_snapshot import append_snapshot, load_history
from worldcup_mvp.the_odds_api import OddsApiError, fetch_odds, list_sports


DEFAULT_HISTORY = Path(__file__).parent / "data" / "odds_history_bra-jpn.json"
DEFAULT_FEED = Path(__file__).parent / "data" / "odds_live_feed.example.json"


def _print_movement_report(result: dict[str, Any]) -> None:
    print("盘口变动分析")
    print(f"{result['home']} vs {result['away']}（{result['match_id']}）")
    print(f"对比区间：{result['from_snapshot']} → {result['to_snapshot']}")
    print(f"窗口快照数：{result['window_size']}")
    print("\n分析：")
    for line in result["analysis"]:
        print(f"- {line}")


def _build_collector(args: argparse.Namespace):
    load_dotenv()
    sport = args.sport or os.environ.get("ODDS_SPORT", "soccer_fifa_world_cup")
    regions = args.regions or os.environ.get("ODDS_REGIONS", "uk,eu,us")
    return create_collector(
        args.source,
        home=args.home,
        away=args.away,
        feed_path=args.feed,
        sport=sport,
        event_id=args.event_id,
        regions=regions,
        api_key=get_odds_api_key(),
        fox_url=args.fox_url,
        sporttery_match_id=args.event_id,
    )


def cmd_record(args: argparse.Namespace) -> int:
    try:
        collector = _build_collector(args)
        snapshot = collector.fetch(args.match_id)
    except (ValueError, OddsApiError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if snapshot is None:
        print("未获取到盘口数据", file=sys.stderr)
        return 1

    payload = append_snapshot(
        args.history,
        snapshot,
        match_id=args.match_id,
        home=args.home,
        away=args.away,
    )
    print(f"已写入快照，当前共 {len(payload['snapshots'])} 条")
    print(f"来源：{snapshot.get('source', 'unknown')}")

    if hasattr(collector, "last_meta") and collector.last_meta:
        print(f"API 赛事 ID：{collector.last_meta['event_id']}")
    if hasattr(collector, "last_usage") and collector.last_usage:
        remaining = collector.last_usage.get("remaining")
        if remaining is not None:
            print(f"API 剩余配额：{remaining}")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    history = load_history(args.history)
    result = analyze_movement(history, lookback=args.lookback)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_movement_report(result)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    try:
        collector = _build_collector(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"数据源：{args.source}，每 {args.interval}s 采集一次，Ctrl+C 停止")

    try:
        while True:
            try:
                snapshot = collector.fetch(args.match_id)
            except (OddsApiError, RuntimeError) as exc:
                print(f"[错误] {exc}", file=sys.stderr)
                snapshot = None

            if snapshot is not None:
                payload = append_snapshot(
                    args.history,
                    snapshot,
                    match_id=args.match_id,
                    home=args.home,
                    away=args.away,
                )
                count = len(payload["snapshots"])
                print(f"[{snapshot['recorded_at']}] {snapshot.get('source')} → 累计 {count} 条")
                if count >= 2:
                    result = analyze_movement(payload, lookback=args.lookback)
                    if not args.quiet:
                        _print_movement_report(result)
                        print()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n已停止监听")
        return 0


def cmd_list_events(args: argparse.Namespace) -> int:
    load_dotenv()
    api_key = get_odds_api_key()
    if not api_key:
        print("list-events 需要 ODDS_API_KEY", file=sys.stderr)
        return 1

    sport = args.sport or os.environ.get("ODDS_SPORT", "soccer_fifa_world_cup")
    regions = args.regions or os.environ.get("ODDS_REGIONS", "uk,eu,us")

    try:
        if args.sports:
            sports = list_sports(api_key)
            print(json.dumps(sports, ensure_ascii=False, indent=2))
            return 0

        events, usage = fetch_odds(api_key, sport=sport, regions=regions)
    except OddsApiError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    rows = [
        {
            "event_id": event["id"],
            "home_team": event["home_team"],
            "away_team": event["away_team"],
            "commence_time": event.get("commence_time"),
            "bookmakers": len(event.get("bookmakers", [])),
        }
        for event in events
    ]
    if args.json:
        print(json.dumps({"events": rows, "usage": usage}, ensure_ascii=False, indent=2))
    else:
        if not rows:
            print(f"{sport} 当前无可用赛事（可能未开赛或 sport_key 不正确）")
        for row in rows:
            print(
                f"{row['event_id']} | {row['home_team']} vs {row['away_team']} | "
                f"{row['commence_time']} | bookmakers={row['bookmakers']}"
            )
        if usage.get("remaining") is not None:
            print(f"\nAPI 剩余配额：{usage['remaining']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--history", type=Path, default=DEFAULT_HISTORY, help="盘口历史 JSON 路径")
    common.add_argument("--match-id", default="wc2026-r32-bra-jpn", help="内部比赛 ID")
    common.add_argument("--home", default="巴西", help="主队名称")
    common.add_argument("--away", default="日本", help="客队名称")
    common.add_argument(
        "--source",
        choices=("file", "sporttery", "api", "fox"),
        default="sporttery",
        help="数据源：sporttery=体彩官方，api=The Odds API，fox=FOX Sports，file=本地 feed",
    )
    common.add_argument("--feed", type=Path, default=DEFAULT_FEED, help="file 数据源路径")
    common.add_argument("--sport", help="The Odds API sport_key，默认 soccer_fifa_world_cup")
    common.add_argument("--regions", help="The Odds API 区域，如 uk,eu,us")
    common.add_argument("--event-id", help="The Odds API 赛事 ID（可替代队名匹配）")
    common.add_argument("--fox-url", help="自定义 FOX Sports 赔率页面 URL")

    parser = argparse.ArgumentParser(description="采集并分析欧赔/亚盘盘口变动")
    subparsers = parser.add_subparsers(dest="command", required=True)

    record_parser = subparsers.add_parser("record", parents=[common], help="采集一次并追加快照")
    record_parser.set_defaults(func=cmd_record)

    analyze_parser = subparsers.add_parser("analyze", parents=[common], help="分析已有盘口历史")
    analyze_parser.add_argument("--lookback", type=int, help="使用最近 N 条快照作为窗口")
    analyze_parser.add_argument("--json", action="store_true", help="输出 JSON")
    analyze_parser.set_defaults(func=cmd_analyze)

    watch_parser = subparsers.add_parser("watch", parents=[common], help="持续采集并分析")
    watch_parser.add_argument("--interval", type=float, default=300.0, help="轮询间隔（秒）")
    watch_parser.add_argument("--lookback", type=int, default=5, help="分析窗口快照数")
    watch_parser.add_argument("--quiet", action="store_true", help="仅记录，不打印分析")
    watch_parser.set_defaults(func=cmd_watch)

    list_parser = subparsers.add_parser("list-events", parents=[common], help="列出 The Odds API 可用赛事")
    list_parser.add_argument("--json", action="store_true", help="输出 JSON")
    list_parser.add_argument("--sports", action="store_true", help="列出全部 sport_key")
    list_parser.set_defaults(func=cmd_list_events)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
