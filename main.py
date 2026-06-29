"""世界杯胜平负控制台 MVP 入口。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from worldcup_mvp import load_match_file
from worldcup_mvp.analyzer import OUTCOME_LABELS


DEFAULT_DATA = Path(__file__).parent / "data" / "matches_2026-06-29.json"


def _percentage(value: float) -> str:
    return f"{value:.1%}"


def _display_time(iso_time: str | None) -> str:
    if not iso_time:
        return "未提供"
    return iso_time.replace("T", " ").replace("+08:00", "（北京时间）")


def print_report(metadata: dict[str, Any], matches: list[dict[str, Any]]) -> None:
    print("世界杯胜平负控制台 MVP")
    print(f"数据快照：{metadata.get('data_as_of') or '未提供'}")
    print(f"赔率来源：{metadata.get('source') or '未提供'}")
    print("说明：以下均为常规 90 分钟市场概率，不构成赛果保证。")

    for index, match in enumerate(matches, start=1):
        odds = match["odds"]
        probabilities = match["probabilities"]
        ranking = match["ranking"]
        print("\n" + "=" * 68)
        print(f"{index}. {match['home']} vs {match['away']}｜{match.get('stage') or '阶段未提供'}")
        print(f"赛事日期：{match.get('fixture_date') or '未提供'}")
        print(f"开赛时间：{_display_time(match.get('kickoff_beijing'))}")
        print(
            "赔率："
            f"主胜 {odds['home']:.2f}｜平 {odds['draw']:.2f}｜客胜 {odds['away']:.2f}"
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
        print("分析：")
        for line in match["analysis"]:
            print(f"- {line}")


def main() -> int:
    parser = argparse.ArgumentParser(description="输出足球比赛胜平负市场概率与中文分析")
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_DATA,
        help="比赛 JSON 文件路径",
    )
    parser.add_argument("--json", action="store_true", help="改为输出机器可读 JSON")
    args = parser.parse_args()

    try:
        metadata, matches = load_match_file(args.file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps({"metadata": metadata, "matches": matches}, ensure_ascii=False, indent=2))
    else:
        print_report(metadata, matches)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
