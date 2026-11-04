"""New workbench routes must not alter the old dashboard entry or start training."""

import json
import socket
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch

from dashboard import DashboardHandler, DashboardHTTPServer


class ConsoleRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = DashboardHTTPServer(("127.0.0.1", 0), DashboardHandler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=2)

    def test_navigation_connection_burst_waits_for_accept_without_refusal(self):
        # Hold accept while one navigation's CSS/modules/APIs connect together.
        # The old five-connection queue cannot accommodate this burst.
        with DashboardHTTPServer(('127.0.0.1', 0), DashboardHandler) as server:
            connections = []
            try:
                for _ in range(16):
                    connections.append(socket.create_connection(server.server_address, timeout=1))
                self.assertEqual(len(connections), 16)
            finally:
                for connection in connections:
                    connection.close()

    def test_console_redirect_serves_built_app(self):
        with urllib.request.urlopen(self.base + "/console") as response:
            self.assertTrue(response.url.endswith("/static/console/index.html"))
            self.assertIn('id="app"', response.read().decode("utf-8"))

    def test_capabilities_report_no_club_training_and_no_credentials(self):
        with patch(
            "worldcup_mvp.statistical_model.load_statistical_model",
            return_value={"model_version": "national-test", "secret": "never-expose"},
        ):
            with urllib.request.urlopen(
                self.base + "/api/console/capabilities"
            ) as response:
                data = json.load(response)
        self.assertIs(data["club_training_enabled"], False)
        self.assertEqual(data["national_model"], "national-test")
        self.assertNotIn("secret", data)

    def test_v1_rejects_nonobject_body_and_cross_origin_mutation(self):
        for body, origin, status in [
            (b"[]", self.base, 400),
            (b"{}", "https://untrusted.invalid", 403),
        ]:
            req = urllib.request.Request(
                self.base + "/api/v1/state",
                data=body,
                headers={"Content-Type": "application/json", "Origin": origin},
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(req)
            self.assertEqual(caught.exception.code, status)
            caught.exception.close()

    def test_v1_source_failure_returns_error_instead_of_demo_rows(self):
        from worldcup_mvp.console.sources import SourceError

        with patch("worldcup_mvp.console.api.get", side_effect=SourceError("offline")):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(self.base + "/api/v1/matches")
            self.assertEqual(caught.exception.code, 502)
            self.assertEqual(
                json.load(caught.exception), {"success": False, "error": "offline"}
            )
            caught.exception.close()


if __name__ == "__main__":
    unittest.main()
