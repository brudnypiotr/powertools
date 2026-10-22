"""About and Update: version, changelog from git, update by fast-forward pull and restart.

The copy is a git clone; Update = `git pull --ff-only`, then the launcher is started again (it repeats the setup
when setup/version.txt changed) and this process quits.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__

ROOT = Path(__file__).resolve().parents[1]
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class GitError(RuntimeError):
    pass


def git_exe() -> str | None:
    found = shutil.which("git")
    if found:
        return found
    for p in (Path(os.environ.get("LOCALAPPDATA", "")) / "powertools" / "git" / "cmd" / "git.exe",  # setup's Portable Git
              Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "cmd" / "git.exe"):
        if os.environ.get("LOCALAPPDATA") and p.exists():
            return str(p)
    return None


def git(*args: str, timeout: int = 60) -> str:
    exe = git_exe()
    if not exe:
        raise GitError("git not found")
    r = subprocess.run([exe, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, creationflags=FLAGS)
    if r.returncode != 0:
        raise GitError((r.stderr or r.stdout).strip()[-400:] or f"git {args[0]} failed")
    return r.stdout.strip()


def _log(rng: str, n: int = 40) -> list[dict]:
    out = git("log", rng, f"-n{n}", "--date=short", "--pretty=format:%h%x09%ad%x09%s")
    items = []
    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            items.append({"commit": parts[0], "date": parts[1], "subject": parts[2]})
    return items


def about(fetch: bool = False) -> dict:
    info = {"version": __version__, "python": sys.version.split()[0], "root": str(ROOT), "git": bool(git_exe())}
    try:
        info["commit"] = git("rev-parse", "--short", "HEAD")
        info["branch"] = git("rev-parse", "--abbrev-ref", "HEAD")
        info["changelog"] = _log("HEAD", 40)
        info["dirty"] = bool(git("status", "--porcelain", "--untracked-files=no"))
        if fetch:
            git("fetch", "--quiet", "origin", timeout=40)
        upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        info["upstream"] = upstream
        info["pending"] = _log(f"HEAD..{upstream}", 50)
        info["fetched"] = fetch
    except (GitError, subprocess.TimeoutExpired) as e:
        info["error"] = str(e)
    return info


def update() -> dict:
    before = git("rev-parse", "--short", "HEAD")
    git("pull", "--ff-only", "--quiet", timeout=120)
    after = git("rev-parse", "--short", "HEAD")
    return {"before": before, "after": after, "changed": before != after}


RESTART = """@echo off
rem Written by powertools before an update restart: wait until the old process is gone, then start the launcher.
set "PORTFILE={portfile}"
set /a N=0
:wait
if not exist "%PORTFILE%" goto start
set /a N+=1
if %N% GEQ 30 goto start
ping -n 2 127.0.0.1 >nul
goto wait
:start
ping -n 2 127.0.0.1 >nul
echo %date% %time% restarting >> "{log}"
call "{launcher}" --no-browser >> "{log}" 2>&1
echo %date% %time% launcher exit %errorlevel% >> "{log}"
"""


def restart_later(port_file: Path) -> Path:
    """Start the launcher again once this process has quit. A script file avoids cmd's quoting rules
    (a command line with nested quotes reached cmd as \\" and did nothing)."""
    local = ROOT / "local"
    local.mkdir(exist_ok=True)
    script, log = local / "restart.cmd", local / "restart.log"
    script.write_text(RESTART.format(portfile=port_file, log=log, launcher=ROOT / "powertools.cmd"), encoding="ascii",
                      errors="replace", newline="\r\n")  # cmd needs CRLF for labels
    if sys.platform == "win32":
        detached = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(["cmd", "/c", str(script)], cwd=ROOT, creationflags=detached, close_fds=True)
    return script
