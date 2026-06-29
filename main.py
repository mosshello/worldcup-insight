"""世界杯胜平负控制台 MVP 入口。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from worldcup_mvp import (
    ConfigurationError,
    DataSourceError,
    UnifiedDataManager,
    analyze_match,
    load_backtest_file,
    load_match_file,
)
from worldcup_mvp.analyzer import OUTCOME_LABELS


def _percentage(value: float) -> str:
    return f"{value:.1%}"


def _display_time(iso_time: str | None) -> str:
    if not iso_time:
        return "未提供"
    return iso_time.replace("T", " ").replace("+08:00", "（北京时间）")


def print_report(metadata: dict[str, Any], matches: list[dict[str, Any]]) -> None:
    print("世界杯综合分析控制台 MVP")
    print(f"数据快照：{metadata.get('data_as_of') or '未提供'}")
    print(f"赔率来源：{metadata.get('source') or '未提供'}")
    print("说明：以下均为常规 90 分钟概率；综合概率是未校准的透明启发式结果。")

    for index, match in enumerate(matches, start=1):
        odds = match["odds"]
        probabilities = match["probabilities"]
        ranking = match["ranking"]
        print("\n" + "=" * 68)
        print(f"{index}. {match['home']} vs {match['away']}｜{match.get('stage') or '阶段未提供'}")
        print(f"赛事日期：{match.get('fixture_date') or '未提供'}")
        print(f"开赛时间：{_display_time(match.get('kickoff_beijing'))}")
        print(
            "欧赔参考（Polymarket）："
            f"主胜 {odds['home']:.2f}｜平 {odds['draw']:.2f}｜客胜 {odds['away']:.2f}"
        )
        sporttery = match.get("sporttery")
        if sporttery:
            had = sporttery["had"]
            hhad = sporttery["hhad"]
            print(
                f"体彩HAD（{sporttery.get('match_number') or '编号未知'}）："
                f"胜 {had['odds']['home']:.2f}｜平 {had['odds']['draw']:.2f}｜"
                f"负 {had['odds']['away']:.2f}｜更新 {had['updated_at']}"
            )
            print(
                f"体彩HHAD（主队{hhad['handicap']:+d}）："
                f"让胜 {hhad['odds']['home']:.2f}｜让平 {hhad['odds']['draw']:.2f}｜"
                f"让负 {hhad['odds']['away']:.2f}｜更新 {hhad['updated_at']}"
            )
            sporttery_probabilities = match["sporttery_probabilities"]
            print(
                "体彩HAD去水概率："
                f"胜 {_percentage(sporttery_probabilities['home'])}｜"
                f"平 {_percentage(sporttery_probabilities['draw'])}｜"
                f"负 {_percentage(sporttery_probabilities['away'])}"
            )
        print(
            "去水概率："
            f"主胜 {_percentage(probabilities['home'])}｜"
            f"平 {_percentage(probabilities['draw'])}｜"
            f"客胜 {_percentage(probabilities['away'])}"
        )
        print(f"市场水位：{_percentage(match['overround'])}")
        print("市场排序：" + " > ".join(OUTCOME_LABELS[item] for item in ranking))
        print(
            f"首选：{OUTCOME_LABELS[match['pick']]}｜"
            f"次选：{OUTCOME_LABELS[match['second_pick']]}｜"
            f"信心：{match['confidence']}"
        )
        if match["context_available"]:
            context = match["context_probabilities"]
            print(
                "综合概率："
                f"主胜 {_percentage(context['home'])}｜"
                f"平 {_percentage(context['draw'])}｜"
                f"客胜 {_percentage(context['away'])}"
            )
            print(
                f"综合首选：{OUTCOME_LABELS[match['context_pick']]}｜"
                f"综合信心：{match['context_confidence']}｜"
                f"上下文边际：{match['context_edge']:+.3f}"
            )
        print("分析：")
        for line in match["analysis"]:
            print(f"- {line}")


def print_backtest(metadata: dict[str, Any], results: list[dict[str, Any]]) -> None:
    print("世界杯综合分析回测")
    print(f"赛前数据截止：{metadata.get('data_as_of') or '未提供'}")
    for index, result in enumerate(results, start=1):
        print("\n" + "=" * 68)
        print(f"{index}. {result['home']} vs {result['away']}")
        print(
            f"预测：{result['predicted_label']}｜实际：{result['actual_label']} "
            f"{result['score']}｜{'命中' if result['hit'] else '未命中'}"
        )
        market = result["market_probabilities"]
        context = result["context_probabilities"]
        print(
            "市场概率："
            f"主胜 {_percentage(market['home'])}｜平 {_percentage(market['draw'])}｜"
            f"客胜 {_percentage(market['away'])}"
        )
        print(
            "综合概率："
            f"主胜 {_percentage(context['home'])}｜平 {_percentage(context['draw'])}｜"
            f"客胜 {_percentage(context['away'])}"
        )
        for line in result["analysis"]:
            print(f"- {line}")
        print(f"限制：{result['sample_note']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="从统一实时接口输出世界杯综合分析")
    parser.add_argument(
        "--file",
        type=Path,
        help="重放或回测用的本地 JSON 快照；不提供时默认调用实时接口",
    )
    parser.add_argument("--json", action="store_true", help="改为输出机器可读 JSON")
    parser.add_argument("--backtest", action="store_true", help="按赛前截止时间执行历史回测")
    parser.add_argument(
        "--date",
        default=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
        help="赛事日期，格式 YYYY-MM-DD；按赛程时区查询",
    )
    parser.add_argument("--doctor", action="store_true", help="检查环境变量和两个实时接口")
    parser.add_argument("--save-snapshot", type=Path, help="保存脱敏后的统一数据快照")
    args = parser.parse_args()

    try:
        if args.doctor:
            report = UnifiedDataManager.from_env().doctor()
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print("无密钥公开数据接口检查通过")
                for provider in report["providers"]:
                    print(f"- {provider['provider']}：{'正常' if provider['ok'] else '异常'}")
            return 0
        if args.backtest:
            if args.file is None:
                parser.error("--backtest 必须同时提供 --file")
            metadata, matches = load_backtest_file(args.file)
        elif args.file is not None:
            metadata, matches = load_match_file(args.file)
        else:
            manager = UnifiedDataManager.from_env()
            payload = manager.collect(args.date)
            if args.save_snapshot:
                manager.save_snapshot(payload, args.save_snapshot)
            metadata = {
                "data_as_of": payload["data_as_of"],
                "source": payload["source"],
                "sources": payload["sources"],
                "quality_checks": payload["quality_checks"],
                "odds_format": payload["odds_format"],
                "model": "market-plus-transparent-context-v1",
            }
            matches = [analyze_match(match) for match in payload["matches"]]
    except (ConfigurationError, DataSourceError, OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    if args.json:
        key = "backtests" if args.backtest else "matches"
        print(json.dumps({"metadata": metadata, key: matches}, ensure_ascii=False, indent=2))
    elif args.backtest:
        print_backtest(metadata, matches)
    else:
        print_report(metadata, matches)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
