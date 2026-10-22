"""Shared helpers for the feasibility checks.

Every check appends results as (id, status, detail). Details must never hold message text, names,
subjects or other content: only statuses, counts, HTTP codes, types and device names, so the report
can be pasted into a chat safely.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

CHECKS_DIR = Path(__file__).resolve().parent
OUT_DIR = CHECKS_DIR / "out"
TOKEN_FILE = CHECKS_DIR / ".token"
IS_WIN = sys.platform == "win32"

OK, WARN, FAIL, SKIP = "OK", "WARN", "FAIL", "SKIP"
_COLORS = {OK: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m", SKIP: "\033[90m"}

if IS_WIN:
    os.system("")  # enables ANSI colors in the Windows console


@dataclass
class Result:
    id: str
    status: str
    detail: str = ""


@dataclass
class Context:
    interactive: bool = True
    whisper: bool = False
    model: str = "turbo"
    seconds: int = 10
    results: list[Result] = field(default_factory=list)
    shared: dict = field(default_factory=dict)  # data passed between checks (never written to the report)

    def add(self, id: str, status: str, detail: str = "") -> None:
        self.results.append(Result(id, status, detail))
        color = _COLORS.get(status, "")
        print(f"  {color}[{status:<4}]\033[0m {id}: {detail}", flush=True)

    def ask(self, prompt: str, default: str = "") -> str:
        if not self.interactive:
            return default
        try:
            return input(prompt).strip()
        except EOFError:
            return default

    def confirm_start(self, title: str, instructions: str) -> bool:
        """Print what the user has to do; Enter starts, 's' skips."""
        print(f"\n== {title} ==")
        for line in instructions.strip().splitlines():
            print(f"   {line.strip()}")
        if not self.interactive:
            return True
        return self.ask("   Press Enter to start, or type s + Enter to skip: ").lower() != "s"


def section(title: str) -> None:
    print(f"\n### {title}", flush=True)
