"""Measuring the models: the same conversations on another one."""

from __future__ import annotations

import time

from ..llm import LLMError
from .kit import Kit, Part
from .sorting import Sorting
from .state import cat


class Compare(Part):
    def __init__(self, kit: Kit, sorting: Sorting):
        super().__init__(kit)
        self.sorting = sorting

    def run(self, provider: str, limit: int = 30) -> dict:
        """The same conversations on another model: agreement with what the user confirmed, time and AI credits."""
        rows = [i for i in self.state.all() if i.get("by") == "model" and i.get("text")]
        rows.sort(key=lambda i: (not i.get("reviewed"), i["last"]), reverse=False)
        rows = rows[:max(1, min(limit, 100))]
        if not rows:
            raise ValueError("run an Update first: there is nothing to compare yet")
        prompt = {"me": self.state.me, "profile": self.kit.profile(), "corrections": ""}
        out = {"provider": provider, "items": 0, "reviewed": 0, "right": 0, "same": 0, "seconds": 0.0, "credits": 0.0, "errors": 0, "rows": []}
        t0 = time.time()
        for i in rows:
            try:
                a = self.sorting.classify(i, prompt, provider=provider)
            except LLMError as e:
                out["errors"] += 1
                out["error"] = str(e)[:200]
                if out["errors"] >= 3 and not out["items"]:
                    break
                continue
            out["items"] += 1
            out["credits"] += a.get("credits") or 0.0
            out["same"] += a["category"] == i["category"]
            if i.get("reviewed"):
                out["reviewed"] += 1
                out["right"] += a["category"] == cat(i)
            out["rows"].append({"title": i["title"][:80], "now": a["category"], "before": i["category"],
                                "you": cat(i) if i.get("reviewed") else ""})
        out["seconds"], out["credits"] = round(time.time() - t0, 1), round(out["credits"], 2)
        return out
