"""The org chart against a fake Graph: the chain in one request, the branch walked with $batch, Markdown and CSV."""

import csv
import io
from datetime import datetime

import pytest

from powertools.graph import org
from powertools.graph.client import Graph, TokenExpired

#   ceo
#   └ vp
#     ├ dir ── mgr ── ana, bo
#     │      └ eve
#     └ other (a contact, not a user: skipped)
PEOPLE = {
    "ceo": ("Cora CEO", "Chief Executive", "Board"),
    "vp": ("Vic VP", "Vice President", "Operations"),
    "dir": ("Dina Director", "Director", "Delivery"),
    "mgr": ("Max Manager", "Manager", "Delivery"),
    "eve": ("Eve Expert", "", ""),
    "ana": ("Ana Analyst", "Analyst", "Delivery"),
    "bo": ("Bo Builder", "Developer", "Delivery"),
}
REPORTS = {"ceo": ["vp"], "vp": ["dir"], "dir": ["mgr", "eve"], "mgr": ["ana", "bo"]}
UP = {k: m for m, ks in REPORTS.items() for k in ks}


def user(uid):
    name, title, dept = PEOPLE[uid]
    return {"id": uid, "displayName": name, "jobTitle": title or None, "department": dept or None, "mail": f"{uid}@x"}


class FakeGraph:
    def __init__(self, me="ana"):
        self.me, self.calls, self.batches = me, [], []

    def get(self, path, params=None, headers=None):
        self.calls.append(path)
        if path == "/users":
            assert headers == org.EVENTUAL and params["$search"].startswith('"displayName:')
            q = params["$search"].split(":")[1].split('"')[0].lower()
            return {"value": [user(k) for k in PEOPLE if PEOPLE[k][0].lower().startswith(q)]}
        uid = self.me if path == "/me" else path.split("/")[2]
        assert "manager($levels=max" in params["$expand"] and headers == org.EVENTUAL
        node = top = user(uid)
        while uid in UP:
            uid = UP[uid]
            node["manager"] = user(uid)
            node = node["manager"]
        return top

    def batch(self, paths):
        assert len(paths) <= 20
        self.batches.append(paths)
        out = []
        for p in paths:
            uid = p.split("/")[2]
            kids = [user(k) for k in REPORTS.get(uid, [])]
            if uid == "vp":
                kids.append({"id": "other", "@odata.type": "#microsoft.graph.orgContact", "displayName": "A contact"})
            out.append({"value": kids})
        return out


def test_the_chain_comes_in_one_request_the_person_first():
    g = FakeGraph()
    c = org.chain(g)
    assert [p.id for p in c] == ["ana", "mgr", "dir", "vp", "ceo"] and len(g.calls) == 1
    assert c[0].manager == "mgr" and c[-1].manager == ""


def test_search_by_name():
    assert [p.name for p in org.search(FakeGraph(), "di")] == ["Dina Director"]
    assert org.search(FakeGraph(), "d") == []          # too short: no request


def test_walk_level_by_level_with_batches_and_a_depth():
    g = FakeGraph()
    root = org.chain(g, "vp")[0]
    assert org.walk(g, root, depth=0) == 5               # dir, mgr, eve, ana, bo; the contact is skipped
    assert [len(b) for b in g.batches] == [1, 1, 2, 2]   # one request per level (vp, dir, mgr+eve, ana+bo)
    assert [p.name for p in org.everyone(root)][:4] == ["Vic VP", "Dina Director", "Eve Expert", "Max Manager"]
    root2 = org.chain(FakeGraph(), "vp")[0]
    assert org.walk(FakeGraph(), root2, depth=2) == 3 and max(p.level for p in org.everyone(root2)) == 2


def test_markdown_and_csv_with_the_managers_above(tmp_path):
    g = FakeGraph()
    people = org.chain(g, "dir")
    root, above = people[0], people[1:]
    org.walk(g, root)
    _, md, cs = org.save(tmp_path, root, above, 0, False, datetime(2026, 10, 23, 17, 0))
    assert md.name == "2026-10-23_dina-director.md" and md.parent.name == "org"
    text = md.read_text(encoding="utf-8")
    assert "people_below: 4" in text and "levels_below: 2" in text
    assert "## Above\n\n- **Cora CEO**, Chief Executive, Board\n  - **Vic VP**, Vice President, Operations" in text
    assert "- **Dina Director**, Director, Delivery (2 direct, 4 in total)" in text
    assert "    - **Ana Analyst**, Analyst, Delivery" in text and "  - **Eve Expert**\n" in text
    rows = list(csv.DictReader(io.StringIO(cs.read_text(encoding="utf-8-sig"))))
    assert [(r["level"], r["name"]) for r in rows][:3] == [("-2", "Cora CEO"), ("-1", "Vic VP"), ("0", "Dina Director")]
    assert next(r for r in rows if r["name"] == "Bo Builder")["manager"] == "Max Manager"


class FakeResponse:
    def __init__(self, status, data, headers=None):
        self.status_code, self._data, self.headers, self.ok = status, data, headers or {}, 200 <= status < 300

    def json(self):
        return self._data


def test_batch_sends_throttled_requests_again_and_stops_on_401(monkeypatch):
    g = Graph(lambda: "tok")
    sent = []

    def post(url, json=None, headers=None, timeout=None):
        sent.append([r["url"] for r in json["requests"]])
        if len(sent) == 1:
            return FakeResponse(200, {"responses": [{"id": "0", "status": 200, "body": {"value": [1]}},
                                                    {"id": "1", "status": 429, "headers": {"Retry-After": "0"}}]})
        return FakeResponse(200, {"responses": [{"id": "1", "status": 200, "body": {"value": [2]}}]})
    monkeypatch.setattr(g.s, "post", post)
    assert g.batch(["/a", "/b"]) == [{"value": [1]}, {"value": [2]}] and sent == [["/a", "/b"], ["/b"]]
    monkeypatch.setattr(g.s, "post", lambda *a, **k: FakeResponse(200, {"responses": [{"id": "0", "status": 401}]}))
    with pytest.raises(TokenExpired):
        g.batch(["/a"])
    with pytest.raises(ValueError):
        g.batch(["/x"] * 21)


def test_the_interactive_chart_is_one_offline_file_and_can_be_opened_again(tmp_path):
    g = FakeGraph()
    people = org.chain(g, "vp")
    org.walk(g, people[0])
    files = org.save(tmp_path, people[0], people[1:], 0, False, datetime(2026, 10, 23, 17, 0))
    assert [f.suffix for f in files] == [".html", ".md", ".csv"] and files[0].with_suffix(".json").exists()
    page = files[0].read_text(encoding="utf-8")
    assert "window.OrgChart = OrgChart" in page and '"name": "Bo Builder"' in page    # the script and the data inlined
    assert "http" not in page.split("<script>")[0].replace('content="width', "")      # nothing fetched from the network
    saved = org.saved(tmp_path)
    assert saved == [{"name": "2026-10-23_vic-vp", "root": "Vic VP", "people": 5, "generated": "2026-10-23 17:00"}]
    d = org.load(tmp_path, "2026-10-23_vic-vp")
    assert d["above"][0]["name"] == "Cora CEO" and d["root"]["reports"][0]["name"] == "Dina Director"
    with pytest.raises(ValueError):
        org.load(tmp_path, "../../secret")


def test_a_name_with_script_cannot_break_the_page(tmp_path):
    root = org.Person("x", "</script><script>alert(1)</script>")
    page = org.page(org.chart_data(root, [], datetime(2026, 1, 1)))
    assert "</script><script>alert(1)" not in page
