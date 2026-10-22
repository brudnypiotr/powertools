"""Paths and settings. Everything the app writes lives in %LOCALAPPDATA%\\powertools (never in the repo)."""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path


def data_dir() -> Path:
    if os.environ.get("POWERTOOLS_HOME"):
        d = Path(os.environ["POWERTOOLS_HOME"])
    else:
        d = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "powertools"
    d.mkdir(parents=True, exist_ok=True)
    return d


DEFAULTS: dict = {
    "hotkey": "alt+q",
    "model": "turbo",            # faster-whisper name: turbo, large-v3, medium, small
    "language": "",              # "" = detect; "pl", "en"
    "input_device": "",          # "" = Windows default microphone; else a device name
    "paste": True,               # False = only copy to the clipboard
    "restore_clipboard": True,
    "vocabulary": [],            # words Whisper should expect (names, systems, jargon)
    "replacements": [],          # [{"from": "merlo", "to": "Merlot"}], whole words, case-insensitive
    "beam_size": 5,
    "open_window_on_start": True,
    "app_window": True,          # open the window as an Edge app window (no browser bars) when Edge is there
    "output_dir": "",            # "" = Documents\\powertools; point it at the wiki inbox
    "mail_attachments": True,
    "meeting_prompt": True,      # ask "Record?" when Teams starts using the microphone
    "meeting_mic": "",           # "" = Windows default microphone (WASAPI name)
    "meeting_output": "",        # "" = Windows default output; the loopback of this device is "Others"
    "meeting_final_model": "large-v3",  # model for the final pass, or "live" to keep the live transcript
    "meeting_keep_audio_days": 7,
}
MODELS = ["turbo", "large-v3", "medium", "small"]


def output_dir(settings) -> "Path":
    d = Path(settings["output_dir"]) if settings["output_dir"] else Path.home() / "Documents" / "powertools"
    d.mkdir(parents=True, exist_ok=True)
    return d


def validate(key: str, value):
    """Returns the cleaned value or raises ValueError."""
    if key not in DEFAULTS:
        raise ValueError(f"unknown setting: {key}")
    default = DEFAULTS[key]
    if key == "model" and value not in MODELS:
        raise ValueError(f"model must be one of {', '.join(MODELS)}")
    if key == "language" and value not in ("", "pl", "en"):
        raise ValueError("language must be '', 'pl' or 'en'")
    if key == "hotkey":
        value = str(value).strip().lower()
        if not value:
            raise ValueError("hotkey cannot be empty")
    if key == "vocabulary":
        if not isinstance(value, list):
            raise ValueError("vocabulary must be a list")
        value = [str(w).strip() for w in value if str(w).strip()]
    if key == "replacements":
        if not isinstance(value, list):
            raise ValueError("replacements must be a list")
        value = [{"from": str(r["from"]).strip(), "to": str(r["to"]).strip()}
                 for r in value if isinstance(r, dict) and str(r.get("from", "")).strip()]
    if key == "meeting_final_model" and value not in MODELS + ["live"]:
        raise ValueError("meeting_final_model must be a model name or 'live'")
    if key == "meeting_keep_audio_days":
        value = int(value)
        if not 0 <= value <= 365:
            raise ValueError("meeting_keep_audio_days must be 0..365")
    if key == "beam_size":
        value = int(value)
        if not 1 <= value <= 10:
            raise ValueError("beam_size must be 1..10")
    if isinstance(default, bool):
        value = bool(value)
    elif isinstance(default, str):
        value = str(value)
    return value


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "settings.json"
        self._lock = threading.Lock()
        self._data = copy.deepcopy(DEFAULTS)
        if self.path.exists():
            try:
                stored = json.loads(self.path.read_text(encoding="utf-8"))
                for k, v in stored.items():
                    if k in DEFAULTS:
                        try:
                            self._data[k] = validate(k, v)
                        except (ValueError, TypeError, KeyError):
                            pass  # keep the default for a broken value
            except (OSError, ValueError):
                pass

    def __getitem__(self, key: str):
        with self._lock:
            return copy.deepcopy(self._data[key])

    def all(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def update(self, changes: dict) -> dict:
        """Validates every change first, then saves. Returns the keys that changed."""
        clean = {k: validate(k, v) for k, v in changes.items()}
        with self._lock:
            changed = {k: v for k, v in clean.items() if self._data[k] != v}
            self._data.update(clean)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        return changed
