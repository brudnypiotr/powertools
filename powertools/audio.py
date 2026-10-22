"""Microphone capture for dictation: 16 kHz mono float32 through sounddevice (as letmetalk, which worked)."""

from __future__ import annotations

import logging
import threading

import numpy as np

log = logging.getLogger("powertools.audio")
RATE = 16000


def input_devices() -> list[str]:
    """Names of input devices of the default host API, without duplicates."""
    import sounddevice as sd

    default_api = sd.default.hostapi
    names = []
    for d in sd.query_devices():
        if d["max_input_channels"] > 0 and d["hostapi"] == default_api and d["name"] not in names:
            names.append(d["name"])
    return names


def resolve_device(name: str):
    """Device index for a saved name; None (Windows default) when empty or no longer present."""
    if not name:
        return None
    import sounddevice as sd

    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and d["hostapi"] == sd.default.hostapi and d["name"] == name:
            return i
    log.warning("input device %r not found, using the default", name)
    return None


class Recorder:
    def __init__(self):
        self._chunks: list[np.ndarray] = []
        self._stream = None
        self._lock = threading.Lock()
        self.level = 0.0  # last block RMS, for the UI

    def start(self, device_name: str = "") -> None:
        import sounddevice as sd

        with self._lock:
            self._chunks = []
            self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                                          device=resolve_device(device_name), callback=self._callback)
            self._stream.start()

    def _callback(self, indata, frames, time_info, status) -> None:
        self._chunks.append(indata.copy())
        self.level = float(np.sqrt(np.mean(np.square(indata))))

    def stop(self) -> np.ndarray:
        with self._lock:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None
            self.level = 0.0
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(self._chunks, axis=0).flatten()

    @property
    def active(self) -> bool:
        return self._stream is not None
