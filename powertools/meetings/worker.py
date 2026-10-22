"""Meeting recorder, run as a subprocess so an audio-driver crash cannot take the app down.

    python -c <bootstrap> powertools.meetings.worker <session dir> <mic name or ""> <output name or "">

Records the microphone ("me") and the WASAPI loopback of the output device ("others") with PyAudioWPatch (the
library that passed the feasibility check), and writes 16 kHz mono WAV chunks every CHUNK_S seconds:
me_0000.wav, others_0000.wav, ... Events go to stdout as JSON lines:
  {"event": "started", "mic": ..., "output": ...}
  {"event": "chunk", "track": "me", "index": 3, "path": ..., "start": 90.0}
  {"event": "level", "me": 0.02, "others": 0.1}
  {"event": "error", "message": ...}
  {"event": "stopped", "seconds": 1834.5}
It stops when the file STOP appears in the session dir (or stdin closes).
"""

from __future__ import annotations

import json
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

RATE = 16000
CHUNK_S = 30


def emit(**event) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def to_16k_mono(frames: np.ndarray, rate: int, channels: int) -> np.ndarray:
    if frames.size == 0:
        return np.zeros(0, dtype=np.float32)
    mono = frames.reshape(-1, channels).mean(axis=1) if channels > 1 else frames
    if rate == RATE:
        return mono.astype(np.float32)
    n = int(len(mono) * RATE / rate)
    return np.interp(np.linspace(0, len(mono) - 1, n), np.arange(len(mono)), mono).astype(np.float32)


def write_wav(path: Path, audio: np.ndarray) -> None:
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    tmp = path.with_suffix(".part")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    tmp.replace(path)


class Track:
    def __init__(self, name: str, rate: int, channels: int):
        self.name, self.rate, self.channels = name, rate, channels
        self.buf: list[np.ndarray] = []
        self.lock = threading.Lock()
        self.level = 0.0
        self.index = 0

    def callback(self, data, frames, info, status):
        import pyaudiowpatch as pa

        arr = np.frombuffer(data, dtype=np.float32)
        with self.lock:
            self.buf.append(arr)
        self.level = float(np.sqrt(np.mean(np.square(arr)))) if arr.size else 0.0
        return (None, pa.paContinue)

    def flush(self, folder: Path, final: bool = False) -> None:
        with self.lock:
            raw = np.concatenate(self.buf) if self.buf else np.zeros(0, np.float32)
            self.buf = []
        audio = to_16k_mono(raw, self.rate, self.channels)
        if audio.size == 0 and not final:
            audio = np.zeros(RATE * CHUNK_S, np.float32)  # loopback delivers nothing while the output is silent
        if audio.size:
            path = folder / f"{self.name}_{self.index:04d}.wav"
            write_wav(path, audio)
            emit(event="chunk", track=self.name, index=self.index, path=str(path), start=self.index * CHUNK_S)
            self.index += 1


def find(p, names_ok, wanted: str, default_info):
    if not wanted:
        return default_info
    for i in range(p.get_device_count()):
        d = p.get_device_info_by_index(i)
        if names_ok(d) and d["name"] == wanted:
            return d
    emit(event="warning", message=f"device {wanted!r} not found, using the default")
    return default_info


def main() -> None:
    folder, mic_name, out_name = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    folder.mkdir(parents=True, exist_ok=True)
    stop_file = folder / "STOP"
    import pyaudiowpatch as pa

    p = pa.PyAudio()
    streams = []
    try:
        wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
        mic = find(p, lambda d: d["hostApi"] == wasapi["index"] and d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice"),
                   mic_name, p.get_device_info_by_index(wasapi["defaultInputDevice"]))
        default_loop = p.get_default_wasapi_loopback()
        loop = default_loop
        if out_name:
            loop = next((d for d in p.get_loopback_device_info_generator() if d["name"].startswith(out_name)), default_loop)
        tracks = []
        for name, dev in (("me", mic), ("others", loop)):
            ch = max(1, min(2, int(dev["maxInputChannels"])))
            rate = int(dev["defaultSampleRate"])
            t = Track(name, rate, ch)
            s = p.open(format=pa.paFloat32, channels=ch, rate=rate, input=True, input_device_index=dev["index"],
                       frames_per_buffer=1024, stream_callback=t.callback)
            tracks.append(t)
            streams.append(s)
        emit(event="started", mic=mic["name"], output=loop["name"])

        def stdin_closed() -> None:
            sys.stdin.read()
            stop_file.touch()

        threading.Thread(target=stdin_closed, daemon=True).start()
        t0 = next_chunk = time.time()
        next_chunk += CHUNK_S
        while not stop_file.exists():
            time.sleep(0.25)
            emit(event="level", me=round(tracks[0].level, 4), others=round(tracks[1].level, 4))
            if time.time() >= next_chunk:
                next_chunk += CHUNK_S
                for t in tracks:
                    t.flush(folder)
        for s in streams:
            s.stop_stream()
        for t in tracks:
            t.flush(folder, final=True)
        emit(event="stopped", seconds=round(time.time() - t0, 1))
    except Exception as e:
        emit(event="error", message=f"{type(e).__name__}: {e}"[:400])
    finally:
        for s in streams:
            try:
                s.close()
            except Exception:
                pass
        p.terminate()


if __name__ == "__main__":
    main()
