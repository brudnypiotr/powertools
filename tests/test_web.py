"""The window API against a real local server with the app's settings/history in a temp folder."""

import json
import urllib.error
import urllib.request

import pytest


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path))
    from powertools.app import App
    from powertools.web.server import serve

    app = App(selftest=True)
    app.input_devices = lambda: ["Mic A"]
    httpd = serve(app)
    yield app, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    app.store.close()


def call(base, method, path, body=None, header=True):
    headers = {"Content-Type": "application/json", **({"X-Powertools": "1"} if header else {})}
    req = urllib.request.Request(base + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def test_state_settings_history(server):
    app, base = server
    assert call(base, "GET", "/api/state")["dictation"] == "idle"
    r = call(base, "POST", "/api/settings", {"language": "pl", "beam_size": 3})
    assert sorted(r["changed"]) == ["beam_size", "language"]
    app.store.add("raw", "hello", "en", 1, 0.1)
    assert call(base, "GET", "/api/history?q=hel")[0]["text"] == "hello"
    assert call(base, "GET", "/api/devices") == ["Mic A"]


def test_correction_adds_rule_and_word(server):
    app, base = server
    r = call(base, "POST", "/api/correction", {"wrong": "merlo", "right": "Merlot"})
    assert {"from": "merlo", "to": "Merlot"} in r["replacements"] and "Merlot" in r["vocabulary"]


def test_bad_setting_is_400(server):
    _, base = server
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "POST", "/api/settings", {"model": "huge"})
    assert e.value.code == 400


def test_post_without_header_is_refused(server):
    _, base = server
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "POST", "/api/toggle", {}, header=False)
    assert e.value.code == 403


def test_page_is_served(server):
    _, base = server
    html = urllib.request.urlopen(base + "/", timeout=5).read().decode()
    assert "<title>powertools</title>" in html


def test_about_and_busy_update(server):
    app, base = server
    a = call(base, "GET", "/api/about")
    assert a["version"] and "changelog" in a or "error" in a
    app.dictation.state = "recording"
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "POST", "/api/update", {})
    assert e.value.code == 400
    app.dictation.state = "idle"


def test_icon_is_served(server):
    _, base = server
    r = urllib.request.urlopen(base + "/icon.png", timeout=5)
    assert r.headers["Content-Type"] == "image/png" and r.read()[:4] == b"\x89PNG"
