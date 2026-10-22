"""The Whisper engine: one thread owns the model (6 GB of VRAM holds one at a time) and runs jobs by priority.

Priorities: dictation 0 (the user waits), live meeting chunks 5, final meeting passes 9. A job may ask for another
model (the final pass uses large-v3): the engine swaps models, and returns to the default one when idle.
A dictation job never forces a swap; it runs on whatever model is loaded.
"""

from __future__ import annotations

import itertools
import logging
import os
import queue
import site
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger("powertools.engine")
DICTATION, LIVE, FINAL = 0, 5, 9


@dataclass
class Result:
    text: str
    language: str = ""
    language_p: float = 0.0
    audio_s: float = 0.0
    took_s: float = 0.0
    error: str = ""
    segments: list = field(default_factory=list)   # [(start_s, end_s, text)]


@dataclass
class Job:
    audio: object                      # numpy float32 16 kHz mono, or a path to an audio file
    done: Callable[[Result], None]
    options: dict = field(default_factory=dict)


def add_cuda_dlls() -> None:
    """The nvidia-cublas/cudnn wheels put their DLLs in site-packages/nvidia/*/bin (as letmetalk does)."""
    if sys.platform != "win32":
        return
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        for lib in ("nvidia/cublas/bin", "nvidia/cudnn/bin"):
            p = os.path.join(sp, lib)
            if os.path.isdir(p):
                os.add_dll_directory(p)
                os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]


class Engine:
    def __init__(self, model_name: str):
        self.default_model = model_name
        self.model_name = model_name
        self.state = "idle"            # idle -> loading -> ready | error
        self.info: dict = {}
        self._model = None
        self._jobs: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._thread = threading.Thread(target=self._run, name="whisper", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def status(self) -> dict:
        return {"state": self.state, "model": self.model_name, "default_model": self.default_model,
                "queued": self._jobs.qsize(), **self.info}

    def submit(self, audio, done: Callable[[Result], None], priority: int = DICTATION, **options) -> None:
        self._jobs.put((priority, next(self._seq), Job(audio, done, options)))

    def reload(self, model_name: str) -> None:
        self._jobs.put((-1, next(self._seq), ("reload", model_name)))

    def stop(self) -> None:
        self._jobs.put((-2, next(self._seq), None))

    # ------------------------------------------------------------------ worker thread
    def _load(self, name: str) -> None:
        self.state, self.info, self._model = "loading", {}, None
        self.model_name = name
        try:
            add_cuda_dlls()
            import ctranslate2
            from faster_whisper import WhisperModel
        except Exception as e:
            self.state, self.info = "error", {"error": f"faster-whisper not installed: {e}"}
            log.exception("import failed")
            return
        gpus = ctranslate2.get_cuda_device_count()
        attempts = ([("cuda", "float16")] if gpus else []) + [("cpu", "int8")]
        last = ""
        for device, compute in attempts:
            t0 = time.time()
            try:
                self._model = WhisperModel(name, device=device, compute_type=compute)
                self.info = {"device": device, "compute": compute, "load_s": round(time.time() - t0, 1)}
                self.state = "ready"
                log.info("model %s loaded on %s/%s in %.1f s", name, device, compute, time.time() - t0)
                return
            except Exception as e:
                last = f"{device}: {type(e).__name__}: {e}"
                log.warning("load on %s failed: %s", device, e)
        self.state, self.info = "error", {"error": last[:300]}

    def _transcribe(self, job: Job) -> Result:
        t0 = time.time()
        segments, info = self._model.transcribe(
            job.audio,
            language=job.options.get("language") or None,
            beam_size=job.options.get("beam_size", 5),
            initial_prompt=job.options.get("initial_prompt"),
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        segs = [(round(s.start, 2), round(s.end, 2), s.text.strip()) for s in segments]
        text = " ".join(t for _, _, t in segs if t).strip()
        return Result(text, info.language, round(info.language_probability, 2), info.duration, time.time() - t0,
                      segments=segs)

    def _run(self) -> None:
        self._load(self.model_name)
        while True:
            _prio, _seq, job = self._jobs.get()
            if job is None:
                return
            if isinstance(job, tuple):
                self.default_model = job[1]
                self._model = None  # free the GPU before loading the next model
                self._load(job[1])
                continue
            want = job.options.get("model")
            if want and want != self.model_name and self.state != "loading":
                self._model = None
                self._load(want)
            if self.state != "ready":
                job.done(Result("", error=f"model not ready ({self.state}): {self.info.get('error', '')}"))
                continue
            try:
                result = self._transcribe(job)
            except Exception as e:
                log.exception("transcription failed")
                result = Result("", error=f"{type(e).__name__}: {e}")
            try:
                job.done(result)
            except Exception:
                log.exception("result callback failed")
            if self._jobs.empty() and self.model_name != self.default_model:
                self._model = None
                self._load(self.default_model)
