"""About and Update: version, changelog from git, update by fast-forward pull and restart.

The copy is a git clone; Update = `git pull --ff-only`, then the launcher is started again (it repeats the setup
when setup/version.txt changed) and this process quits.
"""

from __future__ import annotations

import json
import os
import re
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
        if fetch and not has_commit():
            attach()  # a half-connected ZIP copy (setup's fetch failed): finish connecting it now
        info["commit"] = git("rev-parse", "--short", "HEAD")
        info["branch"] = git("rev-parse", "--abbrev-ref", "HEAD")
        info["remote"] = git("remote", "get-url", "origin")
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
        if git_exe() and not has_commit():
            info["error"] = f"this copy is not connected to the repository yet (internet or proxy?): {e}"
    return info


def has_commit(root: Path = ROOT) -> bool:
    """False for a git repo whose HEAD has no commit yet (`git init` done, fetch never finished)."""
    exe = git_exe()
    if not exe:
        return False
    r = subprocess.run([exe, "rev-parse", "--verify", "-q", "HEAD"], cwd=root, capture_output=True, text=True,
                       creationflags=FLAGS)
    return r.returncode == 0


def update_source(root: Path = ROOT) -> dict:
    """Remote and branch from setup/site.json (an organisation's own copy has one), else from setup/sources.json."""
    u: dict = {}
    for name in ("sources.json", "site.json"):
        try:
            u = {**u, **(json.loads((root / "setup" / name).read_text(encoding="utf-8")).get("update") or {})}
        except (OSError, ValueError):
            pass
    return {"remote": u.get("remote") or "https://github.com/brudnypiotr/powertools.git", "branch": u.get("branch") or "main"}


def attach(root: Path = ROOT) -> dict:
    """Make a ZIP copy a clone of the official remote, or add the remote when a clone has none, so Update works.
    Never changes an existing origin (a developer's SSH remote stays). Ignored files (.venv, local/) are untouched."""
    exe = git_exe()
    if not exe:
        raise GitError("git not found")

    def g(*args: str, timeout: int = 60) -> str:
        r = subprocess.run([exe, *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=FLAGS)
        if r.returncode != 0:
            raise GitError((r.stderr or r.stdout).strip()[-400:] or f"git {args[0]} failed")
        return r.stdout.strip()

    src = update_source(root)
    if not (root / ".git").exists():
        g("init", "-q")
        g("symbolic-ref", "HEAD", f"refs/heads/{src['branch']}")  # = init -b, which git before 2.28 lacks
        g("remote", "add", "origin", src["remote"])
        g("fetch", "-q", "origin", src["branch"], timeout=600)
        g("reset", "-q", "--hard", f"origin/{src['branch']}")
        g("branch", "-q", "-u", f"origin/{src['branch']}")
        return {"attached": True, "remote": src["remote"], "commit": g("rev-parse", "--short", "HEAD"),
                "message": f"ZIP copy connected to {src['remote']} ({src['branch']})"}
    if "origin" not in g("remote").split():
        g("remote", "add", "origin", src["remote"])
        g("fetch", "-q", "origin", src["branch"], timeout=600)
        try:
            g("branch", "-q", "-u", f"origin/{src['branch']}")
        except GitError:
            pass  # another branch is checked out: Update follows its own upstream when it has one
        return {"attached": True, "remote": src["remote"], "message": f"origin added: {src['remote']}"}
    url = g("remote", "get-url", "origin")
    if not has_commit(root):  # an earlier attach stopped after init (fetch failed): finish it, origin stays as it is
        g("fetch", "-q", "origin", src["branch"], timeout=600)
        g("reset", "-q", "--hard", f"origin/{src['branch']}")
        g("branch", "-q", "-u", f"origin/{src['branch']}")
        return {"attached": True, "remote": url, "commit": g("rev-parse", "--short", "HEAD"),
                "message": f"connection to {url} ({src['branch']}) finished"}
    return {"attached": False, "remote": url, "message": f"already connected to {url}"}


def switch(remote: str, root: Path = ROOT) -> dict:
    """Take updates from another repository from now on. Only when it can be read and continues this copy's history
    (its branch contains the current commit), so the next Update is an ordinary fast-forward. Local files stay."""
    exe = git_exe()
    if not exe:
        raise GitError("git not found")

    def g(*args: str, timeout: int = 120) -> str:
        r = subprocess.run([exe, *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=FLAGS)
        if r.returncode != 0:
            raise GitError((r.stderr or r.stdout).strip()[-400:] or f"git {args[0]} failed")
        return r.stdout.strip()

    branch = update_source(root)["branch"]
    g("fetch", "-q", remote, branch, timeout=600)     # asks for the sign-in when the repository is private
    try:
        g("merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD")
    except GitError as e:
        raise GitError("that repository does not continue this version: update first, or ask who maintains it") from e
    old = g("remote", "get-url", "origin")
    g("remote", "set-url", "origin", remote)
    try:
        g("fetch", "-q", "origin", timeout=600)
        g("branch", "-q", "-u", f"origin/{branch}")
    except GitError:
        g("remote", "set-url", "origin", old)
        raise
    return {"remote": remote, "before": old}


def _previous_file() -> Path:
    return ROOT / "local" / "update-previous.json"


def remember(commit: str) -> None:
    """The version before this update, for the way back."""
    text = git("show", f"{commit}:powertools/__init__.py")
    m = re.search(r'__version__ = "([^"]+)"', text)
    _previous_file().parent.mkdir(exist_ok=True)
    _previous_file().write_text(json.dumps({"commit": commit, "version": m[1] if m else commit[:7]}), encoding="utf-8")


def previous() -> dict:
    """{commit, version} of the version before the last update; {} when there is none."""
    try:
        return json.loads(_previous_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def rollback() -> dict:
    p = previous()
    if not p:
        raise GitError("no earlier version is known on this computer")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise GitError("files of this copy were changed by hand: put them back first")
    before = git("rev-parse", "--short", "HEAD")
    git("reset", "--quiet", "--hard", p["commit"])
    _previous_file().unlink()
    return {"before": before, "after": p["commit"][:7], "version": p["version"], "changed": True}


def update() -> dict:
    full, before = git("rev-parse", "HEAD"), git("rev-parse", "--short", "HEAD")
    git("pull", "--ff-only", "--quiet", timeout=120)
    after = git("rev-parse", "--short", "HEAD")
    if before != after:
        remember(full)
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
