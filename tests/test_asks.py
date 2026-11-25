"""Several matters in one conversation, other people's answers, Jira between updates, what is new, the start of the day."""

import time

import pytest

from powertools.assistant import collect
from powertools.exclusions import Exclusions

from .test_assistant import ANNA, NOTHING, OLA, Model, iso, mail, make
from .test_assistant_failures import WIDE, said_in_chat
from .test_second_audit import group

Q = {"a": "Is the window on 12 December fine?", "b": "Who confirms it with the airline?", "c": "Can you send the agenda?"}
TEXT = " ".join(Q.values())


def ask(kind, action, quote, same="", person="Jan", decision=False):
    return {"same": same, "kind": kind, "action": action, "due": "", "person": person, "quote": quote, "decision": decision}


THREE = [ask("reply", "Confirm the window", Q["a"]), ask("reply", "Say who confirms", Q["b"]), ask("todo", "Send the agenda", Q["c"])]


def three(tmp_path, then=None):
    """A group chat with three questions; `then(user)` gives the asks of every later sorting."""
    n = {"k": 0}

    def triage(u):
        if "12 December" not in u:
            return Model().triage(u)
        n["k"] += 1
        return {"summary": "Jan plans the migration.", "category": "fyi", "commitments": [], "asks": THREE if n["k"] == 1 or not then else then(u)}
    a, g = make(tmp_path, Model(triage=triage), auto_drafts=False)
    group(g, "Jan", "Ola", "Piotr B")
    g.chat_msgs = [said_in_chat("t1", 2, TEXT)]
    a.updates.start()
    return a, g


def rows(a, which="mine"):
    return {i["action"]: i for i in a.view()[which] if i["source"] == "teams"}


def test_three_questions_are_three_items_of_one_conversation(tmp_path):
    a, g = three(tmp_path)
    r = rows(a)
    assert set(r) == {"Confirm the window", "Say who confirms", "Send the agenda"}
    assert len({i["key"] for i in r.values()}) == 3 and {i["quote"] for i in r.values()} == set(Q.values())
    assert sum(bool(i.get("parent")) for i in r.values()) == 2
    a.actions.act(r["Say who confirms"]["key"], "done")
    a.actions.act(r["Send the agenda"]["key"], "snooze", "2099-01-01")
    assert set(rows(a)) == {"Confirm the window"}
    a.actions.act(r["Say who confirms"]["key"], "reopen")
    assert len(rows(a)) == 2


def test_answering_one_closes_only_that_one(tmp_path):
    a, g = three(tmp_path, lambda u: [ask("reply", "Name the person who confirms", Q["b"], same="2"), ask("todo", "Send the agenda", Q["c"], same="3")])
    keys = {k: i["key"] for k, i in rows(a).items()}
    a.actions.act(keys["Send the agenda"], "matter", a.memory.create_matter("Migration"))
    g.chat_msgs = [*g.chat_msgs, said_in_chat("t2", 0.2, "The window is fine.", me=True)]
    a.answers.check()
    r = rows(a)
    assert "Confirm the window" not in r and [i["key"] for i in a.view()["answered"]] == [keys["Confirm the window"]]
    assert r["Name the person who confirms"]["key"] == keys["Say who confirms"]          # the same item, in new words
    assert r["Send the agenda"]["key"] == keys["Send the agenda"] and r["Send the agenda"]["matter"] == "migration"
    asked = a.kit.llm.asked("triage")[-1]["user"]
    assert "[1] Confirm the window" in asked and "[3] Send the agenda" in asked


def test_what_i_marked_done_does_not_come_back_with_the_same_sentence(tmp_path):
    a, g = three(tmp_path, lambda u: THREE)
    a.actions.act(rows(a)["Send the agenda"]["key"], "done")
    g.chat_msgs = [*g.chat_msgs, said_in_chat("t2", 0.2, "Any news?")]
    a.updates.start(WIDE)
    assert set(rows(a)) == {"Confirm the window", "Say who confirms"} and len([i for i in a.state.all() if i["source"] == "teams"]) == 3


def test_a_new_question_is_a_new_item(tmp_path):
    new = "And do we need a second window?"
    a, g = three(tmp_path, lambda u: [*THREE, ask("reply", "Say if a second window is needed", new)])
    g.chat_msgs = [*g.chat_msgs, said_in_chat("t2", 0.2, new)]
    a.updates.start(WIDE)
    assert len(rows(a)) == 4 and rows(a)["Say if a second window is needed"]["unread"]


def test_two_questions_of_one_person_are_not_both_closed_by_one_answer(tmp_path):
    two = THREE[:2]
    answers = iter([two, two[1:]])
    a, g = make(tmp_path, Model(triage=lambda u: {"summary": "s", "category": "fyi", "commitments": [], "asks": next(answers)} if "12 December" in u
                                else Model().triage(u)), auto_drafts=False)
    g.chat_msgs = [said_in_chat("t1", 2, TEXT)]
    a.updates.start()
    g.chat_msgs = [*g.chat_msgs, said_in_chat("t2", 0.2, "The window is fine.", me=True)]
    a.answers.check()
    assert set(rows(a)) == {"Say who confirms"}


def test_muting_one_mutes_the_conversation_and_no_asks_are_made_up(tmp_path):
    a, g = three(tmp_path)
    a.actions.act(rows(a)["Say who confirms"]["key"], "mute")
    assert rows(a) == {}
    g.chat_msgs = [*g.chat_msgs, said_in_chat("t2", 0.2, "12 December?")]
    asked = len(a.kit.llm.asked("triage"))
    a.updates.start(WIDE)
    assert rows(a) == {} and len(a.kit.llm.asked("triage")) == asked


def test_odd_asks_are_left_out(tmp_path):
    odd = ["text", {"kind": "todo"}, ask("dance", "x", ""), ask("todo", "Review rule 12", "not in the text"), *[ask("todo", f"Thing {n}", "") for n in range(9)]]
    a, g = make(tmp_path, Model(triage=lambda u: {"summary": "s", "category": "fyi", "commitments": [], "asks": odd}))
    a.updates.start()
    mine = [i for i in a.view()["mine"] if i["source"] == "mail" and i["title"] == "Review of rule 12"]
    assert len(mine) == 4 and mine[0]["quote"] == ""


def test_a_conversation_is_one_source_for_ask_and_goes_away_whole(tmp_path):
    a, g = three(tmp_path)
    assert sum("Teams" in s["title"] for s in a.asking._sources("window agenda airline")) == 1
    a.kit.settings.update({"exclude": ["Jan"]})
    a.updates.start(WIDE)
    assert not [i for i in a.state.all() if i["source"] == "teams"]


# ---------------------------------------------------------------- other people's answers
def test_what_i_wait_for_closes_when_they_answer(tmp_path):
    a, g = make(tmp_path, Model(triage=lambda u: {**NOTHING, "category": "fyi", "summary": "Ola sent it."} if "here it is" in u else Model().triage(u)))
    a.updates.start(WIDE)
    assert [i["title"] for i in a.view()["waiting"]] == ["Contract"]
    g.threads["cv3"].append(mail("e9", "cv3", "Contract", 0.2, OLA, "here it is, signed"))
    a.answers.check()
    v = a.view()
    assert not v["waiting"] and [(i["title"], i["done_by"]) for i in v["answered"]] == [("Contract", "them")]


def test_a_question_of_mine_is_not_read_again_because_they_wrote_once_more(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    n = len(a.kit.llm.calls)
    g.threads["cv1"].append(mail("e9", "cv1", "Review of rule 12", 0.2, ANNA, "ping"))
    a.answers.check()
    assert len(a.kit.llm.calls) == n


# ---------------------------------------------------------------- Jira
class Jira:
    base = "https://j"

    def __init__(self):
        self.status, self.comments, self.updated, self.searches = "indeterminate", [], iso(5), []

    def myself(self):
        return {"accountId": "me-1", "displayName": "Piotr B"}

    def search(self, jql, fields="", limit=0):
        self.searches.append(jql)
        return [{"key": "ABC-1", "fields": {"summary": "Fix the import", "status": {"name": "S", "statusCategory": {"key": self.status}},
                                            "assignee": {"displayName": "Piotr B"}, "reporter": {"displayName": "Ola"}, "updated": self.updated,
                                            "comment": {"comments": self.comments}}}]


class JiraService:
    def __init__(self, j):
        self.j = j

    def client(self):
        return self.j

    def creds(self):
        return type("C", (), {"base_url": "https://j", "token": "t"})()


def with_jira(tmp_path):
    a, g = make(tmp_path, Model(triage=lambda u: {**NOTHING, "category": "reply", "summary": "s", "action": "Answer Ola"} if "ABC-1" in u else Model().triage(u)))
    g.threads, a.kit.jira = {}, JiraService(Jira())
    a.updates.start()
    assert "jira:ABC-1" in [i["key"] for i in a.view()["mine"]]
    return a, g, a.kit.jira.j


def test_my_jira_comment_answers_the_question(tmp_path):
    a, g, j = with_jira(tmp_path)
    j.comments, j.updated = [{"author": {"accountId": "me-1", "displayName": "Piotr B"}, "created": iso(0.2), "body": "Done so."}], iso(0.2)
    a.answers.check()
    i = a.state.item("jira:ABC-1")
    assert i["last_from_me"] and i["status"] == "done" and i["done_by"] == "answer"


def test_a_closed_issue_closes_whatever_the_model_says_and_needs_no_graph_token(tmp_path):
    a, g, j = with_jira(tmp_path)
    j.status, j.updated = "done", iso(0.2)
    a.kit.graph.tokens.token = ""
    g.calls = []
    a.answers.check()
    assert a.state.item("jira:ABC-1")["status"] == "done" and a.state.item("jira:ABC-1")["done_by"] == "resolved" and g.calls == []


def test_an_issue_that_did_not_change_is_not_read_again(tmp_path):
    a, g, j = with_jira(tmp_path)
    n = len(a.kit.llm.calls)
    a.answers.check()
    assert len(a.kit.llm.calls) == n


def test_jira_text_without_a_comment_of_mine(tmp_path):
    j = Jira()
    j.comments = [{"author": {"displayName": "Ola"}, "created": iso(1), "body": "?"}]
    i = next(collect.jira_items(j, collect.datetime.now().astimezone() - collect.timedelta(days=1), Exclusions([]), collect.Reading()))
    assert not i["last_from_me"] and not i["resolved"] and i["key"] == "jira:ABC-1"


# ---------------------------------------------------------------- what is new
def test_the_first_update_is_not_all_new_the_next_marks_what_came(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    assert not [i for i in a.view()["mine"] if i.get("unread")]
    g.chat_msgs = [*g.chat_msgs, said_in_chat("t2", 0.2, "And one more question?")]
    a.updates.start(WIDE)
    new = [i["key"] for i in a.view()["mine"] if i.get("unread")]
    assert new == ["teams:c1"] and a.view()["unread"] == 1
    a.actions.seen(new)
    assert a.view()["unread"] == 0
    a.state.item("teams:c1")["unread"] = True
    a.actions.seen([])                                                                   # all of them
    assert a.view()["unread"] == 0


def test_my_own_message_is_not_news(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    g.chat_msgs = [*g.chat_msgs, said_in_chat("t2", 0.2, "Sure.", me=True)]
    a.updates.start(WIDE)
    assert a.view()["unread"] == 0


# ---------------------------------------------------------------- the start of the day
def test_an_update_can_write_the_brief_of_the_day(tmp_path):
    m = Model(brief={"lines": ["First the rules."]})
    a, g = make(tmp_path, m)
    a.updates.start(brief=True)
    assert a.view()["brief"]["lines"] == ["First the rules."]
    a.state.data["brief"]["at"] = time.time() - 2 * 86400
    assert a.view()["brief"] is None                                                     # yesterday's is not today's


def test_a_brief_that_fails_does_not_fail_the_update(tmp_path):
    a, g = make(tmp_path, Model(brief={"nothing": 1}))
    a.updates.start(brief=True)
    assert a.state.data["cursor"] and any("brief" in p for p in a.state.data["last"]["problems"]) and a.view()["brief"] is None


def test_the_brief_is_still_wanted_after_the_token_came(tmp_path):
    from powertools.graph.client import TokenExpired
    a, g = make(tmp_path, Model(brief={"lines": ["x"]}))
    g.fail = {"/me": TokenExpired("x")}
    job = a.updates.start(brief=True)
    g.fail = {}
    job.run(job)
    assert a.view()["brief"]["lines"] == ["x"]


@pytest.mark.parametrize("old", [{}, {"asks": "nonsense"}])
def test_items_of_an_older_version_go_on(tmp_path, old):
    a, g = make(tmp_path)
    a.updates.start()
    i = a.state.item("teams:c1")
    for k in ("wrote", "unread", "kind", "who"):
        i.pop(k, None)
    i.update(old)
    g.chat_msgs = [*g.chat_msgs, said_in_chat("t2", 0.2, "Sure.", me=True)]
    a.answers.check()
    assert a.state.item("teams:c1")["status"] == "done"
