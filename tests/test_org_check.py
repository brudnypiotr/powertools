"""checks/org.py on a fake Graph: every approach finds the same branch, and the report never holds names."""
from checks.common import Context
from checks import org

# boss -> [mgr, other]; mgr -> [me, peer]; other -> [x]
TREE = {"boss": ["mgr", "other"], "mgr": ["me", "peer"], "other": ["x"], "me": [], "peer": [], "x": []}
UP = {"me": "mgr", "mgr": "boss"}
NAMES = {k: f"Secret Name {k}" for k in TREE}


def person(uid):
    return {"id": uid, "displayName": NAMES[uid], "jobTitle": "Secret Title", "department": "Secret Dept" if uid != "x" else None}


class FakeGraph:
    def __init__(self, levels=True):
        self.levels, self.calls = levels, []

    def get(self, path, params=None, headers=None):
        self.calls.append(path)
        uid = "me" if path.startswith("/me") else path.split("/")[2] if path.startswith("/users/") else None
        if path == "/me" and params and "$expand" in params:
            if not self.levels:
                return 400, None, "Request_UnsupportedQuery: levels"
            node, m = {"id": "me"}, None
            cur = node
            for up in ("mgr", "boss"):
                cur["manager"] = {"id": up}; cur = cur["manager"]
            return 200, node, ""
        if path == "/me":
            return 200, person("me"), ""
        if path.endswith("/manager"):
            return (200, {"id": UP[uid]}, "") if uid in UP else (404, None, "Request_ResourceNotFound: no manager")
        if path.endswith("/directReports"):
            return 200, {"value": [person(c) for c in TREE[uid]]}, ""
        if path == "/users":
            if "$expand" in params:
                return 200, {"value": [{"id": k, **({"manager": {"id": v}} if (v := UP.get(k) or next((p for p, c in TREE.items() if k in c), None)) else {})} for k in TREE]}, ""
            return 200, {"value": [{"id": k} for k in TREE], "@odata.count": 6}, ""
        if path == "/me/people":
            return 200, {"value": [{"id": "peer"}]}, ""
        return 404, None, "not found"

    def post(self, path, body):
        assert path == "/$batch" and len(body["requests"]) <= 20
        return 200, {"responses": [{"id": r["id"], "status": 200,
                                    "body": {"value": [person(c) for c in TREE[r["url"].split("/")[2]]]}} for r in body["requests"]]}, ""


def results(g):
    ctx = Context(interactive=False)
    org.probe_graph(ctx, g)
    return ctx, {r.id: r for r in ctx.results}


def test_all_approaches_see_the_same_branch_and_no_names_leak():
    g = FakeGraph()
    ctx, r = results(g)
    assert r["org.chain.one_request"].status == "OK" and "2 levels up" in r["org.chain.one_request"].detail
    assert "org.chain.step_by_step" not in r                          # not needed when $levels works
    assert r["org.reports.manager"].detail == "your manager has 2 direct reports"
    for k in ("org.walk.plain", "org.walk.batch"):                    # the branch under the manager's manager
        assert "5 people, 2 levels down" in r[k].detail and "with department 4" in r[k].detail
    assert "6 requests" in r["org.walk.plain"].detail and "3 requests" in r["org.walk.batch"].detail  # one per level
    assert "(of 6)" in r["org.directory.list"].detail and "(5 have one)" in r["org.directory.with_manager"].detail
    text = " ".join(x.detail for x in ctx.results)
    assert "Secret" not in text                                        # counts only, never content


def test_without_levels_it_climbs_step_by_step():
    ctx, r = results(FakeGraph(levels=False))
    assert r["org.chain.one_request"].status == "WARN"
    assert r["org.chain.step_by_step"].status == "OK" and "2 levels up, 3 requests" in r["org.chain.step_by_step"].detail
