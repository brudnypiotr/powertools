"""Paths and settings. Everything the app writes lives in %LOCALAPPDATA%\\powertools (never in the repo)."""

from __future__ import annotations

import copy
import os
import threading
from pathlib import Path

from . import files


def data_dir() -> Path:
    if os.environ.get("POWERTOOLS_HOME"):
        d = Path(os.environ["POWERTOOLS_HOME"])
    else:
        d = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "powertools"
    d.mkdir(parents=True, exist_ok=True)
    return d


DEFAULTS: dict = {
    "hotkey": "alt+q",
    "model": "turbo",            # faster-whisper name (turbo, large-v3, medium, small) or parakeet-v3
    "language": "",              # "" = detect; "pl", "en"
    "input_device": "",          # "" = Windows default microphone; else a device name
    "paste": True,               # False = only copy to the clipboard
    "restore_clipboard": True,
    "vocabulary": [],            # words Whisper should expect (names, systems, jargon)
    "replacements": [],          # [{"from": "merlo", "to": "Merlot"}], whole words, case-insensitive
    "beam_size": 5,
    "small_on_cpu": True,        # without a usable GPU: turbo -> small, large-v3 -> turbo (the big ones are too slow)
    "open_window_on_start": True,
    "app_window": True,          # open the window as an Edge app window (no browser bars) when Edge is there
    "output_dir": "",            # "" = Documents\\powertools; point it at the wiki inbox
    "mail_attachments": True,
    "notifications": True,       # Windows notifications for meetings (call noticed, recording, saved)
    "meeting_prompt": True,      # ask "Record?" when Teams starts using the microphone
    "meeting_mic": "",           # "" = Windows default microphone (WASAPI name)
    "meeting_output": "",        # "" = Windows default output; the loopback of this device is "Others"
    "meeting_final_model": "large-v3",  # model for the final pass, or "live" to keep the live transcript
    "meeting_keep_audio_days": 7,
    "meeting_mute_sync": True,   # "Me" muted = my microphone is not recorded (the mute hotkey, or Teams' local API)
    "meeting_mute_hotkey": "ctrl+shift+m",  # Teams' own mute key, passed on to Teams: toggles "Me" too ("" = off)
    "meeting_summary": True,     # after the transcript: summary and key points from the local model (Ollama)
    "jira_url": "",              # e.g. https://<site>.atlassian.net
    "jira_email": "",            # Atlassian e-mail (Jira Cloud sign-in)
    "jira_attachments": True,
    "jira_max_attachment_mb": 25,
    "ai_provider": "",           # "" = not chosen yet (local); "local" = Ollama; "copilot" = GitHub Copilot (text leaves the laptop)
    "copilot_model": "gpt-6-luna",
    "copilot_effort": "low",     # reasoning effort; "" = the model's default
    "copilot_daily_credits": 80, # AI credits per day, 0 = no limit
    "memory_dir": "",            # "" = Documents\\powertools\\memory (never the export folder: that one may be a shared inbox)
    "exclude": [],               # never read: people, words in a title, "folder: <mail folder>"
    "setup_done": False,         # the first-start questions of the Recap were answered
    "check_answers": True,       # every 5 minutes with a token: questions answered in Teams or by mail close themselves
    "auto_drafts": True,         # an Update also drafts answers (kept in the app, never sent)
    "silence_days": 3,           # waiting for others: quiet after this many days
    "matter_quiet_days": 14,     # a matter without news for this long is asked about
    "llm_model": "gemma4:e4b",   # Ollama model for clean-up, wording and recap (Local AI tab: test, download)
    "llm_gpu": "auto",           # "auto" = as much on the GPU as fits; "cpu" = leave the GPU to Whisper
    "llm_keep_alive_min": 5,     # minutes the model stays loaded after a call (0 = unload right away)
    "dictation_cleanup": False,  # the local model removes fillers and fixes punctuation after Whisper
    "wording_hotkey": "alt+w",   # English corrections for the selected text
    "wording_mode": "review",    # "review" = show mistakes and a better version; "replace" = paste it over the selection
}
MODELS = ["turbo", "large-v3", "medium", "small", "parakeet-v3"]


def output_dir(settings) -> Path:
    d = Path(settings["output_dir"]) if settings["output_dir"] else Path.home() / "Documents" / "powertools"
    d.mkdir(parents=True, exist_ok=True)
    return d


def memory_dir(settings) -> Path:
    d = Path(settings["memory_dir"]) if settings["memory_dir"] else Path.home() / "Documents" / "powertools" / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------ what a setting may be: one rule per key
def one_of(*allowed):
    def rule(key: str, value):
        if value not in allowed:
            raise ValueError(f"{key} must be one of {', '.join(repr(a) for a in allowed)}")
        return value
    return rule


def whole(low: int, high: int):
    def rule(key: str, value):
        value = int(value)
        if not low <= value <= high:
            raise ValueError(f"{key} must be {low}..{high}")
        return value
    return rule


def hotkey(key: str, value):
    value = str(value).strip().lower()
    if not value and key != "meeting_mute_hotkey":       # that one may be off
        raise ValueError(f"{key} cannot be empty")
    return value


def model_name(key: str, value):
    value = str(value).strip()
    if not value or any(c.isspace() for c in value):
        raise ValueError(f"{key} must be a model name, e.g. {DEFAULTS[key]}")
    return value


def words(key: str, value):
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return [str(w).strip() for w in value if str(w).strip()]


def replacements(key: str, value):
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return [{"from": str(r["from"]).strip(), "to": str(r["to"]).strip()} for r in value if isinstance(r, dict) and str(r.get("from", "")).strip()]


def https(key: str, value):
    value = str(value).strip().rstrip("/")
    if value and not value.startswith("https://"):
        raise ValueError("Jira site must start with https://")
    return value


RULES = {
    "hotkey": hotkey, "wording_hotkey": hotkey, "meeting_mute_hotkey": hotkey, "model": one_of(*MODELS), "language": one_of("", "pl", "en"),
    "beam_size": whole(1, 10), "vocabulary": words, "replacements": replacements, "exclude": words,
    "meeting_final_model": one_of(*MODELS, "live"), "meeting_keep_audio_days": whole(0, 365),
    "ai_provider": one_of("", "local", "copilot"), "copilot_model": model_name, "copilot_effort": one_of("", "low", "medium", "high"),
    "copilot_daily_credits": whole(0, 5000), "llm_model": model_name, "llm_gpu": one_of("auto", "cpu"), "llm_keep_alive_min": whole(0, 120),
    "wording_mode": one_of("review", "replace"), "silence_days": whole(1, 365), "matter_quiet_days": whole(1, 365),
    "jira_url": https, "jira_max_attachment_mb": whole(1, 2000),
}


def validate(key: str, value):
    """The cleaned value, or ValueError."""
    if key not in DEFAULTS:
        raise ValueError(f"unknown setting: {key}")
    if key in RULES:
        return RULES[key](key, value)
    return type(DEFAULTS[key])(value)       # a plain switch or text


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "settings.json"
        self._lock = threading.Lock()
        self._data = copy.deepcopy(DEFAULTS)
        for k, v in files.load_json(self.path).items():
            if k in DEFAULTS:
                try:
                    self._data[k] = validate(k, v)
                except (ValueError, TypeError, KeyError):
                    pass  # keep the default for a broken value

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
            files.save_json(self.path, self._data, indent=2)
        return changed
