"""What the second audit found (after the refactoring)."""

import pytest

from powertools import files
from powertools.graph.client import GraphError
from powertools.jobs import Stopped

from .test_assistant import ANNA, Model, iso, mail, make
from .test_assistant_failures import WIDE, said_in_chat


def group(g, *members):
    """The chat c1 as a group chat."""
    real = g.get

    def get(p, params=None, headers=None):
        if p == "/me/chats":
            return {"value": [{"id": "c1", "chatType": "group", "topic": "Team", "members": [{"displayName": m} for m in members],
                               "lastMessagePreview": {"createdDateTime": g.chat_msgs[-1]["createdDateTime"]}}]}
        return real(p, params, headers)
    g.get = get


def test_in_a_group_my_last_word_does_not_close_someones_question(tmp_path):
    a, g = make(tmp_path)
    group(g, "Jan", "Ola", "Piotr B")
    a.updates.start()
    g.chat_msgs = [*g.chat_msgs, said_in_chat("t2", 0.2, "By the way, lunch at noon?", me=True)]
    a.answers.check()
    assert a.state.data["items"]["teams:c1"]["status"] == "open"                # the model says it still waits for me


def test_my_answer_followed_by_a_thank_you_ends_my_draft_too(tmp_path):
    a, g = make(tmp_path, Model(triage=lambda u: Model().triage(u)))
    a.updates.start()
    did = a.state.data["items"]["teams:c1"]["draft"]
    a.drafts.save(did, "my own words")
    g.chat_msgs = [*g.chat_msgs, said_in_chat("t2", 0.2, "Sure, at 3 pm.", me=True), said_in_chat("t3", 0.1, "Thanks, and Monday?")]
    a.updates.start(WIDE)
    assert did not in [d["id"] for d in a.drafts.list()]


def test_trying_again_does_not_mark_a_draft_as_old(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    i = a.state.data["items"]["teams:c1"]
    a.drafts.save(i["draft"], "my own words")
    i["pending"] = True                                                        # the app was closed before it was sorted again
    a.updates.start()
    assert not a.drafts.get(a.state.data["items"]["teams:c1"]["draft"])["old"]


def test_my_own_message_does_not_end_later(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    a.actions.act("teams:c1", "snooze", "2099-01-01")
    g.chat_msgs = [*g.chat_msgs, said_in_chat("t2", 0.2, "I will look at it next week.", me=True)]
    a.updates.start(WIDE)
    i = a.state.data["items"]["teams:c1"]
    assert i["status"] == "snoozed" and i["until"] == "2099-01-01"


def test_a_matter_that_was_locked_gets_its_entry_next_time(tmp_path, monkeypatch):
    a, g = make(tmp_path)
    mid = a.memory.create_matter("Rule 12 review")
    a.updates.start()
    a.actions.act("mail:cv1", "matter", mid)
    g.threads["cv1"].append(mail("e8", "cv1", "Review of rule 12", 0.5, ANNA, "new facts about rule 12"))
    real = a.memory.add_entry
    monkeypatch.setattr(a.memory, "add_entry", lambda *x: (_ for _ in ()).throw(files.Busy("locked")))
    a.updates.start()
    assert any("matter" in p for p in a.state.data["last"]["problems"])
    calls = []
    monkeypatch.setattr(a.memory, "add_entry", lambda m, i: (calls.append(i["key"]), real(m, i)))
    a.updates.start()
    assert "mail:cv1" in calls


def test_stopped_while_the_matters_are_written_goes_on_next_time(tmp_path):
    a, g = make(tmp_path)
    real = a.filing.update_files
    a.filing.update_files = lambda *x: (_ for _ in ()).throw(Stopped())
    with pytest.raises(Stopped):
        a.updates.start()
    a.filing.update_files, a.updates.job = real, None
    a.updates.start()
    assert a.state.data["items"]["teams:c1"].get("draft")


def test_a_new_exclusion_also_removes_drafts_and_lines_about_it(tmp_path):
    a, g = make(tmp_path)
    mid = a.memory.create_matter("Rule 12 review")
    a.updates.start()
    a.actions.act("mail:cv1", "matter", mid)
    d = a.drafts.create("mail:cv1")
    assert "rule 12" in a.memory.read(f"matters/{mid}.md").split("## Timeline")[1]
    a.kit.settings.update({"exclude": ["Anna"]})
    a.updates.start()
    assert not (a.memory.root / "drafts" / f"{d['id']}.md").exists()
    assert "rule 12 reviewed" not in a.memory.read(f"matters/{mid}.md")
    assert not [p for p in (a.memory.root / ".history").glob("*.json") if "rule 12 reviewed" in p.read_text(encoding="utf-8")]


def test_a_new_exclusion_counts_for_the_check_of_answers_at_once(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    g.chat_msgs = [*g.chat_msgs, said_in_chat("t2", 0.2, "Sure.", me=True)]
    a.kit.settings.update({"exclude": ["Jan"]})
    asked = len(a.kit.llm.calls)
    a.answers.check()
    assert len(a.kit.llm.calls) == asked


def test_people_stay_when_they_cannot_be_read(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    a.state.data["people_at"] = 0
    g.fail = {"/me/directReports": GraphError(503, "Busy", "later")}
    a.updates.start()
    assert [p["name"] for p in a.people.all()] == ["Ola"]


def test_a_chat_with_one_person_closes_by_rule_even_when_the_model_is_wrong(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    g.chat_msgs = [*g.chat_msgs, {**said_in_chat("t2", 0.2, "Yes.", me=True), "createdDateTime": iso(0.2)}]
    a.answers.check()
    assert a.state.data["items"]["teams:c1"]["status"] == "done"
