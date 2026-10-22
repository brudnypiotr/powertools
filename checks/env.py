"""H: the laptop runs everything in user space (no admin), Python works from LOCALAPPDATA, GPU is usable,
and the hosts we need are reachable through the system proxy."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

from .common import FAIL, IS_WIN, OK, SKIP, WARN, Context

HOSTS = {
    "graph": "https://graph.microsoft.com/v1.0/$metadata",
    "pypi": "https://pypi.org/simple/pip/",
    "huggingface": "https://huggingface.co/api/models/Systran/faster-whisper-small",
    "github": "https://github.com/",
}


def http_status(url: str, timeout: int = 10) -> str:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "powertools-check"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return str(r.status)
    except urllib.error.HTTPError as e:
        return str(e.code)  # any HTTP answer means the network path works
    except Exception as e:
        return f"unreachable ({type(e).__name__})"


def run(ctx: Context) -> None:
    ctx.add("env.os", OK, f"{platform.system()} {platform.release()} {platform.version()}")
    ctx.add("env.powershell", OK if os.environ.get("POWERTOOLS_PS_VERSION") else SKIP,
            f"PS {os.environ.get('POWERTOOLS_PS_VERSION', '?')}, execution policy {os.environ.get('POWERTOOLS_EXEC_POLICY', '?')}")

    if IS_WIN:
        import ctypes
        admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        ctx.add("env.admin", WARN if admin else OK, "running elevated (unexpected)" if admin else "not elevated, as expected")
    else:
        ctx.add("env.admin", SKIP, "not Windows")

    exe = sys.executable
    local = os.environ.get("LOCALAPPDATA", "")
    where = "under LOCALAPPDATA" if local and exe.lower().startswith(local.lower()) else "elsewhere"
    ctx.add("env.python", OK, f"{platform.python_version()} ({where}; AppLocker allowed it to start)")

    try:
        import tkinter  # noqa: F401  (needed for the recording overlay; missing in embeddable Python)
        ctx.add("env.tkinter", OK, "available")
    except Exception as e:
        ctx.add("env.tkinter", WARN, f"missing ({type(e).__name__}); overlay would need another approach")

    proxies = urllib.request.getproxies()
    ctx.add("env.proxy", OK, ("system proxy for " + ",".join(sorted(proxies))) if proxies else "no proxy configured")

    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run([smi, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=15).stdout.strip()
            ctx.add("env.gpu", OK if out else WARN, out or "nvidia-smi returned nothing")
        except Exception as e:
            ctx.add("env.gpu", WARN, f"nvidia-smi failed ({type(e).__name__})")
    else:
        ctx.add("env.gpu", WARN if IS_WIN else SKIP, "nvidia-smi not found (no NVIDIA driver on PATH)")

    base = local or os.path.expanduser("~")
    free_gb = shutil.disk_usage(base).free / 1e9
    ctx.add("env.disk", OK if free_gb > 10 else WARN, f"{free_gb:.0f} GB free on the LOCALAPPDATA drive")

    if not ctx.interactive and not IS_WIN:
        ctx.add("env.network", SKIP, "dry run")
        return
    for name, url in HOSTS.items():
        status = http_status(url)
        ctx.add(f"env.net.{name}", OK if status.isdigit() else FAIL, f"HTTP {status}" if status.isdigit() else status)
