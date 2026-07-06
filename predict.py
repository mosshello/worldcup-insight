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
from worldcup_mvp.model_training import build_training_report, ingest_settled_predictions
from worldcup_mvp.tournament_forecast import build_tournament_forecast
from worldcup_mvp.statistical_model import (
    load_statistical_model,
    predict_statistical_match,
    train_statistical_model,
)


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


def cmd_train(args: argparse.Namespace) -> int:
    ingestion = ingest_settled_predictions()
    report = build_training_report()
    report["ingestion"] = ingestion
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"模型状态：{report['status']}｜有效样本 {report['valid_samples']}/"
            f"{report['minimum_samples']}｜方向命中率 {report['direction_hit_rate']}"
        )
        print(report["note"])
    return 0


def cmd_tournament(args: argparse.Namespace) -> int:
    report = build_tournament_forecast()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print("世界杯冠军 / 决赛概率榜")
    for index, item in enumerate(report["rankings"][:10], 1):
        print(
            f"{index:>2}. {item['team']}｜冠军 {item['champion_probability']:.1%}｜"
            f"进决赛 {item['final_probability']:.1%}｜亚军 {item['runner_up_probability']:.1%}"
        )
    print("\n最可能冠亚军对阵")
    for item in report["final_pairs"][:5]:
        print(f"- {item['pair']}：{item['probability']:.1%}")
    return 0


def cmd_train_worldcup(args: argparse.Namespace) -> int:
    try:
        artifact = train_statistical_model(refresh=args.refresh)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(artifact, ensure_ascii=False, indent=2))
    else:
        counts = artifact["counts"]
        metrics = artifact["metrics"]["world_cup_2026"]
        print(
            f"{artifact['model_version']} 训练完成｜基础 {counts['foundation']} 场｜"
            f"2026封存验证 {counts['world_cup_2026_test']} 场"
        )
        print(
            f"世界杯验证 LogLoss {metrics['log_loss']}｜Brier {metrics['brier_score']}｜"
            f"方向准确率 {metrics['direction_accuracy']}"
        )
        print(artifact["activation"]["reason"])
    return 0


def cmd_model_report(args: argparse.Namespace) -> int:
    artifact = load_statistical_model()
    if artifact is None:
        print("尚未训练统计模型，请先运行 train-worldcup。", file=sys.stderr)
        return 1
    payload = {key: artifact.get(key) for key in ("model_version", "trained_at", "status", "data_source", "boundaries", "counts", "parameters", "metrics", "activation")}
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else (
        f"模型 {payload['model_version']}｜状态 {payload['status']}｜训练 {payload['counts']['train']} 场｜"
        f"验证 {payload['counts']['validation']} 场｜2026测试 {payload['counts']['world_cup_2026_test']} 场"
    ))
    return 0


def cmd_model_predict(args: argparse.Namespace) -> int:
    prediction = predict_statistical_match(args.home, args.away, neutral=args.neutral)
    if prediction is None:
        print("尚未训练统计模型，请先运行 train-worldcup。", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(prediction, ensure_ascii=False, indent=2))
    else:
        had = prediction["had"]
        print(f"{args.home} vs {args.away}｜xG {prediction['expected_goals']['home']}-{prediction['expected_goals']['away']}")
        print(f"主/平/客 {had['home']:.1%}/{had['draw']:.1%}/{had['away']:.1%}")
        print("比分：" + " / ".join(f"{item['score']} {item['probability']:.1%}" for item in prediction["top_scores"][:3]))
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
    predict_parser.add_argument("--foreign", choices=("auto", "fox", "api", "none"), default="auto")
    predict_parser.add_argument("--json", action="store_true")
    predict_parser.set_defaults(func=cmd_predict)

    slate_parser = sub.add_parser("slate", help="完整分析全部已开售场次")
    slate_parser.add_argument("--json", action="store_true")
    slate_parser.set_defaults(func=cmd_slate)

    audit_parser = sub.add_parser("audit-training", help="审计训练语料是否存在时间泄漏或伪赛果")
    audit_parser.add_argument("--json", action="store_true")
    audit_parser.set_defaults(func=cmd_audit_training)

    train_parser = sub.add_parser("train", help="评估训练语料并在达到门槛后允许校准")
    train_parser.add_argument("--json", action="store_true")
    train_parser.set_defaults(func=cmd_train)

    tournament_parser = sub.add_parser("tournament", help="输出世界杯冠军与冠亚军概率榜")
    tournament_parser.add_argument("--json", action="store_true")
    tournament_parser.set_defaults(func=cmd_tournament)

    train_worldcup_parser = sub.add_parser("train-worldcup", help="训练 Elo + 双 Poisson 并封存验证2026世界杯")
    train_worldcup_parser.add_argument("--refresh", action="store_true", help="刷新公开历史赛果")
    train_worldcup_parser.add_argument("--json", action="store_true")
    train_worldcup_parser.set_defaults(func=cmd_train_worldcup)

    model_report_parser = sub.add_parser("model-report", help="查看统计模型版本、边界和验证指标")
    model_report_parser.add_argument("--json", action="store_true")
    model_report_parser.set_defaults(func=cmd_model_report)

    model_predict_parser = sub.add_parser("model-predict", help="用统计影子模型预测单场")
    model_predict_parser.add_argument("--home", required=True)
    model_predict_parser.add_argument("--away", required=True)
    model_predict_parser.add_argument("--neutral", action="store_true")
    model_predict_parser.add_argument("--json", action="store_true")
    model_predict_parser.set_defaults(func=cmd_model_predict)
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "predict" and not args.match_id and not (args.home and args.away):
        parser.error("predict 需要 --match-id 或 --home 与 --away")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
