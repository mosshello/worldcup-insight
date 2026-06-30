"""只用于供应商公开 HTTPS API 的轻量 JSON 客户端。"""

from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib import error, parse, request


class DataSourceError(RuntimeError):
    """外部数据源请求或响应无效。"""


class HttpJsonClient:
    """带超时、有限重试和密钥脱敏的 HTTPS JSON 客户端。"""

    RETRYABLE_STATUS = {403, 429, 500, 502, 503, 504}
    BACKOFF_403_SECONDS = (0.8, 2.0, 4.0)

    def __init__(
        self,
        base_url: str,
        *,
        provider_name: str,
        timeout: float = 10.0,
        max_retries: int = 2,
        opener: Callable[..., Any] = request.urlopen,
    ) -> None:
        parsed = parse.urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("数据源地址必须是有效的 HTTPS URL")
        self.base_url = base_url.rstrip("/")
        self.provider_name = provider_name
        self.timeout = timeout
        self.max_retries = max_retries
        self._opener = opener

    def get_json(
        self,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        query_string = parse.urlencode(
            {key: value for key, value in (query or {}).items() if value is not None}
        )
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query_string:
            url = f"{url}?{query_string}"
        req = request.Request(
            url,
            headers={"User-Agent": "worldcup-console-mvp/2.0", **(headers or {})},
            method="GET",
        )

        for attempt in range(self.max_retries + 1):
            try:
                with self._opener(req, timeout=self.timeout) as response:
                    body = response.read().decode("utf-8")
                try:
                    return json.loads(body)
                except json.JSONDecodeError as exc:
                    raise DataSourceError(f"{self.provider_name} 返回了无效 JSON") from exc
            except error.HTTPError as exc:
                if exc.code in self.RETRYABLE_STATUS and attempt < self.max_retries:
                    if exc.code == 403 and attempt < len(self.BACKOFF_403_SECONDS):
                        time.sleep(self.BACKOFF_403_SECONDS[attempt])
                    else:
                        time.sleep(0.25 * (attempt + 1))
                    continue
                raise DataSourceError(
                    f"{self.provider_name} 请求失败（HTTP {exc.code}）"
                ) from None
            except (error.URLError, TimeoutError, OSError):
                if attempt < self.max_retries:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                # 不拼接底层异常或URL，防止查询参数中的密钥进入日志。
                raise DataSourceError(f"{self.provider_name} 网络请求失败或超时") from None

        raise DataSourceError(f"{self.provider_name} 请求失败")
