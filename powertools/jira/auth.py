"""Where the Jira site and token come from. The default is the same token woynich-wiki uses, so nothing new to set up.

Token, first found wins:
  1. a token pasted in the Jira tab (kept encrypted with DPAPI, like the Graph token),
  2. woynich-wiki's secret store: Windows Credential Manager entry `jira-token` of service `woynich-wiki`
     (written by `kb secrets set jira-token` / Settings > Keys and tokens),
  3. the generic Windows credential `copilot-jira` (user = Atlassian e-mail, password = token), the jira-snapshot skill's,
  4. JIRA_API_TOKEN (and JIRA_EMAIL) environment variables.
Site and e-mail: Settings > Jira, else `jira.base_url` / `jira.email` from woynich-wiki's instance/config/profile.yaml.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from ..config import data_dir
from ..graph.token import TokenStore, _dpapi

REPO = Path(__file__).resolve().parents[2]


@dataclass
class Credentials:
    base_url: str = ""
    email: str = ""
    token: str = ""
    token_source: str = ""
    site_source: str = ""


class JiraTokenStore(TokenStore):
    """Same DPAPI file as the Graph token, but a Jira token is not a JWT and never expires on its own."""

    def set(self, token: str) -> None:
        token = token.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if len(token) < 10 or any(c.isspace() for c in token):
            raise ValueError("this does not look like a Jira token")
        data = token.encode("utf-8")
        self.path.write_bytes(_dpapi(data, True) if sys.platform == "win32" else data)
        if sys.platform != "win32":
            os.chmod(self.path, 0o600)
        self._token = token

    def get(self) -> str:
        return self._token

    def expires_in(self) -> float:
        return float("inf") if self._token else 0


def windows_credential(target: str) -> tuple[str, str] | None:
    """(user name, secret) of a generic credential in Windows Credential Manager; None elsewhere or when missing."""
    if sys.platform != "win32":
        return None
    import ctypes
    import ctypes.wintypes as w

    class ATTR(ctypes.Structure):
        _fields_ = [("Keyword", w.LPWSTR), ("Flags", w.DWORD), ("ValueSize", w.DWORD), ("Value", ctypes.POINTER(ctypes.c_ubyte))]

    class CRED(ctypes.Structure):
        _fields_ = [("Flags", w.DWORD), ("Type", w.DWORD), ("TargetName", w.LPWSTR), ("Comment", w.LPWSTR),
                    ("LastWritten", w.FILETIME), ("CredentialBlobSize", w.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", w.DWORD), ("AttributeCount", w.DWORD), ("Attributes", ctypes.POINTER(ATTR)),
                    ("TargetAlias", w.LPWSTR), ("UserName", w.LPWSTR)]
    adv = ctypes.windll.advapi32
    cred = ctypes.POINTER(CRED)()
    if not adv.CredReadW(ctypes.c_wchar_p(target), 1, 0, ctypes.byref(cred)):  # 1 = CRED_TYPE_GENERIC
        return None
    try:
        c = cred.contents
        if not c.CredentialBlobSize or not c.CredentialBlob:
            return None
        raw = ctypes.string_at(c.CredentialBlob, int(c.CredentialBlobSize))
        try:
            secret = raw.decode("utf-16-le").rstrip("\x00")
        except UnicodeDecodeError:
            secret = raw.decode("utf-8", errors="replace").rstrip("\x00")
        return c.UserName or "", secret
    finally:
        adv.CredFree(cred)


def woynich_keyring_token(name: str = "jira-token", service: str = "woynich-wiki") -> str:
    """What `keyring.get_password(service, name)` returns on Windows, without the keyring package: the entry sits under
    `name@service` once another secret of the service was saved after it, else under `service` with user `name`."""
    found = windows_credential(f"{name}@{service}")
    if found and found[1]:
        return found[1]
    found = windows_credential(service)
    if found and found[0] == name and found[1]:
        return found[1]
    return ""


def woynich_dirs() -> list[Path]:
    home = Path.home()
    out = []
    if os.environ.get("KB_INSTANCE"):
        out.append(Path(os.environ["KB_INSTANCE"]).parent)
    out += [REPO.parent / "woynich-wiki"] + [home / p / "woynich-wiki" for p in ("", "Projects", "Documents", "Desktop",
                                                                                 "source/repos", "repos", "git")]
    return out


def read_profile_jira(text: str) -> dict:
    """`jira:` block of a YAML profile (base_url, email) without a YAML library: top-level key, indented scalars."""
    out, inside = {}, False
    for line in text.splitlines():
        if re.match(r"^jira:\s*(#.*)?$", line):
            inside = True
            continue
        if inside:
            if line and not line[0].isspace() and not line.lstrip().startswith("#"):
                break
            m = re.match(r"^\s+(base_url|email):\s*['\"]?([^'\"#]*?)['\"]?\s*(#.*)?$", line)
            if m and m.group(2):
                out[m.group(1)] = m.group(2).strip()
    return out


def woynich_profile() -> tuple[dict, str]:
    for d in woynich_dirs():
        p = d / "instance" / "config" / "profile.yaml"
        try:
            if p.is_file():
                found = read_profile_jira(p.read_text(encoding="utf-8"))
                if found.get("base_url"):
                    return found, str(p)
        except OSError:
            continue
    return {}, ""


def token_store() -> JiraTokenStore:
    return JiraTokenStore(data_dir() / "jira-token.bin")


def resolve(settings, store: JiraTokenStore | None = None) -> Credentials:
    c = Credentials()
    prof, prof_path = woynich_profile()
    if settings["jira_url"]:
        c.base_url, c.site_source = settings["jira_url"], "Settings"
    elif prof.get("base_url"):
        c.base_url, c.site_source = prof["base_url"], f"woynich-wiki ({prof_path})"
    elif os.environ.get("JIRA_BASE_URL"):
        c.base_url, c.site_source = os.environ["JIRA_BASE_URL"], "JIRA_BASE_URL"
    c.base_url = c.base_url.rstrip("/")
    c.email = settings["jira_email"] or prof.get("email", "") or os.environ.get("JIRA_EMAIL", "")

    store = store or token_store()
    if store.get():
        c.token, c.token_source = store.get(), "pasted in powertools"
    elif tok := woynich_keyring_token():
        c.token, c.token_source = tok, "woynich-wiki (Keys and tokens)"
    elif found := windows_credential("copilot-jira"):
        c.email = c.email or found[0]
        c.token, c.token_source = found[1], "Windows credential copilot-jira"
    elif os.environ.get("JIRA_API_TOKEN"):
        c.token, c.token_source = os.environ["JIRA_API_TOKEN"], "JIRA_API_TOKEN"
    return c
