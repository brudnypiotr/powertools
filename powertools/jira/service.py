"""Jira downloads for the window: search, download with linked issues to a depth, refresh with change notes.

Output (export folder): `jira/<KEY>.md` and `jira/<KEY>/<attachment>`. What was downloaded last time (the snapshot we
compare against) lives in %LOCALAPPDATA%\\powertools\\jira\\<site>\\<KEY>.json, so a refresh can say what changed.
"""

from __future__ import annotations

import json
import re
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from .. import export
from ..config import data_dir, output_dir
from ..jobs import Job, Jobs
from . import auth, render
from .client import Jira, JiraError, issue_keys

FOLLOW = ("links", "subtasks", "parent", "children")
OPS = {"=", "!=", "~", "!~", ">", ">=", "<", "<=", "in", "not in", "is", "is not"}
MAX_ISSUES = 2000


def quote(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_jql(form: dict) -> str:
    """Search form -> JQL. Every filled box adds one AND clause; `jql` is appended as it is."""
    parts = []
    if form.get("project"):
        parts.append(f"project = {quote(form['project'].strip())}")
    keys = issue_keys(form.get("keys", ""))
    if keys:
        parts.append(f"key in ({', '.join(keys)})")
    if form.get("epic"):
        e = issue_keys(form["epic"])
        if not e:
            raise ValueError("epic must be an issue key, e.g. ABC-123")
        parts.append(f'(parent = {e[0]} OR "Epic Link" = {e[0]})')
    labels = [l for l in re.split(r"[,\s]+", form.get("labels", "")) if l]
    if labels:
        parts.append(f"labels in ({', '.join(quote(l) for l in labels)})")
    if form.get("text"):
        parts.append(f"text ~ {quote(form['text'].strip())}")
    for flt in form.get("filters") or []:
        name, op, value = str(flt.get("field", "")).strip(), str(flt.get("op", "=")).strip().lower(), str(flt.get("value", "")).strip()
        if not name:
            continue
        if op not in OPS:
            raise ValueError(f"unknown operator {op!r}")
        fld = name if re.fullmatch(r"[A-Za-z0-9_]+|cf\[\d+\]", name) else quote(name)
        if op in ("in", "not in"):
            val = "(" + ", ".join(quote(v.strip()) for v in value.split(",") if v.strip()) + ")"
        elif op in ("is", "is not"):
            val = value.upper() if value.upper() in ("EMPTY", "NULL") else "EMPTY"
        else:
            val = quote(value)
        parts.append(f"{fld} {op.upper() if op in ('in', 'not in', 'is', 'is not') else op} {val}")
    if form.get("updated_days"):
        days = int(form["updated_days"])
        if not 0 < days <= 3650:
            raise ValueError("updated within must be 1..3650 days")
        parts.append(f"updated >= -{days}d")
    if form.get("jql", "").strip():
        parts.append(f"({form['jql'].strip()})")
    if not parts:
        raise ValueError("fill at least one search box")
    return " AND ".join(parts) + " ORDER BY updated DESC"


class JiraService:
    def __init__(self, settings, jobs: Jobs):
        self.settings, self.jobs = settings, jobs
        self.tokens = auth.token_store()
        self._me: dict = {}
        self._me_at = 0.0
        self._fields: list[dict] = []
        self._prefer = ""          # token source that passed the last Check
        self._checks: list[dict] = []
        self._checked_for: tuple = ()

    # ------------------------------------------------------------------ connection
    def creds(self) -> auth.Credentials:
        return auth.resolve(self.settings, self.tokens, self._prefer)

    def client(self) -> Jira:
        c = self.creds()
        return Jira(c.base_url, c.email, c.token)

    def state_dir(self, base: str) -> Path:
        d = data_dir() / "jira" / export.slug(re.sub(r"^https?://", "", base), 80)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def status(self, check: bool = False) -> dict:
        """check=True (the Check button) tries every token candidate and keeps the first one Jira accepts."""
        c = self.creds()
        key = (c.base_url, self.settings["jira_email"], tuple((x["source"], x["token"]) for x in c.candidates))
        if c.base_url and c.candidates and (check or key != self._checked_for or time.time() - self._me_at > 600):
            self._checked_for = key  # a new site, e-mail or token is checked right away, not after 10 minutes
            self._check(c)
            c = self.creds()
        out = {"site": c.base_url, "site_source": c.site_source, "email": c.email, "token_source": c.token_source,
               "token": auth.fingerprint(c.token), "has_token": bool(c.token), "cloud": "atlassian.net" in c.base_url,
               "user": self._me.get("name", ""), "error": self._me.get("error", ""), "checks": self._checks,
               "checked_at": self._me_at, "downloaded": 0}
        if c.base_url:
            out["downloaded"] = len(list(self.state_dir(c.base_url).glob("*.json")))
        return out

    def _check(self, c: auth.Credentials) -> None:
        results, ok, seen = [], None, set()
        for cand in c.candidates:
            ident = (cand["token"], cand["email"])
            row = {"source": cand["source"], "email": cand["email"], "token": auth.fingerprint(cand["token"])}
            if ident in seen:
                row.update(ok=None, detail="same token as above")
            elif ok:
                row.update(ok=None, detail="not tried (an earlier one works)")
            else:
                seen.add(ident)
                try:
                    me = Jira(c.base_url, cand["email"], cand["token"]).myself()
                    ok = {"name": me.get("displayName") or me.get("name", ""), "source": cand["source"]}
                    row.update(ok=True, detail=f"accepted: {ok['name']}")
                except Exception as e:
                    row.update(ok=False, detail=str(e))
            results.append(row)
        self._checks, self._me_at = results, time.time()
        if ok:
            self._prefer, self._me = ok["source"], {"name": ok["name"]}
        else:
            self._me = {"error": results[0]["detail"] if len(results) == 1 else "Jira refused every token below"}

    def set_token(self, token: str) -> dict:
        before = (self._prefer, self._me, self._checks)
        self.tokens.set(token)
        self._prefer = "saved in powertools"
        st = self.status(check=True)
        if st["token_source"] != "saved in powertools" or st["error"]:
            self.tokens.clear()
            self._prefer, self._me, self._checks = before  # keep using the token that worked before
            first = next((x["detail"] for x in st["checks"] if x["source"] == "saved in powertools"), st["error"])
            raise ValueError(f"Jira did not accept this token: {first}")
        return st

    def clear_token(self) -> dict:
        self.tokens.clear()
        self._prefer = ""
        return self.status(check=True)

    def fields(self) -> list[dict]:
        if not self._fields:
            self._fields = sorted(({"id": f["id"], "name": f.get("name", f["id"]), "custom": bool(f.get("custom"))}
                                   for f in self.client().fields()), key=lambda f: f["name"].lower())
        return self._fields

    # ------------------------------------------------------------------ listing
    def _load(self, base: str, key: str) -> dict:
        p = self.state_dir(base) / f"{key}.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, base: str, key: str, state: dict) -> None:
        p = self.state_dir(base) / f"{key}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)

    def search(self, form: dict, limit: int = 200) -> dict:
        jql = form["jql_only"].strip() if form.get("jql_only") else build_jql(form)
        j = self.client()
        issues = j.search(jql, limit=limit)
        return {"jql": jql, "issues": [self._row(j.base, i) for i in issues]}

    def _row(self, base: str, i: dict) -> dict:
        f = i.get("fields") or {}
        st = self._load(base, i["key"])
        updated = f.get("updated", "")
        return {"key": i["key"], "summary": f.get("summary", ""), "status": render.text_of(f.get("status")),
                "type": render.text_of(f.get("issuetype")), "updated": updated,
                "downloaded": st.get("downloaded_at", ""),
                "changed": bool(st) and st.get("snapshot", {}).get("updated", "") != updated}

    def downloaded(self, base: str = "") -> list[dict]:
        base = base or self.creds().base_url
        if not base:
            return []
        rows = []
        for p in self.state_dir(base).glob("*.json"):
            try:
                st = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            s = st.get("snapshot", {})
            rows.append({"key": s.get("key", p.stem), "summary": s.get("summary", ""), "status": s.get("status", ""),
                         "type": s.get("type", ""), "updated": s.get("updated", ""), "downloaded": st.get("downloaded_at", ""),
                         "last_result": st.get("last_result", ""), "found_via": st.get("found_via", "")})
        return sorted(rows, key=lambda r: r["downloaded"], reverse=True)

    # ------------------------------------------------------------------ download
    def download(self, seed: dict, opts: dict) -> Job:
        """seed: {mode: keys|epic|jql, value}. opts: depth 0..5, follow [links, subtasks, parent, children],
        attachments, max_issues."""
        mode, value = seed.get("mode", "keys"), str(seed.get("value", "")).strip()
        depth = int(opts.get("depth", 0))
        if not 0 <= depth <= 5:
            raise ValueError("depth must be 0..5")
        follow = [f for f in opts.get("follow", ["links", "subtasks"]) if f in FOLLOW]
        max_issues = int(opts.get("max_issues", 200))
        if not 1 <= max_issues <= MAX_ISSUES:
            raise ValueError(f"max issues must be 1..{MAX_ISSUES}")
        attachments = bool(opts.get("attachments", self.settings["jira_attachments"]))
        if mode == "keys" and not issue_keys(value):
            raise ValueError("no issue keys found (e.g. ABC-123 or a browse link)")
        if mode == "epic" and not issue_keys(value):
            raise ValueError("epic must be an issue key")
        if mode == "jql" and not value:
            raise ValueError("JQL is empty")
        j = self.client()

        def run(job: Job) -> None:
            if not job.total:  # first start (a resumed job keeps its own state)
                if mode == "keys":
                    seeds = issue_keys(value)
                elif mode == "epic":
                    epic = issue_keys(value)[0]
                    seeds = [epic] + self._children(j, epic, max_issues)
                else:
                    seeds = [i["key"] for i in j.search(value, fields="updated", limit=max_issues)]
                job.queue = deque((k, 0) for k in seeds)
                job.seen, job.counts = set(seeds), {"new": 0, "changed": 0, "unchanged": 0, "failed": 0}
                job.total = len(seeds)
                if not seeds:
                    job.note("nothing matched")
            via = f"{mode}: {value}"[:200]
            while job.queue:
                key, d = job.queue[0]
                try:
                    issue, result = self._one(j, key, attachments, via if d == 0 else f"{via} (depth {d})", d, job.note)
                except JiraError as e:
                    if e.status in (401, 403):
                        raise
                    job.note(f"{key}: {e}")
                    job.counts["failed"] += 1
                    issue, result = None, "failed"
                job.queue.popleft()
                job.done += 1
                if result in ("new", "changed"):
                    job.files.append(str(output_dir(self.settings) / "jira" / f"{key}.md"))
                if result != "failed":
                    job.counts[result] += 1
                if issue and d < depth:
                    for nk in self._neighbours(j, issue, follow, max_issues):
                        if nk not in job.seen and len(job.seen) < max_issues:
                            job.seen.add(nk)
                            job.queue.append((nk, d + 1))
                job.total = job.done + len(job.queue)
            if len(job.seen) >= max_issues:
                job.note(f"stopped at {max_issues} issues (raise 'Max issues' to follow further)")
            job.note(", ".join(f"{v} {k}" for k, v in job.counts.items() if v) or "nothing to do")

        what = {"keys": ", ".join(issue_keys(value)[:3]) + ("..." if len(issue_keys(value)) > 3 else ""),
                "epic": f"epic {value}", "jql": value[:60]}[mode]
        return self.jobs.submit(f"Jira: {what}" + (f" + links to depth {depth}" if depth else ""), run)

    def refresh(self, keys: list[str] | None, opts: dict) -> Job:
        """Re-check downloaded issues: one search finds those updated in Jira since their download; only those are fetched."""
        j = self.client()
        all_keys = [r["key"] for r in self.downloaded(j.base)]
        keys = [k for k in (keys or all_keys) if k in set(all_keys)]
        if not keys:
            raise ValueError("nothing downloaded yet")
        attachments = bool(opts.get("attachments", self.settings["jira_attachments"]))

        def run(job: Job) -> None:
            if not job.total:
                job.counts = {"changed": 0, "unchanged": 0, "failed": 0}
                stale, seen = [], set()
                for i in range(0, len(keys), 100):
                    batch = keys[i:i + 100]
                    for it in j.search(f"key in ({', '.join(batch)})", fields="updated", limit=len(batch)):
                        seen.add(it["key"])
                        old = self._load(j.base, it["key"]).get("snapshot", {})
                        if old.get("updated") != (it.get("fields") or {}).get("updated") or force_md_missing(self, it["key"]):
                            stale.append(it["key"])
                gone = [k for k in keys if k not in seen]
                for k in gone:
                    job.note(f"{k}: not found any more (moved, deleted or no access)")
                job.counts["failed"] += len(gone)
                job.counts["unchanged"] += len(seen) - len(stale)
                job.queue = deque(stale)
                job.total = len(stale)
                if not stale:
                    job.note(f"all {len(seen)} up to date")
            while job.queue:
                key = job.queue[0]
                try:
                    _, result = self._one(j, key, attachments, None, None, job.note)
                except JiraError as e:
                    if e.status in (401, 403):
                        raise
                    job.note(f"{key}: {e}")
                    result = "failed"
                job.queue.popleft()
                job.done += 1
                job.counts[result if result in job.counts else "changed"] += 1
                if result in ("new", "changed"):
                    job.files.append(str(output_dir(self.settings) / "jira" / f"{key}.md"))
            job.note(", ".join(f"{v} {k}" for k, v in job.counts.items() if v))

        return self.jobs.submit(f"Jira: refresh {len(keys)} downloaded issue(s)", run)

    # ------------------------------------------------------------------ one issue
    def _children(self, j: Jira, key: str, limit: int) -> list[str]:
        try:
            found = j.search(f'parent = {key} OR "Epic Link" = {key}', fields="updated", limit=limit)
        except JiraError as e:
            if e.status != 400:  # "Epic Link" does not exist on this site
                raise
            found = j.search(f"parent = {key}", fields="updated", limit=limit)
        return [i["key"] for i in found]

    def _neighbours(self, j: Jira, issue: dict, follow: list[str], limit: int) -> list[str]:
        f = issue.get("fields") or {}
        out = []
        if "links" in follow:
            out += [l["key"] for l in render.links_of(f)]
        if "subtasks" in follow:
            out += [s.get("key", "") for s in f.get("subtasks") or []]
        if "parent" in follow:
            out += [k for k in ((f.get("parent") or {}).get("key", ""), render.epic_of(issue)) if k]
        if "children" in follow and render.text_of(f.get("issuetype")).lower() == "epic":
            out += self._children(j, issue["key"], limit)
        return [k for k in dict.fromkeys(out) if k and k != issue.get("key")]

    def _one(self, j: Jira, key: str, attachments: bool, found_via: str | None, depth: int | None, note) -> tuple[dict, str]:
        """Download one issue; returns (issue JSON, new | changed | unchanged)."""
        issue = j.issue(key)
        key = issue["key"]  # a moved issue answers under its new key
        base_out = output_dir(self.settings) / "jira"
        md_path = base_out / f"{key}.md"
        old = self._load(j.base, key)
        snap = render.snapshot(issue)
        files_state: dict = dict(old.get("files") or {})

        # attachments: only the ones we do not have yet
        limit = self.settings["jira_max_attachment_mb"] * 1_000_000
        listing, new_files = [], 0
        for a in (issue.get("fields") or {}).get("attachment") or []:
            aid, name, size = str(a.get("id")), a.get("filename", "file"), int(a.get("size") or 0)
            rel = files_state.get(aid, "")
            if rel and (base_out / rel).exists():
                listing.append((name, rel, size))
                continue
            if attachments and size <= limit:
                p = export.unique(base_out / key / export.safe_filename(name))
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(j.attachment(a))
                rel = p.relative_to(base_out).as_posix()
                files_state[aid] = rel
                new_files += 1
                listing.append((name, rel, size))
            else:
                if attachments:
                    note(f"{key}: {name} skipped ({size // 1_000_000} MB > {limit // 1_000_000} MB)")
                listing.append((name, "", size))
        images = {aid: rel.replace(" ", "%20") for aid, rel in files_state.items()}

        if old and old.get("snapshot") == snap and md_path.exists() and not new_files:
            old["checked_at"] = now()
            self._save(j.base, key, old)
            return issue, "unchanged"
        changes = render.diff(old["snapshot"], snap) if old.get("snapshot") else []
        result = "changed" if old.get("snapshot") and changes else ("new" if not old.get("snapshot") else "unchanged")
        if result == "unchanged" and new_files:
            changes = [f"{new_files} attachment(s) downloaded"]
        history = render.history_since(issue, old["snapshot"].get("updated", "")) if result == "changed" else []
        downloads = list(old.get("downloads") or [])
        if result != "unchanged" or not downloads:
            downloads.insert(0, f"{now()[:16].replace('T', ' ')}: {result}" + (f" ({len(changes)} change(s))" if changes else ""))
        meta = {"status": result, "previous": old.get("downloaded_at", ""),
                "found_via": found_via or old.get("found_via", ""), "depth": depth if depth is not None else old.get("depth", 0)}
        export.write(md_path, render.markdown(issue, j.base, meta, images, listing,
                                              changes, history, downloads[:30]))
        self._save(j.base, key, {"snapshot": snap, "downloaded_at": now(), "files": files_state, "downloads": downloads[:30],
                                 "found_via": meta["found_via"], "depth": meta["depth"], "last_result": result,
                                 "last_changes": changes})
        if result == "changed":
            note(f"{key}: " + "; ".join(changes[:4]) + (" ..." if len(changes) > 4 else ""))
        return issue, result


def force_md_missing(svc: JiraService, key: str) -> bool:
    return not (output_dir(svc.settings) / "jira" / f"{key}.md").exists()


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
