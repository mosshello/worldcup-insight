"""世界杯盘口可视化仪表盘 HTTP 服务。"""

from __future__ import annotations

import argparse
import json
import mimetypes
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from worldcup_mvp.cache_refresher import refresh_sporttery_cache, start_background_refresh
from worldcup_mvp.dashboard_data import (
    get_fusion_prediction,
    get_history_dashboard,
    get_overview,
    get_sporttery_matches,
    get_upcoming_score_predictions,
    list_history_files,
)

PROJECT_ROOT = Path(__file__).resolve().parent
WEB_ROOT = PROJECT_ROOT / "web"

_CLIENT_GONE_ERRORS = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "WorldcupInsightDashboard/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[dashboard] {self.address_string()} - {format % args}")

    def _safe_write(self, data: bytes) -> None:
        try:
            self.wfile.write(data)
        except _CLIENT_GONE_ERRORS:
            return

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self._safe_write(body)
        except _CLIENT_GONE_ERRORS:
            return

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_type, _ = mimetypes.guess_type(str(path))
        content_type = content_type or "application/octet-stream"
        data = path.read_bytes()

        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self._safe_write(data)
        except _CLIENT_GONE_ERRORS:
            return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path

        try:
            if route in ("/", "/index.html"):
                self._send_file(WEB_ROOT / "index.html")
                return

            if route.startswith("/static/"):
                relative = route.removeprefix("/static/")
                target = (WEB_ROOT / relative).resolve()
                if WEB_ROOT.resolve() not in target.parents and target != WEB_ROOT.resolve():
                    self.send_error(HTTPStatus.FORBIDDEN)
                    return
                self._send_file(target)
                return

            if route == "/api/overview":
                self._send_json(get_overview())
                return

            if route == "/api/histories":
                self._send_json({"histories": list_history_files()})
                return

            if route == "/api/sporttery/matches":
                self._send_json(get_sporttery_matches())
                return

            if route == "/api/sporttery/predictions":
                self._send_json(get_upcoming_score_predictions())
                return

            if route == "/api/cache/refresh":
                self._send_json(refresh_sporttery_cache())
                return

            if route.startswith("/api/sporttery/predict/"):
                match_id = urllib.parse.unquote(route.removeprefix("/api/sporttery/predict/"))
                query = urllib.parse.parse_qs(parsed.query)
                foreign = (query.get("foreign") or ["fox"])[0]
                self._send_json(get_fusion_prediction(match_id=match_id, foreign_source=foreign))
                return

            if route.startswith("/api/history/"):
                filename = urllib.parse.unquote(route.removeprefix("/api/history/"))
                self._send_json(get_history_dashboard(filename))
                return

            self.send_error(HTTPStatus.NOT_FOUND)
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
        except _CLIENT_GONE_ERRORS:
            return
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> int:
    parser = argparse.ArgumentParser(description="启动世界杯盘口可视化仪表盘")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument(
        "--cache-interval",
        type=float,
        default=300.0,
        help="后台刷新体彩缓存间隔（秒），0 表示关闭",
    )
    args = parser.parse_args()

    if not WEB_ROOT.exists():
        raise SystemExit(f"缺少前端目录：{WEB_ROOT}")

    initial = refresh_sporttery_cache()
    print(f"[cache-refresh] startup: {initial.get('message') or initial.get('error') or initial}")

    _, stop_event = start_background_refresh(args.cache_interval)

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    url = f"http://{args.host}:{args.port}"
    print("世界杯盘口可视化仪表盘")
    print(f"访问地址：{url}")
    if args.cache_interval > 0:
        print(f"缓存刷新：每 {args.cache_interval:.0f} 秒")
    print("按 Ctrl+C 停止服务")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止服务")
    finally:
        if stop_event is not None:
            stop_event.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
