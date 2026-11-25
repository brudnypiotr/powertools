"""Deleting and editing what the app keeps (with a way back where something could be lost), the chat, dictation into it."""

import time

import numpy as np
import pytest

from powertools.assistant.memory import Memory
from powertools.config import Settings
from powertools.dictation import Dictation
from powertools.engine import Result

from .test_assistant import Model, make
from .test_web import call, server  # noqa: F401  (fixture)

M = {"ask": {"answer": "You wait for Ola.", "sources": ["S1"]}, "remember": {"edits": []}}


# ---------------------------------------------------------------- the chat
def test_the_chat_survives_a_restart_and_a_turn_can_be_deleted(tmp_path):
    a, g = make(tmp_path, Model(**M))
    a.updates.start()
    first, second = a.asking.ask("Who do I wait for?"), a.asking.ask("What else?")
    b, _ = make(tmp_path, Model(**M))
    assert [t["question"] for t in b.asking.talk()] == ["Who do I wait for?", "What else?"]
    b.asking.forget(first["id"])
    assert [t["id"] for t in b.asking.talk()] == [second["id"]]
    b.asking.forget()
    assert b.asking.talk() == []


def test_a_question_about_the_open_item_gets_that_conversation_first(tmp_path):
    a, g = make(tmp_path, Model(**M))
    a.updates.start()
    a.asking.ask("What does it say?", about="mail:cv1")
    user = a.kit.llm.asked("ask")[-1]["user"]
    assert user.index("[S1] Mail: Review of rule 12") and "is looking at: Review rule 12" in user
    assert a.asking.talk()[-1]["about"] == "Review of rule 12"
    a.asking.ask("And this?", about="mail:gone")                               # not there any more: an ordinary question


# ---------------------------------------------------------------- items and promises
def test_an_item_can_be_corrected_and_deleted_and_both_can_be_undone(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    a.actions.act("mail:cv1", "edit", '{"action": "Review rules 12 and 13", "due": "Monday", "person": "Anna K"}')
    i = a.view()["decisions"][0]
    assert (i["action"], i["due"], i["person"]) == ("Review rules 12 and 13", "Monday", "Anna K") and i["edited"]
    a.actions.act("mail:cv1", "undo")
    assert a.state.item("mail:cv1")["due"] == "Friday" and not a.state.item("mail:cv1").get("edited")
    a.actions.act("mail:cv1", "delete")
    v = a.view()
    assert not [x for k in ("decisions", "mine", "changed", "closed", "ignored") for x in v[k] if x["key"] == "mail:cv1"]
    a.actions.act("mail:cv1", "undo")
    assert a.view()["decisions"]
    with pytest.raises(ValueError):
        a.actions.act("mail:cv1", "edit", '{"action": " "}')
    with pytest.raises(ValueError):
        a.actions.act("mail:cv1", "edit", "no json")


def test_a_promise_can_be_corrected(tmp_path):
    a, g = make(tmp_path)
    a.updates.start({"kind": "days", "days": 9})
    p = next(p for p in a.state.promises() if p["mine"])
    a.state.close_promise(p["id"], "open", what="send the new plan", due="Friday")
    q = next(p for p in a.state.promises() if p["mine"])
    assert (q["id"], q["what"], q["due"]) == (p["id"], "send the new plan", "Friday")
    a.state.close_promise(p["id"], "done")
    assert not [p for p in a.state.promises() if p["mine"]]


def test_corrections_can_be_looked_at_and_taken_back(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    a.actions.act("mail:cv1", "category", "fyi")
    c = a.actions.corrections()
    assert c[0]["now"] == "fyi" and c[0]["title"] == "Review of rule 12"
    a.actions.forget_correction(c[0]["at"])
    assert a.actions.corrections() == []


# ---------------------------------------------------------------- memory: files, lines, matters, people
def test_a_file_can_be_deleted_and_brought_back(tmp_path):
    m = Memory(tmp_path / "mem", tmp_path / "hist")
    m.write("people/ola.md", "# Ola\n")
    entry = m.remove("people/ola.md")
    assert m.read("people/ola.md") == "" and "people/ola.md" not in m.files()
    m.undo(entry["id"])
    assert m.read("people/ola.md") == "# Ola\n"
    again = m.remove("people/ola.md")
    m.write("people/ola.md", "# New\n")
    with pytest.raises(ValueError, match="there again"):
        m.undo(again["id"])
    with pytest.raises(KeyError):
        m.remove("people/nobody.md")


def test_any_line_can_be_edited_or_deleted_also_in_the_timeline(tmp_path):
    a, g = make(tmp_path)
    mid = a.memory.create_matter("Rule 12 review")
    a.updates.start()
    a.actions.act("mail:cv1", "matter", mid)
    f = f"matters/{mid}.md"
    line = [ln for ln in a.memory.read(f).splitlines() if "Anna wants" in ln][0]
    e = a.memory.line(f, line, "- 2026-11-25 · Anna asked again")
    assert "Anna asked again (you)" in a.memory.read(f) and "Anna wants" not in a.memory.read(f).split("## Timeline")[1]
    a.memory.line(f, "- 2026-11-25 · Anna asked again (you)", "")
    assert "Anna asked again" not in a.memory.read(f)
    with pytest.raises(ValueError, match="not in the file"):
        a.memory.line(f, line, "x")
    with pytest.raises(ValueError):
        a.memory.undo(e["id"])                                                  # changed since


def test_a_matter_can_be_renamed_and_deleted_and_comes_back_with_its_items(tmp_path):
    a, g = make(tmp_path)
    mid = a.memory.create_matter("Rule 12 review")
    a.updates.start()
    a.actions.act("mail:cv1", "matter", mid)
    a.memory.rename_matter(mid, "Rules of the year")
    assert [m["title"] for m in a.view()["matters"]] == ["Rules of the year"] and "# Rules of the year" in a.memory.read(f"matters/{mid}.md")
    entry = a.memory.remove(f"matters/{mid}.md")
    v = a.view()
    assert v["matters"] == [] and v["decisions"][0]["matter_title"] == ""
    a.memory.undo(entry["id"])
    assert a.view()["decisions"][0]["matter_title"] == "Rules of the year"
    with pytest.raises(ValueError):
        a.memory.rename_matter(mid, "  ")


def test_a_direct_report_can_be_hidden_and_added_again(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    a.people.remove("Ola")
    assert a.people.all() == []
    a.state.data["people_at"] = 0
    a.updates.start()
    assert a.people.all() == []                                                 # stays hidden after an Update
    a.people.add("Ola")
    assert [p["name"] for p in a.people.all()] == ["Ola"]


def test_a_thrown_away_draft_comes_back(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    did = a.state.item("teams:c1")["draft"]
    a.drafts.close(did, "dropped")
    assert not a.state.item("teams:c1").get("draft")
    a.drafts.restore(did)
    assert a.state.item("teams:c1")["draft"] == did and did in [d["id"] for d in a.drafts.list()]
    d = a.drafts.new("Week", "x")
    assert a.drafts.save(d["id"], "y", title="My week")["title"] == "My week"


def test_a_meeting_card_can_be_thrown_away_and_comes_back(tmp_path):
    prep = {"goal": "G", "known": [], "open": [], "questions": [], "agenda": []}
    a, g = make(tmp_path, Model(meeting_prep=prep))
    a.updates.start()
    c = a.briefs.prep("ev1")
    entry = a.briefs.drop("ev1")
    assert not a.view()["events"][0]["prepared"] and a.memory.read(c["file"]) == ""
    a.memory.undo(entry["id"])
    assert "G" in a.memory.read(c["file"])
    with pytest.raises(KeyError):
        a.briefs.drop("ev1")


def test_what_was_missed_is_a_file_like_any_other(tmp_path):
    a, g = make(tmp_path)
    a.state.data["missed"] = ["an old one"]                                     # of an earlier version
    a.actions.feedback("The mails of the airline are always for me")
    assert "airline" in a.memory.read("missed.md") and "an old one" in a.memory.read("missed.md") and a.state.data["missed"] == []
    a.memory.line("missed.md", "- The mails of the airline are always for me (you)", "")
    assert "airline" not in a.kit.corrections() and "an old one" in a.kit.corrections()


# ---------------------------------------------------------------- the history is not in the memory folder
def test_the_history_moves_out_of_the_memory_folder(tmp_path):
    old = tmp_path / "mem" / ".history"
    old.mkdir(parents=True)
    (old / "1.json").write_text('{"id": "1", "file": "profile.md", "before": "", "after": "# Me\\n", "existed": false, "why": ""}')
    (tmp_path / "mem" / "profile.md").write_text("# Me\n")
    m = Memory(tmp_path / "mem", tmp_path / "hist")
    m.undo("1")
    assert not old.exists() and (tmp_path / "hist").is_dir() and m.read("profile.md") == ""


# ---------------------------------------------------------------- dictation into the window
class Recorder:
    def start(self, name):
        pass

    def stop(self):
        return np.zeros(16000, dtype=np.float32)


class Engine:
    state, info = "ready", {}

    def submit(self, audio, done, **k):
        done(Result("the deadline is Thursday", "en", 1, 0.1))


class Store:
    def add(self, *a):
        return 7


def test_dictation_into_the_window_pastes_nothing(tmp_path):
    pasted = []
    d = Dictation(Settings(tmp_path / "s.json"), Engine(), Recorder(), Store(), lambda *a, **k: pasted.append(a), lambda s: None)
    d.toggle("window")
    assert d.target == "window"
    d.toggle()                                                                  # the hotkey stops it too
    assert pasted == [] and d.last["to"] == "window" and d.last["text"] == "the deadline is Thursday"
    seq = d.last["seq"]
    d.toggle()
    d.toggle()
    assert len(pasted) == 1 and d.last["to"] == "" and d.last["seq"] == seq + 1


# ---------------------------------------------------------------- through the API
def test_deleting_and_editing_through_the_window(server, tmp_path):  # noqa: F811
    app, base = server
    app.settings.update({"memory_dir": str(tmp_path / "mem")})
    mid = call(base, "POST", "/api/matters", {"title": "Alpha"})["id"]
    call(base, "POST", f"/api/matters/{mid}/rename", {"title": "Beta"})
    e = call(base, "DELETE", f"/api/memory/file?name=matters/{mid}.md")
    call(base, "POST", "/api/memory/undo", {"id": e["id"]})
    assert call(base, "GET", "/api/memory")["files"] == [f"matters/{mid}.md"]
    call(base, "POST", "/api/memory/line", {"file": f"matters/{mid}.md", "line": "# Beta", "new": "# Gamma"})
    n = app.store.add("raw", "hello", "en", 1, 0.1)
    assert call(base, "POST", f"/api/history/{n}", {"text": "hello there"})["text"] == "hello there"
    call(base, "DELETE", "/api/history")
    assert call(base, "GET", "/api/history") == []
    app.graph.jobs.submit("x", lambda j: None, "mail")
    time.sleep(0.1)
    assert call(base, "DELETE", "/api/jobs") == []
    assert call(base, "DELETE", "/api/assistant/talk") == []


def test_saved_charts_and_jira_rows_can_be_removed(server, tmp_path):  # noqa: F811
    app, base = server
    app.settings.update({"output_dir": str(tmp_path / "out"), "jira_url": "https://j.example.invalid"})
    d = tmp_path / "out" / "org"
    d.mkdir(parents=True)
    for ext in ("json", "md", "html", "csv"):
        (d / f"2026-11-25_ola.{ext}").write_text('{"root": {"name": "Ola"}}')
    assert call(base, "DELETE", "/api/org/saved/2026-11-25_ola") == [] and not list(d.iterdir())
    app.jira._save("https://j.example.invalid", "ABC-1", {"snapshot": {"key": "ABC-1"}})
    assert call(base, "POST", "/api/jira/forget", {"keys": ["ABC-1", "../x"]}) == []


def test_a_recording_can_be_renamed_and_deleted_with_its_transcript(server, tmp_path):  # noqa: F811
    from powertools.meetings.manager import Session

    app, base = server
    md = tmp_path / "t.md"
    md.write_text('---\ntitle: "Teams call"\n---\n# Teams call\n\n## Transcript\n')
    folder = app.meetings.root / "20261125-100000"
    folder.mkdir()
    Session(folder, {"title": "Teams call", "started": time.time(), "status": "done", "md": str(md)}).save()
    call(base, "POST", "/api/meetings/20261125-100000/title", {"title": "Contract sync"})
    assert app.meetings.get("20261125-100000").meta["title"] == "Contract sync" and "# Contract sync" in md.read_text()
    call(base, "DELETE", "/api/meetings/20261125-100000?file=1")
    assert not folder.exists() and not md.exists()
