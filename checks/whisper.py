"""H: the faster-whisper models are already on the laptop (Hugging Face cache) or can be downloaded, and
with --whisper: the model loads on the GPU and transcribes fast enough for dictation."""

from __future__ import annotations

import os
import site
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .common import FAIL, OK, OUT_DIR, SKIP, WARN, Context


def cache_dirs() -> list[Path]:
    dirs = []
    for env in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if os.environ.get(env):
            dirs.append(Path(os.environ[env]))
    if os.environ.get("HF_HOME"):
        dirs.append(Path(os.environ["HF_HOME"]) / "hub")
    dirs.append(Path.home() / ".cache" / "huggingface" / "hub")
    seen, out = set(), []
    for d in dirs:
        if d.is_dir() and d.resolve() not in seen:
            seen.add(d.resolve())
            out.append(d)
    return out


def find_models() -> list[tuple[str, float, bool]]:
    found = []
    for d in cache_dirs():
        for m in d.glob("models--*whisper*"):
            size = sum(f.stat().st_size for f in m.rglob("*") if f.is_file()) / 1e9
            complete = any(m.glob("snapshots/*/model.bin"))
            found.append((m.name.replace("models--", "").replace("--", "/"), size, complete))
    return found


def hf_reachable() -> str:
    req = urllib.request.Request("https://huggingface.co/api/models/Systran/faster-whisper-small",
                                 headers={"User-Agent": "powertools-check"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except Exception as e:
        return f"unreachable ({type(e).__name__})"


def add_cuda_dlls() -> None:
    """pip's nvidia-cublas/cudnn wheels put DLLs in site-packages/nvidia/*/bin (as letmetalk does)."""
    if sys.platform != "win32":
        return
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        for lib in ("nvidia/cublas/bin", "nvidia/cudnn/bin"):
            p = os.path.join(sp, lib)
            if os.path.isdir(p):
                os.add_dll_directory(p)
                os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]


def load_test(ctx: Context) -> None:
    try:
        add_cuda_dlls()
        import ctranslate2
        from faster_whisper import WhisperModel
    except Exception as e:
        ctx.add("whisper.import", FAIL, f"{type(e).__name__}: {e}"[:200] + " (run: check.cmd -Only whisper -Whisper)")
        return
    gpus = ctranslate2.get_cuda_device_count()
    ctx.add("whisper.cuda", OK if gpus else WARN, f"{gpus} CUDA device(s) visible to CTranslate2")

    model = None
    for device, compute in (("cuda", "float16"), ("cpu", "int8")):
        if device == "cuda" and not gpus:
            continue
        for local_only in (True, False):
            t0 = time.time()
            try:
                model = WhisperModel(ctx.model, device=device, compute_type=compute, local_files_only=local_only)
                src = "from cache" if local_only else "downloaded"
                ctx.add("whisper.load", OK, f"{ctx.model} on {device}/{compute} {src} in {time.time() - t0:.1f} s")
                break
            except Exception as e:
                if not local_only:
                    ctx.add(f"whisper.load.{device}", WARN, f"{type(e).__name__}: {e}"[:200])
        if model:
            break
    if not model:
        ctx.add("whisper.load", FAIL, "model could not be loaded on GPU or CPU")
        return

    wav = OUT_DIR / "mic.wav"
    if not wav.exists():
        ctx.add("whisper.transcribe", SKIP, "no checks/out/mic.wav yet (run the audio check first)")
        return
    t0 = time.time()
    segments, info = model.transcribe(str(wav), vad_filter=True)
    text = " ".join(s.text.strip() for s in segments)
    took = time.time() - t0
    print(f"\n   Transcript (shown only here, not saved): {text}\n")
    ctx.add("whisper.transcribe", OK if text else WARN,
            f"{info.duration:.1f} s audio in {took:.1f} s (RTF {took / max(info.duration, 0.1):.2f}), "
            f"language {info.language} p={info.language_probability:.2f}, {len(text.split())} words")


def run(ctx: Context) -> None:
    models = find_models()
    if models:
        for name, size, complete in models:
            ctx.add("whisper.cache", OK if complete else WARN,
                    f"{name}: {size:.1f} GB" + ("" if complete else " (no model.bin: not a faster-whisper model, or incomplete)"))
    else:
        ctx.add("whisper.cache", WARN, f"no faster-whisper models in {', '.join(map(str, cache_dirs())) or '~/.cache/huggingface/hub'}")
    status = hf_reachable() if ctx.interactive else "skipped (dry run)"
    ctx.add("whisper.huggingface", OK if status.startswith("HTTP 200") else WARN, status)
    if ctx.whisper:
        load_test(ctx)
    else:
        ctx.add("whisper.load", SKIP, "load test off (check.cmd -Only whisper -Whisper, or -WhisperVenv <letmetalk venv>)")
