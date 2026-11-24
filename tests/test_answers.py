"""Did I answer in the meantime? Open questions close by themselves; and Stop, and what is left of the AI credits."""

import threading
import time
from datetime import datetime, timezone

import pytest

from powertools.brain import Brain, Usage
from powertools.config import Settings
from powertools.copilot import quota_view
from powertools.graph.client import TokenExpired
from powertools.jobs import Jobs, Stopped
from powertools.llm import LLMError

from .test_assistant import ME_ADDR, NOTHING, Model, iso, mail, make
from .test_assistant_failures import said_in_chat

ME = {"id": "u-me", "displayName": "Piotr B"}


def test_an_answer_written_in_teams_closes_the_question_without_an_update(tmp_path):
    a, g = make(tmp_path, Model(triage=lambda u: {**NOTHING, "category": "fyi", "summary": "Settled."} if "Thanks" in u else Model().triage(u)))
    a.updates.start()
    cursor, asked = a.state.data["cursor"], len(a.kit.llm.asked("triage"))
    g.chat_msgs = [*g.chat_msgs, said_in_chat("t2", 0.2, "Sure, at 3 pm.", me=True), said_in_chat("t3", 0.1, "Thanks!")]
    a.answers.check()
    i = a.state.data["items"]["teams:c1"]
    assert i["status"] == "done" and i["done_by"] == "answer" and not i.get("draft")
    assert len(a.kit.llm.asked("triage")) == asked + 1 and a.state.data["cursor"] == cursor        # one conversation, no new range
    v = a.view()
    assert [x["key"] for x in v["answered"]] == ["teams:c1"] and v["checked"]
    a.actions.act("teams:c1", "reopen")
    assert "teams:c1" in [x["key"] for x in a.view()["mine"]] and not a.view()["answered"]


def test_an_answer_by_mail_closes_it_too(tmp_path):
    a, g = make(tmp_path, Model(triage=lambda u: {**NOTHING, "category": "reply", "summary": "s", "action": "Answer"}))
    a.updates.start()
    g.threads["cv1"].append(mail("e7", "cv1", "Review of rule 12", 0.2, ME_ADDR, "Done, see the file."))
    a.answers.check()
    assert a.state.data["items"]["mail:cv1"]["status"] == "done" and a.state.data["items"]["teams:c1"]["status"] == "open"


def test_nothing_is_asked_of_the_model_when_i_wrote_nothing(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    asked = len(a.kit.llm.calls)
    g.chat_msgs = [*g.chat_msgs, said_in_chat("t2", 0.1, "Hello? My question?")]
    a.answers.check()
    assert len(a.kit.llm.calls) == asked and a.state.data["items"]["teams:c1"]["status"] == "open"


def test_a_task_i_wrote_about_stays_when_the_model_says_it_is_not_done(tmp_path):
    a, g = make(tmp_path, Model(triage=lambda u: {**NOTHING, "category": "todo", "summary": "s", "action": "Send the file"}))
    a.updates.start()
    g.chat_msgs = [*g.chat_msgs, said_in_chat("t2", 0.2, "I will send it tomorrow.", me=True)]
    a.answers.check()
    i = a.state.data["items"]["teams:c1"]
    assert i["status"] == "open" and i["last_from_me"]                                   # shown as "you wrote"


def test_a_question_i_answered_closes_even_when_the_model_is_away(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    g.chat_msgs = [*g.chat_msgs, said_in_chat("t2", 0.2, "Sure.", me=True)]
    a.kit.llm.error = "no model"
    a.answers.check()
    assert a.state.data["items"]["teams:c1"]["status"] == "done"


def test_a_reaction_is_seen_and_shown_to_the_model(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    g.chat_msgs[0]["reactions"] = [{"reactionType": "👍", "createdDateTime": iso(0.1), "user": {"user": {"id": "u-me"}}}]
    a.answers.check()
    assert "I reacted to the latest message with 👍" in a.kit.llm.asked("triage")[-1]["user"]


def test_the_check_needs_a_token_and_never_runs_next_to_an_update(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    g.fail = {"/chats": TokenExpired("x")}
    a.answers.check()
    assert a.updates.job.state == "done"                                                          # the Update is not touched
    a.updates.job.state = "running"
    with pytest.raises(ValueError, match="running"):
        a.answers.check()


def test_only_open_questions_of_mine_are_looked_at(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    for i in a.state.data["items"].values():
        i["status"] = "done"
    g.calls = []
    assert a.answers.check() is None and g.calls == []


# ---------------------------------------------------------------- Stop
def test_a_stopped_update_keeps_what_it_has_and_the_range(tmp_path):
    a, g = make(tmp_path)
    real = a.sorting.classify

    def classify(i, *x, **k):
        a.stop()
        return real(i, *x, **k)
    a.sorting.classify = classify
    with pytest.raises(Stopped):
        a.updates.start()
    assert a.state.data["cursor"] == "" and sum("summary" in i for i in a.state.data["items"].values()) >= 1
    a.sorting.classify, a.updates.job = real, None
    a.updates.start()
    assert a.state.data["cursor"] and all(not i.get("pending") for i in a.state.data["items"].values())


def test_jobs_can_be_stopped_while_waiting(tmp_path):
    jobs, gate, ran = Jobs(), threading.Event(), []
    jobs.submit("first", lambda j: gate.wait(5))
    second = jobs.submit("second", lambda j: ran.append(1))
    jobs.stop(second)
    gate.set()
    for _ in range(50):
        if second.state == "stopped" and not jobs.busy():
            break
        time.sleep(0.05)
    assert second.state == "stopped" and not ran


def test_a_stopped_question_gives_no_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path))

    class Local:
        def chat(self, *a, **k):
            brain.abort()
            return "late answer", {}
    s = Settings(tmp_path / "s.json")
    brain = Brain(s, local=Local(), usage=Usage(tmp_path / "u.json"))
    with pytest.raises(LLMError, match="stopped"):
        brain.chat("s", "u")


# ---------------------------------------------------------------- what is left of the credits
def test_quota_says_what_is_left_and_until_when():
    now = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
    q = quota_view({"plan": "business", "tbb": True, "snapshots": {
        "chat": {"entitlement": -1, "used": 0, "remaining_pct": 100, "unlimited": True, "reset_ms": None, "tbb": False},
        "premium_interactions": {"entitlement": 4000, "used": 1435.4, "remaining_pct": 64.1, "unlimited": False,
                                 "reset_ms": datetime(2026, 12, 1, tzinfo=timezone.utc).timestamp() * 1000, "tbb": True}}}, now)
    assert q["unit"] == "AI credits" and q["left"] == 2564.6 and q["entitlement"] == 4000 and round(q["used_pct"]) == 36
    assert q["days_left"] == 6 and q["work_days_left"] == 4 and q["reset"].startswith("2026-12-01")


def test_quota_without_figures_still_knows_the_cycle():
    q = quota_view({"plan": None, "tbb": False, "snapshots": {}}, datetime(2026, 12, 30, tzinfo=timezone.utc))
    assert q["left"] is None and q["reset"].startswith("2027-01-01") and q["days_left"] == 2


def test_credits_used_since_the_reading_are_taken_off(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path))
    s = Settings(tmp_path / "s.json")
    s.update({"ai_provider": "copilot"})
    b = Brain(s, usage=Usage(tmp_path / "u.json"))
    b.cloud.days_left = lambda: 5
    b.cloud.quota = lambda refresh=False: {"left": 100.0, "entitlement": 200.0, "used_pct": 50.0, "unit": "AI credits", "spent_then": 0.0}
    b.usage.add("ask", {"credits": 10.0})
    q = b.available()["quota"]
    assert q["left"] == 90.0 and q["used_pct"] == 55.0
