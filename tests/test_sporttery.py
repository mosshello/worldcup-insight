"""体彩 API 与融合预测测试。"""

import json
import unittest
from unittest.mock import patch

from worldcup_mvp.fusion_predictor import predict_match
from worldcup_mvp.sporttery_api import match_to_snapshot, normalize_match, parse_trend_flag
from worldcup_mvp.dashboard_data import _kickoff_date
from worldcup_mvp.data_manager import SPORTTERY_CODE_ALIASES


SAMPLE_RAW_MATCH = {
    "matchId": 2040337,
    "homeTeamAbbName": "巴西",
    "awayTeamAbbName": "日本",
    "leagueAbbName": "世界杯",
    "matchDate": "2026-06-30",
    "matchTime": "01:00:00",
    "had": {
        "h": "1.49",
        "d": "3.72",
        "a": "5.28",
        "hf": "-1",
        "df": "0",
        "af": "1",
        "updateDate": "2026-06-29",
        "updateTime": "12:22:52",
    },
    "hhad": {
        "h": "2.71",
        "d": "3.15",
        "a": "2.26",
        "goalLine": "-1",
        "goalLineValue": "-1.00",
        "hf": "0",
        "df": "0",
        "af": "0",
        "updateDate": "2026-06-29",
        "updateTime": "12:22:56",
    },
}

SAMPLE_HISTORY = {
    "had_history": [
        {"recorded_at": "2026-06-27T09:14:08", "home": 1.66, "draw": 3.28, "away": 4.5},
        {"recorded_at": "2026-06-29T12:22:52", "home": 1.49, "draw": 3.72, "away": 5.28},
    ],
    "hhad_history": [
        {"recorded_at": "2026-06-27T09:14:08", "home": 3.07, "draw": 3.38, "away": 1.97, "goal_line": -1},
        {"recorded_at": "2026-06-29T12:22:56", "home": 2.71, "draw": 3.15, "away": 2.26, "goal_line": -1},
    ],
}


class SportteryApiTests(unittest.TestCase):
    def test_normalize_match(self) -> None:
        match = normalize_match(SAMPLE_RAW_MATCH)
        self.assertEqual(match["match_id"], "2040337")
        self.assertEqual(match["pools"]["had"]["home"], 1.49)

    def test_normalize_match_business_date(self) -> None:
        raw = {**SAMPLE_RAW_MATCH, "businessDate": "2026-06-30", "matchDate": "2026-07-01"}
        match = normalize_match(raw)
        self.assertEqual(match["business_date"], "2026-06-30")
        self.assertEqual(
            _kickoff_date({**match, "kickoff_beijing": "2026-07-01T01:00:00+08:00"}),
            "2026-06-30",
        )

    def test_norway_code_alias(self) -> None:
        self.assertEqual(SPORTTERY_CODE_ALIASES.get("NOW"), "NOR")

    def test_build_date_tabs_from_business_dates(self) -> None:
        from worldcup_mvp.dashboard_data import _build_date_tabs, _build_date_buckets

        predictions = [
            {"business_date": "2026-06-30", "confidence": "高", "unified_linked": True},
            {"business_date": "2026-07-01", "confidence": "中", "unified_linked": False},
            {"business_date": "2026-07-02", "confidence": "高", "unified_linked": False},
        ]
        tabs = _build_date_tabs(predictions)
        self.assertEqual(tabs[0]["label"], "全部")
        self.assertEqual(len(tabs), 4)
        buckets = _build_date_buckets(predictions)
        self.assertEqual(buckets[""]["total"], 3)
        self.assertEqual(buckets["2026-07-01"]["total"], 1)

    def test_match_to_snapshot(self) -> None:
        match = normalize_match(SAMPLE_RAW_MATCH)
        snapshot = match_to_snapshot(match)
        self.assertEqual(snapshot["european"]["home"], 1.49)
        self.assertIn("sporttery", snapshot)

    def test_parse_trend_flag(self) -> None:
        self.assertEqual(parse_trend_flag("-1"), "down")
        self.assertEqual(parse_trend_flag("1"), "up")
        self.assertEqual(parse_trend_flag("0"), "flat")


class FusionPredictorTests(unittest.TestCase):
    def test_predict_with_foreign_alignment(self) -> None:
        match = normalize_match(SAMPLE_RAW_MATCH)
        result = predict_match(
            match,
            sporttery_history=SAMPLE_HISTORY,
            foreign_odds={"home": 1.69, "draw": 3.8, "away": 5.2},
            foreign_source="fox-sports/fanduel",
        )
        self.assertEqual(result["direction"], "主胜")
        self.assertIn("confidence", result)
        self.assertTrue(any("体彩" in line for line in result["analysis"]))

    @patch("worldcup_mvp.sporttery_api._request")
    def test_fetch_matches_mock(self, mock_request: unittest.mock.Mock) -> None:
        from worldcup_mvp.sporttery_api import fetch_matches

        mock_request.return_value = {
            "success": True,
            "value": {
                "matchInfoList": [
                    {"subMatchList": [SAMPLE_RAW_MATCH]},
                ],
            },
        }
        matches = fetch_matches()
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["home"], "巴西")

    @patch("worldcup_mvp.sporttery_api.time.sleep")
    @patch("worldcup_mvp.sporttery_api._request_once")
    def test_request_retries_on_403(
        self, mock_once: unittest.mock.Mock, mock_sleep: unittest.mock.Mock
    ) -> None:
        from worldcup_mvp.sporttery_api import SportteryApiError, _request

        err403 = SportteryApiError("HTTP 403: blocked")
        err403.http_code = 403
        mock_once.side_effect = [err403, err403, {"success": True, "value": {}}]

        payload = _request("https://example.com")
        self.assertTrue(payload["success"])
        self.assertEqual(mock_once.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("worldcup_mvp.sporttery_api.time.sleep")
    @patch("worldcup_mvp.sporttery_api._request_once")
    def test_request_gives_up_after_max_retries(
        self, mock_once: unittest.mock.Mock, mock_sleep: unittest.mock.Mock
    ) -> None:
        from worldcup_mvp.sporttery_api import SportteryApiError, _request

        err403 = SportteryApiError("HTTP 403: blocked")
        err403.http_code = 403
        mock_once.side_effect = [err403, err403, err403]

        with self.assertRaises(SportteryApiError):
            _request("https://example.com")
        self.assertEqual(mock_once.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)


if __name__ == "__main__":
    unittest.main()
