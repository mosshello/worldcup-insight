"""仪表盘 HTTP 处理测试。"""

import io
import json
import unittest
from unittest.mock import MagicMock, patch

from dashboard import DashboardHandler


class DashboardHandlerTests(unittest.TestCase):
    def test_web_stylesheet_mime_does_not_depend_on_host_registry(self):
        import tempfile
        from pathlib import Path
        handler=self._handler()
        handler.send_response=MagicMock();handler.send_header=MagicMock();handler.end_headers=MagicMock()
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'test.css';path.write_text('body {color: black}',encoding='utf8')
            with patch('dashboard.mimetypes.guess_type',return_value=('text/plain',None)):
                handler._send_file(path)
        handler.send_header.assert_any_call('Content-Type','text/css; charset=utf-8')

    def test_dataset_import_has_separate_size_limit_and_keeps_origin_check(self):
        handler = self._handler()
        handler.path = "/api/v1/datasets/import"
        handler.headers = {"Content-Length":"70000", "Content-Type":"application/json", "Host":"127.0.0.1:8891"}
        handler._read_json_body = MagicMock(return_value={"competition_id":"jp.j1", "dataset":{}})
        handler._send_json = MagicMock()
        with patch("worldcup_mvp.console.api.post", return_value={"success":True}) as post:
            handler.do_POST()
            post.assert_called_once()
        handler.headers["Origin"] = "https://unrelated.example"
        handler.do_POST()
        self.assertEqual(handler._send_json.call_args.kwargs["status"],403)
        handler.headers.pop("Origin")
        handler.headers["Content-Length"] = str(16*1024*1024+1)
        handler.do_POST()
        self.assertEqual(handler._send_json.call_args.kwargs["status"],413)
        handler.headers["Content-Length"] = "70000"
        handler.path = "/api/v1/state"
        handler.do_POST()
        self.assertEqual(handler._send_json.call_args.kwargs["status"],413)

    def _handler(self) -> DashboardHandler:
        request = MagicMock()
        request.makefile.return_value = io.BytesIO()
        client_address = ("127.0.0.1", 12345)
        handler = DashboardHandler(request, client_address, MagicMock())
        handler.wfile = io.BytesIO()
        return handler

    def test_send_json_survives_connection_aborted(self) -> None:
        handler = self._handler()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile.write = MagicMock(side_effect=ConnectionAbortedError(10053, "aborted"))

        handler._send_json({"ok": True})
        handler.send_response.assert_called_once()

    def test_send_file_survives_broken_pipe(self) -> None:
        handler = self._handler()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile.write = MagicMock(side_effect=BrokenPipeError())

        from pathlib import Path

        web_root = Path(__file__).resolve().parent.parent / "web"
        handler._send_file(web_root / "index.html")
        handler.send_response.assert_called_once()

    @patch("dashboard.refresh_sporttery_cache")
    def test_cache_refresh_route(self, mock_refresh: MagicMock) -> None:
        mock_refresh.return_value = {"success": True, "count": 2, "message": "ok"}
        handler = self._handler()
        handler.path = "/api/cache/refresh"
        handler._send_json = MagicMock()

        handler.do_GET()

        mock_refresh.assert_called_once()
        payload = handler._send_json.call_args[0][0]
        self.assertTrue(payload["success"])


class CacheRefresherTests(unittest.TestCase):
    def setUp(self):
        # Test refresh orchestration without touching live settlement or LLMs.
        for name, value in (("sync_finished_matches", {"settled": 0}),
                            ("auto_review_finished_deviations", {"generated": 0})):
            stub = patch("worldcup_mvp.cache_refresher." + name, return_value=value)
            stub.start()
            self.addCleanup(stub.stop)

    @patch("worldcup_mvp.cache_refresher.get_upcoming_score_predictions")
    def test_refresh_success(self, mock_predict: MagicMock) -> None:
        from worldcup_mvp.cache_refresher import refresh_sporttery_cache

        mock_predict.return_value = {
            "success": True,
            "predictions": [{"match_id": "1"}, {"match_id": "2"}],
        }
        result = refresh_sporttery_cache()
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 2)

    @patch("worldcup_mvp.cache_refresher.get_upcoming_score_predictions")
    def test_refresh_api_error(self, mock_predict: MagicMock) -> None:
        from worldcup_mvp.cache_refresher import refresh_sporttery_cache
        from worldcup_mvp.sporttery_api import SportteryApiError

        mock_predict.side_effect = SportteryApiError("HTTP 403")
        result = refresh_sporttery_cache()
        self.assertFalse(result["success"])
        self.assertIn("403", result["error"])


if __name__ == "__main__":
    unittest.main()
