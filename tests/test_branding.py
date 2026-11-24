"""The look of an organisation's copy: zip import, colours that stay readable, nothing from another app's name, safe serving."""

import io
import json
import re
import subprocess
import urllib.error
import urllib.request
import zipfile

import pytest

from powertools import branding, updater
from tests.test_web import call, server  # noqa: F401


@pytest.fixture()
def look(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(updater, "ROOT", tmp_path / "copy")
    return tmp_path


def zipped(**files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, body in files.items():
            z.writestr(name.replace("__", "/").replace("_DOT_", "."), body)
    return b.getvalue()


def test_another_apps_zip_gives_logo_and_background_but_not_its_name(look):
    meta = json.dumps({"title": "Other", "app_name": "Other app", "contact": "x@example.invalid"})
    s = branding.import_zip(zipped(branding__logo_DOT_png=b"P", branding__background_DOT_jpg=b"J", branding__icon_DOT_ico=b"I", branding__branding_DOT_json=meta))
    assert s["name"] == "powertools" and set(s["files"]) == {"logo", "background"}
    assert sorted(p.name for p in branding.local_dir().iterdir()) == ["background.jpg", "logo.png"]
    branding.import_zip(zipped(logo_DOT_svg="<svg/>"))
    assert branding.find("logo").name == "logo.svg" and branding.find("background")               # one file per role, the rest stays


def test_a_bad_zip_changes_nothing(look):
    branding.import_zip(zipped(logo_DOT_png=b"P"))
    for bad in (b"not a zip", zipped(readme_DOT_txt="x"), zipped(logo_DOT_png=b"NEW", branding_DOT_json="{broken")):
        with pytest.raises(ValueError):
            branding.import_zip(bad)
    assert branding.find("logo").read_bytes() == b"P"
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("../../logo.png", b"Q")
    branding.import_zip(b.getvalue())
    assert branding.find("logo") == branding.local_dir() / "logo.png" and not (look / "logo.png").exists()


def test_the_local_look_wins_over_the_organisations_and_reset_goes_back(look):
    org = updater.ROOT / "branding"
    org.mkdir(parents=True)
    (org / "branding.json").write_text(json.dumps({"powertools_name": "Org Tools", "colors": {"accent": "#AA0000", "sidebar": "#101020"}}), encoding="utf-8")
    (org / "logo.png").write_bytes(b"ORG")
    assert branding.settings() == {"name": "Org Tools", "colors": {"accent": "#aa0000", "sidebar": "#101020"}}
    branding.save({"colors": {"accent": "#00aa00"}})
    assert branding.settings()["colors"] == {"accent": "#00aa00", "sidebar": "#101020"} and branding.settings()["name"] == "Org Tools"
    assert branding.state()["local"]
    branding.reset()
    assert branding.settings()["colors"]["accent"] == "#aa0000" and (org / "logo.png").read_bytes() == b"ORG" and not branding.state()["local"]


def test_only_real_colours_get_into_the_css(look):
    for bad in ("red", "#12", "#123456; } body { display: none", "url(x)"):
        with pytest.raises(ValueError):
            branding.save({"colors": {"accent": bad}})
    org = updater.ROOT / "branding"
    org.mkdir(parents=True)
    (org / "branding.json").write_text(json.dumps({"colors": {"accent": "#111; }", "sidebar": ["x"]}, "powertools_name": {"a": 1}}), encoding="utf-8")
    assert branding.css() == "" and branding.settings()["colors"] == {}


@pytest.mark.parametrize("accent", ["#7345ff", "#ffee00", "#000000", "#ffffff", "#5b5bd6"])
@pytest.mark.parametrize("sidebar", ["#14152f", "#ffffff", "#808080", "#ffee00"])
def test_text_stays_readable_whatever_is_picked(look, accent, sidebar):
    branding.save({"colors": {"accent": accent, "sidebar": sidebar}})
    css = branding.css()
    light, dark = css.split("@media", 1)
    for part, panel in ((light, "#ffffff"), (dark, "#1c1c21")):
        v = dict(re.findall(r"--([a-z0-9-]+): (#[0-9a-f]{6})", part))
        assert branding.contrast(v["accent"], panel) >= 4.5 and branding.contrast(v["on-accent"], v["accent"]) >= 4.5
        assert branding.contrast(v["accent-text"], v["accent-soft"]) >= 4.5
        assert branding.contrast(v["side-ink"], v["side"]) >= 4 and branding.contrast(v["side-active-text"], v["side2"]) >= 4


def test_pack_makes_a_zip_that_imports_to_the_same_look(look):
    with pytest.raises(ValueError):
        branding.pack(look)
    branding.import_zip(zipped(logo_DOT_png=b"P"))
    branding.save({"powertools_name": "Org Tools", "colors": {"accent": "#7345ff"}})
    before, z = branding.settings(), branding.pack(look).read_bytes()
    branding.reset()
    branding.import_zip(z)
    assert branding.settings() == before and branding.find("logo").read_bytes() == b"P"


def test_the_window_serves_the_look_and_an_svg_cannot_run(server, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(updater, "ROOT", tmp_path / "copy")
    app, base = server

    def get(path):
        with urllib.request.urlopen(base + path, timeout=5) as r:
            return r.read(), r.headers
    assert get("/branding.css")[0] == b""
    for path in ("/branding/logo", "/branding/branding.json", "/branding/../settings.json", "/branding/"):
        with pytest.raises(urllib.error.HTTPError) as e:
            get(path)
        assert e.value.code == 404
    req = urllib.request.Request(base + "/api/branding/zip", method="POST", headers={"X-Powertools": "1", "X-Powertools-Key": app.door.key}, data=zipped(logo_DOT_svg="<svg/>"))
    urllib.request.urlopen(req, timeout=5).read()
    body, head = get("/branding/logo?1")
    assert body == b"<svg/>" and "sandbox" in head["Content-Security-Policy"] and head["Content-Type"] == "image/svg+xml"
    assert call(base, "POST", "/api/branding", {"colors": {"accent": "#7345ff"}})["colors"]["accent"] == "#7345ff"
    assert b"--accent: #" in get("/branding.css")[0]


def test_branding_is_not_in_the_public_repository():
    r = subprocess.run(["git", "check-ignore", "branding/logo.png", "branding.zip"], cwd=updater.ROOT, capture_output=True, text=True)
    assert r.returncode == 128 or len(r.stdout.split()) == 2
