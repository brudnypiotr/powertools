"""Local language model through Ollama (http://127.0.0.1:11434): nothing leaves the laptop.

Ollama is unpacked per user into %LOCALAPPDATA%\\powertools\\ollama from the official GitHub release ZIP (no installer,
no admin rights) and runs its own server; powertools only talks HTTP to it (stdlib, no new
packages). The GPU is shared with Whisper (6 GB holds both only when the model is small), so:
- Whisper asks the model to unload before it loads a model on the GPU (Engine.before_gpu),
- a server started by powertools keeps 1 GB of VRAM free for Whisper (OLLAMA_GPU_OVERHEAD),
- "llm_gpu": "cpu" runs the model on the CPU only (num_gpu 0).
Interactive calls (dictation clean-up, wording) go before background ones (recap): a recap waits between items
while an interactive call is running.
"""

from __future__ import annotations

import hashlib
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
import zipfile
from pathlib import Path

from .config import data_dir

log = logging.getLogger("powertools.llm")
URL = os.environ.get("POWERTOOLS_OLLAMA", "http://127.0.0.1:11434")
DOWNLOAD = "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip"
HF_BASE = "https://huggingface.co"
SUGGESTED = ["gemma4:e2b", "gemma4:e4b"]
HF_MODELS: dict[str, dict] = {
    "gemma4:e2b": {
        "repo": "google/gemma-4-E2B-it-qat-q4_0-gguf",
        "tag": "latest",
        "file": "gemma-4-E2B_q4_0-it.gguf",
        "digest": "sha256:fa401b55b07ee70a54c6dae3903c783a6e65064312529ea57175cb5f8dec6634",
        "size": 3349516256,
        "template": "{{ .Prompt }}",
        "renderer": "gemma4",
        "parser": "gemma4",
        "parameters": {"temperature": 1, "top_k": 64, "top_p": 0.95},
    },
    "gemma4:e4b": {
        "repo": "google/gemma-4-E4B-it-qat-q4_0-gguf",
        "tag": "latest",
        "file": "gemma-4-E4B_q4_0-it.gguf",
        "digest": "sha256:676c35070db6dbe52f93e9c864ee0fba4eddea94b9c875d9cb10daff453fbaee",
        "size": 5154941280,
        "template": "{{ .Prompt }}",
        "renderer": "gemma4",
        "parser": "gemma4",
        "parameters": {"temperature": 1, "top_k": 64, "top_p": 0.95},
    },
}
INTERACTIVE, BACKGROUND = 0, 1
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NS = 1e9

# The test prompt: a short work message with typical mistakes, the same task as the wording hotkey.
TEST_TEXT = ("Hi Mark, regarding to our yesterday meeting I send you the updated rules. Please let me know if "
             "you have any doubts or if something is not clear for you, we can discuss about it tomorrow. "
             "I am waiting for your feedback until Friday.")


class LLMError(RuntimeError):
    pass


def ollama_dir() -> Path:
    return data_dir() / "ollama"


def models_dir() -> Path:
    custom = os.environ.get("OLLAMA_MODELS", "").strip()
    return Path(custom) if custom else Path.home() / ".ollama" / "models"


def _hf_spec(model: str) -> dict | None:
    m = (model or "").strip()
    if m in HF_MODELS:
        return dict(HF_MODELS[m])
    for prefix in ("hf.co/", "huggingface.co/"):
        if m.startswith(prefix):
            m = m[len(prefix):]
            break
    if "/" not in m:
        return None
    repo, _, tag = m.partition(":")
    tag = tag or "Q4_K_M"
    return {"repo": repo, "tag": tag, "file": f"{repo.split('/')[-1]}-{tag}.gguf"}


def find_exe() -> str:
    local = os.environ.get("LOCALAPPDATA", "")
    for p in (ollama_dir() / "ollama.exe",
              Path(local) / "powertools" / "ollama" / "ollama.exe" if local else None,
              Path(local) / "Programs" / "Ollama" / "ollama.exe" if local else None):
        if p and p.exists():
            return str(p)
    return shutil.which("ollama") or ""


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
        env.setdefault("OLLAMA_MODELS", str(models_dir()))
        log.info("starting %s serve", exe)
        subprocess.Popen([exe, "serve"], cwd=str(Path(exe).parent), env=env,
                         creationflags=FLAGS | getattr(subprocess, "DETACHED_PROCESS", 0),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        self._started = True
        t0 = time.time()
        while time.time() - t0 < wait:
            if self.running():
                return True
            time.sleep(0.5)
        return False

    def install(self, progress, url: str = DOWNLOAD) -> str:
        """Download the official Windows ZIP from GitHub, unpack to %LOCALAPPDATA%\\powertools\\ollama and start it."""
        dest = ollama_dir()
        dl_dir = data_dir() / "downloads"
        dl_dir.mkdir(parents=True, exist_ok=True)
        part = dl_dir / "ollama-windows-amd64.zip.part"
        archive = dl_dir / "ollama-windows-amd64.zip"
        total = 0
        if not (archive.exists() and zipfile.is_zipfile(archive)):
            req = urllib.request.Request(url, headers={"User-Agent": "powertools"})
            try:
                with urllib.request.urlopen(req, timeout=3600) as r, part.open("wb") as f:
                    total = int(r.headers.get("Content-Length") or 0)
                    done = 0
                    progress("downloading", 0, total)
                    while True:
                        chunk = r.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        progress("downloading", done, total)
            except urllib.error.HTTPError as e:
                part.unlink(missing_ok=True)
                raise LLMError(f"GitHub HTTP {e.code}: {e.reason}") from e
            except (urllib.error.URLError, OSError) as e:
                part.unlink(missing_ok=True)
                raise LLMError(f"Download failed ({type(e).__name__}: {e})") from e
            if not zipfile.is_zipfile(part):
                part.unlink(missing_ok=True)
                raise LLMError("Downloaded file is not a valid ZIP archive")
            os.replace(part, archive)
        else:
            total = archive.stat().st_size
        progress("unpacking", total, total)
        stage = dest.with_name("ollama.new")
        old = dest.with_name("ollama.old")
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(old, ignore_errors=True)
        try:
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(stage)
        except (zipfile.BadZipFile, OSError) as e:
            shutil.rmtree(stage, ignore_errors=True)
            archive.unlink(missing_ok=True)
            raise LLMError(f"Could not unpack Ollama ZIP: {e}") from e
        archive.unlink(missing_ok=True)
        if not (stage / "ollama.exe").exists():
            found = next(stage.rglob("ollama.exe"), None)
            if not found:
                shutil.rmtree(stage, ignore_errors=True)
                raise LLMError("ollama.exe was not found in the ZIP archive")
            inner = dest.with_name("ollama.inner")
            shutil.rmtree(inner, ignore_errors=True)
            os.replace(found.parent, inner)
            shutil.rmtree(stage, ignore_errors=True)
            os.replace(inner, stage)
        try:
            if dest.exists():
                os.replace(dest, old)
            os.replace(stage, dest)
        except OSError as e:
            if not dest.exists() and old.exists():
                os.replace(old, dest)
            shutil.rmtree(stage, ignore_errors=True)
            raise LLMError(f"Could not replace {dest}: close Ollama if it is running ({e})") from e
        shutil.rmtree(old, ignore_errors=True)
        progress("starting", total, total)
        running = self.ensure_running()
        self._avail = {"installed": True, "running": running, "at": time.time()}
        return str(dest / "ollama.exe")

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

    def _ensure_blob(self, blob_path: Path, digest: str) -> None:
        """Make sure Ollama sees the downloaded blob even if OLLAMA_MODELS points elsewhere."""
        req = urllib.request.Request(f"{self.url}/api/blobs/{digest}", method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=10):
                return
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise LLMError(f"Ollama blob check HTTP {e.code}") from e
        except (urllib.error.URLError, OSError) as e:
            raise LLMError(f"Ollama is not running ({type(e).__name__})") from e
        with blob_path.open("rb") as f:
            up = urllib.request.Request(
                f"{self.url}/api/blobs/{digest}", method="POST", data=f,
                headers={"Content-Length": str(blob_path.stat().st_size), "Content-Type": "application/octet-stream"},
            )
            try:
                with urllib.request.urlopen(up, timeout=3600):
                    pass
            except urllib.error.HTTPError as e:
                raise LLMError(f"Ollama blob upload HTTP {e.code}") from e
            except (urllib.error.URLError, OSError) as e:
                raise LLMError(f"Ollama blob upload failed ({type(e).__name__})") from e

    def _pull_hf(self, model: str, spec: dict, progress) -> None:
        """Download a GGUF model in Python from Hugging Face and register it locally in Ollama."""
        repo, tag = spec["repo"], spec["tag"]
        manifest_digest = ""
        manifest_size = 0
        mreq = urllib.request.Request(
            f"{HF_BASE}/v2/{repo}/manifests/{tag}",
            headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json", "User-Agent": "powertools"},
        )
        try:
            with urllib.request.urlopen(mreq, timeout=30) as r:
                manifest = json.loads(r.read() or b"{}")
            layer = next((x for x in (manifest.get("layers") or [])
                          if x.get("mediaType") == "application/vnd.ollama.image.model"), {})
            manifest_digest = layer.get("digest", "")
            manifest_size = int(layer.get("size") or 0)
        except Exception as e:
            log.info("HF v2 manifest lookup for %s:%s skipped (%s)", repo, tag, e)
        pinned = spec.get("digest", "")
        digest = pinned or manifest_digest
        expected_hex = digest.split(":", 1)[-1] if digest.startswith("sha256:") else ""
        total = int(spec.get("size") or manifest_size or 0)
        urls: list[str] = []
        if digest:
            urls.append(f"{HF_BASE}/v2/{repo}/blobs/{digest}")
        if spec.get("file"):
            urls.append(f"{HF_BASE}/{repo}/resolve/main/{spec['file']}")
        if not urls:
            raise LLMError(f"Could not resolve a Hugging Face download URL for {model}")
        blobs = models_dir() / "blobs"
        blobs.mkdir(parents=True, exist_ok=True)
        existing = blobs / f"sha256-{expected_hex}" if expected_hex else None
        if existing and existing.exists() and (not total or existing.stat().st_size == total):
            actual_hex = expected_hex
            blob_path = existing
            total = existing.stat().st_size
            progress("verifying cached model", total, total)
        else:
            part = blobs / f"sha256-{expected_hex or 'download'}.part"
            last_err: Exception | None = None
            actual_hex = ""
            for url in urls:
                req = urllib.request.Request(url, headers={"User-Agent": "powertools"})
                try:
                    with urllib.request.urlopen(req, timeout=3600) as r, part.open("wb") as f:
                        total = int(r.headers.get("Content-Length") or total or 0)
                        done = 0
                        h = hashlib.sha256()
                        magic = b""
                        progress("downloading from Hugging Face", 0, total)
                        while True:
                            chunk = r.read(256 * 1024)
                            if not chunk:
                                break
                            if len(magic) < 4:
                                magic += chunk[: 4 - len(magic)]
                            f.write(chunk)
                            h.update(chunk)
                            done += len(chunk)
                            progress("downloading from Hugging Face", done, total)
                    if magic != b"GGUF":
                        part.unlink(missing_ok=True)
                        raise LLMError("Downloaded file is not a valid GGUF model")
                    if total and done != total:
                        part.unlink(missing_ok=True)
                        raise LLMError(f"Incomplete model download ({done} of {total} bytes)")
                    actual_hex = h.hexdigest()
                    if expected_hex and actual_hex != expected_hex:
                        part.unlink(missing_ok=True)
                        raise LLMError("Downloaded model failed SHA-256 verification")
                    last_err = None
                    break
                except LLMError as e:
                    last_err = e
                except urllib.error.HTTPError as e:
                    part.unlink(missing_ok=True)
                    last_err = LLMError(f"Hugging Face HTTP {e.code}: {e.reason}")
                except (urllib.error.URLError, OSError) as e:
                    part.unlink(missing_ok=True)
                    last_err = LLMError(f"Hugging Face download failed ({type(e).__name__}: {e})")
            if last_err is not None or not actual_hex:
                raise last_err if isinstance(last_err, LLMError) else LLMError("Hugging Face download failed")
            blob_path = blobs / f"sha256-{actual_hex}"
            os.replace(part, blob_path)
        progress("registering model in Ollama", total, total)
        self._ensure_blob(blob_path, f"sha256:{actual_hex}")
        body: dict = {
            "model": model,
            "files": {spec.get("file") or "model.gguf": f"sha256:{actual_hex}"},
            "stream": False,
        }
        for k in ("template", "renderer", "parser", "parameters"):
            if spec.get(k):
                body[k] = spec[k]
        try:
            self._req("POST", "/api/create", body, timeout=600)
        except LLMError as e:
            if "renderer" not in str(e).lower() and "parser" not in str(e).lower():
                raise
            body.pop("renderer", None)
            body.pop("parser", None)
            self._req("POST", "/api/create", body, timeout=600)
        progress("success", total, total)

    def pull(self, model: str, progress) -> None:
        """Download a model; progress(status, completed_bytes, total_bytes)."""
        if not self.ensure_running():
            raise LLMError("Ollama is not installed or not running")
        spec = _hf_spec(model)
        if spec:
            self._pull_hf(model, spec, progress)
            return
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
