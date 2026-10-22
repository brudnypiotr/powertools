"""H: without admin rights we can record the microphone ("Me") and the system sound ("Others", WASAPI
loopback) at the same time, also when the sound goes to headphones."""

from __future__ import annotations

import json
import subprocess
import sys

from .common import FAIL, IS_WIN, OK, OUT_DIR, SKIP, WARN, Context

SILENT_DB = -55.0


def list_devices(ctx: Context) -> None:
    try:
        import sounddevice as sd
    except Exception as e:
        ctx.add("audio.devices", FAIL, f"sounddevice missing ({type(e).__name__})")
        return
    apis = [a["name"] for a in sd.query_hostapis()]
    wasapi = next((i for i, a in enumerate(sd.query_hostapis()) if "WASAPI" in a["name"]), None)
    devs = sd.query_devices()
    n_wasapi = sum(1 for d in devs if d["hostapi"] == wasapi) if wasapi is not None else 0
    ctx.add("audio.hostapis", OK if wasapi is not None or not IS_WIN else FAIL,
            f"{', '.join(apis)}; WASAPI devices: {n_wasapi}")
    try:
        din, dout = sd.default.device
        out_name = devs[dout]["name"] if dout is not None and dout >= 0 else "?"
        in_name = devs[din]["name"] if din is not None and din >= 0 else "?"
        phones = any(w in out_name.lower() for w in ("head", "słuch", "earphone", "buds", "jabra", "airpods"))
        ctx.add("audio.default", OK, f"mic: {in_name} | output: {out_name}"
                                     + (" (looks like headphones)" if phones else ""))
    except Exception as e:
        ctx.add("audio.default", WARN, f"cannot read defaults ({type(e).__name__})")


def record(ctx: Context, backend: str) -> dict:
    cmd = [sys.executable, "-m", "checks.audio_worker", backend, str(ctx.seconds), str(OUT_DIR)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=ctx.seconds + 40)
    except subprocess.TimeoutExpired:
        return {"backend": backend, "error": "timed out (the library hung)"}
    lines = [ln for ln in p.stdout.strip().splitlines() if ln.startswith("{")]
    if not lines:
        return {"backend": backend, "error": (p.stderr.strip().splitlines() or ["no output"])[-1][:300]}
    return json.loads(lines[-1])


def judge(ctx: Context, r: dict) -> bool:
    b = r["backend"]
    if "error" in r:
        ctx.add(f"audio.{b}", FAIL, r["error"])
        return False
    good = True
    for key, label in (("mic", "Me (microphone)"), ("loopback", "Others (system sound)")):
        s = r[key]
        if s["seconds"] < ctx.seconds * 0.8 and key == "mic":
            ctx.add(f"audio.{b}.{key}", WARN, f"{label}: only {s['seconds']} s captured")
            good = False
        elif s["rms_db"] < SILENT_DB:
            ctx.add(f"audio.{b}.{key}", WARN, f"{label}: silent ({s['rms_db']} dBFS, {s['seconds']} s)")
            good = False
        else:
            ctx.add(f"audio.{b}.{key}", OK, f"{label}: {s['rms_db']} dBFS, {s['seconds']} s")
    ctx.add(f"audio.{b}.devices", OK, f"mic: {r.get('mic_device')} | loopback of: {r.get('speaker_device')}; "
                                      f"{r.get('tone')}")
    return good


def run(ctx: Context) -> None:
    list_devices(ctx)
    if not IS_WIN or not ctx.interactive:
        ctx.add("audio.record", SKIP, "needs Windows and a person at the microphone")
        return
    if not ctx.confirm_start("Audio", f"Put on the headphones you use for Teams (best: during a Teams call).\n"
                                      f"For {ctx.seconds} s: talk into the microphone. You will hear quiet beeps;\n"
                                      f"that is the test sound for the system-sound track.\n"
                                      f"Recordings land in {OUT_DIR} (mic.wav, loopback.wav) so you can listen."):
        ctx.add("audio.record", SKIP, "skipped by user")
        return
    if judge(ctx, record(ctx, "soundcard")):
        return
    print("   Trying the second library (PyAudioWPatch) ...")
    if ctx.confirm_start("Audio, second try", f"Same again: talk for {ctx.seconds} s."):
        judge(ctx, record(ctx, "pyaudiowpatch"))
