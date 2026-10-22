"""Minimal read-only Jira REST client (Cloud v3 and Data Center v2): issue, JQL search, fields, attachments.

Cloud (`*.atlassian.net`): e-mail + API token, basic auth. Data Center: personal access token, bearer.
"""

from __future__ import annotations

import base64
import logging
import re
import time

log = logging.getLogger("powertools.jira")
KEY = re.compile(r"\b([A-Z][A-Z0-9_]+-\d+)\b")


class JiraError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Jira {status}: {message}" if status else message)
        self.status = status


def issue_keys(text: str) -> list[str]:
    """Every issue key in pasted text (keys, browse URLs, comma or line separated), in order, no duplicates."""
    return list(dict.fromkeys(KEY.findall(str(text).upper())))


class Jira:
    def __init__(self, base_url: str, email: str, token: str):
        import requests

        if not base_url:
            raise JiraError(0, "Jira site not set: Settings > Jira site (or jira.base_url in woynich-wiki)")
        if not token:
            raise JiraError(0, "no Jira token: paste one in the Jira tab, or set it in woynich-wiki (Keys and tokens)")
        self.base = base_url.rstrip("/")
        self.cloud = "atlassian.net" in self.base
        if self.cloud and not email:
            raise JiraError(0, "Jira Cloud needs your Atlassian e-mail: Settings > Jira e-mail")
        self.api = "3" if self.cloud else "2"
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "powertools"
        self.s.headers["Authorization"] = ("Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()
                                           if self.cloud else f"Bearer {token}")

    def _request(self, url: str, params: dict | None = None, accept: str = "application/json"):
        for attempt in range(5):
            r = self.s.get(url, params=params, headers={"Accept": accept}, timeout=120)
            if (r.status_code == 429 or r.status_code >= 500) and attempt < 4:
                wait = int(r.headers.get("Retry-After") or 2 ** attempt)
                log.warning("Jira %s, waiting %s s", r.status_code, wait)
                time.sleep(min(wait, 60))
                continue
            break
        if r.status_code in (401, 403):
            raise JiraError(r.status_code, "the token or e-mail was refused (expired, wrong, or no access)")
        if r.status_code == 404:
            raise JiraError(404, "not found (or no permission to see it)")
        if r.status_code >= 400:
            try:
                body = r.json()
                msg = "; ".join(body.get("errorMessages") or []) or "; ".join(f"{k}: {v}" for k, v in (body.get("errors") or {}).items())
            except ValueError:
                msg = r.text[:200]
            raise JiraError(r.status_code, msg or r.reason)
        if "html" in (r.headers.get("Content-Type") or "") and accept == "application/json":
            raise JiraError(r.status_code, "Jira returned a login page instead of data; the token is missing or expired")
        return r

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._request(f"{self.base}/rest/api/{self.api}/{path.lstrip('/')}", params).json()

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
        return self._request(url, accept="*/*").content
