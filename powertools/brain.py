"""The model the whole app talks to: the local one (Ollama) or GitHub Copilot, as the user chose in the window.

Copilot is never a default: until the user picks it, everything stays with the local model. Every call to Copilot is
counted per day (calls, tokens, AI credits) in usage.json, and the daily limit stops further calls.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from .config import data_dir
from .copilot import Copilot
from .llm import INTERACTIVE, LLMError, Ollama

log = logging.getLogger("powertools.brain")


class Usage:
    """Copilot calls, tokens and AI credits per day, with a split by task."""

    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "usage.json"
        self._lock = threading.Lock()
        try:
            self.days: dict = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.days = {}

    @staticmethod
    def _day() -> str:
        return time.strftime("%Y-%m-%d")

    def add(self, task: str, stats: dict) -> None:
        credits = stats.get("credits") or 0.0
        with self._lock:
            d = self.days.setdefault(self._day(), {"calls": 0, "credits": 0.0, "tokens_in": 0, "tokens_out": 0, "by": {}})
            d["calls"] += 1
            d["credits"] = round(d["credits"] + credits, 4)
            d["tokens_in"] += stats.get("prompt_tokens") or 0
            d["tokens_out"] += stats.get("tokens") or 0
            t = d["by"].setdefault(task or "other", {"calls": 0, "credits": 0.0})
            t["calls"] += 1
            t["credits"] = round(t["credits"] + credits, 4)
            self.days = dict(sorted(self.days.items())[-400:])
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.days), encoding="utf-8")
            os.replace(tmp, self.path)

    def today(self) -> dict:
        with self._lock:
            return dict(self.days.get(self._day()) or {"calls": 0, "credits": 0.0, "tokens_in": 0, "tokens_out": 0, "by": {}})

    def month(self) -> float:
        with self._lock:
            return round(sum(d["credits"] for k, d in self.days.items() if k.startswith(time.strftime("%Y-%m"))), 2)


class Brain:
    def __init__(self, settings, local: Ollama | None = None, cloud: Copilot | None = None, usage: Usage | None = None):
        self.settings = settings
        self.local = local or Ollama(settings)
        self.cloud = cloud or Copilot(settings)
        self.usage = usage or Usage()

    @property
    def is_cloud(self) -> bool:
        return self.settings["ai_provider"] == "copilot"

    @property
    def model(self) -> str:
        return self.settings["copilot_model"] if self.is_cloud else self.settings["llm_model"]

    @property
    def parallel(self) -> int:
        """How many background calls make sense at once (the local model answers one at a time)."""
        return 3 if self.is_cloud else 1

    def available(self) -> dict:
        """For the window: can the model be used, and where does the text go?"""
        a = self.local.available()
        if self.is_cloud:
            a = {"installed": True, "running": True}   # the sign-in is checked in the AI tab and on the first call
        return {**a, "provider": self.settings["ai_provider"] or "local", "chosen": bool(self.settings["ai_provider"]),
                "model": self.model, "credits_today": round(self.usage.today()["credits"], 2)}

    def chat(self, system: str, user: str, schema: dict | None = None, priority: int = INTERACTIVE,
             max_tokens: int = 800, num_ctx: int = 4096, timeout: float = 180, model: str = "", task: str = "",
             provider: str = "") -> tuple[str, dict]:
        """Like Ollama.chat, on the chosen provider (`provider` forces one: the comparison in the AI tab)."""
        cloud = provider == "copilot" if provider else self.is_cloud
        kw = dict(schema=schema, priority=priority, max_tokens=max_tokens, timeout=timeout, model=model)
        if not cloud:
            return self.local.chat(system, user, num_ctx=num_ctx, **kw)
        limit = self.settings["copilot_daily_credits"]
        if limit and self.usage.today()["credits"] >= limit:
            raise LLMError(f"the daily limit of {limit} AI credits is used up (Settings > AI)")
        text, stats = self.cloud.chat(system, user, **kw)
        self.usage.add(task, stats)
        return text, stats

    def unload_all(self) -> None:
        self.local.unload_all()

    def stop(self) -> None:
        self.cloud.stop()
