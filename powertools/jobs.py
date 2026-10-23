"""Background jobs (exports), one at a time. A job that hits an expired token pauses and resumes from its cursor."""

from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .graph.client import GraphError, TokenExpired

log = logging.getLogger("powertools.jobs")
_ids = itertools.count(1)


@dataclass
class Job:
    title: str
    run: Callable[["Job"], None]
    id: int = field(default_factory=lambda: next(_ids))
    state: str = "queued"          # queued | running | done | error | needs_token
    done: int = 0
    total: int = 0
    notes: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    error: str = ""
    result: str = ""               # what the window opens when the job is done (e.g. a saved org chart)
    started: float = 0.0
    finished: float = 0.0

    def note(self, text: str) -> None:
        self.notes.append(text)
        log.info("job %s: %s", self.id, text)

    def view(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "title", "state", "done", "total", "notes", "files", "error",
                                              "result", "started", "finished")}


class Jobs:
    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self.all: list[Job] = []
        threading.Thread(target=self._worker, name="jobs", daemon=True).start()

    def submit(self, title: str, run: Callable[[Job], None]) -> Job:
        job = Job(title, run)
        self.all.append(job)
        self.all = self.all[-30:]
        self._q.put(job)
        return job

    def resume_paused(self) -> int:
        paused = [j for j in self.all if j.state == "needs_token"]
        for j in paused:
            j.state, j.error = "queued", ""
            self._q.put(j)
        return len(paused)

    def get(self, job_id: int) -> Job:
        for j in self.all:
            if j.id == job_id:
                return j
        raise KeyError(job_id)

    def _worker(self) -> None:
        while True:
            job = self._q.get()
            job.state, job.started = "running", job.started or time.time()
            try:
                job.run(job)
                job.state = "done"
            except TokenExpired:
                job.state, job.error = "needs_token", "The Graph token expired. Paste a new one; the export continues."
            except GraphError as e:
                job.state, job.error = "error", str(e)
            except Exception as e:
                log.exception("job %s failed", job.id)
                job.state, job.error = "error", f"{type(e).__name__}: {e}"
            job.finished = time.time()
