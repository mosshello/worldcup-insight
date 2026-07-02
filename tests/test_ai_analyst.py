"""DeepSeek AI 分析测试。"""

import json
import unittest
from io import BytesIO
from unittest import mock

from worldcup_mvp.ai_analyst import (
    build_match_context,
    chat_match_analysis,
    get_analyze_status,
)


class AiAnalystTests(unittest.TestCase):
    def test_build_match_context_includes_shift_and_pool(self) -> None:
        fusion = {
            "foreign_source_resolved": "polymarket",
            "prediction": {
                "home": "法国",
                "away": "瑞典",
                "direction": "主胜",
                "probabilities": {"home": 0.55, "draw": 0.25, "away": 0.20},
                "foreign": {"probabilities": {"home": 0.48, "draw": 0.27, "away": 0.25}},
                "analysis": ["主盘偏热"],
            },
            "score_prediction": {
                "match_id": "2040346",
                "direction": "主胜",
                "confidence": "高",
                "predicted_score": "2-0",
                "kickoff_beijing": "2026-07-01T03:00:00+08:00",
            },
            "direction_shift": {
                "available": True,
                "severity": "medium",
                "opening_label": "主胜",
                "current_label": "客胜",
                "upset_candidates": ["客胜"],
                "alerts": ["冷门受热：客胜 SP 持续下调"],
            },
            "pool_analysis": {
                "summary_bullets": ["总进球最看好 2 球"],
                "kelly_had": [{"label": "客胜", "expected_value": 0.08}],
            },
            "probability_deltas_pp": {"away": 5.0},
            "probability_delta_alerts": ["away"],
        }
        context = build_match_context(fusion)
        self.assertIn("法国 vs 瑞典", context)
        self.assertIn("冷门受热", context)
        self.assertIn("总进球", context)

    @mock.patch("worldcup_mvp.ai_analyst.get_deepseek_api_key", return_value=None)
    def test_chat_without_api_key(self, _mock_key: mock.Mock) -> None:
        result = chat_match_analysis({}, "这场有冷门吗？")
        self.assertFalse(result["success"])
        self.assertIn("DEEPSEEK_API_KEY", result["error"])

    @mock.patch("worldcup_mvp.ai_analyst.get_deepseek_api_key", return_value="test-key")
    @mock.patch("worldcup_mvp.ai_analyst.get_deepseek_model", return_value="deepseek-chat")
    @mock.patch("worldcup_mvp.ai_analyst.request.urlopen")
    def test_chat_success(
        self,
        mock_urlopen: mock.Mock,
        _mock_model: mock.Mock,
        _mock_key: mock.Mock,
    ) -> None:
        payload = {
            "choices": [{"message": {"content": "数据支持客队受热，但方向尚未完全翻转。"}}],
        }
        response = mock.Mock()
        response.read.return_value = json.dumps(payload).encode("utf-8")
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=False)
        mock_urlopen.return_value = response

        fusion = {
            "prediction": {"home": "A", "away": "B", "probabilities": {"home": 0.4, "draw": 0.3, "away": 0.3}},
            "score_prediction": {"match_id": "1", "direction": "主胜"},
        }
        result = chat_match_analysis(fusion, "客队会爆冷吗？")
        self.assertTrue(result["success"])
        self.assertIn("受热", result["reply"])

    @mock.patch("worldcup_mvp.ai_analyst.get_deepseek_api_key", return_value="secret")
    def test_status_configured(self, _mock_key: mock.Mock) -> None:
        status = get_analyze_status()
        self.assertTrue(status["configured"])
        self.assertEqual(status["provider"], "deepseek")


class AnalyzeChatRouteTests(unittest.TestCase):
    @mock.patch("dashboard.run_match_chat_analysis")
    def test_analyze_chat_post(self, mock_chat: mock.Mock) -> None:
        from dashboard import DashboardHandler

        mock_chat.return_value = {"success": True, "reply": "ok"}
        request = mock.Mock()
        request.makefile.return_value = BytesIO()
        handler = DashboardHandler(request, ("127.0.0.1", 12345), mock.Mock())
        handler.wfile = BytesIO()
        handler.path = "/api/analyze/chat"
        handler.headers = {"Content-Length": "0"}
        body = json.dumps({"match_id": "2040346", "question": "有冷门吗？"}).encode("utf-8")
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = BytesIO(body)
        handler._send_json = mock.Mock()

        handler.do_POST()

        mock_chat.assert_called_once()
        handler._send_json.assert_called_once()


if __name__ == "__main__":
    unittest.main()
