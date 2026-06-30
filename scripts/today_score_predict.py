"""体彩未开赛赛事比分预测 CLI。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from worldcup_mvp.score_predictor import list_upcoming_matches, predict_upcoming_scores
from worldcup_mvp.sporttery_api import SportteryApiError


def cmd_list(args: argparse.Namespace) -> int:
    payload = list_upcoming_matches()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("success") else 1
    if not payload.get("success"):
        print(payload.get("error"), file=sys.stderr)
        return 1
    if not payload["matches"]:
        print("当前体彩竞彩网没有未开赛的足球赛事。")
        return 0
    for match in payload["matches"]:
        had = match["pools"].get("had") or {}
        print(
            f"{match['match_id']} | {match['home']} vs {match['away']} | "
            f"{match.get('league')} | {match.get('kickoff_beijing')} | "
            f"胜平负 {had.get('home', '-')}/{had.get('draw', '-')}/{had.get('away', '-')}"
        )
    return 0


def cmd_predict(args: argparse.Namespace) -> int:
    try:
        results = predict_upcoming_scores()
    except SportteryApiError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"predictions": results, "count": len(results)}, ensure_ascii=False, indent=2))
        return 0

    if not results:
        print("当前体彩竞彩网没有未开赛的足球赛事。")
        return 0

    for item in results:
        print("=" * 60)
        print(
            f"{item['home']} vs {item['away']}｜{item.get('league') or '竞彩'}｜"
            f"{item.get('kickoff_beijing') or '开赛时间待定'}"
        )
        print(f"胜平负方向：{item['direction']}（次选 {item['second']}）｜信心 {item['confidence']}")
        print(f"体彩与外网一致：{'是' if item['aligned_with_fox'] else '否'}")
        print(f"比分预测：{item['predicted_score']}")
        if item["alt_scores"]:
            print(f"备选比分：{'；'.join(item['alt_scores'])}")
        print(f"体彩胜平负：{item['sporttery_had']}")
        print(f"体彩让球：{item['sporttery_hhad']}")
        if item["hhad_direction"]:
            print(f"让球方向：{item['hhad_direction']}")
        print(f"外网 Moneyline：{item['fox_moneyline']}")
        if item["direction_note"]:
            print(f"提示：{item['direction_note']}")

    print("=" * 60)
    print(f"共 {len(results)} 场未开赛赛事（数据来源：中国体育彩票竞彩网）")
    print("说明：比分来自体彩「猜比分」固定奖金最低项；方向来自体彩胜平负去水概率。")
    print("仅为市场定价推演，不构成投注建议或赛果保证。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="体彩未开赛赛事比分预测")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="列出体彩未开赛足球赛事")
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(func=cmd_list)

    predict_parser = sub.add_parser("predict", help="预测全部未开赛赛事比分与方向")
    predict_parser.add_argument("--json", action="store_true")
    predict_parser.set_defaults(func=cmd_predict)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
