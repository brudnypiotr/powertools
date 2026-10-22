"""Where the Jira site, e-mail and token come from. powertools keeps its own; nothing is read from other apps.

Site and e-mail: Settings > Jira (else JIRA_BASE_URL / JIRA_EMAIL). Token: pasted in the Jira tab, kept encrypted with
DPAPI like the Graph token (else JIRA_API_TOKEN). Jira Cloud: API token + e-mail. Data Center: personal access token.
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


def token_store() -> JiraTokenStore:
    return JiraTokenStore(data_dir() / "jira-token.bin")


def resolve(settings, store: JiraTokenStore | None = None) -> Credentials:
    c = Credentials()
    if settings["jira_url"]:
        c.base_url, c.site_source = settings["jira_url"], "Settings"
    elif os.environ.get("JIRA_BASE_URL"):
        c.base_url, c.site_source = os.environ["JIRA_BASE_URL"], "JIRA_BASE_URL"
    c.base_url = c.base_url.rstrip("/")
    c.email = settings["jira_email"] or os.environ.get("JIRA_EMAIL", "")
    store = store or token_store()
    if store.get():
        c.token, c.token_source = store.get(), "saved in powertools"
    elif os.environ.get("JIRA_API_TOKEN"):
        c.token, c.token_source = os.environ["JIRA_API_TOKEN"], "JIRA_API_TOKEN"
    return c
