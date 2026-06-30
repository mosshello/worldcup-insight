"""从环境变量或 .env 文件读取配置。"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path | None = None) -> None:
    """将 .env 中的 KEY=VALUE 写入 os.environ（不覆盖已有环境变量）。"""
    file_path = Path(path) if path else Path(__file__).resolve().parent.parent / ".env"
    if not file_path.exists():
        return

    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_odds_api_key() -> str | None:
    load_dotenv()
    key = os.environ.get("ODDS_API_KEY", "").strip()
    return key or None
