"""The window: a local web server on 127.0.0.1 (stdlib only).

Safety on a shared machine: only 127.0.0.1, the Host header must match (stops DNS rebinding), every request for
data needs the key of the window (door.py), and every request that changes something must carry the X-Powertools header (a foreign web page cannot add it without
a CORS preflight, which this server never answers).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from ..files import Busy
from .door import OPEN

log = logging.getLogger("powertools.web")
STATIC = Path(__file__).parent / "static"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".ico": "image/x-icon",
         ".png": "image/png", ".webmanifest": "application/manifest+json"}
ROUTES: list[tuple[str, re.Pattern, object]] = []


def route(method: str, pattern: str):
    def deco(fn):
        ROUTES.append((method, re.compile(f"^{pattern}$"), fn))
        return fn
    return deco


class Handler(BaseHTTPRequestHandler):
    server_version = "powertools"
    app = None  # set by serve()

    def log_message(self, fmt, *args):
        pass

    def send(self, code: int, body, ctype: str = "application/json; charset=utf-8", headers: dict | None = None) -> None:
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def json_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}

    def query(self, name: str, default: str = "") -> str:
        return (parse_qs(urlparse(self.path).query).get(name) or [default])[0]

    def _dispatch(self, method: str) -> None:
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return self.send(403, {"error": "bad host"})
        if method != "GET" and self.headers.get("X-Powertools") != "1":
            return self.send(403, {"error": "missing X-Powertools header"})
        path, door = urlparse(self.path).path, self.app.door
        if path in ("", "/") and self.query("t"):
            # only the right key gets the cookie; a wrong one just opens the window, which then asks for the key
            extra = {"Set-Cookie": door.cookie()} if door.is_key(self.query("t")) else {}
            return self.send(302, b"", headers={"Location": "/", **extra})
        if path.startswith("/api/") and path not in OPEN and not door.admits(self.headers):
            return self.send(403, {"error": "locked"})
        if method == "GET" and not path.startswith("/api/"):
            return self._static(path)
        for m, rx, fn in ROUTES:
            match = rx.match(path)
            if m == method and match:
                try:
                    return fn(self, *map(unquote, match.groups()))
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
                except Busy as e:
                    return self.send(409, {"error": str(e)})
                except KeyError as e:
                    return self.send(404, {"error": f"not found: {e}"})
                except Exception as e:
                    log.error("%s %s failed\n%s", method, path, traceback.format_exc())
                    return self.send(500, {"error": f"{type(e).__name__}: {e}"})
        self.send(404, {"error": "no such route"})

    def _branding(self, path: str) -> None:
        """The organisation's colours and pictures (see branding.py); an SVG may not run anything."""
        from .. import branding

        if path == "/branding.css":
            return self.send(200, branding.css().encode("utf-8"), TYPES[".css"])
        p = branding.find(path.removeprefix("/branding/"))
        if not p:
            return self.send(404, {"error": "not found"})
        self.send(200, p.read_bytes(), branding.TYPES[p.suffix], {
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox", "X-Content-Type-Options": "nosniff"})

    def _static(self, path: str) -> None:
        if path.startswith("/branding"):
            return self._branding(path)
        name = "index.html" if path in ("", "/") else path.lstrip("/")
        f = (STATIC / name).resolve()
        if STATIC.resolve() not in f.parents or not f.is_file():
            return self.send(404, {"error": "not found"})
        self.send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")


def serve(app, port: int = 0) -> ThreadingHTTPServer:
    from . import api, api_assistant, api_branding, api_graph, api_jira, api_llm, api_meetings, api_org  # noqa: F401  (register the routes)

    Handler.app = app
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, name="web", daemon=True).start()
    return httpd
