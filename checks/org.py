"""H: the org chart (who reports to whom, job titles, departments) can be read without admin rights, and fast enough
to export a whole branch (e.g. everyone under a director).

Approaches compared, each with its time and number of requests:
  Graph (the Graph Explorer token powertools already keeps):
    - chain up:        /me?$expand=manager($levels=max)       one request for the whole management chain
    - one level down:  /users/{id}/directReports               the classic way, one request per person
    - a branch, plain: walk down from a manager, one request per person
    - a branch, batch: the same walk, 20 people per request ($batch)
    - the directory:   /users?$expand=manager                 everyone with their manager, page by page: the
                                                              whole tree from a flat list in a few requests
    - people:          /me/people                             the people you work with most (relevance)
  Active Directory (LDAP, Windows only): the manager and directReports attributes, no token, no expiry; needs the
  company network (office or VPN).

Only counts, statuses, field names and times are reported: never names, titles or e-mail addresses.
"""

from __future__ import annotations

import json
import subprocess
import time

from .common import FAIL, IS_WIN, OK, SKIP, WARN, Context

LIMIT = 300          # people per branch walk (enough to compare the approaches, not an export)
PROFILE = ["displayName", "jobTitle", "department", "officeLocation", "companyName", "employeeId", "mail"]
EVENTUAL = {"ConsistencyLevel": "eventual"}


def run(ctx: Context) -> None:
    graph_part(ctx)
    ad_part(ctx)


# ------------------------------------------------------------------------------------------------ Graph

def app_token() -> str:
    """The token the powertools app keeps (DPAPI), so nothing has to be pasted again; "" when none is valid."""
    try:
        from powertools.config import data_dir
        from powertools.graph.token import TokenStore
        return TokenStore(data_dir() / "graph-token.bin").get()
    except Exception:
        return ""


def graph_part(ctx: Context) -> None:
    from .graph import Graph, load_token
    token, source = app_token(), "the powertools app"
    if not token:
        token, source = load_token(ctx, prompt=ctx.interactive), "the checker"
    if not token:
        ctx.add("org.graph", SKIP, "no valid Graph token (get one in powertools: Teams & mail > Get token)")
        return
    ctx.add("org.token", OK, f"from {source}")
    probe_graph(ctx, Graph(token))


def probe_graph(ctx: Context, g) -> None:
    st, me, err = g.get("/me", {"$select": "id," + ",".join(PROFILE)})
    if st != 200:
        ctx.add("org.me", FAIL, f"HTTP {st} {err}")
        return
    filled = [f for f in PROFILE if me.get(f)]
    ctx.add("org.profile_fields", OK if "jobTitle" in filled else WARN, "filled on your profile: " + ", ".join(filled))

    chain = chain_up(ctx, g)
    st, data, err = g.get("/me/directReports", {"$select": "id"})
    ctx.add("org.reports.me", OK if st == 200 else WARN,
            f"{len(data.get('value', []))} direct reports" if st == 200 else f"HTTP {st} {err}")
    if chain:
        st, data, err = g.get(f"/users/{chain[0]}/directReports", {"$select": "id"})
        ctx.add("org.reports.manager", OK if st == 200 else WARN,
                f"your manager has {len(data.get('value', []))} direct reports" if st == 200 else f"HTTP {st} {err}")
        root = chain[min(1, len(chain) - 1)]   # your manager's manager: a branch of a sensible size
        seq = walk(g, root, batch=False)
        ctx.add("org.walk.plain", OK if seq["people"] else WARN, describe(seq, "one request per person"))
        bat = walk(g, root, batch=True)
        ctx.add("org.walk.batch", OK if bat["people"] else WARN, describe(bat, "20 people per $batch request")
                + (f"; {seq['seconds'] / bat['seconds']:.1f}x faster" if bat["seconds"] and seq["seconds"] else ""))
    directory(ctx, g)
    st, data, err = g.get("/me/people", {"$top": "25", "$select": "id"})
    ctx.add("org.people", OK if st == 200 else WARN,
            f"{len(data.get('value', []))} people you work with (relevance)" if st == 200 else f"HTTP {st} {err} (People.Read)")


def chain_up(ctx: Context, g) -> list[str]:
    """Your managers' ids, nearest first: in one request when $levels works, else one request per level."""
    t0 = time.time()
    st, data, err = g.get("/me", {"$select": "id", "$expand": "manager($levels=max;$select=id,jobTitle,department)"},
                          headers=EVENTUAL)
    ids = []
    if st == 200:
        m = data.get("manager")
        while m:
            ids.append(m["id"])
            m = m.get("manager")
        ctx.add("org.chain.one_request", OK if ids else WARN, f"{len(ids)} levels up in {time.time() - t0:.1f} s")
        if ids:
            return ids
    else:
        ctx.add("org.chain.one_request", WARN, f"HTTP {st} {err}")
    t0, cur = time.time(), "/me"
    for _ in range(15):
        st, m, err = g.get(f"{cur}/manager", {"$select": "id"})
        if st != 200:
            break
        ids.append(m["id"])
        cur = f"/users/{m['id']}"
    ctx.add("org.chain.step_by_step", OK if ids else (WARN if st == 404 else FAIL),
            f"{len(ids)} levels up, {len(ids) + 1} requests, {time.time() - t0:.1f} s" if ids else f"HTTP {st} {err} (User.Read.All?)")
    return ids


def walk(g, root: str, batch: bool) -> dict:
    """Breadth-first down from root, up to LIMIT people: how many, how deep, how many requests, how long, and how
    many have a job title and a department (the org chart is only as good as these fields)."""
    t0, todo, seen, depth, requests, titled, dept = time.time(), [root], {root}, 0, 0, 0, 0
    while todo and len(seen) < LIMIT:
        nxt = []
        groups = [todo[i:i + 20] for i in range(0, len(todo), 20)] if batch else [[x] for x in todo]
        for grp in groups:
            requests += 1
            for people in (reports_batch(g, grp) if batch else [reports_one(g, grp[0])]):
                for p in people:
                    if p["id"] in seen or len(seen) >= LIMIT:
                        continue
                    seen.add(p["id"]); nxt.append(p["id"])
                    titled += bool(p.get("jobTitle")); dept += bool(p.get("department"))
        todo = nxt
        depth += bool(nxt)
    return {"people": len(seen) - 1, "depth": depth, "requests": requests, "seconds": round(time.time() - t0, 1),
            "titled": titled, "dept": dept, "capped": len(seen) >= LIMIT}


def reports_one(g, uid: str) -> list[dict]:
    st, data, _ = g.get(f"/users/{uid}/directReports", {"$select": "id,jobTitle,department", "$top": "999"})
    return data.get("value", []) if st == 200 else []


def reports_batch(g, uids: list[str]) -> list[list[dict]]:
    body = {"requests": [{"id": str(i), "method": "GET", "url": f"/users/{u}/directReports?$select=id,jobTitle,department&$top=999"}
                         for i, u in enumerate(uids)]}
    st, data, _ = g.post("/$batch", body)
    if st != 200:
        return []
    return [r.get("body", {}).get("value", []) for r in data.get("responses", []) if r.get("status") == 200]


def describe(w: dict, how: str) -> str:
    return (f"{how}: {w['people']} people, {w['depth']} levels down, {w['requests']} requests, {w['seconds']} s"
            f"{' (stopped at the limit)' if w['capped'] else ''}; with job title {w['titled']}, with department {w['dept']}")


def directory(ctx: Context, g) -> None:
    t0 = time.time()
    st, data, err = g.get("/users", {"$select": "id,jobTitle,department", "$top": "999", "$count": "true"}, headers=EVENTUAL)
    if st != 200:
        ctx.add("org.directory.list", WARN, f"HTTP {st} {err} (User.ReadBasic.All)")
        return
    total = data.get("@odata.count")
    ctx.add("org.directory.list", OK, f"{len(data.get('value', []))} people on page 1 of the directory"
            f"{f' (of {total})' if total else ''} in {time.time() - t0:.1f} s; more pages: {'@odata.nextLink' in data}")
    t0 = time.time()
    st, data, err = g.get("/users", {"$select": "id", "$expand": "manager($select=id)", "$top": "100"})
    if st != 200:
        ctx.add("org.directory.with_manager", WARN, f"HTTP {st} {err}")
        return
    rows = data.get("value", [])
    with_mgr = sum(1 for u in rows if u.get("manager"))
    pages = f"; the whole directory: about {-(-int(total) // len(rows))} requests" if total and rows else ""
    ctx.add("org.directory.with_manager", OK if with_mgr else WARN,
            f"{len(rows)} people with their manager in one request ({with_mgr} have one), {time.time() - t0:.1f} s{pages}")


# ------------------------------------------------------------------------------------------- Active Directory

AD_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
  $t0 = Get-Date
  $s = [adsisearcher]"(&(objectCategory=person)(sAMAccountName=$env:USERNAME))"
  'manager','directReports','title','department' | ForEach-Object { [void]$s.PropertiesToLoad.Add($_) }
  $me = $s.FindOne()
  if (-not $me) { throw 'your account was not found in the directory' }
  $out = [ordered]@{ ok = $true; has_manager = [bool]$me.Properties['manager'].Count; reports = $me.Properties['directreports'].Count
                     has_title = [bool]$me.Properties['title'].Count; has_department = [bool]$me.Properties['department'].Count; levels = 0 }
  $dn = [string]($me.Properties['manager'] | Select-Object -First 1)
  while ($dn -and $out.levels -lt 15) {
    $m = ([adsi]"LDAP://$dn"); $out.levels++
    $dn = [string]($m.Properties['manager'] | Select-Object -First 1)
  }
  $out.seconds = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
  $out | ConvertTo-Json -Compress
} catch { [ordered]@{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress }
"""


def ad_part(ctx: Context) -> None:
    if not IS_WIN:
        ctx.add("org.ad", SKIP, "Active Directory: Windows only")
        return
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", AD_SCRIPT],
                           capture_output=True, text=True, timeout=90)
        r = json.loads((p.stdout.strip().splitlines() or ["{}"])[-1])
    except Exception as e:
        ctx.add("org.ad", WARN, f"{type(e).__name__}: {e}")
        return
    if not r.get("ok"):
        ctx.add("org.ad", WARN, f"not reachable: {r.get('error', '?')[:160]} (office network or VPN?)")
        return
    ctx.add("org.ad", OK, f"no token needed: manager {'yes' if r['has_manager'] else 'no'}, {r['levels']} levels up, "
            f"{r['reports']} direct reports, job title {'yes' if r['has_title'] else 'no'}, department "
            f"{'yes' if r['has_department'] else 'no'}, {r['seconds']} s")
