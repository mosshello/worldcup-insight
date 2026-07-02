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
    export_predictions_csv,
    export_predictions_payload,
    get_bet_simulation,
    get_fusion_prediction,
    get_history_dashboard,
    get_overview,
    get_sporttery_matches,
    get_upcoming_score_predictions,
    list_history_files,
    run_match_chat_analysis,
)
from worldcup_mvp.ai_analyst import get_analyze_status
from worldcup_mvp.ai_review_cache import (
    generate_review_for_match_id,
    get_review,
    list_reviews,
)
from worldcup_mvp.unified_bridge import get_provider_health
from worldcup_mvp.settlement import get_settlement_summary, settle_open_predictions
from worldcup_mvp.sporttery_api import SportteryApiError

PROJECT_ROOT = Path(__file__).resolve().parent
WEB_ROOT = PROJECT_ROOT / "web"

_CLIENT_GONE_ERRORS = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "WorldcupInsightDashboard/1.2"

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

    def _send_download(self, content: bytes, filename: str, content_type: str) -> None:
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self._safe_write(content)
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
            if path.suffix in (".js", ".css", ".html"):
                self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self._safe_write(data)
        except _CLIENT_GONE_ERRORS:
            return

    def _parse_query(self) -> dict[str, list[str]]:
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    def _float_query(self, query: dict[str, list[str]], key: str, default: float) -> float:
        raw = (query.get(key) or [str(default)])[0]
        try:
            return float(raw)
        except ValueError:
            return default

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        query = self._parse_query()

        try:
            if route in ("/", "/index.html") or route.startswith("/match/"):
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

            if route == "/api/doctor":
                self._send_json(get_provider_health())
                return

            if route == "/api/analyze/status":
                self._send_json(get_analyze_status())
                return

            if route == "/api/analyze/reviews":
                self._send_json(list_reviews())
                return

            if route == "/api/analyze/review":
                match_id = (query.get("match_id") or [""])[0]
                review = get_review(str(match_id))
                if not review:
                    self._send_json({"success": False, "error": "暂无缓存复盘"}, status=HTTPStatus.NOT_FOUND)
                    return
                self._send_json({"success": True, "review": review})
                return

            if route == "/api/overview":
                mode = (query.get("mode") or ["sporttery"])[0]
                self._send_json(get_overview(mode=mode))
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

            if route == "/api/export/predictions.json":
                payload = export_predictions_payload()
                body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
                self._send_download(body, "predictions.json", "application/json; charset=utf-8")
                return

            if route == "/api/export/predictions.csv":
                csv_text = export_predictions_csv()
                self._send_download(
                    csv_text.encode("utf-8-sig"),
                    "predictions.csv",
                    "text/csv; charset=utf-8",
                )
                return

            if route == "/api/cache/refresh":
                self._send_json(refresh_sporttery_cache())
                return

            if route == "/api/settlement/summary":
                self._send_json(get_settlement_summary())
                return

            if route == "/api/settlement/run":
                lookback = int((query.get("lookback") or ["7"])[0])
                self._send_json(settle_open_predictions(lookback_days=lookback))
                return

            if route == "/api/bet/simulate":
                match_id = (query.get("match_id") or [None])[0]
                stake_had = self._float_query(query, "stake_had", 100.0)
                stake_crs = self._float_query(query, "stake_crs", 50.0)
                self._send_json(
                    get_bet_simulation(
                        match_id=match_id,
                        stake_had=stake_had,
                        stake_crs=stake_crs,
                    )
                )
                return

            if route.startswith("/api/sporttery/predict/"):
                match_id = urllib.parse.unquote(route.removeprefix("/api/sporttery/predict/"))
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

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path

        try:
            if route == "/api/analyze/chat":
                body = self._read_json_body()
                match_id = str(body.get("match_id") or "").strip()
                question = str(body.get("question") or "").strip()
                history = body.get("history")
                foreign = str(body.get("foreign") or "auto").strip() or "auto"
                if not match_id:
                    self._send_json({"success": False, "error": "缺少 match_id"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if not isinstance(history, list):
                    history = None
                try:
                    self._send_json(
                        run_match_chat_analysis(
                            match_id=match_id,
                            question=question,
                            history=history,
                            foreign_source=foreign,
                        )
                    )
                except SportteryApiError as exc:
                    self._send_json({"success": False, "error": str(exc)})
                return

            if route == "/api/analyze/auto-review":
                body = self._read_json_body()
                match_id = str(body.get("match_id") or "").strip()
                force = bool(body.get("force"))
                if not match_id:
                    self._send_json({"success": False, "error": "缺少 match_id"}, status=HTTPStatus.BAD_REQUEST)
                    return
                self._send_json(generate_review_for_match_id(match_id, force=force))
                return

            self.send_error(HTTPStatus.NOT_FOUND)
        except json.JSONDecodeError:
            self._send_json({"success": False, "error": "请求体必须是 JSON"}, status=HTTPStatus.BAD_REQUEST)
        except _CLIENT_GONE_ERRORS:
            return
        except Exception as exc:
            self._send_json({"success": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)


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
    parser.add_argument(
        "--refresh-interval",
        type=int,
        default=60,
        help="前端建议自动刷新间隔（秒），0 表示关闭",
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
    if args.refresh_interval > 0:
        print(f"前端刷新建议：每 {args.refresh_interval} 秒")
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
