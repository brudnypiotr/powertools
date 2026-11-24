"""GitHub Copilot as a model provider (Copilot SDK): text goes to GitHub, and only when the user picked it.

Every call is its own session without tools, skills, MCP or custom instructions (SDK mode "empty"), in an empty
folder, with every permission request rejected: mail and chats are data, so the model can only answer with text.
Sign-in: a fine-grained GitHub token with "Copilot Requests" (stored with DPAPI), else the user signed in to the
Copilot CLI. Needs a Copilot licence. The SDK downloads its runtime from GitHub on first use (checked SHA-256).
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import re
import sys
import threading
import time
import urllib.request
from concurrent import futures
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import data_dir
from .graph.token import _dpapi
from .llm import INTERACTIVE, LLMError

log = logging.getLogger("powertools.copilot")
NO_LICENCE = ("this GitHub account has no Copilot access (the token itself is fine). Use the account the licence is "
              "assigned to, and check that the organisation allows the Copilot CLI.")
NO_SCOPE = ("the GitHub token lacks the Copilot Requests permission (github.com > Settings > Fine-grained tokens > "
            "Permissions > Account > Copilot Requests)")
NOT_SIGNED_IN = "GitHub Copilot is not signed in: open the AI tab and paste a GitHub token"


def strict(schema: dict) -> dict:
    """The schema as strict structured output wants it: closed objects with every property required."""
    s = copy.deepcopy(schema)

    def walk(node) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(s)
    return s


def explain(e: Exception) -> str:
    """What a Copilot refusal means: 401 = the token lacks the permission, 403 = no licence or a policy."""
    msg, low = str(e), str(e).lower()
    if "not authorized to use this copilot feature" in low or ('"status":403' in low.replace(" ", "") and "copilot" in low):
        return NO_LICENCE
    if "copilot requests" in low and "permission" in low:
        return NO_SCOPE
    if "bad credentials" in low:
        return "GitHub refused the token (wrong, expired or revoked): create a new one in the AI tab"
    if "not authenticated" in low or "no github oauth token" in low:
        return NOT_SIGNED_IN
    return f"{type(e).__name__}: {msg}"[:300]


def runtime_gone(e: Exception) -> bool:
    """The runtime process ended or its pipe broke (the laptop slept, it crashed): worth one new start."""
    return isinstance(e, (ConnectionError, EOFError)) or type(e).__name__ == "ProcessExitedError" or bool(
        re.search(r"process exited|not connected|connection (lost|closed|reset)|pipe|client (is )?(closed|stopped|not started)", str(e), re.I))


def parse_expiry(header: str) -> datetime | None:
    """GitHub-Authentication-Token-Expiration: '2027-11-24 12:00:00 UTC' or '... +0100'."""
    h = (header or "").strip().replace(" UTC", " +0000")
    try:
        return datetime.strptime(h, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None


def quota_view(q: dict, now: datetime) -> dict:
    """The figure GitHub reports for this account: AI credits under usage-based billing, else premium requests. The cycle
    of an organisation ends on the 1st of the month, 00:00 UTC, unless GitHub says another time."""
    snaps = q.get("snapshots") or {}
    pick = (next((k for k, v in snaps.items() if v.get("tbb")), None) or next((k for k in snaps if "premium" in k or "credit" in k), None)
            or next((k for k, v in snaps.items() if not v.get("unlimited")), None))
    v = snaps.get(pick) or {}
    known = bool(v) and not v.get("unlimited") and (v.get("entitlement") or 0) > 0
    reset = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=timezone.utc)
    if v.get("reset_ms"):
        reset = datetime.fromtimestamp(v["reset_ms"] / 1000, timezone.utc)
    days = max(0, (reset.date() - now.date()).days)
    return {"plan": q.get("plan"), "unit": "AI credits" if q.get("tbb") or v.get("tbb") else "premium requests",
            "unlimited": bool(v.get("unlimited")), "entitlement": v.get("entitlement") if known else None,
            "left": round(max(0.0, v["entitlement"] - v["used"]), 1) if known else None,
            "used_pct": 100.0 * v["used"] / v["entitlement"] if known else None, "reset": reset.isoformat(), "days_left": days,
            "work_days_left": sum((now.date() + timedelta(days=n)).weekday() < 5 for n in range(days)), "checked": time.time()}


class SecretStore:
    """The GitHub token, encrypted with Windows DPAPI for this user (a private plain file on other systems)."""

    def __init__(self, path: Path):
        self.path = path

    def get(self) -> str:
        try:
            raw = self.path.read_bytes()
            return (_dpapi(raw, False) if sys.platform == "win32" else raw).decode("utf-8")
        except (OSError, ValueError):
            return ""

    def set(self, token: str) -> None:
        token = token.strip()
        if token.startswith("ghp_"):
            raise ValueError("classic tokens (ghp_...) do not work with Copilot: create a fine-grained one")
        if not token.startswith(("github_pat_", "gho_", "ghu_")) or any(c.isspace() for c in token):
            raise ValueError("this does not look like a GitHub token (it starts with github_pat_)")
        data = token.encode("utf-8")
        self.path.write_bytes(_dpapi(data, True) if sys.platform == "win32" else data)
        if sys.platform != "win32":
            os.chmod(self.path, 0o600)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def _reject(_request, _invocation=None):
    from copilot.rpc import PermissionDecisionReject

    return PermissionDecisionReject(feedback="powertools runs the model without tools")


class Copilot:
    name = "copilot"

    def __init__(self, settings, home: Path | None = None):
        self.settings = settings
        self.home = home or data_dir() / "copilot"
        self.secret = SecretStore(data_dir() / "github-token.bin")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client = None
        self._client_token = ""
        self._lock = threading.Lock()
        self._expiry: dict = {"token": "", "at": 0.0, "when": None, "busy": False}
        self._quota: dict = {"at": 0.0, "busy": False, "view": None, "token": ""}
        self.spent = lambda: 0.0             # credits this app used so far (the Brain's counter)
        self._live: set = set()              # answers the user is waiting for, for Stop

    @property
    def model(self) -> str:
        return self.settings["copilot_model"]

    # ------------------------------------------------------------------ the SDK's event loop and runtime process
    def _call(self, coro, timeout: float, live: bool = False):
        with self._lock:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                self._loop.set_exception_handler(lambda _l, ctx: log.info("copilot: %s", ctx.get("message", "")))
                threading.Thread(target=self._loop.run_forever, name="copilot", daemon=True).start()
                self._start_lock = asyncio.Lock()
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        if live:
            self._live.add(fut)
        try:
            return fut.result(timeout)
        except futures.CancelledError as e:
            raise LLMError("stopped") from e
        except (TimeoutError, futures.TimeoutError) as e:   # two classes before Python 3.11
            fut.cancel()
            raise LLMError(f"GitHub Copilot did not answer in {timeout:.0f} s") from e
        finally:
            self._live.discard(fut)

    def abort(self) -> None:
        for fut in list(self._live):
            fut.cancel()                     # the session of that answer is closed and deleted on the way out

    async def _ready(self):
        """The running client; started on the first call and again after the token changed."""
        async with self._start_lock:
            token = self.secret.get()
            if self._client is not None and token != self._client_token:
                await self._stop_client()
            if self._client is None:
                from copilot import CopilotClient, RuntimeConnection

                work = self.home / "work"
                work.mkdir(parents=True, exist_ok=True)
                runtime = RuntimeConnection.for_spawned_stdio(base_directory=str(self.home), working_directory=str(work))
                client = CopilotClient(mode="empty", runtime=runtime,
                                       github={"token": token} if token else {"use_logged_in_user": True})
                await client.start()
                self._client, self._client_token = client, token
            return self._client

    async def _drop(self, client) -> None:
        """Forget a runtime that is gone, unless another call already started a new one."""
        async with self._start_lock:
            if self._client is client:
                await self._stop_client()

    async def _stop_client(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            await client.stop()
        except Exception as e:
            log.info("stopping the Copilot runtime: %s", e)

    def stop(self) -> None:
        if self._client is not None:
            try:
                self._call(self._stop_client(), 15)
            except Exception as e:
                log.info("copilot stop: %s", e)

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        """Signed in? Which models? Costs nothing: no prompt is sent (the first call downloads the runtime)."""
        out = {"token": bool(self.secret.get()), "ok": False, "login": "", "detail": "", "models": [], "model": self.model}

        async def go():
            client = await self._ready()
            auth = await client.get_auth_status()
            return auth, (await client.list_models() if auth.is_authenticated else [])
        try:
            auth, models = self._call(go(), 300)
        except ImportError:
            out["detail"] = "the Copilot SDK is not installed: run powertools.cmd --setup"
            return out
        except Exception as e:
            out["detail"] = explain(e)
            return out
        out["login"] = auth.login or ""
        out["models"] = sorted(m.id for m in models)
        out["ok"] = bool(auth.is_authenticated)
        out["detail"] = "" if out["ok"] else (auth.status_message or "not signed in")
        if out["ok"] and self.model not in out["models"]:
            out["detail"] = f"this account does not offer {self.model}: pick another model"
        return out

    def days_left(self) -> int | None:
        """Days until the GitHub token expires; None: no token, no date, or not known yet. GitHub says it in a header of
        any API call (asked in the background every 6 hours, costs no credits)."""
        token = self.secret.get()
        if not token:
            return None
        e = self._expiry
        if (e["token"] != token or time.time() - e["at"] > 6 * 3600) and not e["busy"]:
            e.update(token=token, at=time.time(), busy=True, when=e["when"] if e["token"] == token else None)

            def ask() -> None:
                try:
                    req = urllib.request.Request("https://api.github.com/user", headers={
                        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "powertools"})
                    with urllib.request.urlopen(req, timeout=10) as r:
                        e["when"] = parse_expiry(r.headers.get("GitHub-Authentication-Token-Expiration", ""))
                except Exception as ex:
                    log.info("GitHub token date: %s", type(ex).__name__)
                finally:
                    e["busy"] = False
            threading.Thread(target=ask, name="github-expiry", daemon=True).start()
        return None if e["when"] is None else max(0, (e["when"] - datetime.now(timezone.utc)).days)

    def quota(self, refresh: bool = False) -> dict | None:
        """What the account has left (None: not known yet). Asked in the background every 5 minutes; opens a session but
        sends no prompt, so it costs nothing."""
        token, q = self.secret.get(), self._quota
        if q["token"] != token:
            q.update(token=token, at=0.0, view=None)
        if token and not q["busy"] and (refresh or time.time() - q["at"] > 300):
            q.update(at=time.time(), busy=True)

            def ask() -> None:
                try:
                    spent = self.spent()
                    q["view"] = {**quota_view(self._call(self._read_quota(), 300), datetime.now(timezone.utc)), "spent_then": spent}
                except Exception as e:
                    log.info("quota: %s", explain(e))
                finally:
                    q["busy"] = False
            threading.Thread(target=ask, name="copilot-quota", daemon=True).start()
        return q["view"]

    async def _read_quota(self) -> dict:
        client = await self._ready()
        session = await client.create_session(**self._isolated())
        try:
            try:
                q = await session.rpc.quota.refresh()
            except Exception:
                q = await session.rpc.quota.get()
            snaps = {k: {"entitlement": v.entitlement_requests, "used": v.used_requests, "remaining_pct": v.remaining_percentage,
                         "unlimited": v.is_unlimited_entitlement, "reset_ms": v.reset_date_epoch_ms, "tbb": v.token_based_billing}
                     for k, v in (q.snapshots or {}).items()}
            return {"plan": str(getattr(q.plan_tier, "value", q.plan_tier)), "tbb": q.is_tbb_user, "snapshots": snaps}
        finally:
            await self._discard(client, session)

    def set_token(self, token: str) -> dict:
        self.secret.set(token)
        return self.status()

    # ------------------------------------------------------------------ chat
    async def _ask(self, system: str, user: str, schema: dict | None, model: str, timeout: float):
        client = await self._ready()
        kw = self._isolated(model=model, system_message={"mode": "replace", "content": system})
        effort = self.settings["copilot_effort"]
        if effort:
            try:
                session = await client.create_session(**kw, inference={"reasoning_effort": effort})
            except Exception as e:
                if "effort" not in str(e).lower():
                    raise
                session = await client.create_session(**kw)   # a model without reasoning levels
        else:
            session = await client.create_session(**kw)
        try:
            send = {"response_schema": strict(schema)} if schema else {}
            event = await session.send_and_wait(user, timeout=timeout, **send)
            text = getattr(getattr(event, "data", None), "content", None) or ""
            tin = tout = 0
            credits = None
            try:
                m = await session.rpc.usage.get_metrics()
                tin = sum((x.usage.input_tokens or 0) for x in m.model_metrics.values())
                tout = sum((x.usage.output_tokens or 0) for x in m.model_metrics.values())
                credits = None if m.total_nano_aiu is None else m.total_nano_aiu / 1e9
            except Exception as e:
                log.info("no usage figures: %s", e)
            return text, tin, tout, credits
        finally:
            await self._discard(client, session)

    def _isolated(self, **more) -> dict:
        """A session that can only answer with text: no tools, skills or instructions, in an empty folder."""
        return {"working_directory": str(self.home / "work"), "tools": {"available_tools": []}, "permissions": {"on_request": _reject},
                "skills": {"enabled": False}, "enable_config_discovery": False, "instructions": {"skip_custom_instructions": True}, **more}

    @staticmethod
    async def _discard(client, session) -> None:
        try:
            await session.disconnect()
            await client.delete_session(session.session_id)   # nothing of the conversation stays on disk
        except Exception as e:
            log.info("closing the Copilot session: %s", e)

    def chat(self, system: str, user: str, schema: dict | None = None, priority: int = INTERACTIVE,
             max_tokens: int = 800, num_ctx: int = 0, timeout: float = 180, model: str = "") -> tuple[str, dict]:
        """One answer (JSON text when `schema` is given) and stats with the AI credits used. Raises LLMError."""
        model = model or self.model
        t0 = time.time()
        for attempt in (1, 2):
            used = self._client
            try:
                text, tin, tout, credits = self._call(self._ask(system, user, schema, model, timeout), timeout + 120,
                                                  live=priority == INTERACTIVE)
                break
            except LLMError:
                raise
            except ImportError as e:
                raise LLMError("the Copilot SDK is not installed: run powertools.cmd --setup") from e
            except Exception as e:
                if attempt == 2 or not runtime_gone(e):
                    raise LLMError(explain(e)) from e
                log.warning("the Copilot runtime is gone (%s): starting it again", type(e).__name__)
                self._call(self._drop(used), 30)
        if not text.strip():
            raise LLMError("GitHub Copilot returned an empty answer")
        took = round(time.time() - t0, 1)
        return text, {"model": model, "total_s": took, "prompt_tokens": tin, "tokens": tout, "credits": credits,
                      "tok_s": round(tout / took, 1) if took else 0}
