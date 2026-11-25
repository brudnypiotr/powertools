"""What the user had copied comes back after a dictation, pictures too (the real clipboard on Windows)."""

import struct
import sys
import types

import pytest

from powertools import clipboard, paste

DIB = struct.pack("<IiiHHIIiiII", 40, 2, 2, 1, 32, 0, 16, 0, 0, 0, 0) + bytes(range(16))      # a 2x2 picture


@pytest.fixture()
def keys(monkeypatch):
    sent = []
    monkeypatch.setitem(sys.modules, "keyboard", types.SimpleNamespace(send=sent.append, is_pressed=lambda k: False))
    monkeypatch.setattr(paste.time, "sleep", lambda s: None)
    return sent


class Text:
    """pyperclip"""

    def __init__(self, text=""):
        self.text, self.log = text, []

    def paste(self):
        return self.text

    def copy(self, text):
        self.text = text
        self.log.append(text)


@pytest.mark.skipif(sys.platform == "win32", reason="text only elsewhere")
def test_text_comes_back(monkeypatch, keys):
    clip = Text("my own text")
    monkeypatch.setitem(sys.modules, "pyperclip", clip)
    paste.deliver("dictated")
    assert clip.log == ["dictated", "my own text"] and keys == ["ctrl+v"]
    paste.deliver("dictated", paste=False)
    assert clip.text == "dictated"


@pytest.mark.skipif(sys.platform == "win32", reason="text only elsewhere")
def test_a_clipboard_that_cannot_be_read_is_left_alone(monkeypatch, keys):
    clip = Text()
    clip.paste = lambda: (_ for _ in ()).throw(RuntimeError("locked"))
    monkeypatch.setitem(sys.modules, "pyperclip", clip)
    paste.deliver("dictated")
    assert clip.log == ["dictated"]


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows clipboard")
def test_a_picture_and_its_text_come_back(keys):
    pytest.importorskip("pyperclip")
    clipboard._write([(8, DIB), (13, "alt text\0".encode("utf-16-le"))])
    paste.deliver("dictated")
    back = dict(clipboard.save())
    assert back[8][:len(DIB)] == DIB and back[13].decode("utf-16-le").rstrip("\0") == "alt text"


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows clipboard")
def test_an_empty_clipboard_stays_empty(keys):
    pytest.importorskip("pyperclip")
    clipboard._write([])
    paste.deliver("dictated")
    assert clipboard.save() == []
