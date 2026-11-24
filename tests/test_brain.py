"""Model providers: the local one is the default, Copilot only when chosen, counted and limited; sign-in errors."""

import pytest

from powertools import brain, copilot
from powertools.config import Settings
from powertools.llm import LLMError


class Fake:
    def __init__(self, name, credits=None):
        self.name, self.credits, self.calls = name, credits, []

    def chat(self, system, user, **kw):
        self.calls.append(kw)
        return self.name, {"model": self.name, "credits": self.credits, "prompt_tokens": 100, "tokens": 20, "total_s": 1}

    def available(self):
        return {"installed": False, "running": False}


def make(tmp_path, credits=30.0):
    s = Settings(tmp_path / "s.json")
    return s, brain.Brain(s, Fake("local"), Fake("cloud", credits), brain.Usage(tmp_path / "u.json"))


def test_nothing_goes_to_copilot_until_the_user_picks_it(tmp_path):
    s, b = make(tmp_path)
    assert s["ai_provider"] == "" and not b.is_cloud
    assert b.chat("s", "u", task="triage")[0] == "local"
    a = b.available()
    assert a["provider"] == "local" and not a["chosen"] and not a["installed"]
    s.update({"ai_provider": "copilot"})
    assert b.chat("s", "u", task="triage")[0] == "cloud" and b.available()["installed"]
    with pytest.raises(ValueError):
        s.update({"ai_provider": "openai"})


def test_credits_are_counted_by_task_and_the_daily_limit_stops_calls(tmp_path):
    s, b = make(tmp_path)
    s.update({"ai_provider": "copilot", "copilot_daily_credits": 50})
    b.chat("s", "u", task="triage")
    b.chat("s", "u", task="draft")
    t = b.usage.today()
    assert t["calls"] == 2 and t["credits"] == 60.0 and t["by"]["draft"]["calls"] == 1 and b.usage.month() == 60.0
    with pytest.raises(LLMError, match="daily limit"):
        b.chat("s", "u")
    assert b.chat("s", "u", provider="local")[0] == "local"              # the local model has no limit
    assert brain.Usage(tmp_path / "u.json").today()["calls"] == 2        # survives a restart


def test_strict_schema_closes_every_object():
    s = {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "array", "items": {
        "type": "object", "properties": {"c": {"type": "string"}}, "required": []}}}, "required": ["a"]}
    out = copilot.strict(s)
    assert out["required"] == ["a", "b"] and out["additionalProperties"] is False
    assert out["properties"]["b"]["items"]["required"] == ["c"] and "additionalProperties" not in s


def test_a_copilot_refusal_is_explained():
    assert copilot.explain(RuntimeError('{"status": 403} copilot')) == copilot.NO_LICENCE
    assert copilot.explain(RuntimeError("Not authenticated. Please authenticate first.")) == copilot.NOT_SIGNED_IN
    assert "refused the token" in copilot.explain(RuntimeError("(401): GitHub returned: Bad credentials"))


def test_the_github_token_is_stored_for_this_user_only(tmp_path):
    st = copilot.SecretStore(tmp_path / "t.bin")
    for bad in ("ghp_classic", "hello", "github_pat_ with space"):
        with pytest.raises(ValueError):
            st.set(bad)
    st.set(" github_pat_abc ")
    assert st.get() == "github_pat_abc"
    st.clear()
    assert st.get() == ""
