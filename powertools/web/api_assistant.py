"""Window API of the Recap: update, items, matters, drafts, ask, briefs, meetings ahead, people, memory files, ideas."""

from __future__ import annotations

from pathlib import Path

from ..graph.client import TokenExpired
from ..llm import LLMError
from .api_graph import guarded
from .server import route

COMPARE: dict = {"state": "", "result": {}, "error": ""}


def answer(fn):
    """A model that cannot answer now is a message for the user, not a server error."""
    def wrapper(h, *args):
        try:
            return fn(h, *args)
        except LLMError as e:
            raise ValueError(str(e)) from e
    return wrapper


def text(h, *names) -> list[str]:
    b = h.json_body()
    return [str(b.get(n, "") or "") for n in names]


# ------------------------------------------------------------------ recap
@route("GET", "/api/assistant")
def view(h):
    a = h.app.assistant
    h.send(200, {**a.view(), "llm": h.app.llm.available(), "graph": h.app.graph.status(), "draft_list": a.drafts.list(),
                 "people": a.people()})


@route("POST", "/api/assistant/update")
@guarded
def update(h):
    b = h.json_body()
    # without a token the job is made all the same: it pauses at once and goes on when the token is there
    h.app.assistant.update(b.get("range"), b.get("sources"))
    h.send(200, h.app.assistant.view())


@route("POST", "/api/assistant/check")
@guarded
def check(h):
    """Did I answer in the meantime? Needs a token now (the window then waits for one and calls again)."""
    if not h.app.graph.tokens.get():
        raise TokenExpired("no valid token")
    h.app.assistant.check_answers()
    h.send(200, h.app.assistant.view())


@route("POST", "/api/assistant/stop")
def stop(h):
    h.app.assistant.stop()
    h.send(200, h.app.assistant.view())


@route("POST", "/api/assistant/act")
def act(h):
    h.app.assistant.act(*text(h, "key", "action", "value"))
    h.send(200, h.app.assistant.view())


@route("POST", "/api/assistant/promise")
def promise(h):
    h.app.assistant.promise(*text(h, "id", "state"))
    h.send(200, h.app.assistant.view())


@route("POST", "/api/assistant/setup")
def setup(h):
    h.send(200, h.app.assistant.setup(h.json_body()))


@route("POST", "/api/assistant/brief")
@answer
def brief(h):
    b = h.json_body()
    h.send(200, h.app.assistant.brief(str(b.get("kind", "day")), int(b.get("days") or 0)))


@route("POST", "/api/assistant/ask")
@answer
def ask(h):
    h.send(200, h.app.assistant.ask(*text(h, "question")))


@route("GET", "/api/assistant/talk")
def talk(h):
    h.send(200, h.app.assistant.talk)


@route("DELETE", "/api/assistant/talk")
def talk_clear(h):
    h.app.assistant.talk = []
    h.send(200, [])


@route("POST", "/api/assistant/feedback")
def feedback(h):
    h.app.assistant.feedback(*text(h, "text"))
    h.send(200, {"saved": True})


# ------------------------------------------------------------------ matters and memory files
@route("POST", "/api/matters")
def matter_new(h):
    h.send(200, {"id": h.app.assistant.memory.create_matter(*text(h, "title"))})


@route("POST", r"/api/matters/([^/]+)/status")
def matter_status(h, mid):
    h.app.assistant.memory.set_status(mid, *text(h, "status", "reason", "return_on"))
    h.send(200, h.app.assistant.memory.matters(every=True))


@route("GET", "/api/memory")
def memory_files(h):
    m = h.app.assistant.memory
    h.send(200, {"root": str(m.root), "files": m.files()})


@route("GET", "/api/memory/file")
def memory_file(h):
    name = h.query("name")
    h.app.assistant.memory.path(name)
    h.send(200, {"name": name, "text": h.app.assistant.memory.read(name)})


@route("POST", "/api/memory/file")
def memory_save(h):
    """Saving from the window; refused when the file changed on disk since it was opened."""
    name, new, opened = text(h, "name", "text", "opened")
    m = h.app.assistant.memory
    with m.lock:
        if m.read(name) != opened.replace("\r\n", "\n"):
            raise ValueError("the file changed since you opened it: open it again")
        m.write(name, new.replace("\r\n", "\n"), "edited in the window")
    h.send(200, {"name": name})


@route("POST", "/api/memory/edit")
def memory_edit(h):
    """A change the model proposed and the user accepted."""
    file, old, new = text(h, "file", "old", "new")
    h.send(200, h.app.assistant.memory.edit([{"file": file, "old": old, "new": new}], "accepted by you"))


@route("POST", "/api/memory/undo")
def memory_undo(h):
    h.send(200, {"file": h.app.assistant.memory.undo(*text(h, "id"))})


@route("POST", "/api/memory/open")
def memory_open(h):
    from .api_graph import _reveal

    root = h.app.assistant.memory.root
    _reveal(root, False)
    h.send(200, {"path": str(root)})


# ------------------------------------------------------------------ drafts
@route("GET", "/api/drafts")
def drafts(h):
    scopes = h.app.graph.tokens.scopes()
    h.send(200, {"drafts": h.app.assistant.drafts.list(), "outlook": "Mail.ReadWrite" in scopes})


@route("POST", "/api/drafts")
@answer
def draft_new(h):
    key, kind, note, title, body = text(h, "key", "kind", "note", "title", "body")
    d = h.app.assistant.drafts
    h.send(200, d.create(key, kind, note) if key else d.new(title, body))


@route("POST", r"/api/drafts/([0-9a-z-]+)")
def draft_save(h, did):
    h.send(200, h.app.assistant.drafts.save(did, *text(h, "body")))


@route("POST", r"/api/drafts/([0-9a-z-]+)/revise")
@answer
def draft_revise(h, did):
    h.send(200, h.app.assistant.drafts.revise(did, *text(h, "remark", "body")))


@route("POST", r"/api/drafts/([0-9a-z-]+)/close")
def draft_close(h, did):
    h.app.assistant.drafts.close(did, *text(h, "status"))
    h.send(200, {"closed": did})


@route("POST", r"/api/drafts/([0-9a-z-]+)/outlook")
@guarded
def draft_outlook(h, did):
    from ..graph.client import GraphError

    try:
        h.send(200, h.app.assistant.drafts.to_outlook(did))
    except GraphError as e:
        raise ValueError("Graph refused to write the draft: in Graph Explorer consent to Mail.ReadWrite (Modify "
                         f"permissions), then get a new token ({e.code})" if e.status == 403 else str(e)) from e


@route("POST", "/api/style")
def style_rule(h):
    h.app.assistant.drafts.add_rule(*text(h, "rule"))
    h.send(200, {"saved": True})


# ------------------------------------------------------------------ meetings ahead, people
@route("POST", "/api/assistant/prep")
@answer
def prep(h):
    b = h.json_body()
    h.send(200, h.app.assistant.prep(str(b.get("id", "")), bool(b.get("again")), str(b.get("person", ""))))


@route("GET", "/api/people")
def people(h):
    h.send(200, h.app.assistant.people())


@route("GET", "/api/people/card")
def person(h):
    h.send(200, h.app.assistant.person(h.query("name")))


@route("POST", "/api/people")
def person_add(h):
    h.app.assistant.add_person(*text(h, "name", "title", "mail"))
    h.send(200, h.app.assistant.people())


@route("POST", "/api/people/remove")
def person_remove(h):
    h.app.assistant.remove_person(*text(h, "name"))
    h.send(200, h.app.assistant.people())


@route("POST", "/api/people/note")
def person_note(h):
    name, note = text(h, "name", "note")
    h.app.assistant.note_person(name, note)
    h.send(200, h.app.assistant.person(name))


# ------------------------------------------------------------------ ideas, comparing models
@route("GET", "/api/feedback")
def feedback_info(h):
    from .. import diagnostics, feedback

    h.send(200, {"issues": feedback.issues(), "contact": feedback.contact(), "report": diagnostics.as_text(diagnostics.report(h.app))})


@route("POST", "/api/feedback/package")
def feedback_package(h):
    from .. import feedback

    b = h.json_body()
    p = feedback.package(h.app, str(b.get("text", "")), bool(b.get("diagnostics")), [str(x) for x in b.get("pictures") or []])
    h.send(200, {"path": str(p), "name": p.name, "contact": feedback.contact()})


@route("POST", "/api/feedback/show")
def feedback_show(h):
    from .. import feedback
    from .api_graph import _reveal

    p = feedback.desktop() / Path(*text(h, "name")).name
    if not (p.name.startswith("powertools-feedback-") and p.is_file()):
        raise KeyError(p.name)
    _reveal(p, True)
    h.send(200, {"path": str(p)})


@route("POST", "/api/idea")
def idea(h):
    from ..feedback import issue_url

    b = h.json_body()
    h.send(200, {"url": issue_url(h.app, str(b.get("text", "")), bool(b.get("diagnostics")))})


@route("GET", "/api/ai/compare")
def compare_state(h):
    h.send(200, COMPARE)


@route("POST", "/api/ai/compare")
def compare(h):
    b = h.json_body()
    provider = str(b.get("provider", ""))
    if provider not in ("local", "copilot"):
        raise ValueError("provider must be local or copilot")
    if COMPARE["state"] == "running":
        raise ValueError("a comparison is already running")
    limit = int(b.get("limit") or 30)
    COMPARE.update(state="running", result={}, error="")

    def run(job) -> None:
        try:
            COMPARE.update(result=h.app.assistant.compare(provider, limit), state="done")
        except Exception as e:
            COMPARE.update(state="error", error=str(e)[:300])
    h.app.graph.jobs.submit("Compare models", run, "ai")
    h.send(200, COMPARE)
