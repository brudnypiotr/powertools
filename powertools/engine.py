"""The Whisper engine: one thread owns the model (the GPU has room for one at a time) and runs jobs in order."""

from __future__ import annotations

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


@dataclass
class Result:
    text: str
    language: str = ""
    language_p: float = 0.0
    audio_s: float = 0.0
    took_s: float = 0.0
    error: str = ""


@dataclass
class Job:
    audio: object                      # numpy float32, 16 kHz mono
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
        self.model_name = model_name
        self.state = "idle"            # idle -> loading -> ready | error
        self.info: dict = {}
        self._model = None
        self._jobs: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="whisper", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def status(self) -> dict:
        return {"state": self.state, "model": self.model_name, "queued": self._jobs.qsize(), **self.info}

    def submit(self, audio, done: Callable[[Result], None], **options) -> None:
        self._jobs.put(Job(audio, done, options))

    def reload(self, model_name: str) -> None:
        self._jobs.put(("reload", model_name))

    def stop(self) -> None:
        self._jobs.put(None)

    # ------------------------------------------------------------------ worker thread
    def _load(self) -> None:
        self.state = "loading"
        self.info = {}
        self._model = None
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
                self._model = WhisperModel(self.model_name, device=device, compute_type=compute)
                self.info = {"device": device, "compute": compute, "load_s": round(time.time() - t0, 1)}
                self.state = "ready"
                log.info("model %s loaded on %s/%s in %.1f s", self.model_name, device, compute, time.time() - t0)
                return
            except Exception as e:
                last = f"{device}: {type(e).__name__}: {e}"
                log.warning("load on %s failed: %s", device, e)
        self.state, self.info = "error", {"error": last[:300]}

    def _transcribe(self, job: Job) -> Result:
        audio_s = len(job.audio) / 16000
        t0 = time.time()
        segments, info = self._model.transcribe(
            job.audio,
            language=job.options.get("language") or None,
            beam_size=job.options.get("beam_size", 5),
            initial_prompt=job.options.get("initial_prompt"),
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return Result(text, info.language, round(info.language_probability, 2), audio_s, time.time() - t0)

    def _run(self) -> None:
        self._load()
        while True:
            job = self._jobs.get()
            if job is None:
                return
            if isinstance(job, tuple) and job[0] == "reload":
                self.model_name = job[1]
                self._model = None  # free the GPU before loading the next model
                self._load()
                continue
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
