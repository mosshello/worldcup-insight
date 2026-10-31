"""New workbench routes must not alter the old dashboard entry or start training."""
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from dashboard import DashboardHandler


class ConsoleRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=2)

    def test_console_redirect_serves_built_app(self):
        with urllib.request.urlopen(self.base + "/console") as response:
            self.assertTrue(response.url.endswith("/static/console/index.html"))
            self.assertIn('id="app"', response.read().decode("utf-8"))

    def test_capabilities_report_no_club_training_and_no_credentials(self):
        with patch("worldcup_mvp.statistical_model.load_statistical_model", return_value={"model_version": "national-test", "secret": "never-expose"}):
            with urllib.request.urlopen(self.base + "/api/console/capabilities") as response:
                data = json.load(response)
        self.assertIs(data["club_training_enabled"], False)
        self.assertEqual(data["national_model"], "national-test")
        self.assertNotIn("secret", data)


if __name__ == "__main__":
    unittest.main()
