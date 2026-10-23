"""Window API for the org chart (Org tab). Uses the Graph token of the Teams and Mail tabs."""

from __future__ import annotations

from .api_graph import guarded
from .server import route


@route("GET", "/api/org/chain")
@guarded
def org_chain(h):
    h.send(200, h.app.graph.org_chain())


@route("GET", "/api/org/search")
@guarded
def org_search(h):
    h.send(200, h.app.graph.org_search(h.query("q") or ""))


@route("POST", "/api/org/export")
@guarded
def org_export(h):
    b = h.json_body()
    h.send(200, h.app.graph.export_org(str(b.get("id", "")), int(b.get("depth", 0)), bool(b.get("above", True))).view())
