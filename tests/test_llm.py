"""Local model features against a fake Ollama and a fake Graph: clean-up guard, wording, recap, test verdict."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from powertools import cleanup, llm, recap, wording
from powertools.config import Settings
from powertools.dictation import Dictation
from powertools.store import Store
from tests.test_app_core import FakeEngine, FakeRecorder
from tests.test_graph_export import FakeGraph

NOW = datetime.now(timezone.utc)


def iso(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeLLM:
    """chat() answers from a list (str or dict -> JSON); records what it was asked."""

    def __init__(self, *answers, error=None):
        self.answers, self.error, self.calls = list(answers), error, []

    def chat(self, system, user, schema=None, **kw):
        self.calls.append({"system": system, "user": user, "schema": schema, **kw})
        if self.error:
            raise llm.LLMError(self.error)
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if callable(a):
            self.answers.insert(0, a)
            a = a(user)
        return (json.dumps(a) if isinstance(a, dict) else a), {"model": "m", "total_s": 0.5, "tok_s": 30}


def test_settings_for_the_local_model(tmp_path):
    s = Settings(tmp_path / "s.json")
    assert s["llm_model"] == "gemma4:e4b" and s["wording_hotkey"] == "alt+w" and not s["dictation_cleanup"]
    s.update({"wording_hotkey": " Ctrl+W ", "llm_gpu": "cpu", "llm_keep_alive_min": "0"})
    assert s["wording_hotkey"] == "ctrl+w" and s["llm_keep_alive_min"] == 0
    for bad in ({"llm_gpu": "tpu"}, {"wording_mode": "auto"}, {"llm_model": "two words"}, {"llm_keep_alive_min": 500}):
        with pytest.raises(ValueError):
            s.update(bad)


def test_parse_json_tolerates_a_fence():
    assert llm.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(llm.LLMError):
        llm.parse_json("sure, here it is")


# ---------------------------------------------------------------- dictation clean-up
def test_cleanup_keeps_the_transcript_when_the_model_misbehaves():
    text = "yyy no więc wyślij mi proszę ten plik do jutra"
    assert cleanup.clean(FakeLLM("Wyślij mi, proszę, ten plik do jutra."), text)[0] == "Wyślij mi, proszę, ten plik do jutra."
    assert cleanup.clean(FakeLLM("OK."), text)[0] == text                                   # ate the sentence
    assert cleanup.clean(FakeLLM(text + " i coś jeszcze dodane od siebie"), text)[0] == text   # added words
    kept, note = cleanup.clean(FakeLLM(error="Ollama is not running"), text)
    assert kept == text and "not running" in note
    assert cleanup.clean(FakeLLM("x"), "krótko")[0] == "krótko"                              # too short to bother


def test_dictation_with_cleanup_keeps_raw_in_history(tmp_path):
    import time

    s = Settings(tmp_path / "s.json")
    s.update({"dictation_cleanup": True, "replacements": [{"from": "merlo", "to": "Merlot"}]})
    pasted, states = [], []
    fake = FakeLLM("Sprawdź, proszę, regułę w merlo dzisiaj.")
    d = Dictation(s, FakeEngine("yyy sprawdź proszę regułę w merlo dzisiaj"), FakeRecorder(2.0),
                  Store(tmp_path / "h.sqlite"), lambda t, paste, restore: pasted.append(t), states.append,
                  cleaner=lambda t: cleanup.clean(fake, t))
    d.toggle(); d.toggle()
    for _ in range(100):
        if pasted:
            break
        time.sleep(0.02)
    assert pasted == ["Sprawdź, proszę, regułę w Merlot dzisiaj."]
    row = d.store.recent()[0]
    assert row["raw"] == "yyy sprawdź proszę regułę w merlo dzisiaj" and row["text"] == pasted[0]
    for _ in range(50):
        if states[-1] == "idle":
            break
        time.sleep(0.02)
    assert states[-1] == "idle"


# ---------------------------------------------------------------- wording
ANSWER = {"language": "English", "improved": "Regarding our meeting yesterday, I am sending you the rules.",
          "issues": [{"wrong": "regarding to", "better": "regarding", "why": "no 'to' after regarding"},
                     {"wrong": "same", "better": "same", "why": "no change"}]}
TEXT = "Regarding to our yesterday meeting I send you the rules."


def make_wording(tmp_path, answer=ANSWER, copied=TEXT, mode="review", installed=True):
    s = Settings(tmp_path / "s.json")
    s.update({"wording_mode": mode})
    out = {"pasted": [], "states": [], "shown": 0}

    def show():
        out["shown"] += 1
    model = FakeLLM(answer)
    model.available = lambda: {"installed": installed, "running": installed}
    w = wording.Wording(s, model, lambda t, paste, restore: out["pasted"].append(t), out["states"].append,
                        show, copy=lambda: copied)
    return w, out


def test_wording_review_shows_the_window(tmp_path):
    w, out = make_wording(tmp_path)
    w.hotkey()
    assert out["shown"] == 1 and out["pasted"] == []
    assert w.last["state"] == "done" and w.last["changed"]
    assert [i["wrong"] for i in w.last["issues"]] == ["regarding to"]   # the non-change is dropped
    assert out["states"] == ["thinking", "idle"]


def test_wording_replace_pastes_over_the_selection(tmp_path):
    w, out = make_wording(tmp_path, mode="replace")
    w.hotkey()
    assert out["pasted"] == [ANSWER["improved"]] and out["shown"] == 0 and w.last["replaced"]


def test_wording_nothing_selected_and_not_english(tmp_path):
    w, out = make_wording(tmp_path, copied="")
    w.hotkey()
    assert w.last["state"] == "error" and "Nothing selected" in w.last["error"] and out["shown"] == 1
    w, _ = make_wording(tmp_path, answer={"language": "Polish", "issues": [], "improved": "Cześć"}, copied="Cześć")
    r = w.check("Cześć")
    assert r["issues"] == [] and not r["changed"]
    with pytest.raises(ValueError):
        w.check("  ")


def test_wording_hotkey_without_ollama_stays_quiet(tmp_path):
    w, out = make_wording(tmp_path, installed=False, copied="should not be copied")
    w.hotkey()
    assert w.last["state"] == "error" and "Ollama" in w.last["error"]
    assert out["shown"] == 0 and out["states"] == ["error"] and w.llm.calls == []


def test_available_is_cached(tmp_path, monkeypatch):
    o = llm.Ollama(Settings(tmp_path / "s.json"))
    calls = []
    monkeypatch.setattr(llm, "find_exe", lambda: "")
    monkeypatch.setattr(o, "running", lambda: calls.append(1) or False)
    assert o.available() == {"installed": False, "running": False}
    o.available(); o.available()
    assert len(calls) == 1


# ---------------------------------------------------------------- recap
class FakeJobs:
    def __init__(self):
        self.all = []

    def submit(self, title, run):
        from powertools.jobs import Job
        job = Job(title, run)
        run(job)              # synchronous for the test
        job.state = "done"
        self.all.append(job)
        return job


class FakeService:
    def __init__(self, g):
        self.g, self.jobs = g, FakeJobs()


ME = {"id": "u-me", "displayName": "Piotr B", "mail": "me@example.invalid", "userPrincipalName": "me@example.invalid"}


def addr(name, a):
    return {"emailAddress": {"name": name, "address": a}}


def recap_graph():
    asks = [{"id": "e1", "conversationId": "cv1", "subject": "Review of rule 12", "receivedDateTime": iso(5),
             "from": addr("Anna", "anna@example.invalid"), "toRecipients": [addr("Piotr B", "me@example.invalid")],
             "body": {"contentType": "text", "content": "Can you review rule 12 by Friday?"}, "webLink": "https://o/e1"}]
    news = [{"id": "e2", "conversationId": "cv2", "subject": "Weekly digest", "receivedDateTime": iso(3),
             "from": addr("News", "no-reply@example.invalid"), "body": {"contentType": "text", "content": "Top stories"}}]
    answered = [{"id": "e3", "conversationId": "cv3", "subject": "Lunch", "receivedDateTime": iso(8),
                 "from": addr("Ola", "ola@example.invalid"), "body": {"contentType": "text", "content": "Lunch?"}},
                {"id": "e4", "conversationId": "cv3", "subject": "RE: Lunch", "sentDateTime": iso(7), "receivedDateTime": iso(7),
                 "from": addr("Piotr B", "me@example.invalid"), "body": {"contentType": "text", "content": "Sure, 12:30"}}]
    threads = {"cv1": asks, "cv2": news, "cv3": answered}

    def messages(params):
        f = params.get("$filter", "")
        for cid, msgs in threads.items():
            if f"'{cid}'" in f:
                return {"value": msgs}
        raise AssertionError(f)
    inbox = {"value": asks + news + answered[:1]}
    chat = {"value": [{"id": "c1", "chatType": "oneOnOne", "members": [{"displayName": "Jan", "userId": "u-jan"}],
                       "lastMessagePreview": {"createdDateTime": iso(2)}, "webUrl": "https://t/c1"},
                      {"id": "c2", "chatType": "group", "topic": "Old", "members": [],
                       "lastMessagePreview": {"createdDateTime": iso(24 * 20)}}]}
    chat_msgs = {"value": [{"id": "t1", "messageType": "message", "createdDateTime": iso(2),
                            "from": {"user": {"id": "u-jan", "displayName": "Jan"}},
                            "body": {"contentType": "text", "content": "Do you have a minute to answer my question?"}}]}
    return FakeGraph({"/me/mailFolders/inbox/messages": inbox, "/me/messages": messages, "/me/chats": chat,
                      "/chats/c1/messages": chat_msgs, "/me": ME})


def test_recap_sorts_caches_and_learns(tmp_path):
    s = Settings(tmp_path / "s.json")
    def answer(user):
        if "rule 12" in user:
            return {"category": "todo", "summary": "Rule 12 review.", "action": "Review rule 12", "due": "Friday"}
        if "Lunch" in user:
            return {"category": "fyi", "summary": "Lunch agreed.", "action": "", "due": ""}
        return {"category": "reply", "summary": "Jan has a question.", "action": "Answer Jan", "due": ""}
    model = FakeLLM(answer)
    r = recap.Recap(s, FakeService(recap_graph()), model, tmp_path / "recap.json")
    r.start({"kind": "days", "days": 2}, ["mail", "teams"])
    v = r.view()
    by = {i["title"]: i for i in v["items"]}
    assert set(by) == {"Review of rule 12", "Weekly digest", "Lunch", "Jan"}        # the 20-day-old chat is out
    assert by["Weekly digest"]["category"] == "ignore" and by["Weekly digest"]["by"] == "rule"
    assert by["Review of rule 12"]["action"] == "Review rule 12" and by["Review of rule 12"]["link"] == "https://o/e1"
    assert by["Lunch"]["last_from_me"] and by["Lunch"]["category"] == "fyi"
    assert [("(The latest message is from me.)" in c["user"]) for c in model.calls] == [False, False, True]
    assert by["Jan"]["source"] == "teams" and by["Jan"]["category"] == "reply"
    assert len(model.calls) == 3 and "Piotr B" in model.calls[0]["system"]
    assert all("text" not in i for i in v["items"])                                  # the window never gets the bodies

    r.correct("mail:cv3", "ignore")
    assert r.view()["reviewed"] == 1 and r.view()["agreed"] == 0
    # a second run asks the model only about what changed; the correction survives
    r2 = recap.Recap(s, FakeService(recap_graph()), model, tmp_path / "recap.json")
    r2.start({"kind": "days", "days": 2}, ["mail", "teams"])
    assert len(model.calls) == 3
    assert {i["key"]: i.get("user_category") for i in r2.view()["items"]}["mail:cv3"] == "ignore"
    with pytest.raises(ValueError):
        r2.start({"kind": "all"}, [])


def test_recap_survives_a_model_error(tmp_path):
    s = Settings(tmp_path / "s.json")
    r = recap.Recap(s, FakeService(recap_graph()), FakeLLM(error="Ollama is not running"), tmp_path / "recap.json")
    r.start({"kind": "days", "days": 2}, ["mail"])
    items = r.view()["items"]
    assert any(i.get("error") for i in items)
    assert not r.data["cache"] or all("error" not in c for c in r.data["cache"].values())


# ---------------------------------------------------------------- Ollama client + test verdict
def test_ollama_test_reports_speed_and_gpu_share(tmp_path, monkeypatch):
    s = Settings(tmp_path / "s.json")
    o = llm.Ollama(s)
    calls = []

    def fake_req(method, path, body=None, timeout=10):
        calls.append((method, path, dict(body) if body else body))
        if path == "/api/version":
            return {"version": "0.31.0"}
        if path == "/api/ps":
            return {"models": [{"name": "gemma4:e4b", "size": 8_000_000_000, "size_vram": 4_000_000_000}]}
        if path == "/api/chat":
            if "think" in body:
                raise llm.LLMError('Ollama HTTP 400: "gemma4:e4b" does not support thinking')
            return {"message": {"content": json.dumps({"language": "English", "issues": [
                {"wrong": "regarding to", "better": "regarding", "why": "x"}], "improved": "Hi Mark, ..."})},
                    "load_duration": 3e9, "total_duration": 6e9, "eval_count": 120, "eval_duration": 4e9,
                    "prompt_eval_count": 300, "prompt_eval_duration": 1e9}
        raise AssertionError(path)
    monkeypatch.setattr(o, "_req", fake_req)
    monkeypatch.setattr(llm, "gpu_info", lambda: {"name": "RTX A1000", "total_mb": 6144, "used_mb": 3000, "free_mb": 3144})
    t = o.test()
    assert t["tok_s"] == 30.0 and t["load_s"] == 3.0 and t["gpu_share"] == 50 and t["json_ok"] and t["issues"] == 1
    assert t["verdict"].startswith("fast") and "only 50% of the model fits" in t["verdict"]
    chat = [b for m, p, b in calls if p == "/api/chat"]
    assert chat[0]["think"] is False and "think" not in chat[1]                  # retried without the switch
    assert chat[1]["options"]["temperature"] == 0 and chat[1]["keep_alive"] == "5m"

    s.update({"llm_gpu": "cpu"})
    o.chat("sys", "hi")
    assert [b for m, p, b in calls if p == "/api/chat"][-1]["options"]["num_gpu"] == 0


def test_ollama_not_running(tmp_path, monkeypatch):
    o = llm.Ollama(Settings(tmp_path / "s.json"), url="http://127.0.0.1:9")
    monkeypatch.setattr(llm, "find_exe", lambda: "")
    monkeypatch.setattr(llm, "gpu_info", lambda: {})
    st = o.status()
    assert not st["running"] and not st["installed"]
    assert st["download"].startswith("https://github.com/ollama/ollama/") and st["download"].endswith(".zip")
    with pytest.raises(llm.LLMError):
        o.chat("s", "u")
    o.unload_all()  # no-op, no exception


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _fake_urlopen(payload: bytes):
    import io

    class Resp(io.BytesIO):
        headers = {"Content-Length": str(len(payload))}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

    return lambda req, timeout=0: Resp(payload)


def test_ollama_install_downloads_github_zip_and_unpacks_locally(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path))
    o = llm.Ollama(Settings(tmp_path / "s.json"))
    started = []
    monkeypatch.setattr(o, "ensure_running", lambda: started.append(llm.find_exe()) or True)
    payload = _zip_bytes({"ollama.exe": b"MZ-fake", "lib/ollama/ggml-base.dll": b"DLL"})
    monkeypatch.setattr(llm.urllib.request, "urlopen", _fake_urlopen(payload))
    monkeypatch.setattr(llm, "DOWNLOAD_SHA256", hashlib.sha256(payload).hexdigest())
    events = []
    exe = o.install(lambda st, done, total: events.append((st, done, total)))
    assert exe == str(tmp_path / "ollama" / "ollama.exe")
    assert (tmp_path / "ollama" / "ollama.exe").read_bytes() == b"MZ-fake"
    assert (tmp_path / "ollama" / "lib" / "ollama" / "ggml-base.dll").read_bytes() == b"DLL"
    assert not (tmp_path / "downloads" / "ollama-windows-amd64.zip").exists()
    assert [e[0] for e in events][0] == "downloading" and ("unpacking", len(payload), len(payload)) in events
    assert events[-1] == ("starting", len(payload), len(payload))
    assert started == [exe] and llm.find_exe() == exe
    assert o.available() == {"installed": True, "running": True}


def test_ollama_install_handles_nested_zip_and_rejects_bad_archive(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path))
    o = llm.Ollama(Settings(tmp_path / "s.json"))
    monkeypatch.setattr(o, "ensure_running", lambda: True)

    # Not a zip at all
    monkeypatch.setattr(llm.urllib.request, "urlopen", _fake_urlopen(b"not a zip"))
    with pytest.raises(llm.LLMError, match="not a valid ZIP"):
        o.install(lambda *a: None)
    assert not (tmp_path / "ollama").exists()

    # Zip that does not match the pinned release
    monkeypatch.setattr(llm.urllib.request, "urlopen", _fake_urlopen(_zip_bytes({"ollama.exe": b"MZ-other"})))
    with pytest.raises(llm.LLMError, match="SHA-256"):
        o.install(lambda *a: None)
    assert not (tmp_path / "ollama").exists() and not list((tmp_path / "downloads").glob("*.zip"))

    # Zip without ollama.exe
    readme = _zip_bytes({"readme.txt": b"hi"})
    monkeypatch.setattr(llm.urllib.request, "urlopen", _fake_urlopen(readme))
    monkeypatch.setattr(llm, "DOWNLOAD_SHA256", hashlib.sha256(readme).hexdigest())
    with pytest.raises(llm.LLMError, match="ollama.exe"):
        o.install(lambda *a: None)
    assert not (tmp_path / "ollama").exists()

    # Zip with a wrapper directory
    nested = _zip_bytes({"ollama-win/ollama.exe": b"MZ-nested", "ollama-win/lib/ollama/cuda.dll": b"CUDA"})
    monkeypatch.setattr(llm.urllib.request, "urlopen", _fake_urlopen(nested))
    monkeypatch.setattr(llm, "DOWNLOAD_SHA256", hashlib.sha256(nested).hexdigest())
    exe = o.install(lambda *a: None)
    assert exe == str(tmp_path / "ollama" / "ollama.exe")
    assert (tmp_path / "ollama" / "lib" / "ollama" / "cuda.dll").read_bytes() == b"CUDA"


def test_ollama_pull_downloads_gemma_from_huggingface_and_registers_locally(tmp_path, monkeypatch):
    import hashlib

    assert llm.HF_MODELS["gemma4:e2b"]["repo"] == "google/gemma-4-E2B-it-qat-q4_0-gguf"
    assert llm.HF_MODELS["gemma4:e4b"]["repo"] == "google/gemma-4-E4B-it-qat-q4_0-gguf"

    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "models"))
    o = llm.Ollama(Settings(tmp_path / "s.json"))
    monkeypatch.setattr(o, "ensure_running", lambda: True)

    gguf = b"GGUF\x03\x00\x00\x00fake-gemma-weights"
    digest = "sha256:" + hashlib.sha256(gguf).hexdigest()
    monkeypatch.setitem(llm.HF_MODELS, "gemma4:e2b", {
        **llm.HF_MODELS["gemma4:e2b"], "digest": digest, "size": len(gguf),
    })
    manifest = json.dumps({
        "schemaVersion": 2,
        "layers": [{"mediaType": "application/vnd.ollama.image.model", "digest": digest, "size": len(gguf)}],
    }).encode()

    http_calls = []
    create_calls = []

    def fake_urlopen(req, timeout=0):
        url = req.full_url
        http_calls.append((req.get_method(), url))
        if url.endswith("/manifests/latest"):
            return _fake_urlopen(manifest)(req, timeout)
        if "/blobs/sha256:" in url and "huggingface.co" in url:
            return _fake_urlopen(gguf)(req, timeout)
        if "/api/blobs/" in url and req.get_method() == "HEAD":
            return _fake_urlopen(b"")(req, timeout)
        raise AssertionError(f"unexpected URL: {req.get_method()} {url}")

    def fake_req(method, path, body=None, timeout=10):
        create_calls.append((method, path, dict(body) if body else body))
        if len(create_calls) == 1:
            raise llm.LLMError("Ollama HTTP 400: unknown field renderer")
        return {"status": "success"}

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(o, "_req", fake_req)

    events = []
    o.pull("gemma4:e2b", lambda st, done, total: events.append((st, done, total)))
    blob_file = tmp_path / "models" / "blobs" / f"sha256-{digest.split(':')[1]}"
    assert blob_file.exists() and blob_file.read_bytes() == gguf
    assert any("huggingface.co/v2/google/gemma-4-E2B-it-qat-q4_0-gguf" in u for _, u in http_calls)
    assert not any("/api/pull" in u for _, u in http_calls)
    assert len(create_calls) == 2
    assert create_calls[0][2]["model"] == "gemma4:e2b"
    assert create_calls[0][2]["renderer"] == "gemma4"
    assert "renderer" not in create_calls[1][2]
    assert events[-1] == ("success", len(gguf), len(gguf))

    # Second pull uses the cached blob on disk without re-downloading the GGUF from Hugging Face
    http_calls.clear()
    o.pull("gemma4:e2b", lambda *a: None)
    assert not any("/blobs/sha256:" in u and "huggingface.co" in u for _, u in http_calls)

    # A cached blob that does not match its SHA-256 is downloaded again
    blob_file.write_bytes(b"GGUF tampered")
    http_calls.clear()
    o.pull("gemma4:e2b", lambda *a: None)
    assert blob_file.read_bytes() == gguf
    assert any("/blobs/sha256:" in u and "huggingface.co" in u for _, u in http_calls)

    # Only the pinned models
    for name in ("hf.co/someone/evil-GGUF:Q4_K_M", "someone/evil", "llama3"):
        with pytest.raises(llm.LLMError, match="only gemma4:e2b, gemma4:e4b"):
            o.pull(name, lambda *a: None)


def test_ollama_pull_rejects_bad_gguf_or_sha256_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "models"))
    o = llm.Ollama(Settings(tmp_path / "s.json"))
    monkeypatch.setattr(o, "ensure_running", lambda: True)

    # HTML error page instead of GGUF
    monkeypatch.setattr(llm.urllib.request, "urlopen", _fake_urlopen(b"<html>blocked</html>"))
    with pytest.raises(llm.LLMError, match="not a valid GGUF"):
        o.pull("gemma4:e2b", lambda *a: None)

    # Valid GGUF header, but SHA-256 does not match the pinned official Google digest
    bad_gguf = b"GGUF\x03\x00\x00\x00tampered"
    monkeypatch.setitem(llm.HF_MODELS, "gemma4:e2b", {
        **llm.HF_MODELS["gemma4:e2b"], "digest": "sha256:" + "0" * 64, "size": len(bad_gguf),
    })
    monkeypatch.setattr(llm.urllib.request, "urlopen", _fake_urlopen(bad_gguf))
    with pytest.raises(llm.LLMError, match="SHA-256"):
        o.pull("gemma4:e2b", lambda *a: None)


def test_ollama_delete_unloads_then_removes(tmp_path, monkeypatch):
    o = llm.Ollama(Settings(tmp_path / "s.json"))
    calls = []

    def fake_req(method, path, body=None, timeout=10):
        calls.append((method, path, dict(body) if body else body))
        return {"models": [{"name": "gemma4:e4b", "size": 1, "size_vram": 1}]} if path == "/api/ps" else {}
    monkeypatch.setattr(o, "_req", fake_req)
    o.delete("gemma4:e4b")
    assert calls[1:] == [("POST", "/api/generate", {"model": "gemma4:e4b", "keep_alive": 0}),
                         ("DELETE", "/api/delete", {"model": "gemma4:e4b"})]
    calls.clear()
    o.delete("gemma4:e2b")                     # not loaded: nothing to unload
    assert [p for m, p, b in calls] == ["/api/ps", "/api/delete"]


def test_recap_start_clears_the_old_one_and_shows_the_step(tmp_path):
    s = Settings(tmp_path / "s.json")
    r = recap.Recap(s, FakeService(recap_graph()), FakeLLM(lambda u: {"category": "fyi"}), tmp_path / "recap.json")
    r.start({"kind": "days", "days": 2}, ["mail"])
    assert r.view()["items"] and r.view()["current"] == ""                       # finished: no step shown

    class Waiting(FakeJobs):
        def submit(self, title, run):
            from powertools.jobs import Job
            self.all.append(Job(title, run))                                     # queued behind an export
            return self.all[-1]
    r.graph.jobs = Waiting()
    r.start({"kind": "days", "days": 7}, ["teams"])
    v = r.view()
    assert v["items"] == [] and v["range"]["days"] == 7 and "waiting" in v["current"]
    assert r.data["cache"]                                                        # the cache stays


def test_wording_style_rewrite_uses_its_own_prompt(tmp_path):
    w, out = make_wording(tmp_path)
    w.check("Hi Mark, regarding to our meeting.", "shorter")
    assert w.last["style"] == "shorter" and "shorter" in w.llm.calls[-1]["system"]
    w.check("Hi Mark, regarding to our meeting.")
    assert w.last["style"] == "" and "Do NOT rewrite for style" in w.llm.calls[-1]["system"]
    with pytest.raises(ValueError):
        w.check("text", "poetic")
