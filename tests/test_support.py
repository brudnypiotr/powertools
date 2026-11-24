"""The window's key, the way back after an update, the backup before it, the report for support and the feedback package."""

import json
import subprocess
import urllib.error
import urllib.request
import zipfile

import pytest

from powertools import backup, diagnostics, feedback, updater

from .test_assistant import make
from .test_web import call, server  # noqa: F401  (fixture)


def raw(url, key="", method="GET", cookie=""):
    req = urllib.request.Request(url, method=method, headers={"X-Powertools": "1", **({"X-Powertools-Key": key} if key else {}),
                                                              **({"Cookie": cookie} if cookie else {})}, data=b"{}" if method == "POST" else None)
    return urllib.request.urlopen(req, timeout=5)


# ---------------------------------------------------------------- the key of the window
def test_nothing_is_readable_without_the_key(server):  # noqa: F811
    app, base = server
    for path in ("/api/assistant", "/api/state", "/api/history"):
        with pytest.raises(urllib.error.HTTPError) as e:
            raw(base + path)
        assert e.value.code == 403 and json.loads(e.value.read())["error"] == "locked"
    assert raw(base + "/api/ping").status == 200                              # how a second start finds the first
    assert raw(base + "/app.js").status == 200                                # no data in it
    assert raw(base + "/api/state", app.door.key).status == 200


def test_the_link_with_the_key_sets_a_cookie_and_leaves_the_address_bar(server):  # noqa: F811
    app, base = server

    class Stay(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a):
            return None
    opener = urllib.request.build_opener(Stay)
    with pytest.raises(urllib.error.HTTPError) as e:
        opener.open(f"{base}/?t={app.door.key}")
    cookie = e.value.headers["Set-Cookie"]
    assert e.value.code == 302 and e.value.headers["Location"] == "/" and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert raw(base + "/api/state", cookie=cookie.split(";")[0]).status == 200
    with pytest.raises(urllib.error.HTTPError):
        raw(base + "/api/state", cookie="powertools=wrong")


def test_setup_can_still_close_the_running_app(server):  # noqa: F811
    app, base = server
    quits = []
    app.quit = lambda: quits.append(1)
    assert raw(base + "/api/quit", method="POST").status == 200 and quits    # setup.ps1 has no key


def test_unlock_opens_a_window_with_the_key_and_tells_nobody(server, monkeypatch):  # noqa: F811
    app, base = server
    opened = []
    app.open_window = lambda fresh=False: opened.append(fresh)
    answer = raw(base + "/api/unlock", method="POST").read().decode()
    assert opened == [True] and app.door.key not in answer


def test_the_key_stays_the_same_after_a_restart(tmp_path):
    from powertools.web.door import Door

    assert Door(tmp_path / "k").key == Door(tmp_path / "k").key and len(Door(tmp_path / "k2").key) > 30


# ---------------------------------------------------------------- back after a bad update, backup before it
def repo(tmp_path):
    root = tmp_path / "copy"
    root.mkdir()

    def g(*a):
        return subprocess.run(["git", "-c", "user.email=a@b.c", "-c", "user.name=n", *a], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    g("init", "-q")
    (root / "powertools").mkdir()
    for v in ("1.0.0", "1.1.0"):
        (root / "powertools" / "__init__.py").write_text(f'__version__ = "{v}"\n')
        g("add", "-A")
        g("commit", "-qm", v)
    return root, g


def test_an_update_can_be_taken_back_once(tmp_path, monkeypatch):
    root, g = repo(tmp_path)
    monkeypatch.setattr(updater, "ROOT", root)
    old, new = g("rev-parse", "HEAD~1"), g("rev-parse", "HEAD")
    assert updater.previous() == {}
    updater.remember(old)
    assert updater.previous() == {"commit": old, "version": "1.0.0"}
    assert updater.rollback()["version"] == "1.0.0" and g("rev-parse", "HEAD") == old and updater.previous() == {}
    with pytest.raises(updater.GitError, match="no earlier version"):
        updater.rollback()
    g("reset", "-q", "--hard", new)
    updater.remember(old)
    (root / "powertools" / "__init__.py").write_text("changed by hand")
    with pytest.raises(updater.GitError, match="changed"):
        updater.rollback()
    assert (root / "powertools" / "__init__.py").read_text() == "changed by hand"


def test_the_backup_has_the_state_and_memory_but_no_tokens(tmp_path):
    home, memory = tmp_path / "home", tmp_path / "mem"
    (memory / "matters").mkdir(parents=True)
    (memory / ".history").mkdir()
    (memory / "matters" / "a.md").write_text("x")
    (memory / ".history" / "1.json").write_text("x")
    home.mkdir()
    for n in ("assistant.json", "settings.json", "graph-token.bin", "github-token.bin", "window.key"):
        (home / n).write_text("x")
    for n in range(7):
        z = backup.make(home, memory, f"1.{n}")
    with zipfile.ZipFile(z) as f:
        assert sorted(f.namelist()) == ["assistant.json", "memory/matters/a.md", "settings.json"]
    assert len(list((home / "backups").glob("*.zip"))) == backup.KEEP


# ---------------------------------------------------------------- the report and the package
def test_the_report_has_state_but_no_names_titles_or_text(server, tmp_path, caplog):  # noqa: F811
    app, base = server
    app.settings.update({"memory_dir": str(tmp_path / "mem"), "exclude": ["Hanna HR"], "jira_url": "https://secretcorp.example.invalid"})
    a, g = make(tmp_path)
    a.update()
    app.assistant.data = a.data
    app.assistant.memory.create_matter("Secret merger")
    (diagnostics.data_dir() / "app.log").write_text(
        "2026-11-25 ERROR   powertools.web: GET /api/x failed\nTraceback (most recent call last):\n  File \"/u/x.py\", line 3, in f\n"
        "ValueError: Review of rule 12 by anna@example.invalid about Secret merger\n2026-11-25 INFO    powertools.meetings: meeting started: Salary talk\n",
        encoding="utf-8")
    text = diagnostics.as_text(diagnostics.report(app))
    for private in ("Anna", "rule 12", "Hanna", "Secret merger", "secretcorp", "anna@example", "Salary"):
        assert private not in text, private
    assert '"items"' in text and "ValueError" in text and "line 3" in text and app.door.key not in text


def test_the_feedback_package_is_a_file_with_the_users_words_and_pictures(server, tmp_path, monkeypatch):  # noqa: F811
    app, base = server
    monkeypatch.setattr(feedback, "desktop", lambda: tmp_path)
    png = "data:image/png;base64,iVBORw0KGgo="
    r = call(base, "POST", "/api/feedback/package", {"text": "Bigger buttons", "diagnostics": True, "pictures": [png, "data:text/html;base64,eA=="]})
    with zipfile.ZipFile(r["path"]) as z:
        assert sorted(z.namelist()) == ["diagnostics.json", "feedback.md", "screenshots/1.png"]
        assert "Bigger buttons" in z.read("feedback.md").decode()
    r = call(base, "POST", "/api/feedback/package", {"text": "x", "diagnostics": False, "pictures": []})
    with zipfile.ZipFile(r["path"]) as z:
        assert z.namelist() == ["feedback.md"]
    with pytest.raises(urllib.error.HTTPError):
        call(base, "POST", "/api/feedback/package", {"text": " "})


def test_a_public_repository_gets_no_ideas(server, monkeypatch):  # noqa: F811
    app, base = server
    monkeypatch.setattr(feedback, "repo_url", lambda: "https://github.com/brudnypiotr/powertools")
    assert call(base, "GET", "/api/feedback")["issues"] is False
    with pytest.raises(urllib.error.HTTPError):
        call(base, "POST", "/api/idea", {"text": "x"})
    monkeypatch.setattr(feedback, "repo_url", lambda: "https://github.com/o/r")
    assert call(base, "GET", "/api/feedback")["issues"] is True


def test_what_the_recap_missed_is_told_to_the_model_next_time(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path))
    a, g = make(tmp_path)
    a.feedback("The mails of the airline are always for me")
    a.update()
    assert "The mails of the airline are always for me" in a.llm.asked("triage")[0]["system"]
