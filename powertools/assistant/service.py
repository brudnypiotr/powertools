"""The assistant as the app sees it: builds the parts and gives them names. No logic of its own.

state    what is known between updates (assistant.json)        memory   the Markdown files
update   read every source, then digest                        answered did the user answer in the meantime?
digest   sorting, filing, drafts: what happens to what was read
view     what the window shows                                 actions  what the user does with it
ask, briefs, people, compare                                   skills   the questions to the model
"""

from __future__ import annotations

from pathlib import Path

from ..config import data_dir, memory_dir
from ..jobs import ACTIVE
from .actions import Actions
from .answered import Answers
from .ask import Ask
from .briefs import Briefs
from .compare import Compare
from .digest import Digest, Progress
from .drafts import Drafts
from .filing import Filing
from .kit import Kit
from .memory import Memory
from .people import People
from .sorting import Sorting
from .state import State
from .update import Updates
from .view import View


class Assistant:
    def __init__(self, settings, graph, llm, jira=None, meetings=None, path: Path | None = None, memory: Memory | None = None):
        self.memory = memory or Memory(lambda: memory_dir(settings))
        self.state = State(path or data_dir() / "assistant.json")
        self.kit = kit = Kit(settings, graph, llm, self.memory, self.state, jira, meetings)
        self.drafts = Drafts(kit)
        self.sorting, self.filing = Sorting(kit, self.drafts), Filing(kit)
        self.progress = Progress()
        digest = Digest(kit, self.sorting, self.filing, self.drafts, self.progress)
        self.updates = Updates(kit, digest, self.drafts)
        self.answers = Answers(kit, digest, lambda: self.updates.running)
        self.actions = Actions(kit, self.filing)
        self.asking, self.briefs, self.people = Ask(kit), Briefs(kit), People(kit)
        self.compare = Compare(kit, self.sorting).run
        self.view = View(kit, self.updates, self.answers, self.drafts).build

    def stop(self) -> None:
        """Stop the running Update (or look for answers): what is sorted stays, the range is read again next time."""
        for job in (self.updates.job, self.answers.job, self.progress.job):
            if job is not None and job.state in (*ACTIVE, "needs_token"):
                self.kit.graph.jobs.stop(job)
