"""What happens to conversations that were read: sort them, file them under matters, draft answers."""

from __future__ import annotations

import logging

from ..jobs import Job, Stopped
from ..llm import LLMError
from . import skills
from .drafts import Drafts
from .filing import Filing
from .kit import Kit, Part
from .sorting import Sorting

log = logging.getLogger("powertools.assistant")
MAX_DRAFTS = 6


class Progress:
    """What the running job does now, for the window; step() is where Stop takes effect."""

    def __init__(self):
        self.text = ""
        self.job: Job | None = None

    def say(self, text: str) -> None:
        self.text = text

    def step(self, text: str) -> None:
        if self.job is not None and self.job.stop:
            raise Stopped
        self.text = text


class Digest(Part):
    def __init__(self, kit: Kit, sorting: Sorting, filing: Filing, drafts: Drafts, progress: Progress):
        super().__init__(kit)
        self.sorting, self.filing, self.drafts, self.progress = sorting, filing, drafts, progress

    def run(self, found: list[dict], job: Job, problems: list[str]) -> list[dict]:
        """Returns what was new."""
        todo = self.sorting.merge(found)
        job.total, job.done = len(todo), 0
        job.note(f"{len(found)} conversation(s), {len(todo)} new or changed")
        self.sorting.sort(todo, job, self.progress.say)
        self.progress.step("matching to matters")          # Stop pressed while sorting ends here
        fresh = [i for i in self.state.all() if i.get("fresh") and not i.get("pending") and not i.get("error")]
        unfiled = self.filing.match(fresh)
        touched, failed = self.filing.to_matters(fresh)
        unfiled |= failed
        if unfiled:
            problems.append(f"{len(unfiled)} conversation(s) could not be filed under matters; the next Update tries again")
        self.filing.update_files(touched, self.progress.step)
        if self.kit.settings["auto_drafts"]:
            self._draft(fresh)
        for i in fresh:              # a job that ends before this line files them next time
            if i["key"] not in unfiled:
                i.pop("fresh")
        return todo

    def _draft(self, fresh: list[dict]) -> None:
        wanted = [i for i in fresh if i["category"] == "reply" and i["source"] in ("mail", "teams") and not i.get("draft")][:MAX_DRAFTS]
        for n, i in enumerate(wanted, 1):
            self.progress.step(f"drafting answers {n}/{len(wanted)}")
            try:
                self.drafts.create(i["key"], priority=skills.BACKGROUND)
            except (LLMError, OSError) as e:
                log.warning("draft for %s: %s", i["key"], e)
                return
