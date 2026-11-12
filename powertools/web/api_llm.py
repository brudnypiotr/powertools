"""Window API for the local model (Ollama): status and test, model download, wording and recap."""

from __future__ import annotations

import threading

from ..llm import LLMError
from .api_graph import guarded
from .server import route

INSTALL: dict = {"state": "", "status": "", "completed": 0, "total": 0, "error": ""}
PULL: dict = {"model": "", "state": "", "status": "", "completed": 0, "total": 0, "error": ""}


@route("GET", "/api/llm/status")
def llm_status(h):
    h.send(200, {**h.app.llm.status(), "install": dict(INSTALL), "pull": dict(PULL), "engine": h.app.engine.status()})


@route("POST", "/api/llm/install")
def llm_install(h):
    if INSTALL["state"] == "running":
        raise ValueError("already setting up Ollama")
    INSTALL.update(state="running", status="downloading", completed=0, total=0, error="")

    def progress(status: str, completed: int, total: int) -> None:
        INSTALL.update(status=status, completed=completed or INSTALL["completed"], total=total or INSTALL["total"])

    def run() -> None:
        try:
            h.app.llm.install(progress)
            INSTALL.update(state="done", status="installed")
        except LLMError as e:
            INSTALL.update(state="error", error=str(e))

    threading.Thread(target=run, name="ollama-install", daemon=True).start()
    h.send(200, dict(INSTALL))


@route("POST", "/api/llm/start")
def llm_start(h):
    if not h.app.llm.ensure_running():
        raise ValueError("Ollama is not installed: set it up in the Local AI tab, then try again")
    h.send(200, h.app.llm.status())


@route("POST", "/api/llm/test")
def llm_test(h):
    model = str(h.json_body().get("model", "")).strip()
    try:
        h.send(200, h.app.llm.test(model))
    except LLMError as e:
        raise ValueError(str(e)) from e


@route("POST", "/api/llm/pull")
def llm_pull(h):
    model = str(h.json_body().get("model", "")).strip()
    if not model:
        raise ValueError("which model?")
    if PULL["state"] == "running":
        raise ValueError(f"already downloading {PULL['model']}")
    PULL.update(model=model, state="running", status="starting", completed=0, total=0, error="")

    def progress(status: str, completed: int, total: int) -> None:
        PULL.update(status=status, completed=completed or PULL["completed"], total=total or PULL["total"])

    def run() -> None:
        try:
            h.app.llm.pull(model, progress)
            PULL.update(state="done", status="downloaded")
        except LLMError as e:
            PULL.update(state="error", error=str(e))

    threading.Thread(target=run, name="ollama-pull", daemon=True).start()
    h.send(200, dict(PULL))


@route("POST", "/api/llm/delete")
def llm_delete(h):
    model = str(h.json_body().get("model", "")).strip()
    if not model:
        raise ValueError("which model?")
    if PULL["state"] == "running" and PULL["model"] == model:
        raise ValueError(f"{model} is downloading now")
    try:
        h.app.llm.delete(model)
    except LLMError as e:
        raise ValueError(str(e)) from e
    if PULL["model"] == model:
        PULL.update(model="", state="", status="", error="")
    h.send(200, h.app.llm.status())


@route("GET", "/api/wording")
def wording_last(h):
    h.send(200, h.app.wording.last)


@route("POST", "/api/wording")
def wording_check(h):
    b = h.json_body()
    h.send(200, h.app.wording.check(str(b.get("text", "")), str(b.get("style", ""))))


@route("GET", "/api/recap")
def recap_view(h):
    h.send(200, h.app.recap.view())


@route("POST", "/api/recap")
@guarded
def recap_start(h):
    b = h.json_body()
    if not h.app.graph.status()["has_token"]:
        return h.send(401, {"error": "token", "message": "Paste a fresh token from Graph Explorer."})
    h.app.recap.start(dict(b.get("range", {})), list(b.get("sources", [])))
    h.send(200, h.app.recap.view())


@route("POST", "/api/recap/correct")
def recap_correct(h):
    b = h.json_body()
    h.send(200, h.app.recap.correct(str(b.get("key", "")), str(b.get("category", ""))))


@route("DELETE", "/api/recap")
def recap_clear(h):
    h.send(200, h.app.recap.clear())
