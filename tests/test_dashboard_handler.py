"""仪表盘 HTTP 处理测试。"""

import io
import json
import unittest
from unittest.mock import MagicMock, patch

from dashboard import DashboardHandler


class DashboardHandlerTests(unittest.TestCase):
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
