"""AI 复盘缓存与自动生成测试。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worldcup_mvp.ai_review_cache import (
    auto_review_finished_deviations,
    generate_review_for_card,
    get_review,
    list_reviews,
)


class AiReviewCacheTests(unittest.TestCase):
    def test_generate_review_skips_without_key(self) -> None:
        card = {
            "match_id": "2040351",
            "card_type": "finished",
            "settlement_status": "settled",
            "had_won": False,
            "crs_won": False,
            "home": "比利时",
            "away": "塞内加尔",
        }
        with mock.patch("worldcup_mvp.ai_review_cache.get_deepseek_api_key", return_value=None):
            result = generate_review_for_card(card)
        self.assertTrue(result.get("skipped"))
        self.assertFalse(result.get("configured"))

    def test_generate_review_uses_cache(self) -> None:
        card = {
            "match_id": "2040351",
            "card_type": "finished",
            "settlement_status": "settled",
            "had_won": False,
            "crs_won": True,
            "home": "比利时",
            "away": "塞内加尔",
            "direction": "主胜",
            "actual_had": "平",
            "actual_score": "2:2",
        }
        with tempfile.TemporaryDirectory() as tmp:
            review_path = Path(tmp) / "ai_reviews.json"
            review_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "reviews": {
                            "2040351": {
                                "match_id": "2040351",
                                "success": True,
                                "reply": "已有复盘",
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with mock.patch("worldcup_mvp.ai_review_cache.REVIEW_FILE", review_path), mock.patch(
                "worldcup_mvp.ai_review_cache.get_deepseek_api_key", return_value="test-key"
            ):
                result = generate_review_for_card(card)
        self.assertTrue(result.get("cached"))
        self.assertEqual(result.get("reply"), "已有复盘")

    @mock.patch("worldcup_mvp.ai_review_cache.chat_match_analysis")
    @mock.patch("worldcup_mvp.ai_review_cache.get_deepseek_api_key", return_value="test-key")
    def test_auto_review_finished_deviations(self, _mock_key: mock.Mock, mock_chat: mock.Mock) -> None:
        mock_chat.return_value = {"success": True, "reply": "复盘内容", "model": "deepseek-chat"}
        cards = [
            {
                "match_id": "2040351",
                "card_type": "finished",
                "settlement_status": "settled",
                "had_won": False,
                "crs_won": False,
                "home": "A",
                "away": "B",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            review_path = Path(tmp) / "ai_reviews.json"
            with mock.patch("worldcup_mvp.ai_review_cache.REVIEW_FILE", review_path), mock.patch(
                "worldcup_mvp.ai_review_cache.list_finished_review_cards", return_value=cards
            ), mock.patch(
                "worldcup_mvp.ai_review_cache.get_analyze_status",
                return_value={"configured": True},
            ):
                result = auto_review_finished_deviations(lookback_days=7)
                saved = get_review("2040351")
                payload = list_reviews()
        self.assertEqual(result["generated"], 1)
        self.assertEqual(saved.get("reply"), "复盘内容")
        self.assertEqual(payload["count"], 1)


if __name__ == "__main__":
    unittest.main()
