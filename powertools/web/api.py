"""JSON API of the window. `h.app` is the running App (see app.py)."""

from __future__ import annotations

from .server import route


@route("GET", "/api/ping")
def ping(h):
    h.send(200, {"app": "powertools"})


@route("GET", "/api/state")
def state(h):
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


@route("POST", "/api/quit")
def quit_app(h):
    h.send(200, {"quitting": True})
    h.app.quit()
