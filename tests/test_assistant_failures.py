"""What goes wrong in real life: a forbidden chat, a killed app, a locked file, a model that answers nonsense, my own answers."""

from pathlib import Path

import pytest

from powertools.assistant import collect
from powertools.brain import Usage
from powertools.graph.client import GraphError, TokenExpired
from powertools.llm import LLMError

from .test_assistant import ANNA, NOTHING, OLA, Model, addr, iso, mail, make

WIDE = {"kind": "days", "days": 9}


def fail(exc):
    def f(*a, **k):
        raise exc
    return f


def said_in_chat(mid, hours, text, me=False):
    user = {"id": "u-me", "displayName": "Piotr B"} if me else {"id": "u-jan", "displayName": "Jan"}
    return {"id": mid, "messageType": "message", "createdDateTime": iso(hours), "from": {"user": user},
            "body": {"contentType": "text", "content": text}}


# ---------------------------------------------------------------- an update that meets trouble
def test_one_forbidden_chat_is_named_and_the_rest_goes_on(tmp_path):
    a, g = make(tmp_path)
    g.fail = {"/chats/c1/messages": GraphError(403, "Forbidden", "you left it")}
    a.updates.start()
    assert a.state.data["cursor"] and any("Jan" in p for p in a.state.data["last"]["problems"])


def test_a_service_error_keeps_what_was_read_and_reads_the_range_again(tmp_path):
    a, g = make(tmp_path)
    g.fail = {"/chats/c1/messages": GraphError(503, "Busy", "later")}
    a.updates.start()
    assert a.state.data["cursor"] == "" and "mail:cv1" in a.state.data["items"]


def test_more_chats_than_one_update_reads_is_said(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "MAX_CHATS", 2)
    a, g = make(tmp_path)
    real = g.get

    def get(p, params=None, headers=None):
        if p == "/me/chats":
            return {"value": [{"id": f"x{n}", "chatType": "group", "topic": f"T{n}", "members": [{"displayName": "J"}],
                               "lastMessagePreview": {"createdDateTime": iso(2)}} for n in range(3)]}
        return {"value": g.chat_msgs} if p.startswith("/chats/x") else real(p, params, headers)
    g.get = get
    a.updates.start()
    assert any("newest 2" in p for p in a.state.data["last"]["problems"])


def test_killed_after_sorting_the_next_update_still_matches(tmp_path):
    a, g = make(tmp_path)
    a.filing.match, real = fail(RuntimeError("killed")), a.filing.match
    with pytest.raises(RuntimeError):
        a.updates.start()
    a.state.save()
    a.filing.match, a.updates.job = real, None
    a.updates.start()
    assert a.kit.llm.asked("match matters")


def test_matching_that_failed_is_tried_again(tmp_path):
    calls = []

    def match(user):
        calls.append(user)
        if len(calls) == 1:
            raise LLMError("limit")
        return Model.match_matters
    a, g = make(tmp_path, Model(match_matters=match))
    a.updates.start()
    assert any("matter" in p for p in a.state.data["last"]["problems"])
    a.updates.start()
    assert len(calls) == 2
    a.updates.start()
    assert len(calls) == 2


def test_a_malformed_answer_is_one_item_not_the_whole_update(tmp_path):
    broken = {**NOTHING, "category": "todo", "summary": "x", "commitments": {"a": 1}}
    a, g = make(tmp_path, Model(triage=lambda u: broken))
    a.updates.start()
    assert a.state.data["cursor"] and a.state.data["items"]["mail:cv1"]["commitments"] == []


def test_an_error_in_the_code_for_one_item_does_not_stop_the_others(tmp_path, monkeypatch):
    a, g = make(tmp_path)
    real = a.sorting.classify
    monkeypatch.setattr(a.sorting, "classify", lambda i, *x, **k: fail(TypeError("odd"))() if i["key"] == "mail:cv1" else real(i, *x, **k))
    a.updates.start()
    assert a.state.data["items"]["mail:cv1"]["error"] and a.state.data["items"]["teams:c1"]["category"] == "reply"


def test_a_new_range_while_waiting_for_a_token_is_used(tmp_path):
    a, g = make(tmp_path)
    g.fail = {"/me": TokenExpired("x")}
    job = a.updates.start()
    assert a.updates.start({"kind": "days", "days": 30}) is job
    g.fail = {}
    job.run(job)
    assert "mail:cv3" in a.state.data["items"]                                          # 5 days old: outside the first range


def test_what_was_read_before_the_token_ended_is_not_read_again(tmp_path):
    a, g = make(tmp_path)
    g.fail = {"/me/chats": TokenExpired("x")}
    job = a.updates.start()
    assert a.view()["paused_at"].startswith("reading Teams")
    g.fail, g.calls = {}, []
    job.run(job)
    assert "mail:cv1" in a.state.data["items"] and "/me/mailFolders/inbox/messages" not in g.calls


def test_a_failed_update_is_visible(tmp_path):
    a, g = make(tmp_path)
    job = a.updates.start()
    job.state, job.error = "error", "no connection to Microsoft"
    assert a.view()["job"]["error"] == "no connection to Microsoft"


def test_no_network_is_a_graph_error(monkeypatch):
    import requests

    from powertools.graph import client

    monkeypatch.setattr(client.time, "sleep", lambda s: None)
    g = client.Graph(lambda: "t")
    g.s.get = fail(requests.ConnectionError("offline"))
    with pytest.raises(GraphError, match="no connection"):
        g.get("/me")


def test_trouble_before_the_token_ended_still_counts_after_it(tmp_path):
    a, g = make(tmp_path)
    g.fail = {"/me/messages": GraphError(503, "Busy", "later"), "/me/chats": TokenExpired("x")}
    job = a.updates.start()
    g.fail = {}
    job.run(job)
    assert a.state.data["cursor"] == "" and any("Mail" in p for p in a.state.data["last"]["problems"])


# ---------------------------------------------------------------- my own answers, drafts, promises
def test_my_answer_closes_the_draft_and_is_never_waiting_for_me(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    assert a.state.data["items"]["teams:c1"]["draft"]
    g.chat_msgs = g.chat_msgs + [said_in_chat("t2", 0.5, "Yes, at 3 pm.", me=True)]
    a.updates.start(WIDE)
    i = a.state.data["items"]["teams:c1"]
    assert not i.get("draft") and i["status"] == "done" and i["done_by"] == "answer"   # the model still says "reply"
    assert "teams:c1" in [x["key"] for x in a.view()["closed"]]


def test_a_new_message_replaces_an_untouched_draft_and_marks_an_edited_one(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    first = a.state.data["items"]["teams:c1"]["draft"]
    g.chat_msgs = g.chat_msgs + [said_in_chat("t2", 0.5, "And can you answer my question about Monday?")]
    a.updates.start(WIDE)
    second = a.state.data["items"]["teams:c1"]["draft"]
    assert second != first
    a.drafts.save(second, "my own words")
    g.chat_msgs = g.chat_msgs + [said_in_chat("t3", 0.1, "Any answer to my question?")]
    a.updates.start(WIDE)
    d = a.drafts.get(a.state.data["items"]["teams:c1"]["draft"])
    assert d["id"] == second and d["body"] == "my own words" and d["old"]


def test_the_same_text_goes_to_outlook_once(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    made = []
    g.reply_draft = lambda *x, **k: made.append(1) or {"webLink": "https://o/d"}
    d = a.drafts.create("mail:cv1")
    a.drafts.to_outlook(d["id"])
    a.drafts.to_outlook(d["id"])
    assert len(made) == 1
    a.drafts.save(d["id"], "something else")
    a.drafts.to_outlook(d["id"])
    assert len(made) == 2


def test_a_closed_promise_stays_closed_in_other_words(tmp_path):
    words = iter(["send the plan", "send Ola the plan"])

    def triage(u):
        a = Model().triage(u)
        if "contract" in u:
            a["commitments"] = [{"who": "me", "to": "Ola", "what": next(words), "due": "", "quote": "I will send you the plan tomorrow"}]
        return a
    a, g = make(tmp_path, Model(triage=triage))
    a.updates.start(WIDE)
    a.state.close_promise(next(p["id"] for p in a.state.promises() if p["mine"]), "done")
    g.threads["cv3"].append(mail("e9", "cv3", "Contract", 1, OLA, "thanks"))
    a.updates.start(WIDE)
    assert not [p for p in a.state.promises() if p["mine"]]


def test_an_open_promise_outlives_a_conversation_that_is_only_news(tmp_path):
    a, g = make(tmp_path)
    a.updates.start(WIDE)
    i = a.state.data["items"]["mail:cv3"]
    i.update(category="fyi", last=iso(31 * 24))
    a.updates.prune()
    assert [p for p in a.state.promises() if p["mine"]]


def test_the_model_hears_how_the_user_filed_the_conversation_before(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    a.actions.act("mail:cv1", "category", "fyi")
    g.threads["cv1"].append(mail("e8", "cv1", "Review of rule 12", 0.5, ANNA, "ping about rule 12"))
    a.updates.start()
    assert "filed this conversation as fyi" in a.kit.llm.asked("triage")[-1]["user"]


def test_a_name_with_a_comma_is_one_person(tmp_path):
    a, g = make(tmp_path)
    made = []
    g.threads["cv1"][0]["from"] = addr("Kowalska, Anna", "a@example.invalid")
    a.updates.start()
    g.reply_draft = lambda m, c, everyone=False: made.append(everyone) or {}
    a.drafts.to_outlook(a.drafts.create("mail:cv1")["id"])
    assert made == [False] and a.people.person("Kowalska, Anna")["last"]


# ---------------------------------------------------------------- files
def test_a_file_in_another_encoding_does_not_break_the_window(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    d = a.memory.root / "matters"
    d.mkdir(exist_ok=True)
    (d / "ansi.md").write_bytes("# Zażółć".encode("cp1250"))
    (d / "bom.md").write_text('---\ntitle: "With BOM"\n---\n', encoding="utf-8-sig")
    titles = [m["title"] for m in a.view()["matters"]]
    assert "ansi" in titles and "With BOM" in titles


def test_a_file_made_by_hand_is_a_matter_like_any_other(tmp_path):
    a, g = make(tmp_path, Model(brief={"lines": ["x"]}))
    a.updates.start()
    d = a.memory.root / "matters"
    d.mkdir(exist_ok=True)
    (d / "Budget Q1.md").write_text("# Budget", encoding="utf-8")
    assert a.briefs.brief("week")["lines"] == ["x"]
    a.actions.act("mail:cv1", "matter", "Budget Q1")
    assert "rule 12" in a.memory.read("matters/Budget Q1.md")
    with pytest.raises(KeyError):
        a.memory.matter("../style")


def test_a_locked_file_is_never_taken_for_an_empty_one(tmp_path, monkeypatch):
    from powertools import files

    monkeypatch.setattr(files.time, "sleep", lambda s: None)
    a, g = make(tmp_path)
    a.people.note("Ola", "first note")
    f, real = a.memory.root / "people" / "ola.md", Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: fail(PermissionError("locked"))() if self.name == "ola.md" else real(self))
    with pytest.raises(files.Busy):
        a.people.note("Ola", "second note")
    a.view()
    monkeypatch.undo()
    assert "first note" in f.read_text(encoding="utf-8")


def test_a_damaged_state_file_is_kept_aside(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    (tmp_path / "assistant.json").write_text("{broken")
    b, _ = make(tmp_path)
    b.state.save()
    assert [p.read_text() for p in tmp_path.glob("assistant.bad-*.json")] == ["{broken"]


# ---------------------------------------------------------------- privacy and cost
def test_an_excluded_meeting_never_reaches_the_model(tmp_path):
    from powertools.config import Settings
    from powertools.meetings.manager import MeetingManager, Session

    s = Settings(tmp_path / "s.json")
    s.update({"exclude": ["Hanna HR"]})
    llm = Model()
    m = MeetingManager(s, None, llm=llm, root=tmp_path / "rec")
    folder = tmp_path / "rec" / "x"
    folder.mkdir(parents=True)
    session = Session(folder, {"title": "Talk", "attendees": ["Hanna HR"], "started": 0})
    with pytest.raises(ValueError, match="never read"):
        m.summarize(session)
    assert llm.calls == []


def test_unknown_credits_are_estimated_so_the_limit_still_works(tmp_path):
    u = Usage(tmp_path / "u.json")
    u.add("x", {"credits": 2.0, "prompt_tokens": 9000, "tokens": 1000})
    u.add("x", {"credits": None, "prompt_tokens": 4000, "tokens": 1000})
    assert u.today()["credits"] == pytest.approx(3.0) and u.today()["estimated"] == 1
    v = Usage(tmp_path / "v.json")
    v.add("x", {"credits": None})
    assert v.today()["credits"] == 1.0


def test_a_dead_copilot_runtime_is_started_again(tmp_path, monkeypatch):
    from powertools.config import Settings
    from powertools.copilot import Copilot

    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path))
    c = Copilot(Settings(tmp_path / "s.json"), home=tmp_path / "c")
    tries, dropped = [], []

    async def ask(*a):
        tries.append(1)
        if len(tries) == 1:
            raise BrokenPipeError("the runtime is gone")
        return "ok", 1, 1, 0.1

    async def stop():
        dropped.append(1)
    c._ask, c._stop_client, c._client = ask, stop, object()
    assert c.chat("s", "u")[0] == "ok" and len(tries) == 2 and dropped


def test_an_auth_refusal_is_not_tried_twice(tmp_path, monkeypatch):
    from powertools.config import Settings
    from powertools.copilot import Copilot

    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path))
    c, tries = Copilot(Settings(tmp_path / "s.json"), home=tmp_path / "c"), []

    async def ask(*a):
        tries.append(1)
        raise RuntimeError("Bad credentials")
    c._ask = ask
    with pytest.raises(LLMError, match="refused the token"):
        c.chat("s", "u")
    assert len(tries) == 1


