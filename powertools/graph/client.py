"""Minimal Microsoft Graph client: GET with paging, binary download, 429 back-off, typed errors."""

from __future__ import annotations

import logging
import time
from typing import Callable, Iterator

log = logging.getLogger("powertools.graph")
BASE = "https://graph.microsoft.com/v1.0"


class TokenExpired(Exception):
    """401: the user has to paste a new token; the job pauses and resumes afterwards."""


class GraphError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"HTTP {status} {code}: {message}")
        self.status, self.code = status, code


class Graph:
    def __init__(self, token: Callable[[], str]):
        import requests

        self._token = token
        self.s = requests.Session()

    def _request(self, url: str, params: dict | None, headers: dict | None, stream: bool = False, body: dict | None = None):
        token = self._token()
        if not token:
            raise TokenExpired("no valid token")
        h = {"Authorization": f"Bearer {token}", "Accept": "application/json", **(headers or {})}
        for attempt in range(5):
            if body is None:
                r = self.s.get(url, params=params, headers=h, timeout=120, stream=stream)
            else:
                r = self.s.post(url, json=body, headers=h, timeout=120)
            if (r.status_code == 429 or r.status_code >= 500) and attempt < 4:
                wait = int(r.headers.get("Retry-After", 2 ** attempt))
                log.warning("Graph %s, waiting %s s", r.status_code, wait)
                time.sleep(min(wait, 60))
                continue
            break
        if r.status_code == 401:
            raise TokenExpired("token expired or rejected")
        if not r.ok:
            try:
                err = r.json().get("error", {})
            except ValueError:
                err = {}
            raise GraphError(r.status_code, err.get("code", r.reason), str(err.get("message", ""))[:300])
        return r

    def get(self, path: str, params: dict | None = None, headers: dict | None = None) -> dict:
        url = path if path.startswith("http") else BASE + path
        return self._request(url, params, headers).json()

    def batch(self, requests: list[str]) -> list[dict]:
        """Up to 20 GET paths in one $batch request; their bodies in the same order ({} for a failed one).
        Requests the service throttled (429) are sent again once, after the wait it asks for."""
        if len(requests) > 20:
            raise ValueError("a $batch holds at most 20 requests")
        out: list[dict] = [{} for _ in requests]
        todo = list(range(len(requests)))
        for attempt in range(3):
            body = {"requests": [{"id": str(i), "method": "GET", "url": requests[i]} for i in todo]}
            data = self._request(BASE + "/$batch", None, None, body=body).json()
            again, wait = [], 0
            for r in data.get("responses", []):
                i = int(r["id"])
                if r.get("status") == 429:
                    again.append(i)
                    wait = max(wait, int((r.get("headers") or {}).get("Retry-After", 2)))
                elif r.get("status") == 401:
                    raise TokenExpired("token expired or rejected")
                elif 200 <= r.get("status", 0) < 300:
                    out[i] = r.get("body") or {}
            if not again or attempt == 2:
                break
            time.sleep(min(wait, 30))
            todo = again
        return out

    def pages(self, path: str, params: dict | None = None, headers: dict | None = None,
              max_items: int = 100_000) -> Iterator[dict]:
        """Yields items across @odata.nextLink pages (the link already carries the query)."""
        data = self.get(path, params, headers)
        n = 0
        while True:
            for item in data.get("value", []):
                yield item
                n += 1
                if n >= max_items:
                    return
            nxt = data.get("@odata.nextLink")
            if not nxt:
                return
            data = self.get(nxt, None, headers)

    def download(self, path: str) -> bytes:
        url = path if path.startswith("http") else BASE + path
        return self._request(url, None, {"Accept": "*/*"}).content
