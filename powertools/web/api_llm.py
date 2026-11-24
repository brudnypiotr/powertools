"""Window API for the language model: Ollama (status, test, download), GitHub Copilot (sign-in, usage), wording."""

from __future__ import annotations

import threading

from ..llm import LLMError
from .server import route

INSTALL: dict = {"state": "", "status": "", "completed": 0, "total": 0, "error": ""}
PULL: dict = {"model": "", "state": "", "status": "", "completed": 0, "total": 0, "error": ""}


@route("GET", "/api/llm/status")
def llm_status(h):
    h.send(200, {**h.app.llm.local.status(), "install": dict(INSTALL), "pull": dict(PULL), "engine": h.app.engine.status()})


@route("POST", "/api/llm/install")
def llm_install(h):
    if INSTALL["state"] == "running":
        raise ValueError("already setting up Ollama")
    INSTALL.update(state="running", status="downloading", completed=0, total=0, error="")

    def progress(status: str, completed: int, total: int) -> None:
        INSTALL.update(status=status, completed=completed or INSTALL["completed"], total=total or INSTALL["total"])

    def run() -> None:
        try:
            h.app.llm.local.install(progress)
            INSTALL.update(state="done", status="installed")
        except LLMError as e:
            INSTALL.update(state="error", error=str(e))

    threading.Thread(target=run, name="ollama-install", daemon=True).start()
    h.send(200, dict(INSTALL))


@route("POST", "/api/llm/start")
def llm_start(h):
    if not h.app.llm.local.ensure_running():
        raise ValueError("Ollama is not installed: set it up in the Local AI tab, then try again")
    h.send(200, h.app.llm.local.status())


@route("POST", "/api/llm/test")
def llm_test(h):
    model = str(h.json_body().get("model", "")).strip()
    try:
        h.send(200, h.app.llm.local.test(model))
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
            h.app.llm.local.pull(model, progress)
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
        h.app.llm.local.delete(model)
    except LLMError as e:
        raise ValueError(str(e)) from e
    if PULL["model"] == model:
        PULL.update(model="", state="", status="", error="")
    h.send(200, h.app.llm.local.status())


# ------------------------------------------------------------------ GitHub Copilot
@route("GET", "/api/ai/usage")
def ai_usage(h):
    u, s = h.app.llm.usage, h.app.settings
    h.send(200, {"today": u.today(), "month": u.month(), "limit": s["copilot_daily_credits"],
                 "provider": s["ai_provider"], "model": h.app.llm.model})


@route("GET", "/api/ai/copilot")
def ai_copilot(h):
    h.send(200, h.app.llm.cloud.status())


@route("POST", "/api/ai/copilot/token")
def ai_copilot_token(h):
    h.send(200, h.app.llm.cloud.set_token(str(h.json_body().get("token", ""))))


@route("DELETE", "/api/ai/copilot/token")
def ai_copilot_token_clear(h):
    h.app.llm.cloud.secret.clear()
    h.send(200, {"token": False})


@route("POST", "/api/ai/copilot/test")
def ai_copilot_test(h):
    """The wording task once on Copilot: does it answer, how fast, how many credits."""
    from .. import wording
    from ..llm import TEST_TEXT, parse_json

    try:
        content, stats = h.app.llm.chat(wording.SYSTEM, TEST_TEXT, wording.SCHEMA, max_tokens=400, task="test",
                                        provider="copilot")
        answer = parse_json(content)
    except LLMError as e:
        raise ValueError(str(e)) from e
    h.send(200, {**stats, "improved": answer.get("improved", ""), "issues": len(answer.get("issues") or [])})


@route("GET", "/api/wording")
def wording_last(h):
    h.send(200, h.app.wording.last)


@route("POST", "/api/wording")
def wording_check(h):
    b = h.json_body()
    h.send(200, h.app.wording.check(str(b.get("text", "")), str(b.get("style", ""))))
