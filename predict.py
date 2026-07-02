"""体彩主盘 + 外网辅盘融合预测 CLI。"""

from __future__ import annotations

import argparse
import json
import sys

from worldcup_mvp.dashboard_data import (
    get_fusion_prediction,
    get_sporttery_matches,
    get_upcoming_score_predictions,
)
from worldcup_mvp.training_store import audit_training_corpus


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


def cmd_slate(args: argparse.Namespace) -> int:
    """运行全部在售场次的完整融合分析。"""
    payload = get_upcoming_score_predictions()
    if not payload.get("success"):
        print(payload.get("error"), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    for item in payload.get("predictions") or []:
        print(
            f"{item.get('match_id')} | {item.get('home')} vs {item.get('away')} | "
            f"{item.get('direction')} | {item.get('predicted_score')} | "
            f"流程 {item.get('pipeline_status', 'pending')}"
        )
    return 0


def cmd_audit_training(args: argparse.Namespace) -> int:
    report = audit_training_corpus()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"训练语料：有效 {report['valid_count']} 条，非法 {report['invalid_count']} 条，"
            f"总计 {report['total_count']} 条"
        )
        for item in report.get("invalid_records") or []:
            print(f"- {item.get('sporttery_match_id') or '无ID'}：{'；'.join(item['errors'])}")
    return 1 if report["invalid_count"] else 0


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
    predict_parser.add_argument("--foreign", choices=("auto", "fox", "api", "none"), default="auto")
    predict_parser.add_argument("--json", action="store_true")
    predict_parser.set_defaults(func=cmd_predict)

    slate_parser = sub.add_parser("slate", help="完整分析全部已开售场次")
    slate_parser.add_argument("--json", action="store_true")
    slate_parser.set_defaults(func=cmd_slate)

    audit_parser = sub.add_parser("audit-training", help="审计训练语料是否存在时间泄漏或伪赛果")
    audit_parser.add_argument("--json", action="store_true")
    audit_parser.set_defaults(func=cmd_audit_training)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "predict" and not args.match_id and not (args.home and args.away):
        parser.error("predict 需要 --match-id 或 --home 与 --away")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
