"""Optional HTTPS site login for the console behind its reverse proxy."""
from http.cookies import SimpleCookie, CookieError
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs


class SiteLogin:
    cookie = "__Host-football"
    lifetime = 86400

    def __init__(self, credential_file, page):
        self.password = Path(credential_file).read_bytes().strip()
        if not self.password:
            raise ValueError("登录凭据为空")
        self.page = Path(page).read_text(encoding="utf-8")
        self.sessions = {}
        self.lock = threading.Lock()

    def token(self, handler):
        jar = SimpleCookie()
        try:
            jar.load(handler.headers.get("Cookie", ""))
        except CookieError:
            return ""
        return jar[self.cookie].value if self.cookie in jar else ""

    def reply(self, h, status, body=b"", *, location=None, cookie=None, json=False):
        h.send_response(status)
        h.send_header("Cache-Control", "no-store")
        h.send_header("Content-Type", ("application/json" if json else "text/html") + "; charset=utf-8")
        h.send_header("Content-Length", str(len(body)))
        if location:
            h.send_header("Location", location)
        if cookie is not None:
            age = self.lifetime if cookie else 0
            h.send_header("Set-Cookie", f"{self.cookie}={cookie}; Path=/; Max-Age={age}; Secure; HttpOnly; SameSite=Strict")
        h.end_headers()
        h._safe_write(body)

    def intercept(self, h, route):
        token = self.token(h)
        if route in ("/login", "/logout"):
            if h.command == "POST":
                if h.headers.get("Origin") != "https://" + h.headers.get("Host", ""):
                    self.reply(h, 403)
                    return True
                if route == "/logout":
                    with self.lock:
                        self.sessions.pop(token, None)
                    self.reply(h, 303, location="/login", cookie="")
                    return True
                size = h.headers.get("Content-Length", "")
                if not size.isdecimal() or not 0 < int(size) <= 4096:
                    self.reply(h, 400)
                    return True
                fields = parse_qs(h.rfile.read(int(size)).decode("utf-8", errors="replace"))
                password = fields.get("password", [""])[0].encode()
                if secrets.compare_digest(password, self.password) and fields.get("username") == ["football"]:
                    token = secrets.token_urlsafe(32)
                    now = time.monotonic()
                    with self.lock:
                        self.sessions = {k: v for k, v in self.sessions.items() if v > now}
                        self.sessions[token] = now + self.lifetime
                    self.reply(h, 303, location="/static/console/index.html", cookie=token)
                else:
                    self.reply(h, 401, self.page.replace("<!--error-->", '<p role="alert">账号或密码错误</p>').encode())
            elif route == "/login":
                self.reply(h, 200, self.page.encode())
            else:
                self.reply(h, 405)
            return True
        with self.lock:
            valid = self.sessions.get(token, 0) > time.monotonic()
        if valid:
            return False
        if route.startswith("/api/"):
            self.reply(h, 401, b'{"success":false,"login_required":true}', json=True)
        else:
            self.reply(h, 303, location="/login")
        return True
