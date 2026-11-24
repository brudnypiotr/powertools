"""Window API of the look: name, colours, logo (see branding.py)."""

from __future__ import annotations

from .. import branding
from ..config import output_dir
from .server import route


@route("GET", "/api/branding")
def branding_get(h):
    h.send(200, branding.state())


@route("POST", "/api/branding")
def branding_save(h):
    h.send(200, branding.save(h.json_body()))


@route("POST", "/api/branding/zip")
def branding_zip(h):
    n = int(h.headers.get("Content-Length") or 0)
    if not 0 < n <= branding.MAX_ZIP:
        raise ValueError(f"the zip must be at most {branding.MAX_ZIP // 1_000_000} MB")
    h.send(200, branding.import_zip(h.rfile.read(n)))


@route("POST", "/api/branding/pack")
def branding_pack(h):
    from .api_graph import _reveal

    out = branding.pack(output_dir(h.app.settings))
    _reveal(out, True)
    h.send(200, {"path": str(out)})


@route("DELETE", "/api/branding")
def branding_reset(h):
    h.send(200, branding.reset())
