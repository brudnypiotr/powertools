"""Minimal read-only Jira REST client (Cloud v3 and Data Center v2): issue, JQL search, fields, attachments.

Cloud (`*.atlassian.net`): e-mail + API token, basic auth. Data Center: personal access token, bearer.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("powertools.jira")
KEY = re.compile(r"\b([A-Z][A-Z0-9_]+-\d+)\b")


class JiraError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Jira {status}: {message}" if status else message)
        self.status = status


def _error_text(e: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(e.read().decode("utf-8", errors="replace"))
        return ("; ".join(body.get("errorMessages") or []) or
                "; ".join(f"{k}: {v}" for k, v in (body.get("errors") or {}).items()) or str(body.get("message") or ""))[:300]
    except Exception:
        return ""


def issue_keys(text: str) -> list[str]:
    """Every issue key in pasted text (keys, browse URLs, comma or line separated), in order, no duplicates."""
    return list(dict.fromkeys(KEY.findall(str(text).upper())))


class Jira:
    """urllib, not requests: requests drops the Authorization header when Jira redirects to another host (custom
    domain -> atlassian.net, attachment content -> media host), which shows up as a 401 with a valid token."""

    def __init__(self, base_url: str, email: str, token: str):
        if not base_url:
            raise JiraError(0, "Jira site not set: Settings > Jira site")
        if not token:
            raise JiraError(0, "no Jira token: paste one in the Jira tab")
        self.base = base_url.rstrip("/")
        self.cloud = "atlassian.net" in self.base
        if self.cloud and not email:
            raise JiraError(0, "Jira Cloud needs your Atlassian e-mail: Settings > Jira e-mail")
        self.api = "3" if self.cloud else "2"
        self.auth = ("Basic " + base64.b64encode(f"{email}:{token}".encode()).decode() if self.cloud
                     else f"Bearer {token}")  # Cloud: e-mail + API token; Data Center: personal access token

    def _request(self, url: str, params: dict | None = None, accept: str = "application/json") -> bytes:
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        for attempt in range(5):
            req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "powertools", "Authorization": self.auth})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    body, ctype = r.read(), r.headers.get_content_type() or ""
                break
            except urllib.error.HTTPError as e:
                if (e.code == 429 or e.code >= 500) and attempt < 4:
                    wait = int(e.headers.get("Retry-After") or 2 ** attempt)
                    log.warning("Jira %s, waiting %s s", e.code, wait)
                    time.sleep(min(wait, 60))
                    continue
                detail = _error_text(e)
                if e.code in (401, 403):
                    raise JiraError(e.code, "the token or e-mail was refused" + (f" ({detail})" if detail else "")) from e
                if e.code == 404:
                    raise JiraError(404, "not found (or no permission to see it)") from e
                raise JiraError(e.code, detail or e.reason) from e
            except urllib.error.URLError as e:
                raise JiraError(0, f"cannot reach {self.base}: {e.reason} (network, VPN or proxy?)") from e
        if accept == "application/json" and ("html" in ctype or b"log in with atlassian account" in body[:4000].lower()):
            raise JiraError(401, "Jira returned a login page instead of data; the token is missing, wrong or expired")
        return body

    def get(self, path: str, params: dict | None = None) -> dict:
        return json.loads(self._request(f"{self.base}/rest/api/{self.api}/{path.lstrip('/')}", params).decode("utf-8"))

    def myself(self) -> dict:
        return self.get("myself")

    def fields(self) -> list[dict]:
        return self.get("field")

    def issue(self, key: str) -> dict:
        """Every field, HTML versions (description, comments, text custom fields), field names and the change history."""
        return self.get(f"issue/{key}", {"fields": "*all", "expand": "renderedFields,names,changelog"})

    def search(self, jql: str, fields: str = "summary,status,issuetype,updated", limit: int = 200) -> list[dict]:
        issues: list[dict] = []
        token, start = "", 0
        while len(issues) < limit:
            n = min(100, limit - len(issues))
            if self.cloud:
                data = self.get("search/jql", {"jql": jql, "fields": fields, "maxResults": n,
                                               **({"nextPageToken": token} if token else {})})
                issues += data.get("issues", [])
                token = data.get("nextPageToken") or ""
                if not token or data.get("isLast"):
                    break
            else:
                data = self.get("search", {"jql": jql, "fields": fields, "startAt": start, "maxResults": n})
                got = data.get("issues", [])
                issues += got
                start += len(got)
                if not got or start >= data.get("total", 0):
                    break
        return issues[:limit]

    def attachment(self, att: dict) -> bytes:
        url = att.get("content") or f"{self.base}/rest/api/{self.api}/attachment/content/{att['id']}"
        return self._request(url, accept="*/*")
