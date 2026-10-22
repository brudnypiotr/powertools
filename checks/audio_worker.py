"""Records the default microphone and the default speaker's loopback at the same time.

Runs in a subprocess (python -m checks.audio_worker <backend> <seconds> <out_dir>) so a hang or a COM
crash in an audio library cannot take the checker down. Prints one JSON line as the last line.
"""

from __future__ import annotations

import json
import sys
import time
import wave
from pathlib import Path

import numpy as np

RATE = 48000


def save_wav(path: Path, audio: np.ndarray, rate: int) -> None:
    mono = audio.mean(axis=1) if audio.ndim == 2 else audio
    pcm = (np.clip(mono, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def stats(audio: np.ndarray, rate: int) -> dict:
    if audio.size == 0:
        return {"seconds": 0.0, "rms_db": -120.0}
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    return {"seconds": round(len(audio) / rate, 1), "rms_db": round(20 * np.log10(max(rms, 1e-6)), 1)}


def start_tone(seconds: int) -> str:
    """Quiet 440 Hz beeps on the default output so the loopback has something to capture."""
    try:
        import sounddevice as sd
        t = np.arange(int(RATE * seconds)) / RATE
        tone = 0.05 * np.sin(2 * np.pi * 440 * t) * (np.sin(2 * np.pi * 0.5 * t) > 0)
        sd.play(tone.astype(np.float32), RATE)
        return "tone playing"
    except Exception as e:
        return f"tone failed: {type(e).__name__}"


def with_soundcard(seconds: int, out: Path) -> dict:
    import soundcard as sc

    mic = sc.default_microphone()
    spk = sc.default_speaker()
    loop = sc.get_microphone(id=str(spk.name), include_loopback=True)
    chunk = RATE // 10
    mic_parts, loop_parts = [], []
    tone = start_tone(seconds)
    with mic.recorder(samplerate=RATE) as mr, loop.recorder(samplerate=RATE) as lr:
        end = time.time() + seconds
        while time.time() < end:
            mic_parts.append(mr.record(numframes=chunk))
            loop_parts.append(lr.record(numframes=chunk))
    m, lo = np.concatenate(mic_parts), np.concatenate(loop_parts)
    save_wav(out / "mic.wav", m, RATE)
    save_wav(out / "loopback.wav", lo, RATE)
    return {"backend": "soundcard", "mic_device": mic.name, "speaker_device": spk.name, "tone": tone,
            "mic": stats(m, RATE), "loopback": stats(lo, RATE)}


def with_pyaudiowpatch(seconds: int, out: Path) -> dict:
    import pyaudiowpatch as pyaudio

    p = pyaudio.PyAudio()
    try:
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        mic = p.get_device_info_by_index(wasapi["defaultInputDevice"])
        loop = p.get_default_wasapi_loopback()
        frames: dict[str, list] = {"mic": [], "loop": []}

        def collector(key):
            def cb(data, n, info, status):
                frames[key].append(np.frombuffer(data, dtype=np.float32))
                return (None, pyaudio.paContinue)
            return cb

        streams = []
        for key, dev in (("mic", mic), ("loop", loop)):
            ch = max(1, min(2, int(dev["maxInputChannels"])))
            rate = int(dev["defaultSampleRate"])
            streams.append((key, ch, rate, p.open(format=pyaudio.paFloat32, channels=ch, rate=rate, input=True,
                                                  input_device_index=dev["index"], stream_callback=collector(key))))
        tone = start_tone(seconds)
        time.sleep(seconds)
        result = {"backend": "pyaudiowpatch", "mic_device": mic["name"], "speaker_device": loop["name"], "tone": tone}
        for key, ch, rate, s in streams:
            s.stop_stream()
            s.close()
            audio = np.concatenate(frames[key]).reshape(-1, ch) if frames[key] else np.zeros((0, ch), np.float32)
            save_wav(out / ("mic.wav" if key == "mic" else "loopback.wav"), audio, rate)
            result["mic" if key == "mic" else "loopback"] = stats(audio, rate)
        return result
    finally:
        p.terminate()


def main() -> None:
    backend, seconds, out = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    try:
        fn = with_soundcard if backend == "soundcard" else with_pyaudiowpatch
        result = fn(seconds, out)
    except Exception as e:
        result = {"backend": backend, "error": f"{type(e).__name__}: {e}"[:300]}
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
