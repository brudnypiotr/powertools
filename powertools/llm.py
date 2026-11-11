"""Local language model through Ollama (http://127.0.0.1:11434): nothing leaves the laptop.

Ollama installs per user (no admin rights) and runs its own server; powertools only talks HTTP to it (stdlib, no new
packages). The GPU is shared with Whisper (6 GB holds both only when the model is small), so:
- Whisper asks the model to unload before it loads a model on the GPU (Engine.before_gpu),
- a server started by powertools keeps 1 GB of VRAM free for Whisper (OLLAMA_GPU_OVERHEAD),
- "llm_gpu": "cpu" runs the model on the CPU only (num_gpu 0).
Interactive calls (dictation clean-up, wording) go before background ones (recap): a recap waits between items
while an interactive call is running.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("powertools.llm")
URL = os.environ.get("POWERTOOLS_OLLAMA", "http://127.0.0.1:11434")
DOWNLOAD = "https://ollama.com/download/windows"
SUGGESTED = ["gemma4:e2b", "gemma4:e4b"]
INTERACTIVE, BACKGROUND = 0, 1
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NS = 1e9

# The test prompt: a short work message with typical mistakes, the same task as the wording hotkey.
TEST_TEXT = ("Hi Mark, regarding to our yesterday meeting I send you the updated rules. Please let me know if "
             "you have any doubts or if something is not clear for you, we can discuss about it tomorrow. "
             "I am waiting for your feedback until Friday.")


class LLMError(RuntimeError):
    pass


def find_exe() -> str:
    found = shutil.which("ollama")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    p = Path(local) / "Programs" / "Ollama" / "ollama.exe"
    return str(p) if local and p.exists() else ""


def gpu_info() -> dict:
    """NVIDIA GPU name and VRAM in MB (total, used, free) from nvidia-smi; {} when there is none."""
    smi = shutil.which("nvidia-smi")
    if not smi and sys.platform == "win32":
        p = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        smi = str(p) if p.exists() else ""
    if not smi:
        return {}
    try:
        out = subprocess.run([smi, "--query-gpu=name,memory.total,memory.used,memory.free,driver_version",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=15,
                             creationflags=FLAGS).stdout.strip().splitlines()
    except Exception as e:
        return {"error": f"nvidia-smi failed: {type(e).__name__}"}
    if not out:
        return {"error": "nvidia-smi returned nothing"}
    name, total, used, free, driver = [p.strip() for p in out[0].split(",")][:5]
    try:
        return {"name": name, "total_mb": int(float(total)), "used_mb": int(float(used)), "free_mb": int(float(free)),
                "driver": driver}
    except ValueError:
        return {"name": name, "error": "could not read the memory figures"}


def parse_json(text: str) -> dict:
    """The model's JSON answer; tolerates a ```json fence around it."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    try:
        data = json.loads(t)
    except ValueError as e:
        raise LLMError(f"the model did not return JSON: {t[:120]}") from e
    if not isinstance(data, dict):
        raise LLMError("the model returned JSON, but not an object")
    return data


class Ollama:
    def __init__(self, settings, url: str = URL):
        self.settings, self.url = settings, url
        self._cond = threading.Condition()
        self._interactive = 0
        self._started = False
        self._avail = {"installed": False, "running": False, "at": 0.0}
        self._avail_lock = threading.Lock()

    # ------------------------------------------------------------------ HTTP
    def _req(self, method: str, path: str, body: dict | None = None, timeout: float = 10):
        req = urllib.request.Request(self.url + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", "")
            except Exception:
                msg = ""
            raise LLMError(f"Ollama HTTP {e.code}: {msg or e.reason}") from e
        except (urllib.error.URLError, OSError) as e:
            raise LLMError(f"Ollama is not running ({type(e).__name__})") from e

    def running(self) -> bool:
        try:
            self._req("GET", "/api/version", timeout=2)
            return True
        except LLMError:
            return False

    def ensure_running(self, wait: float = 15) -> bool:
        """Start `ollama serve` when Ollama is installed but its server is not up."""
        if self.running():
            return True
        exe = find_exe()
        if not exe:
            return False
        env = dict(os.environ)
        env.setdefault("OLLAMA_GPU_OVERHEAD", str(1024 ** 3))  # leave 1 GB of VRAM for Whisper
        log.info("starting %s serve", exe)
        subprocess.Popen([exe, "serve"], env=env, creationflags=FLAGS | getattr(subprocess, "DETACHED_PROCESS", 0),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        self._started = True
        t0 = time.time()
        while time.time() - t0 < wait:
            if self.running():
                return True
            time.sleep(0.5)
        return False

    def available(self, max_age: float = 20) -> dict:
        """{installed, running}, refreshed in the background at most every `max_age` s (the window polls often)."""
        if time.time() - self._avail["at"] > max_age and self._avail_lock.acquire(blocking=False):
            def refresh() -> None:
                try:
                    self._avail = {"installed": bool(find_exe()), "running": self.running(), "at": time.time()}
                finally:
                    self._avail_lock.release()
            if self._avail["at"]:
                threading.Thread(target=refresh, name="ollama-check", daemon=True).start()
            else:
                refresh()  # the first answer is real, not a default
        return {"installed": self._avail["installed"], "running": self._avail["running"]}

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        exe = find_exe()
        self._avail = {"installed": bool(exe), "running": self.running(), "at": time.time()}
        out: dict = {"installed": bool(exe), "exe": exe, "running": False, "model": self.settings["llm_model"],
                     "download": DOWNLOAD, "suggested": SUGGESTED, "gpu": gpu_info()}
        try:
            out["version"] = self._req("GET", "/api/version", timeout=2).get("version", "")
        except LLMError as e:
            out["error"] = str(e)
            return out
        out["running"] = True
        tags = self._req("GET", "/api/tags").get("models", [])
        out["models"] = [{"name": m.get("name", ""), "size_gb": round(m.get("size", 0) / 1e9, 1),
                          "params": (m.get("details") or {}).get("parameter_size", ""),
                          "quant": (m.get("details") or {}).get("quantization_level", "")} for m in tags]
        out["loaded"] = self.loaded()
        out["has_model"] = any(m["name"] == out["model"] for m in out["models"])
        return out

    def loaded(self) -> list[dict]:
        """Models in memory now, with the share that sits on the GPU."""
        try:
            ps = self._req("GET", "/api/ps", timeout=3).get("models", [])
        except LLMError:
            return []
        return [{"name": m.get("name", ""), "size_gb": round(m.get("size", 0) / 1e9, 1),
                 "vram_gb": round(m.get("size_vram", 0) / 1e9, 1),
                 "gpu_share": round(100 * m.get("size_vram", 0) / m["size"]) if m.get("size") else 0} for m in ps]

    def unload_all(self) -> None:
        """Free the GPU (Whisper is about to load a model). Quick no-op when Ollama is not running."""
        for m in self.loaded():
            try:
                self._req("POST", "/api/generate", {"model": m["name"], "keep_alive": 0}, timeout=10)
                log.info("unloaded %s to free the GPU", m["name"])
            except LLMError as e:
                log.warning("could not unload %s: %s", m["name"], e)

    # ------------------------------------------------------------------ chat
    def _gate(self, priority: int):
        llm = self

        class Gate:
            def __enter__(self):
                with llm._cond:
                    if priority == INTERACTIVE:
                        llm._interactive += 1
                    else:
                        llm._cond.wait_for(lambda: llm._interactive == 0, timeout=120)

            def __exit__(self, *exc):
                if priority == INTERACTIVE:
                    with llm._cond:
                        llm._interactive -= 1
                        llm._cond.notify_all()

        return Gate()

    def chat(self, system: str, user: str, schema: dict | None = None, priority: int = INTERACTIVE,
             max_tokens: int = 800, num_ctx: int = 4096, timeout: float = 180, model: str = "") -> tuple[str, dict]:
        """One answer (JSON text when `schema` is given) and timing stats. Raises LLMError."""
        model = model or self.settings["llm_model"]
        options = {"temperature": 0, "num_predict": max_tokens, "num_ctx": num_ctx}
        if self.settings["llm_gpu"] == "cpu":
            options["num_gpu"] = 0
        body = {"model": model, "stream": False, "think": False, "options": options,
                "keep_alive": f"{self.settings['llm_keep_alive_min']}m",
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if schema:
            body["format"] = schema
        if not self.running() and not self.ensure_running():
            raise LLMError("Ollama is not installed or not running (see the Local AI tab)")
        with self._gate(priority):
            try:
                r = self._req("POST", "/api/chat", body, timeout=timeout)
            except LLMError as e:
                if "think" not in str(e).lower():
                    raise
                body.pop("think")  # a model without the thinking switch
                r = self._req("POST", "/api/chat", body, timeout=timeout)
        stats = {"model": model, "load_s": round(r.get("load_duration", 0) / NS, 1),
                 "total_s": round(r.get("total_duration", 0) / NS, 1),
                 "prompt_tokens": r.get("prompt_eval_count", 0), "tokens": r.get("eval_count", 0),
                 "tok_s": round(r.get("eval_count", 0) / (r.get("eval_duration") / NS), 1) if r.get("eval_duration") else 0,
                 "prompt_tok_s": round(r.get("prompt_eval_count", 0) / (r.get("prompt_eval_duration") / NS))
                 if r.get("prompt_eval_duration") else 0}
        return (r.get("message") or {}).get("content", ""), stats

    # ------------------------------------------------------------------ test + pull
    def test(self, model: str = "") -> dict:
        """Can this laptop use the model? Load it, run the wording task once, measure speed and the GPU share."""
        from . import wording

        model = model or self.settings["llm_model"]
        before = gpu_info()
        t0 = time.time()
        content, stats = self.chat(wording.SYSTEM, TEST_TEXT, wording.SCHEMA, max_tokens=400, model=model)
        took = round(time.time() - t0, 1)
        after = gpu_info()
        loaded = next((m for m in self.loaded() if m["name"] == model), {})
        try:
            answer = parse_json(content)
            ok_json = isinstance(answer.get("issues"), list) and bool(answer.get("improved"))
        except LLMError:
            answer, ok_json = {}, False
        share = loaded.get("gpu_share", 0)
        tok_s = stats["tok_s"]
        if tok_s >= 20:
            verdict = "fast: fine for dictation clean-up, wording and recap"
        elif tok_s >= 8:
            verdict = "usable: wording and recap are fine; dictation clean-up adds a few seconds"
        else:
            verdict = "slow: try gemma4:e2b, set the dictation model to Parakeet V3 (frees the GPU) or run on the CPU"
        if loaded and share < 60 and self.settings["llm_gpu"] != "cpu":
            verdict += f"; only {share}% of the model fits on the GPU"
        return {"model": model, "took_s": took, **stats, "gpu_share": share, "size_gb": loaded.get("size_gb", 0),
                "vram_gb": loaded.get("vram_gb", 0), "gpu_before": before, "gpu_after": after, "json_ok": ok_json,
                "issues": len(answer.get("issues") or []), "improved": answer.get("improved", ""), "verdict": verdict}

    def pull(self, model: str, progress) -> None:
        """Download a model; progress(status, completed_bytes, total_bytes)."""
        if not self.ensure_running():
            raise LLMError("Ollama is not installed or not running")
        req = urllib.request.Request(self.url + "/api/pull", method="POST",
                                     data=json.dumps({"model": model, "stream": True}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=3600) as r:
                for line in r:
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    if d.get("error"):
                        raise LLMError(d["error"])
                    progress(d.get("status", ""), d.get("completed", 0), d.get("total", 0))
        except urllib.error.HTTPError as e:
            raise LLMError(f"Ollama HTTP {e.code}") from e
        except (urllib.error.URLError, OSError) as e:
            raise LLMError(f"Ollama is not running ({type(e).__name__})") from e
