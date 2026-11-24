"""Copilot against a fake SDK, and the names powertools uses against the real one (where it is installed)."""

import asyncio
import inspect
import sys
import time
import types
from types import SimpleNamespace as NS

import pytest

from powertools.config import Settings
from powertools.llm import LLMError

from .test_assistant import Model, make


class Session:
    def __init__(self, sdk):
        self.sdk, self.session_id = sdk, f"s{len(sdk.sessions)}"
        snap = NS(entitlement_requests=4000.0, used_requests=1000.0, remaining_percentage=75.0, is_unlimited_entitlement=False,
                  reset_date_epoch_ms=None, token_based_billing=True)
        quota = NS(snapshots={"premium_interactions": snap}, plan_tier=NS(value="business"), is_tbb_user=True)

        async def metrics():
            return NS(model_metrics={"m": NS(usage=NS(input_tokens=90, output_tokens=10))}, total_nano_aiu=2.5e9)

        async def refresh():
            if sdk.no_refresh:
                raise RuntimeError("not supported")
            return quota

        async def get():
            return quota
        self.rpc = NS(usage=NS(get_metrics=metrics), quota=NS(refresh=refresh, get=get))

    async def send_and_wait(self, prompt, timeout=60, **kw):
        self.sdk.sent.append({"prompt": prompt, **kw})
        if self.sdk.slow:
            await asyncio.sleep(30)
        return NS(data=NS(content=self.sdk.answer))

    async def disconnect(self):
        self.sdk.closed.append(self.session_id)


class SDK:
    def __init__(self):
        self.sessions, self.sent, self.closed, self.deleted, self.clients, self.stopped = [], [], [], [], [], 0
        self.answer, self.slow, self.no_refresh, self.no_effort = '{"a": 1}', False, False, False

    def module(self):
        sdk = self

        class Client:
            def __init__(self, **kw):
                sdk.clients.append(kw)

            async def start(self):
                pass

            async def stop(self):
                sdk.stopped += 1

            async def get_auth_status(self):
                return NS(is_authenticated=True, login="me", status_message="")

            async def list_models(self):
                return [NS(id="gpt-6-luna"), NS(id="other")]

            async def create_session(self, **kw):
                if sdk.no_effort and "inference" in kw:
                    raise RuntimeError("reasoning effort is not supported by this model")
                sdk.sessions.append(kw)
                return Session(sdk)

            async def delete_session(self, sid):
                sdk.deleted.append(sid)
        m = types.ModuleType("copilot")
        m.CopilotClient = Client
        m.RuntimeConnection = NS(for_spawned_stdio=lambda **kw: kw)
        return m


@pytest.fixture()
def cop(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path))
    from powertools.copilot import Copilot

    sdk = SDK()
    monkeypatch.setitem(sys.modules, "copilot", sdk.module())
    s = Settings(tmp_path / "s.json")
    c = Copilot(s, home=tmp_path / "c")
    c.secret.set("github_pat_x")
    yield c, sdk, s
    c.stop()


def test_every_question_is_an_isolated_session_that_is_deleted(cop):
    c, sdk, s = cop
    text, stats = c.chat("system", "user", {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]})
    k = sdk.sessions[0]
    assert text == '{"a": 1}' and stats["credits"] == 2.5 and stats["prompt_tokens"] == 90
    assert k["tools"] == {"available_tools": []} and k["skills"] == {"enabled": False} and k["enable_config_discovery"] is False
    assert k["instructions"] == {"skip_custom_instructions": True} and k["system_message"] == {"mode": "replace", "content": "system"}
    assert sdk.clients[0]["mode"] == "empty" and sdk.sent[0]["response_schema"]["additionalProperties"] is False
    assert sdk.closed == ["s1"] and sdk.deleted == ["s1"]
    c.chat("system", "again")
    assert len(sdk.clients) == 1                                              # one runtime for all


def test_a_model_without_reasoning_levels_still_answers(cop):
    c, sdk, s = cop
    sdk.no_effort = True
    s.update({"copilot_effort": "low"})
    assert c.chat("s", "u")[0] and "inference" not in sdk.sessions[0]


def test_a_new_token_starts_a_new_runtime(cop):
    c, sdk, s = cop
    c.chat("s", "u")
    c.secret.set("github_pat_y")
    c.chat("s", "u")
    assert len(sdk.clients) == 2 and sdk.stopped == 1 and sdk.clients[1]["github"] == {"token": "github_pat_y"}


def test_an_empty_answer_is_an_error(cop):
    c, sdk, s = cop
    sdk.answer = " "
    with pytest.raises(LLMError, match="empty"):
        c.chat("s", "u")


def test_stop_ends_the_answer_and_its_session(cop):
    import threading

    c, sdk, s = cop
    sdk.slow = True
    threading.Timer(0.3, c.abort).start()
    t0 = time.time()
    with pytest.raises(LLMError, match="stopped"):
        c.chat("s", "u")
    for _ in range(50):
        if sdk.deleted:
            break
        time.sleep(0.02)
    assert time.time() - t0 < 5 and sdk.deleted                               # nothing of it stays on disk


@pytest.mark.parametrize("no_refresh", [False, True])
def test_the_quota_is_read_without_a_prompt(cop, no_refresh):
    c, sdk, s = cop
    sdk.no_refresh = no_refresh
    assert c.quota() is None                                                  # asked in the background
    for _ in range(100):
        if c.quota():
            break
        time.sleep(0.02)
    q = c.quota()
    assert q["left"] == 3000 and q["unit"] == "AI credits" and q["plan"] == "business" and sdk.sent == [] and sdk.deleted


def test_status_names_the_account_and_a_missing_model(cop):
    c, sdk, s = cop
    assert c.status()["login"] == "me" and c.status()["ok"]
    s.update({"copilot_model": "gone"})
    assert "does not offer gone" in c.status()["detail"]


def test_the_real_sdk_has_what_powertools_uses():
    copilot = pytest.importorskip("copilot")
    if not hasattr(copilot, "RuntimeConnection"):
        pytest.skip("another package of that name")
    from copilot import rpc
    from copilot.session import CopilotSession

    new = inspect.signature(copilot.CopilotClient.create_session).parameters
    assert {"model", "system_message", "working_directory", "tools", "permissions", "skills", "enable_config_discovery", "instructions",
            "inference"} <= set(new)
    assert {"mode", "runtime", "github"} <= set(inspect.signature(copilot.CopilotClient.__init__).parameters)
    assert "response_schema" in inspect.signature(CopilotSession.send_and_wait).parameters
    assert {"is_tbb_user", "plan_tier", "snapshots"} <= set(rpc.SessionQuotaRefreshResult.__dataclass_fields__)
    assert {"entitlement_requests", "used_requests", "remaining_percentage", "is_unlimited_entitlement", "reset_date_epoch_ms",
            "token_based_billing"} <= set(rpc.SessionQuotaSnapshot.__dataclass_fields__)
    assert hasattr(rpc, "PermissionDecisionReject")


def test_comparing_models_counts_agreement_and_errors(tmp_path):
    a, g = make(tmp_path)
    a.updates.start()
    a.actions.act("mail:cv1", "right")
    out = a.compare("copilot")
    assert out["items"] >= 3 and out["same"] == out["items"] and out["right"] == out["reviewed"] == 1 and out["credits"] > 0
    a.kit.llm = Model()
    a.kit.llm.error = "limit"
    assert a.compare("copilot")["errors"] == 3
    a.state.data["items"].clear()
    with pytest.raises(ValueError, match="run an Update"):
        a.compare("local")
