"""Exercise the site login boundary with real HTTP requests and disposable credentials."""
from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

from dashboard import DashboardHandler
from worldcup_mvp.console.site_login import SiteLogin


class SiteAccessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        credential = Path(temp.name) / "credential"
        credential.write_bytes(b"test-secret")
        class Handler(DashboardHandler):
            site_login = SiteLogin(credential, Path("web/site-login.html"))
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.connect = partial(HTTPConnection, *server.server_address, timeout=5)

    def request(self, method, path, body=None, **headers):
        conn = self.connect()
        try:
            conn.request(method, path, body, {"Host": "football.test", **headers})
            res = conn.getresponse()
            return res.status, dict(res.getheaders()), res.read()
        finally:
            conn.close()

    def test_protection_session_and_revocation(self):
        for method in ("GET", "POST"):
            status, headers, _ = self.request(method, "/api/v1/state")
            self.assertEqual(status, 401)
            self.assertNotIn("WWW-Authenticate", headers)
        self.assertEqual(self.request("GET", "/static/console/index.html")[1]["Location"], "/login")
        origin = {"Origin": "https://football.test"}
        self.assertEqual(self.request("POST", "/login", "username=football&password=incorrect", **origin)[0], 401)
        status, headers, _ = self.request("POST", "/login", "username=football&password=test-secret", **origin)
        self.assertEqual(status, 303)
        cookie = headers["Set-Cookie"]
        for flag in ("Secure", "HttpOnly", "SameSite=Strict"):
            self.assertIn(flag, cookie)
        session = {"Cookie": cookie.split(";")[0]}
        self.assertEqual(self.request("GET", "/static/console/index.html", **session)[0], 200)
        self.assertEqual(self.request("POST", "/logout", Origin="https://other.test", **session)[0], 403)
        self.assertEqual(self.request("POST", "/logout", **origin, **session)[0], 303)
        self.assertEqual(self.request("GET", "/api/v1/state", **session)[0], 401)
