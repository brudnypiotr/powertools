"""Jira downloads against a fake Jira: JQL builder, link traversal, attachments, refresh with change notes, credentials."""

import copy
import time

import pytest

from powertools.config import Settings
from powertools.jira import auth, render
from powertools.jira.client import JiraError, issue_keys
from powertools.jira.service import JiraService, build_jql
from powertools.jobs import Jobs

NAMES = {"customfield_1": "Story Points", "customfield_2": "Epic Link", "customfield_3": "Sprint",
         "customfield_4": "Acceptance criteria"}


def issue(key, summary, status="To Do", updated="2026-10-20T10:00:00.000+0200", links=(), subtasks=(), type_="Story",
          epic="", comments=(), attachments=(), points=3):
    return {
        "key": key, "names": NAMES,
        "fields": {
            "summary": summary, "status": {"name": status}, "issuetype": {"name": type_}, "priority": {"name": "High"},
            "assignee": {"displayName": "Anna Nowak"}, "reporter": {"displayName": "Jan Kowalski"},
            "labels": ["ssr"], "components": [{"name": "Rules"}], "fixVersions": [], "created": "2026-10-01T09:00:00.000+0200",
            "updated": updated, "description": None,
            "issuelinks": [{"type": {"name": "Blocks", "outward": "blocks", "inward": "is blocked by"},
                            "outwardIssue": {"key": k, "fields": {"summary": f"S {k}", "status": {"name": "Open"}}}} for k in links],
            "subtasks": [{"key": k, "fields": {"summary": f"S {k}", "status": {"name": "Open"}}} for k in subtasks],
            "attachment": [{"id": aid, "filename": name, "size": 4, "content": f"https://jira.example/att/{aid}"}
                           for aid, name in attachments],
            "comment": {"comments": [{"id": cid, "author": {"displayName": who}, "created": "2026-10-02T09:00:00.000+0200",
                                      "updated": "2026-10-02T09:00:00.000+0200", "body": "x"} for cid, who in comments]},
            "customfield_1": points, "customfield_2": epic or None,
            "customfield_3": [{"name": "Sprint 7"}], "customfield_4": None,
        },
        "renderedFields": {
            "description": f'<p>Rule <b>{summary}</b></p><p><img src="/rest/api/2/attachment/content/10"></p>',
            "comment": {"comments": [{"id": cid, "body": f"<p>comment {cid}</p>"} for cid, _ in comments]},
        },
        "changelog": {"histories": [{"created": "2026-10-22T08:00:00.000+0200", "author": {"displayName": "Anna Nowak"},
                                     "items": [{"field": "status", "fromString": "To Do", "toString": status}]}]},
    }


class FakeJira:
    base, cloud = "https://jira.example", False

    def __init__(self, issues, children=None):
        self.issues, self.children, self.calls, self.downloads = issues, children or {}, [], 0

    def issue(self, key):
        self.calls.append(key)
        if key not in self.issues:
            raise JiraError(404, "not found")
        return copy.deepcopy(self.issues[key])

    def search(self, jql, fields="", limit=200):
        if "Epic Link" in jql or jql.startswith("parent ="):
            key = jql.split("=")[1].split()[0]
            return [{"key": k} for k in self.children.get(key, [])]
        if jql.startswith("key in"):
            keys = issue_keys(jql)
            return [{"key": k, "fields": {"updated": self.issues[k]["fields"]["updated"]}} for k in keys if k in self.issues]
        return [{"key": k, "fields": {"updated": i["fields"]["updated"]}} for k, i in self.issues.items() if "ssr" in jql]

    def attachment(self, att):
        self.downloads += 1
        return b"DATA"


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(auth, "woynich_dirs", lambda: [])
    s = Settings(tmp_path / "settings.json")
    s.update({"output_dir": str(tmp_path / "out")})
    service = JiraService(s, Jobs())
    fake = FakeJira({
        "ABC-1": issue("ABC-1", "Root", links=["ABC-2"], subtasks=["ABC-3"], attachments=[("10", "shot.png")],
                       comments=[("c1", "Anna Nowak")]),
        "ABC-2": issue("ABC-2", "Linked", links=["ABC-4"]),
        "ABC-3": issue("ABC-3", "Sub"),
        "ABC-4": issue("ABC-4", "Far"),
        "ABC-9": issue("ABC-9", "Epic", type_="Epic"),
        "ABC-10": issue("ABC-10", "Child", epic="ABC-9"),
    }, children={"ABC-9": ["ABC-10"]})
    service.client = lambda: fake
    return service, fake, tmp_path / "out" / "jira"


def wait(job):
    for _ in range(200):
        if job.state in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_issue_keys_and_jql():
    assert issue_keys("abc-1, https://x/browse/ABC-22 ABC-1") == ["ABC-1", "ABC-22"]
    jql = build_jql({"project": "ABC", "epic": "ABC-9", "labels": "ssr, rules", "updated_days": 7,
                     "filters": [{"field": "Fix versions", "op": "in", "value": "2.4, 2.5"},
                                 {"field": "status", "op": "!=", "value": "Done"},
                                 {"field": "assignee", "op": "is", "value": "empty"}],
                     "jql": "priority = High"})
    assert jql == ('project = "ABC" AND (parent = ABC-9 OR "Epic Link" = ABC-9) AND labels in ("ssr", "rules") AND '
                   '"Fix versions" IN ("2.4", "2.5") AND status != "Done" AND assignee IS EMPTY AND updated >= -7d AND '
                   '(priority = High) ORDER BY updated DESC')
    with pytest.raises(ValueError):
        build_jql({})
    with pytest.raises(ValueError):
        build_jql({"filters": [{"field": "x", "op": "drop", "value": "1"}]})


def test_render_markdown_parts():
    i = issue("ABC-1", "Root", links=["ABC-2"], comments=[("c1", "Anna Nowak")])
    s = render.snapshot(i)
    assert s["epic"] == "" and s["sprints"] == ["Sprint 7"] and s["links"] == ["blocks ABC-2"]
    assert render.custom_fields(i) == {"Story Points": "3"}
    md = render.markdown(i, "https://jira.example", {"status": "new"}, {"10": "ABC-1/shot.png"}, [("shot.png", "ABC-1/shot.png", 4)],
                         [], [], ["2026-10-23 10:00: new"])
    assert 'key: "ABC-1"' in md and "Rule **Root**" in md and "![](ABC-1/shot.png)" in md
    assert "- blocks [ABC-2](ABC-2.md) S ABC-2 (Open)" in md and "### Anna Nowak" in md and "comment c1" in md


def test_download_with_depth_and_attachments(svc):
    service, fake, out = svc
    job = wait(service.download({"mode": "keys", "value": "ABC-1"}, {"depth": 1, "follow": ["links", "subtasks"],
                                                                     "attachments": True}))
    assert job.state == "done", job.error
    assert sorted(fake.calls) == ["ABC-1", "ABC-2", "ABC-3"]  # ABC-4 is depth 2
    assert (out / "ABC-1.md").exists() and (out / "ABC-1" / "shot.png").read_bytes() == b"DATA"
    assert "found_via" in (out / "ABC-2.md").read_text(encoding="utf-8")
    assert job.notes[-1] == "3 new"
    deeper = wait(service.download({"mode": "keys", "value": "ABC-1"}, {"depth": 2, "max_issues": 50}))
    assert "ABC-4" in fake.calls and deeper.notes[-1] == "1 new, 3 unchanged"
    assert fake.downloads == 1  # the attachment is not downloaded again


def test_epic_and_max_issues(svc):
    service, fake, out = svc
    job = wait(service.download({"mode": "epic", "value": "ABC-9"}, {"depth": 0}))
    assert sorted(fake.calls) == ["ABC-10", "ABC-9"] and (out / "ABC-10.md").exists()
    job = wait(service.download({"mode": "keys", "value": "ABC-1"}, {"depth": 3, "max_issues": 2}))
    assert any("stopped at 2" in n for n in job.notes)


def test_refresh_notes_changes(svc):
    service, fake, out = svc
    wait(service.download({"mode": "keys", "value": "ABC-1, ABC-3"}, {"attachments": False}))
    first = (out / "ABC-1.md").read_text(encoding="utf-8")
    assert 'download_status: "new"' in first and "shot.png (0 KB, not downloaded)" in first
    fake.issues["ABC-1"] = issue("ABC-1", "Root v2", status="Done", updated="2026-10-22T09:00:00.000+0200", links=["ABC-2"],
                                 subtasks=["ABC-3"], attachments=[("10", "shot.png")],
                                 comments=[("c1", "Anna Nowak"), ("c2", "Jan Kowalski")], points=5)
    fake.calls.clear()
    job = wait(service.refresh(None, {"attachments": False}))
    assert fake.calls == ["ABC-1"]  # ABC-3 not updated in Jira: not fetched
    assert job.notes[-1] == "1 changed, 1 unchanged"
    md = (out / "ABC-1.md").read_text(encoding="utf-8")
    assert 'download_status: "changed"' in md and "Changed since the last download" in md
    for line in ("Summary: Root → Root v2", "Status: To Do → Done", "Description changed", "New comment by Jan Kowalski",
                 "Story Points: 3 → 5", "Anna Nowak: status To Do → Done"):
        assert line in md, line
    rows = {r["key"]: r for r in service.downloaded(fake.base)}
    assert rows["ABC-1"]["last_result"] == "changed" and rows["ABC-1"]["status"] == "Done"


def test_missing_issue_does_not_stop_job(svc):
    service, fake, out = svc
    job = wait(service.download({"mode": "keys", "value": "ABC-3 ABC-404"}, {}))
    assert job.state == "done" and any("ABC-404" in n for n in job.notes) and job.notes[-1] == "1 new, 1 failed"


def test_credentials_from_woynich_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path))
    prof = tmp_path / "woynich-wiki" / "instance" / "config"
    prof.mkdir(parents=True)
    (prof / "profile.yaml").write_text("instance:\n  title: x\njira:\n  base_url: https://site.atlassian.net/\n"
                                       "  email: 'me@example.com'  # sign-in\ncode_repos: []\n", encoding="utf-8")
    monkeypatch.setattr(auth, "woynich_dirs", lambda: [tmp_path / "woynich-wiki"])
    monkeypatch.setattr(auth, "woynich_keyring_token", lambda: "tok-from-woynich")
    s = Settings(tmp_path / "settings.json")
    c = auth.resolve(s, auth.JiraTokenStore(tmp_path / "t.bin"))
    assert (c.base_url, c.email, c.token, c.token_source) == ("https://site.atlassian.net", "me@example.com",
                                                             "tok-from-woynich", "woynich-wiki (Keys and tokens)")
    store = auth.JiraTokenStore(tmp_path / "t.bin")
    store.set("pasted-token-123")
    s.update({"jira_url": "https://other.example"})
    c = auth.resolve(s, auth.JiraTokenStore(tmp_path / "t.bin"))
    assert (c.base_url, c.token, c.token_source) == ("https://other.example", "pasted-token-123", "pasted in powertools")
