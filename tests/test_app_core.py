"""Settings, text rules, history and the dictation flow with fake audio/engine (no GPU, no microphone)."""

import numpy as np
import pytest

from powertools import textproc
from powertools.config import DEFAULTS, Settings
from powertools.dictation import Dictation
from powertools.engine import Result
from powertools.store import Store


def test_settings_validate_and_persist(tmp_path):
    s = Settings(tmp_path / "s.json")
    assert s["hotkey"] == DEFAULTS["hotkey"]
    changed = s.update({"hotkey": " Ctrl+Shift+Space ", "vocabulary": ["Merlot", " ", "SSIM"]})
    assert changed["hotkey"] == "ctrl+shift+space"
    assert Settings(tmp_path / "s.json")["vocabulary"] == ["Merlot", "SSIM"]
    with pytest.raises(ValueError):
        s.update({"model": "huge"})
    with pytest.raises(ValueError):
        s.update({"nope": 1})


def test_broken_settings_file_falls_back(tmp_path):
    (tmp_path / "s.json").write_text('{"model": "huge", "beam_size": 3')
    assert Settings(tmp_path / "s.json")["model"] == "turbo"


def test_replacements_whole_words_case_insensitive():
    rules = [{"from": "merlo", "to": "Merlot"}, {"from": "jira tiket", "to": "Jira ticket"}]
    out = textproc.apply_replacements("Merlo i merlotek, Jira tiket.", rules)
    assert out == "Merlot i merlotek, Jira ticket."


def test_vocabulary_prompt():
    assert textproc.vocabulary_prompt([]) is None
    assert textproc.vocabulary_prompt(["A", "B"]) == "Glossary: A, B."


def test_store_roundtrip(tmp_path):
    st = Store(tmp_path / "h.sqlite")
    i = st.add("raw", "text one", "pl", 2.0, 0.3)
    st.add("raw", "other", "en", 1.0, 0.2)
    assert [r["text"] for r in st.recent()] == ["other", "text one"]
    assert [r["id"] for r in st.recent(query="one")] == [i]
    assert st.delete(i) and not st.delete(i)


class FakeRecorder:
    def __init__(self, seconds):
        self.seconds, self.level, self.active = seconds, 0.0, False

    def start(self, device):
        self.active = True

    def stop(self):
        self.active = False
        return np.zeros(int(16000 * self.seconds), dtype=np.float32)


class FakeEngine:
    state, info = "ready", {}

    def __init__(self, text):
        self.text, self.calls = text, []

    def submit(self, audio, done, **opts):
        self.calls.append(opts)
        done(Result(self.text, "pl", 0.99, len(audio) / 16000, 0.1))


def make(tmp_path, text="merlo działa", seconds=2.0):
    s = Settings(tmp_path / "s.json")
    s.update({"replacements": [{"from": "merlo", "to": "Merlot"}], "vocabulary": ["Merlot"]})
    pasted, states = [], []
    d = Dictation(s, FakeEngine(text), FakeRecorder(seconds), Store(tmp_path / "h.sqlite"),
                  lambda t, paste, restore: pasted.append(t), states.append)
    return d, pasted, states


def test_dictation_toggle_pastes_cleaned_text(tmp_path):
    d, pasted, states = make(tmp_path)
    assert d.toggle() == "recording"
    d.toggle()
    assert pasted == ["Merlot działa"]
    assert states == ["recording", "transcribing", "idle"]
    assert d.engine.calls[0]["initial_prompt"] == "Glossary: Merlot."
    assert d.store.recent()[0]["raw"] == "merlo działa"


def test_too_short_recording_is_ignored(tmp_path):
    d, pasted, states = make(tmp_path, seconds=0.1)
    d.toggle(); d.toggle()
    assert pasted == [] and states[-1] == "idle" and d.engine.calls == []


def test_no_speech(tmp_path):
    d, pasted, _ = make(tmp_path, text="  ")
    d.toggle(); d.toggle()
    assert pasted == [] and d.last["note"] == "no speech detected"
