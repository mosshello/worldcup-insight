"""CI / 本地：为完场偏差场次批量生成 AI 复盘并写入 data/ai_reviews.json。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from worldcup_mvp.ai_review_cache import auto_review_finished_deviations
from worldcup_mvp.cache_refresher import refresh_sporttery_cache


def main() -> int:
    refresh = refresh_sporttery_cache()
    print(refresh.get("message") or refresh.get("error") or refresh)

    result = auto_review_finished_deviations(lookback_days=14)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result.get("skipped") and not result.get("configured"):
        print("提示：在 GitHub 仓库 Settings → Secrets 添加 DEEPSEEK_API_KEY，或在服务器环境变量中配置。")
        return 0

    if result.get("errors"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
