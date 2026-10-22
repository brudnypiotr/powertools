"""One Jira issue -> a snapshot (what we compare between downloads) and a Markdown file for the wiki inbox."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime

from .. import export

# Fields shown in their own sections or frontmatter; every other non-empty field goes to "Other fields".
STANDARD = {"summary", "description", "status", "issuetype", "priority", "resolution", "assignee", "reporter", "creator",
            "labels", "components", "fixVersions", "versions", "created", "updated", "resolutiondate", "parent",
            "issuelinks", "subtasks", "attachment", "comment", "project", "watches", "votes", "worklog", "timetracking",
            "lastViewed", "statuscategorychangedate", "aggregateprogress", "progress", "thumbnail", "issuerestriction",
            "security", "environment", "duedate", "timespent", "timeestimate", "timeoriginalestimate",
            "aggregatetimespent", "aggregatetimeestimate", "aggregatetimeoriginalestimate", "workratio"}
SKIP_NAMES = {"rank", "development", "team", "flagged", "[chart] date of first response", "epic color", "epic status",
              "epic name", "issue color", "vulnerability", "design", "atlassian project status"}


def jtime(value: str) -> datetime | None:
    """Jira timestamps ('2026-10-20T14:00:00.000+0200') -> aware datetime."""
    if not value:
        return None
    value = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", str(value))
    try:
        return export.parse_time(value)
    except ValueError:
        return None


def shown_time(value: str) -> str:
    dt = jtime(value)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M") if dt else str(value or "")


def person(v) -> str:
    return (v or {}).get("displayName") or (v or {}).get("name") or "" if isinstance(v, dict) else str(v or "")


def text_of(v) -> str:
    """Plain value of any Jira field value: options, users, versions, sprints, lists, ADF documents."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (str, int, float)):
        s = str(v)
        m = re.search(r"\[.*?name=([^,\]]+)", s) if "greenhopper" in s else None  # Data Center sprint strings
        return m.group(1) if m else s
    if isinstance(v, list):
        return ", ".join(t for t in (text_of(x) for x in v) if t)
    if isinstance(v, dict):
        if v.get("type") == "doc":  # Atlassian document format without a rendered version
            return adf_text(v)
        for k in ("displayName", "value", "name", "key", "title"):
            if v.get(k):
                child = v.get("child")
                return str(v[k]) + (f" / {text_of(child)}" if child else "")
    return ""


def adf_text(node) -> str:
    if isinstance(node, list):
        return "".join(adf_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    t = node.get("type")
    if t == "text":
        return node.get("text", "")
    if t in ("mention", "emoji"):
        return (node.get("attrs") or {}).get("text", "")
    if t == "hardBreak":
        return "\n"
    inner = adf_text(node.get("content") or [])
    return inner + "\n" if t in ("paragraph", "heading", "listItem", "codeBlock", "tableRow") else inner


def jira_html(content: str, images: dict[str, str] | None = None) -> str:
    """Jira's rendered HTML -> Markdown-ish text; inline images point at the downloaded attachment when there is one."""
    images = images or {}
    t = content or ""

    def img(m):
        src = html.unescape(m.group(1))
        a = re.search(r"attachment(?:/content|/thumbnail)?/(\d+)", src)
        if a and a.group(1) in images:
            return f"![]({images[a.group(1)]})"
        return "[image]"

    t = re.sub(r'<img[^>]*src="([^"]*)"[^>]*>', img, t, flags=re.I)
    t = re.sub(r"<h(\d)[^>]*>(.*?)</h\1>", lambda m: "\n" + "#" * min(6, int(m.group(1)) + 2) + " " + m.group(2) + "\n",
               t, flags=re.S | re.I)
    t = re.sub(r"<pre[^>]*>(.*?)</pre>",
               lambda m: "\n```\n" + html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip("\n").replace("<", "&lt;") + "\n```\n",
               t, flags=re.S | re.I)
    t = re.sub(r"<tr[^>]*>", "\n|", t, flags=re.I)
    t = re.sub(r"</t[dh]>", " |", t, flags=re.I)
    t = re.sub(r"</?(b|strong)>", "**", t, flags=re.I)
    t = re.sub(r"</?(i|em)>", "_", t, flags=re.I)
    t = re.sub(r"</?(code|tt)>", "`", t, flags=re.I)
    return export.html_to_text(t)


def digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def links_of(f: dict) -> list[dict]:
    out = []
    for link in f.get("issuelinks") or []:
        lt = link.get("type") or {}
        if link.get("outwardIssue"):
            other, rel = link["outwardIssue"], lt.get("outward") or lt.get("name", "relates to")
        elif link.get("inwardIssue"):
            other, rel = link["inwardIssue"], lt.get("inward") or lt.get("name", "relates to")
        else:
            continue
        of = other.get("fields") or {}
        out.append({"key": other.get("key", ""), "rel": rel, "summary": of.get("summary", ""),
                    "status": text_of(of.get("status"))})
    return out


def field_names(issue: dict) -> dict[str, str]:
    return issue.get("names") or {}


def epic_of(issue: dict) -> str:
    f, names = issue.get("fields") or {}, field_names(issue)
    for fid, name in names.items():
        if name.lower() == "epic link" and f.get(fid):
            return text_of(f[fid])
    parent = f.get("parent") or {}
    if text_of((parent.get("fields") or {}).get("issuetype")).lower() == "epic":
        return parent.get("key", "")
    return ""


def custom_fields(issue: dict) -> dict[str, str]:
    f, rendered, names = issue.get("fields") or {}, issue.get("renderedFields") or {}, field_names(issue)
    out = {}
    for fid, value in f.items():
        name = names.get(fid, fid)
        if fid in STANDARD or name.lower() in SKIP_NAMES or name.lower() in ("epic link", "sprint"):
            continue
        r = rendered.get(fid)
        text = jira_html(r) if isinstance(r, str) and r.strip() else text_of(value)
        if text.strip() and not text.startswith("{"):
            out[name] = text.strip()
    return dict(sorted(out.items(), key=lambda kv: kv[0].lower()))


def sprints(issue: dict) -> list[str]:
    f, names = issue.get("fields") or {}, field_names(issue)
    for fid, name in names.items():
        if name.lower() == "sprint" and f.get(fid):
            v = f[fid]
            return [t for t in (text_of(x) for x in (v if isinstance(v, list) else [v])) if t]
    return []


def comments(issue: dict, images: dict[str, str] | None = None) -> list[dict]:
    raw = ((issue.get("fields") or {}).get("comment") or {}).get("comments") or []
    rendered = {c.get("id"): c.get("body") for c in (((issue.get("renderedFields") or {}).get("comment") or {}).get("comments") or [])}
    out = []
    for c in raw:
        body = rendered.get(c.get("id"))
        text = jira_html(body, images) if isinstance(body, str) else text_of(c.get("body"))
        out.append({"id": str(c.get("id")), "author": person(c.get("author")), "created": c.get("created", ""),
                    "updated": c.get("updated", ""), "text": text.strip()})
    return out


def snapshot(issue: dict) -> dict:
    """What we keep between downloads and compare: plain values only."""
    f = issue.get("fields") or {}
    rendered = issue.get("renderedFields") or {}
    desc = jira_html(rendered["description"]) if isinstance(rendered.get("description"), str) else text_of(f.get("description"))
    return {
        "key": issue.get("key", ""), "summary": f.get("summary", ""), "type": text_of(f.get("issuetype")),
        "status": text_of(f.get("status")), "priority": text_of(f.get("priority")),
        "resolution": text_of(f.get("resolution")), "assignee": person(f.get("assignee")),
        "reporter": person(f.get("reporter")), "parent": (f.get("parent") or {}).get("key", ""), "epic": epic_of(issue),
        "labels": sorted(f.get("labels") or []), "components": sorted(text_of(c) for c in f.get("components") or []),
        "fix_versions": sorted(text_of(v) for v in f.get("fixVersions") or []), "sprints": sprints(issue),
        "created": f.get("created", ""), "updated": f.get("updated", ""), "resolved": f.get("resolutiondate") or "",
        "description": digest(desc), "links": sorted(f"{l['rel']} {l['key']}" for l in links_of(f)),
        "subtasks": sorted(s.get("key", "") for s in f.get("subtasks") or []),
        "attachments": {str(a.get("id")): a.get("filename", "") for a in f.get("attachment") or []},
        "comments": {c["id"]: {"author": c["author"], "updated": c["updated"] or c["created"]} for c in comments(issue)},
        "custom": {k: digest(v) if len(v) > 120 else v for k, v in custom_fields(issue).items()},
    }


LABELS = {"summary": "Summary", "type": "Type", "status": "Status", "priority": "Priority", "resolution": "Resolution",
          "assignee": "Assignee", "reporter": "Reporter", "parent": "Parent", "epic": "Epic"}
LISTS = {"labels": "Labels", "components": "Components", "fix_versions": "Fix versions", "sprints": "Sprints",
         "links": "Links", "subtasks": "Sub-tasks"}


def diff(old: dict, new: dict) -> list[str]:
    """Readable list of what changed between two snapshots (empty = nothing that matters changed)."""
    out = []
    for k, label in LABELS.items():
        if old.get(k, "") != new.get(k, ""):
            out.append(f"{label}: {old.get(k) or '(none)'} → {new.get(k) or '(none)'}")
    if old.get("description") != new.get("description"):
        out.append("Description changed")
    for k, label in LISTS.items():
        a, b = set(old.get(k) or []), set(new.get(k) or [])
        if a != b:
            out.append(f"{label}: " + ", ".join([f"+{x}" for x in sorted(b - a)] + [f"−{x}" for x in sorted(a - b)]))
    oc, nc = old.get("comments") or {}, new.get("comments") or {}
    for cid, c in nc.items():
        if cid not in oc:
            out.append(f"New comment by {c['author']} ({shown_time(c['updated'])})")
        elif oc[cid]["updated"] != c["updated"]:
            out.append(f"Comment by {c['author']} edited")
    removed = len(set(oc) - set(nc))
    if removed:
        out.append(f"{removed} comment(s) removed")
    oa, na = old.get("attachments") or {}, new.get("attachments") or {}
    if set(oa) != set(na):
        out.append("Attachments: " + ", ".join([f"+{na[i]}" for i in na if i not in oa] + [f"−{oa[i]}" for i in oa if i not in na]))
    ocu, ncu = old.get("custom") or {}, new.get("custom") or {}
    for name in sorted(set(ocu) | set(ncu), key=str.lower):
        a, b = ocu.get(name, ""), ncu.get(name, "")
        if a != b:
            long = len(a) == 12 and len(b) == 12 and re.fullmatch(r"[0-9a-f]{12}", a + b)
            out.append(f"{name} changed" if long else f"{name}: {a[:80] or '(none)'} → {b[:80] or '(none)'}")
    return out


def history_since(issue: dict, since: str) -> list[str]:
    """Jira's own change history after `since` (the `updated` of the previous download)."""
    start = jtime(since)
    out = []
    for h in (issue.get("changelog") or {}).get("histories") or []:
        at = jtime(h.get("created", ""))
        if start and at and at <= start:
            continue
        for it in h.get("items") or []:
            if it.get("field", "").lower() in ("rank", "rankinglist"):
                continue
            frm, to = (it.get("fromString") or "").strip(), (it.get("toString") or "").strip()
            val = f"{frm[:60] or '(none)'} → {to[:60] or '(none)'}" if len(frm) < 200 and len(to) < 200 else "changed"
            out.append(f"{shown_time(h.get('created', ''))} {person(h.get('author'))}: {it.get('field')} {val}")
    return out


def markdown(issue: dict, base: str, meta: dict, images: dict[str, str], files: list[tuple[str, str, int]],
             changes: list[str], history: list[str], downloads: list[str]) -> str:
    """meta: status (new/changed/unchanged), previous, found_via, depth. files: (name, relative path or '', size)."""
    f = issue.get("fields") or {}
    s = snapshot(issue)
    key = s["key"]
    rendered = issue.get("renderedFields") or {}
    fm = {"source": "jira", "key": key, "title": f"{key}: {s['summary']}", "url": f"{base}/browse/{key}",
          "type": s["type"], "status": s["status"], "priority": s["priority"], "resolution": s["resolution"],
          "assignee": s["assignee"], "reporter": s["reporter"], "parent": s["parent"], "epic": s["epic"],
          "labels": s["labels"], "components": s["components"], "fix_versions": s["fix_versions"], "sprints": s["sprints"],
          "created": s["created"], "updated": s["updated"], "resolved": s["resolved"],
          "links": [f"{l['rel']} {l['key']}" for l in links_of(f)], "subtasks": s["subtasks"],
          "attachments": len(s["attachments"]), "comments": len(s["comments"]),
          "downloaded": datetime.now().astimezone().isoformat(timespec="seconds"),
          "download_status": meta.get("status", "new"), "previous_download": meta.get("previous", ""),
          "changes": changes, "found_via": meta.get("found_via", ""), "depth": meta.get("depth", 0)}
    fm = {k: v for k, v in fm.items() if v not in ("", [], None) or k in ("status", "download_status")}
    lines = [export.frontmatter(fm), f"# {key}: {s['summary']}", ""]
    if meta.get("status") == "changed":
        lines.append(f"> [!note] Changed since the last download ({shown_time(meta.get('previous', ''))})")
        lines += [f"> - {c}" for c in changes]
        if history:
            lines += [">", "> Jira history since then:"] + [f"> - {h}" for h in history]
        lines.append("")
    rows = [("Type", s["type"]), ("Status", s["status"]), ("Priority", s["priority"]), ("Resolution", s["resolution"]),
            ("Assignee", s["assignee"]), ("Reporter", s["reporter"]), ("Parent", s["parent"]), ("Epic", s["epic"]),
            ("Labels", ", ".join(s["labels"])), ("Components", ", ".join(s["components"])),
            ("Fix versions", ", ".join(s["fix_versions"])), ("Sprints", ", ".join(s["sprints"])),
            ("Created", shown_time(s["created"])), ("Updated", shown_time(s["updated"])), ("Resolved", shown_time(s["resolved"]))]
    lines += ["| Field | Value |", "|---|---|"] + [f"| {k} | {cell(v)} |" for k, v in rows if v] + [""]
    desc = rendered.get("description")
    desc = jira_html(desc, images) if isinstance(desc, str) else text_of(f.get("description"))
    lines += ["## Description", "", desc.strip() or "_(empty)_", ""]
    custom = custom_fields(issue)
    if custom:
        lines += ["## Other fields", ""]
        for name, value in custom.items():
            lines += [f"**{name}:** {value}" if "\n" not in value and len(value) < 200 else f"**{name}:**\n\n{value}", ""]
    links = links_of(f)
    if links:
        lines += ["## Links", ""] + [f"- {l['rel']} [{l['key']}]({l['key']}.md) {l['summary']} ({l['status']})" for l in links] + [""]
    subs = f.get("subtasks") or []
    if subs:
        lines += ["## Sub-tasks", ""] + [f"- [{x.get('key')}]({x.get('key')}.md) {(x.get('fields') or {}).get('summary', '')} "
                                         f"({text_of((x.get('fields') or {}).get('status'))})" for x in subs] + [""]
    if files:
        lines += ["## Attachments", ""] + [f"- [{n}]({export_path(p)}) ({size // 1024} KB)" if p else f"- {n} ({size // 1024} KB, not downloaded)"
                                            for n, p, size in files] + [""]
    cs = comments(issue, images)
    if cs:
        lines += ["## Comments", ""]
        for c in cs:
            edited = " (edited)" if c["updated"] and c["updated"] != c["created"] else ""
            lines += [f"### {c['author']} · {shown_time(c['created'])}{edited}", "", c["text"] or "_(empty)_", ""]
    if downloads:
        lines += ["## Downloads", ""] + [f"- {d}" for d in downloads] + [""]
    return "\n".join(lines)


def cell(v: str) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


def export_path(p: str) -> str:
    return p.replace(" ", "%20")
