"""Open file / Open folder only for paths inside the export folder."""
from pathlib import Path

from powertools.web import api_graph


class H:
    def __init__(self, settings, body):
        self.app = type("A", (), {"settings": settings})()
        self._body, self.sent = body, None

    def json_body(self):
        return self._body

    def send(self, code, data):
        self.sent = (code, data)


def test_open_path_only_inside_export_folder(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(api_graph, "_reveal", lambda p, folder: opened.append((Path(p).name, folder)))
    f = tmp_path / "jira" / "QATE-1.md"
    f.parent.mkdir()
    f.write_text("x")
    s = {"output_dir": str(tmp_path)}
    h = H(s, {"path": str(f), "folder": True})
    api_graph.open_path(h)
    assert h.sent[0] == 200 and opened == [("QATE-1.md", True)]
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("x")
    h = H(s, {"path": str(outside)})
    api_graph.open_path(h)
    assert h.sent[0] == 400 and len(opened) == 1


def test_open_output_per_tab_creates_the_subfolder(tmp_path, monkeypatch):
    monkeypatch.setattr(api_graph.sys, "platform", "linux")
    h = H({"output_dir": str(tmp_path)}, {"kind": "jira"})
    api_graph.open_output(h)
    assert h.sent[1]["path"] == str(tmp_path / "jira") and (tmp_path / "jira").is_dir()
