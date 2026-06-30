"""体彩主盘 + 外网辅盘融合预测 CLI。"""

from __future__ import annotations

import argparse
import json
import sys

from worldcup_mvp.dashboard_data import get_fusion_prediction, get_sporttery_matches


def cmd_list(_: argparse.Namespace) -> int:
    payload = get_sporttery_matches()
    if not payload.get("success"):
        print(payload.get("error"), file=sys.stderr)
        return 1
    if _.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    for match in payload["matches"]:
        had = match["pools"].get("had") or {}
        print(
            f"{match['match_id']} | {match['home']} vs {match['away']} | "
            f"{match.get('league')} | "
            f"胜平负 {had.get('home', '-')}/{had.get('draw', '-')}/{had.get('away', '-')}"
        )
    return 0


def cmd_predict(args: argparse.Namespace) -> int:
    try:
        payload = get_fusion_prediction(
            match_id=args.match_id,
            home=args.home,
            away=args.away,
            foreign_source=args.foreign,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    prediction = payload["prediction"]
    print("体彩主盘 + 外网辅盘 融合预测")
    print(f"{prediction['home']} vs {prediction['away']}（体彩 ID {prediction['match_id']}）")
    print(f"联赛：{prediction.get('league') or '—'}")
    print(f"方向：{prediction['direction']}｜次选 {prediction['second_direction']}｜信心 {prediction['confidence']}")
    print(f"体彩返还率：{prediction['return_rate']:.1%}")
    if prediction["foreign"]["source"]:
        print(f"外网参考：{prediction['foreign']['source']}")
    print("\n分析：")
    for line in prediction["analysis"]:
        print(f"- {line}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="体彩主盘融合外网走势预测")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="列出体彩当前可售足球赛事")
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(func=cmd_list)

    predict_parser = sub.add_parser("predict", help="融合预测单场比赛方向")
    predict_parser.add_argument("--match-id", help="体彩 matchId")
    predict_parser.add_argument("--home", help="主队")
    predict_parser.add_argument("--away", help="客队")
    predict_parser.add_argument("--foreign", choices=("fox", "api", "none"), default="fox")
    predict_parser.add_argument("--json", action="store_true")
    predict_parser.set_defaults(func=cmd_predict)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "predict" and not args.match_id and not (args.home and args.away):
        parser.error("predict 需要 --match-id 或 --home 与 --away")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
