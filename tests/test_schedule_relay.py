from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worldcup_mvp.console import sources


class ScheduleRelayTests(unittest.TestCase):
    def test_only_new_complete_observations_can_replace_the_active_schedule(self):
        instant = datetime.now(timezone.utc) - timedelta(seconds=5)
        match = {"matchId": 71, "matchDate": "2026-11-06", "matchTime": "21:00",
                 "homeTeamId": 1, "awayTeamId": 2}
        snapshot = {"captured_at": instant.isoformat(), "source": sources.SPORTTERY + "getMatchCalculatorV1.qry",
                    "value": {"matchInfoList": [{"subMatchList": [match]}]}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(sources, "fetch_matches",
                                                                side_effect=sources.SourceError("567")):
            path = Path(tmp) / "snapshot"
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            rows, captured, transport = sources.read_official_schedule(path)
            self.assertEqual(([r["id"] for r in rows], captured, transport),
                             (["sporttery:71"], instant.isoformat(), "relay"))
            with self.assertRaises(sources.SourceError):
                sources.read_official_schedule(path, (instant + timedelta(seconds=1)).isoformat())
            for changes in ({"captured_at": (instant - timedelta(minutes=4)).isoformat()},
                            {"captured_at": (instant + timedelta(minutes=4)).isoformat()},
                            {"value": {}}, {"value": {"matchInfoList": []}}, {"source": "unverified"}):
                with self.subTest(changes=changes), self.assertRaises(sources.SourceError):
                    path.write_text(json.dumps({**snapshot, **changes}), encoding="utf-8")
                    sources.read_official_schedule(path)

    def test_direct_success_never_uses_a_relay_file(self):
        with patch.object(sources, "fetch_matches", return_value=[]) as direct:
            rows, _, transport = sources.read_official_schedule("missing")
        direct.assert_called_once()
        self.assertEqual((rows, transport), ([], "direct"))
