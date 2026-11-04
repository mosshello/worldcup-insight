"""Full-time analysis precedes official settlement, without inventing pregame evidence."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests.test_console_domain import fixture
from worldcup_mvp.console import api, live, sources
from worldcup_mvp.console.service import ConsoleService
from worldcup_mvp.console.store import Store, encode


class PostmatchTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = Store(Path(folder.name) / "test.sqlite")
        self.app = ConsoleService(self.store)
        now = datetime.now(timezone.utc)
        self.match = sources.normalize_match(fixture())
        self.match["kickoff"] = (now - timedelta(hours=3)).isoformat()
        self.store.upsert_matches([self.match], now.isoformat())
        self.prediction = self.store.save_analysis(self.match["id"], {
            "direction": "主胜", "direction_key": "h", "model_version": "test-only",
            "probabilities": [37, 35, 28], "evidence": [],
        }, {"match": self.match, "context": {}})
        self.prediction["created"] = (now - timedelta(hours=4)).isoformat()
        with self.store.connect() as db:
            db.execute("UPDATE analyses SET payload=?,created_at=? WHERE id=?", (
                encode(self.prediction), self.prediction["created"], self.prediction["id"]))
        self.live = {"state": "completed", "score": "1-2", "score_period": "regular_time",
                     "source": "ESPN", "provider_event_id": "test-event", "source_url": "https://www.espn.com/"}
        self.store.save_live_score(self.match["id"], self.live)

    def row(self):
        return self.app.reviews()[0]

    def test_review_date_is_beijing_kickoff_not_source_calendar_date(self):
        self.match['kickoff'] = '2026-10-30T16:30:00+00:00'
        self.store.upsert_matches([self.match], datetime.now(timezone.utc).isoformat())
        before = self.store.versions(self.match['id'])
        row = self.row()
        self.assertEqual(row['date'], '2026-10-31')
        self.assertEqual(row['kickoff'], self.match['kickoff'])
        self.assertEqual(before, self.store.versions(self.match['id']))

    def test_match_list_exposes_legacy_archive_without_creating_new_prediction(self):
        before = self.store.versions(self.match['id'])
        with patch.object(self.app, 'scores', return_value={}), patch(
                'worldcup_mvp.console.service.legacy_predictions.for_match', return_value={'direction':'客胜'}):
            row = self.app._list_matches()['matches'][0]
        self.assertEqual(row['prediction_archive'], {'legacy':True})
        self.assertEqual(row['analysis_count'], len(before))
        self.assertEqual(before, self.store.versions(self.match['id']))

    def test_finished_match_without_prediction_is_included_with_factual_analysis(self):
        with self.store.connect() as db:
            db.execute("DELETE FROM analyses")
        row = self.row()
        self.assertFalse(row["has_prediction"])
        self.assertIsNone(row["prediction"])
        self.assertEqual(row["pick"], "未归档")
        self.assertEqual(row["postmatch_review"]["status"], "preliminary")
        self.assertEqual(row["postmatch_review"]["comparisons"], [])
        self.assertIn("没有赛前预测归档", encode(row["postmatch_review"]))
        self.assertEqual(row["market_results"], [])
        self.assertEqual(row["ai_review"]["status"], "no_prediction")
        self.assertEqual(self.store.versions(), [])
        self.assertEqual(len(self.store.review_updates(0)["updates"]), 1)
        with self.assertRaisesRegex(ValueError, "缺少赛前"):
            self.app.ai_reviews.packet(row)

    def test_unarchived_in_progress_match_is_not_prematurely_reviewed(self):
        with self.store.connect() as db:
            db.execute("DELETE FROM analyses")
        self.store.save_live_score(self.match["id"], {**self.live, "state": "live"})
        self.assertEqual(self.app.reviews(), [])

    def test_formal_result_fills_missing_live_score_without_prediction(self):
        with self.store.connect() as db:
            db.execute("DELETE FROM analyses")
        self.store.save_live_score(self.match["id"], error="no coverage")
        self.store.save_detail(self.match["id"], {"official_score": "4-3", "results": [
            {"code": "CRS", "combination": "-1:-H"}, {"code": "HAD", "combination": "H"}]})
        row = self.row()
        self.assertEqual(row["score"], "4-3")
        self.assertEqual(row["live_score"]["source"], "体彩官方")
        self.assertEqual(row["live_score"]["state"], "completed")
        self.assertEqual(row["postmatch_review"]["status"], "confirmed")
        self.assertEqual(self.store.versions(), [])

    def test_precise_score_field_and_other_bucket_must_agree(self):
        value = {"sectionsNo999": "4:3", "matchResultList": [
            {"code": "CRS", "combination": "-1:-H"}, {"code": "HAD", "combination": "H"},
            {"code": "TTG", "combination": "7+"}]}
        self.assertEqual(sources.official_score(value), "4-3")
        self.assertIsNone(sources.official_score({**value, "sectionsNo999": ""}))
        self.assertIsNone(sources.official_score({**value, "sectionsNo999": "3:4"}))
        self.assertIsNone(sources.official_score({**value, "isCancel": 1}))
        with self.assertRaises(sources.SourceError):
            sources.official_score({"sectionsNo999": "4:3", "matchResultList": [
                {"code": "CRS", "combination": "4:3"}, {"code": "HAD", "combination": "A"}]})
        with self.assertRaises(sources.SourceError):
            sources.official_score({"sectionsNo999": "4:3", "matchResultList": [
                {"code": "CRS", "combination": "4:3"}, {"code": "TTG", "combination": "6"}]})

    def test_factual_reports_are_isolated_per_match_and_corrections_keep_history(self):
        with self.store.connect() as db:
            db.execute("DELETE FROM analyses")
        other = {**self.match, "id": "sporttery:second", "provider_id": "second", "home": "另一队"}
        self.store.upsert_matches([other], datetime.now(timezone.utc).isoformat())
        self.store.save_live_score(other["id"], {**self.live, "score": "2-0"})
        rows = self.app.reviews()
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["postmatch_review"]["id"], rows[1]["postmatch_review"]["id"])
        self.assertEqual(len(self.store.review_updates(0)["updates"]), 2)
        self.app.reviews()
        self.assertEqual(len(self.store.review_updates(0)["updates"]), 2)

    def test_seven_goal_bucket_accepts_eight_goals_but_rejects_six(self):
        for combination in ('7', '7+', '7及以上'):
            results = [{'code':'CRS', 'combination':'-1:-H'}, {'code':'TTG', 'combination':combination}]
            self.assertEqual(sources.official_score({'sectionsNo999':'5:3', 'matchResultList':results}), '5-3')
            with self.assertRaises(sources.SourceError):
                sources.official_score({'sectionsNo999':'6:0', 'matchResultList':results})

    def test_verified_aliases_still_require_both_teams_and_time(self):
        for league, home, away, mapped_home, mapped_away in [
            ("ita.1", "SSL", "FIO", "SAS", "FIO"),
            ("swe.1", "GIS", "ASA", "GAIS", "AIK"),
            ("fra.2", "RST", "ASN", "RSFC", "ASNL"),
            ("por.1", "BEN", "ALV", "SLB", "ALV"),
            ("bra.1", "RBG", "SAT", "BRA", "SAN"),
            ("uefa.champions", "MNU", "ROM", "MAN", "ROMA"),
        ]:
            match = {**self.match, "home_code": home, "away_code": away}
            event = {"date": match["kickoff"], "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"abbreviation": mapped_home}},
                {"homeAway": "away", "team": {"abbreviation": mapped_away}},
            ]}]}
            with self.subTest(league=league):
                self.assertIs(sources.match_event(match, [event], league), event)
                self.assertIsNone(sources.match_event({**match, "away_code": "wrong"}, [event], league))
                self.assertIsNone(sources.match_event({**match, "kickoff": "2000-01-01T00:00:00Z"}, [event], league))

    def test_live_fulltime_generates_analysis_without_official_or_ai(self):
        before = self.store.versions()
        with patch("worldcup_mvp.ai_analyst._call_deepseek") as call:
            row = self.row()
        report = row["postmatch_review"]
        self.assertEqual(report["status"], "preliminary")
        self.assertEqual(report["score"], "1-2")
        self.assertEqual(report["comparisons"][0]["actual_probability"], 28)
        self.assertEqual(report["comparisons"][0]["top_gap"], 2)
        self.assertTrue(any("不足5" in s for s in report["recommendations"]))
        self.assertTrue(any("无法确认" in s for s in report["findings"]))
        self.assertEqual(row["score"], "待赛果")
        self.assertIsNone(row["consistent"])
        self.assertTrue(all(r["hit"] is None for r in row["market_results"]))
        self.assertEqual(row["ai_review"]["status"], "waiting_result")
        call.assert_not_called()
        self.assertEqual(before, self.store.versions())

    def test_repeated_poll_is_idempotent_and_updates_have_cursor(self):
        first = self.row()["postmatch_review"]
        self.store.save_live_score(self.match["id"], self.live)
        self.assertEqual(first["id"], self.row()["postmatch_review"]["id"])
        initial = self.store.review_updates()
        self.assertEqual(initial, {"cursor": first["id"], "updates": []})
        self.assertEqual(len(self.store.review_updates(0)["updates"]), 1)
        self.assertEqual(self.store.review_updates(first["id"])["updates"], [])

    def test_official_confirmation_and_correction_keep_old_reports(self):
        first = self.row()["postmatch_review"]
        self.store.save_detail(self.match["id"], {"results": [
            {"code": "crs", "combination": "2:1"}, {"code": "had", "combination": "h"}]})
        row = self.row()
        report = row["postmatch_review"]
        self.assertEqual(report["status"], "confirmed")
        self.assertEqual(report["previous_score"], "1-2")
        self.assertGreater(report["id"], first["id"])
        self.assertTrue(row["consistent"])
        self.assertEqual(len(self.store.review_updates(0)["updates"]), 2)

    def test_live_correction_reversal_also_emits_update(self):
        one = self.row()["postmatch_review"]
        self.store.save_live_score(self.match["id"], {**self.live, "score": "2-2"})
        two = self.row()["postmatch_review"]
        self.store.save_live_score(self.match["id"], self.live)
        three = self.row()["postmatch_review"]
        self.assertLess(one["id"], two["id"])
        self.assertLess(two["id"], three["id"])

    def test_unsafe_live_states_and_periods_do_not_generate_analysis(self):
        for change in ({"state": "live"}, {"state": "suspended"}, {"state": "cancelled"},
                       {"score_period": "extra_time"}, {"score_period": "unknown"},
                       {"score": "bad"}, {"provider_event_id": None}):
            with self.subTest(change=change):
                self.store.save_live_score(self.match["id"], {**self.live, **change})
                self.assertEqual(self.row()["postmatch_review"]["status"], "waiting")
        self.store.save_live_score(self.match["id"], self.live)
        self.store.save_live_score(self.match["id"], error="offline")
        self.assertEqual(self.row()["postmatch_review"]["status"], "waiting")
        self.assertEqual(self.store.review_updates()["cursor"], 0)

    def test_postmatch_archive_does_not_become_pregame(self):
        with self.store.connect() as db:
            db.execute("UPDATE analyses SET created_at=?", (datetime.now(timezone.utc).isoformat(),))
        self.assertEqual(self.row()["postmatch_review"]["status"], "waiting")

    def test_official_conflict_blocks_conclusions(self):
        self.store.save_detail(self.match["id"], {"results": [
            {"code": "crs", "combination": "2:1"}, {"code": "had", "combination": "a"}]})
        report = self.row()["postmatch_review"]
        self.assertEqual(report["status"], "conflict")
        self.assertEqual(report["comparisons"], [])

    def test_background_score_collection_creates_report_without_page_request(self):
        with patch.object(live, "fetch_group", return_value=[]), patch.object(live, "normalize", return_value=self.live):
            self.app.sync_scores()
        self.assertEqual(len(self.store.review_updates(0)["updates"]), 1)

    def test_formal_refresh_does_not_request_fundamentals_and_preserves_results_on_failure(self):
        results = [{"code": "crs", "combination": "1:2"}]
        with patch.object(sources, "fetch_odds", return_value={"results": results}), patch.object(sources, "fetch_context") as context:
            self.assertEqual(self.app.reviews(refresh=True)[0]["postmatch_review"]["status"], "confirmed")
            context.assert_not_called()
        with patch.object(sources, "fetch_odds", side_effect=sources.SourceError("offline")):
            self.assertEqual(self.app.reviews(refresh=True)[0]["postmatch_review"]["status"], "confirmed")

    def test_updates_api_and_bad_cursor(self):
        self.row()
        with patch.object(api, "service", return_value=self.app):
            self.assertEqual(len(api.get("/api/v1/review-updates", {"after": ["0"]})["updates"]), 1)
            for value in ["-1", "x", "1.2", "9"*25]:
                with self.assertRaises(ValueError):
                    api.get("/api/v1/review-updates", {"after": [value]})

    def test_espn_extra_time_is_not_regular_time(self):
        event = {"id": "test", "date": self.match["kickoff"], "competitions": [{"competitors": [
            {"homeAway": side, "score": str(score), "team": {"id": side, "abbreviation": code}}
            for side, score, code in [("home", 1, "CHE"), ("away", 2, "MAN")]]}],
            "status": {"period": 2, "type": {"name": "STATUS_FULL_TIME", "state": "post", "completed": True}}}
        key = live.group_key(self.match)
        self.assertEqual(live.normalize(self.match, [event], key)["score_period"], "regular_time")
        for period, name in [(4, "STATUS_FULL_TIME"), (5, "STATUS_FINAL_PEN"), (None, "STATUS_FULL_TIME")]:
            event["status"]["period"] = period
            event["status"]["type"]["name"] = name
            self.assertEqual(live.normalize(self.match, [event], key)["score_period"], "unknown")


if __name__ == "__main__":
    unittest.main()
