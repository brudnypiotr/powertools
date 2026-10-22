"""Where the Jira site, e-mail and token come from. Nothing here needs another app to be installed.

Site and e-mail: Settings > Jira (else JIRA_BASE_URL / JIRA_EMAIL).
Token, first found wins:
  1. pasted in the Jira tab, kept encrypted with DPAPI like the Graph token,
  2. the shared Windows Credential Manager entry `jira-token` (service `woynich-wiki`), read live when it exists,
  3. JIRA_API_TOKEN.
Jira Cloud: API token + e-mail. Data Center: personal access token.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from ..config import data_dir
from ..graph.token import TokenStore, _dpapi


@dataclass
class Credentials:
    base_url: str = ""
    email: str = ""
    token: str = ""
    token_source: str = ""
    site_source: str = ""


class JiraTokenStore(TokenStore):
    """Same DPAPI file as the Graph token, but a Jira token is not a JWT and does not expire on a known date."""

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


def shared_token(name: str = "jira-token", service: str = "woynich-wiki") -> str:
    """What `keyring.get_password(service, name)` returns on Windows, without the keyring package: the entry sits under
    `name@service` once another secret of the service was saved after it, else under `service` with user `name`."""
    found = windows_credential(f"{name}@{service}")
    if found and found[1]:
        return found[1]
    found = windows_credential(service)
    if found and found[0] == name and found[1]:
        return found[1]
    return ""


def token_store() -> JiraTokenStore:
    return JiraTokenStore(data_dir() / "jira-token.bin")


def resolve(settings, store: JiraTokenStore | None = None) -> Credentials:
    store = store or token_store()
    c = Credentials()
    if settings["jira_url"]:
        c.base_url, c.site_source = settings["jira_url"], "Settings"
    elif os.environ.get("JIRA_BASE_URL"):
        c.base_url, c.site_source = os.environ["JIRA_BASE_URL"], "JIRA_BASE_URL"
    c.base_url = c.base_url.rstrip("/")
    c.email = settings["jira_email"] or os.environ.get("JIRA_EMAIL", "")
    if store.get():
        c.token, c.token_source = store.get(), "saved in powertools"
    elif tok := shared_token():
        c.token, c.token_source = tok, "Windows Credential Manager (jira-token)"
    elif os.environ.get("JIRA_API_TOKEN"):
        c.token, c.token_source = os.environ["JIRA_API_TOKEN"], "JIRA_API_TOKEN"
    return c
