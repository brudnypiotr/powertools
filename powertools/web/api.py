"""JSON API of the window. `h.app` is the running App (see app.py)."""

from __future__ import annotations

from .server import route


@route("GET", "/api/ping")
def ping(h):
    h.send(200, {"app": "powertools"})


@route("POST", "/api/unlock")
def unlock(h):
    """A window without the key asks for one: the app opens a window of its own, with the key in its link."""
    h.app.open_window(fresh=True)
    h.send(200, {"opened": True})


@route("GET", "/api/state")
def state(h):
    import time

    h.app.last_poll = time.time()
    h.send(200, h.app.state())


@route("POST", "/api/toggle")
def toggle(h):
    h.send(200, {"dictation": h.app.dictation.toggle()})


@route("GET", "/api/history")
def history(h):
    h.send(200, h.app.store.recent(int(h.query("limit", "50")), h.query("q")))


@route("DELETE", r"/api/history/(\d+)")
def history_delete(h, item_id):
    if not h.app.store.delete(int(item_id)):
        raise KeyError(item_id)
    h.send(200, {"deleted": int(item_id)})


@route("GET", "/api/settings")
def settings_get(h):
    h.send(200, h.app.settings.all())


@route("POST", "/api/settings")
def settings_post(h):
    changed = h.app.apply_settings(h.json_body())
    h.send(200, {"changed": sorted(changed), "settings": h.app.settings.all()})


@route("POST", "/api/correction")
def correction(h):
    """'Add a correction' from the history: a replacement rule wrong -> right (and the right word to the vocabulary)."""
    body = h.json_body()
    wrong, right = str(body.get("wrong", "")).strip(), str(body.get("right", "")).strip()
    if not wrong or not right:
        raise ValueError("both 'wrong' and 'right' are needed")
    s = h.app.settings
    rules = [r for r in s["replacements"] if r["from"].lower() != wrong.lower()] + [{"from": wrong, "to": right}]
    changes = {"replacements": rules}
    if body.get("add_to_vocabulary", True) and right not in s["vocabulary"]:
        changes["vocabulary"] = s["vocabulary"] + [right]
    h.app.apply_settings(changes)
    h.send(200, {"replacements": s["replacements"], "vocabulary": s["vocabulary"]})


@route("GET", "/api/devices")
def devices(h):
    h.send(200, h.app.input_devices())


@route("GET", "/api/log")
def log_tail(h):
    h.send(200, {"lines": h.app.log_tail(int(h.query("lines", "200")))})


@route("GET", "/api/about")
def about(h):
    from .. import updater

    info = updater.about(fetch=h.query("fetch") == "1")
    info["busy"] = h.app.busy()
    info["engine"] = h.app.engine.status()
    info["installed"] = app_installed()
    info["previous"] = updater.previous()
    h.send(200, info)


def app_installed() -> bool:
    """Installed as an Edge app? An Edge --app window also reports display-mode standalone, so the page cannot tell."""
    import sys

    if sys.platform != "win32":
        return False
    from ..app import PORT, installed_profile, web_app_id

    return bool(installed_profile(web_app_id(f"http://127.0.0.1:{PORT}/")))


@route("POST", "/api/update/source")
def update_source(h):
    """Follow another repository (the organisation's own copy) from now on."""
    from .. import updater

    remote = str(h.json_body().get("remote", "")).strip()
    if not remote.startswith("https://") or any(c.isspace() for c in remote):
        raise ValueError("the repository address must start with https://")
    try:
        h.send(200, updater.switch(remote))
    except updater.GitError as e:
        raise ValueError(str(e)) from e


@route("POST", "/api/update")
def update(h):
    _move(h, "update")


@route("POST", "/api/update/rollback")
def rollback(h):
    """Back to the version before the last update."""
    _move(h, "rollback")


def _move(h, what: str) -> None:
    """Another version of the app: a backup first, then a restart."""
    from .. import __version__, backup, updater
    from ..config import data_dir, memory_dir

    busy = h.app.busy()
    if busy:
        raise ValueError(f"not now: {busy}. Try again when it has finished.")
    try:
        saved = str(backup.make(data_dir(), memory_dir(h.app.settings), __version__))
    except OSError as e:
        raise ValueError(f"the backup before the {what} did not work, nothing was changed: {e}") from e
    try:
        result = updater.update() if what == "update" else updater.rollback()
    except updater.GitError as e:
        raise ValueError(f"{what} failed: {e}") from e
    if result["changed"]:
        updater.restart_later(data_dir() / "port.txt")
    h.send(200, {**result, "backup": saved, "restarting": result["changed"]})
    if result["changed"]:
        h.app.quit()


@route("POST", "/api/quit")
def quit_app(h):
    h.send(200, {"quitting": True})
    h.app.quit()
