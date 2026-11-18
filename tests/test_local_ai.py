from copy import deepcopy
import json
import socket
import urllib.error

import pytest

from lyric_analyzer import local_ai as ai
from lyric_analyzer.analysis.comparison import compare
from lyric_analyzer.analysis.expressions import detect
from lyric_analyzer.analysis.report import build_report
from lyric_analyzer.backup import checksum, export_backup, inspect_backup, restore_backup
from lyric_analyzer.history import HistoryStore
from lyric_analyzer.storage import Library


def response(request):
    return {"line_id": request["line_id"], "original": request["original"], "reason": "情景を具体化する",
            "candidates": [{"direction": direction, "text": text} for direction, text in
                           zip(ai.DIRECTIONS, ("改札で切符を握る", "窓辺に切符を置く", "切符が朝を待っている"))]}


def fake_transport(monkeypatch, transform=None):
    calls = []
    def request(path, payload=None, timeout=5):
        calls.append((path, payload, timeout))
        if path == "/api/tags":
            return {"models": [{"name": "local:3b"}, {"name": "cloud:latest"}, {"name": "alias", "remote_host": "https://example.com"}]}
        if path == "/api/show":
            return {"model_info": {"general.architecture": "test"}}
        data = json.loads(payload["messages"][1]["content"])
        value = {"model": payload["model"], "done": True, "message": {"content": json.dumps(response(data), ensure_ascii=False)}}
        return transform(value) if transform else value
    monkeypatch.setattr(ai, "_json_request", request)
    return calls


def test_request_limits_context_and_excludes_metadata_and_other_songs():
    raw = "[Verse]\r\n前の本文\r\n\r\n対象の本文\r\n[Chorus]\r\n後の本文\r\n送らない本文"
    request = ai.prepare_request(raw, 4, " 朝 ")
    assert request["original"] == "対象の本文"
    assert request["context_before"] == ["前の本文"]
    assert request["context_after"] == ["後の本文"]
    assert request["theme"] == "朝"
    assert "送らない本文" not in json.dumps(request, ensure_ascii=False)
    assert "Verse" not in json.dumps(request)
    assert request["line_id"] != ai.prepare_request(raw + "追記", 4)["line_id"]


@pytest.mark.parametrize("line,theme", [(True, ""), (0, ""), (1, ""), (4, "x" * 501)])
def test_bad_selection_or_theme_is_rejected(line, theme):
    with pytest.raises(ai.LocalAIError):
        ai.prepare_request("[Verse]\n前\n\n対象", line, theme)


def test_long_context_is_rejected():
    with pytest.raises(ai.LocalAIError):
        ai.prepare_request("a" * 2001 + "\n対象", 2)


def test_generate_sends_only_previewed_context_and_uses_schema(monkeypatch):
    calls = fake_transport(monkeypatch)
    request = ai.prepare_request("前\n対象\n後\n送らない", 2)
    result = ai.generate("local:3b", request)
    ai.validate_result(result["result"], request)
    assert result["model"] == "local:3b"
    assert [path for path, _, _ in calls] == ["/api/tags", "/api/show", "/api/chat"]
    _, payload, timeout = calls[-1]
    assert json.loads(payload["messages"][1]["content"]) == request
    assert payload["format"] == ai.SCHEMA
    assert payload["stream"] is False
    assert payload["messages"][0]["content"] == ai.LEGACY_SYSTEM
    assert timeout == 45
    assert not any(key in payload for key in ("tools", "web_search"))
    ai.generate("local:3b", request, prompt_version="local-rewrite-2")
    assert calls[-1][1]["messages"][0]["content"] == ai.SYSTEM
    with pytest.raises(ai.LocalAIError):
        ai.generate("local:3b", request, prompt_version="unknown")


@pytest.mark.parametrize("model", ["remote:cloud", "https://example.com", "local\nother", "missing", "alias"])
def test_nonlocal_or_unknown_models_never_receive_lyrics(monkeypatch, model):
    calls = fake_transport(monkeypatch)
    with pytest.raises(ai.LocalAIError):
        ai.generate(model, ai.prepare_request("対象", 1))
    assert not any(path == "/api/chat" for path, _, _ in calls)


def test_remote_alias_and_extra_context_are_rejected(monkeypatch):
    calls = fake_transport(monkeypatch)
    request = ai.prepare_request("対象", 1)
    request["other_songs"] = ["別曲"]
    with pytest.raises(ai.LocalAIError):
        ai.generate("local:3b", request)
    assert calls == []
    def remote(path, payload=None, timeout=5):
        return {"models": [{"name": "local:3b"}]} if path == "/api/tags" else {"remote_host": "https://example.com"}
    monkeypatch.setattr(ai, "_json_request", remote)
    with pytest.raises(ai.LocalAIError):
        ai.generate("local:3b", ai.prepare_request("対象", 1))


@pytest.mark.parametrize("change", [
    lambda r: r.update(original="原文と違う"),
    lambda r: r.update(line_id="wrong"),
    lambda r: r.update(candidates=[]),
    lambda r: r["candidates"][0].update(direction="scene"),
    lambda r: r["candidates"][0].update(text="複数\n行"),
    lambda r: r.update(reason=""),
    lambda r: r.update(extra="unsupported"),
])
def test_bad_output_is_rejected(change):
    request = ai.prepare_request("対象", 1)
    result = response(request)
    change(result)
    with pytest.raises(ai.LocalAIError):
        ai.validate_result(result, request)


def test_connection_error_is_bounded_and_redirects_are_blocked(monkeypatch):
    class FailingOpener:
        def open(self, request, timeout):
            assert request.full_url == ai.ENDPOINT + "/api/tags"
            assert timeout == 5
            raise socket.timeout()
    monkeypatch.setattr(ai.urllib.request, "build_opener", lambda *handlers: FailingOpener())
    with pytest.raises(ai.LocalAIError, match="5秒"):
        ai.list_models()
    with pytest.raises(ai.LocalAIError, match="転送"):
        ai.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")


@pytest.mark.parametrize("failure,expected", [
    (urllib.error.URLError(ConnectionRefusedError("refused")), "Ollamaを起動"),
    (ConnectionRefusedError("refused"), "Ollamaを起動"),
    (urllib.error.URLError(socket.timeout()), "45秒"),
    (urllib.error.HTTPError(ai.ENDPOINT, 500, "server error", {}, None), "HTTP 500"),
    (OSError("connection reset"), "通信が中断"),
])
def test_transport_errors_explain_separate_causes(monkeypatch, failure, expected):
    class FailingOpener:
        def open(self, request, timeout):
            raise failure
    monkeypatch.setattr(ai.urllib.request, "build_opener", lambda *handlers: FailingOpener())
    with pytest.raises(ai.LocalAIError, match=expected):
        ai._json_request("/api/chat", {}, timeout=45)


def test_unreadable_response_is_not_reported_as_connection_failure(monkeypatch):
    import io
    monkeypatch.setattr(ai.urllib.request, "build_opener", lambda *handlers: type("Opener", (), {
        "open": lambda self, request, timeout: io.BytesIO(b"not JSON")})())
    with pytest.raises(ai.LocalAIError, match="応答を読み取れません"):
        ai._json_request("/api/tags")


def test_suggestions_persist_without_changing_lyrics_and_backup_roundtrips(tmp_path, monkeypatch):
    fake_transport(monkeypatch)
    library = Library(tmp_path / "library.db")
    target = library.save("対象", "前\n対象\n後")
    store = HistoryStore(library)
    report = build_report("前\n対象\n後", compare("前\n対象\n後", ()), detect("前\n対象\n後", ()), {})
    run_id = store.save(report, "対象", target)
    before = library.export_json()
    suggestion = ai.generate("local:3b", ai.prepare_request(report["input"], 2))
    store.save_suggestion(run_id, suggestion)
    stored = HistoryStore(Library(library.path)).get(run_id)
    assert stored["ai_suggestions"] == [suggestion]
    assert library.export_json() == before
    restored = Library(tmp_path / "restored.db")
    restore_backup(restored, export_backup(library), confirmed=True)
    assert HistoryStore(restored).get(run_id) == stored
    bad = deepcopy(suggestion)
    bad["request"]["original"] = "違う原文"
    with pytest.raises(ai.LocalAIError):
        store.save_suggestion(run_id, bad)
    assert store.get(run_id) == stored
    archive = json.loads(export_backup(library))
    payload = json.loads(archive["tables"]["analysis_runs"][0]["payload_json"])
    payload["ai_suggestions"][0]["result"]["original"] = "違う引用"
    archive["tables"]["analysis_runs"][0]["payload_json"] = json.dumps(payload, ensure_ascii=False)
    archive["sha256"] = checksum(archive["tables"])
    with pytest.raises(ValueError, match="原文"):
        inspect_backup(json.dumps(archive, ensure_ascii=False).encode("utf-8"))
    for _ in range(19):
        store.save_suggestion(run_id, suggestion)
    with pytest.raises(ValueError, match="20回"):
        store.save_suggestion(run_id, suggestion)
    assert len(store.get(run_id)["ai_suggestions"]) == 20


@pytest.mark.parametrize("transform", [
    lambda r: {**r, "done": False},
    lambda r: {**r, "done_reason": "length"},
    lambda r: {**r, "model": "other"},
    lambda r: {**r, "message": {"content": "not JSON"}},
])
def test_incomplete_or_wrong_chat_responses_are_rejected(monkeypatch, transform):
    fake_transport(monkeypatch, transform)
    with pytest.raises(ai.LocalAIError):
        ai.generate("local:3b", ai.prepare_request("対象", 1))


@pytest.mark.parametrize("method", ["local-rewrite-3", "local-rewrite-5"])
def test_evaluation_schema_binds_exact_input_without_mutating_existing_schemas(method):
    first = ai.prepare_request('Ａの声と"引用"', 1)
    other = ai.prepare_request("別の原文", 1)
    original = deepcopy(ai.SCHEMA)
    constrained = ai.response_schema(first, method)
    for key in ("line_id", "original"):
        assert constrained["properties"][key]["enum"] == [first[key]]
        assert ai.response_schema(other, method)["properties"][key]["enum"] == [other[key]]
    constrained["properties"]["reason"]["type"] = "modified"
    assert ai.SCHEMA == original
    assert ai.response_schema(first, "local-rewrite-1") == original
    assert ai.response_schema(first, "local-rewrite-2") == original


@pytest.mark.parametrize("method", ["local-rewrite-3", "local-rewrite-5"])
def test_evaluation_constraints_do_not_replace_response_validation(monkeypatch, method):
    calls = fake_transport(monkeypatch)
    request = ai.prepare_request("対象", 1)
    result = ai.generate("local:3b", request, method)
    payload = next(payload for path, payload, timeout in calls if path == "/api/chat")
    assert payload["format"]["properties"]["original"]["enum"] == ["対象"]
    assert result["prompt_version"] == method
    def wrong(response):
        value = json.loads(response["message"]["content"])
        value["original"] = "取り違え"
        response["message"]["content"] = json.dumps(value)
        return response
    fake_transport(monkeypatch, wrong)
    with pytest.raises(ai.LocalAIError, match="原文"):
        ai.generate("local:3b", request, method)


def test_experimental_method_is_not_an_app_or_backup_method(monkeypatch):
    fake_transport(monkeypatch)
    request = ai.prepare_request("対象", 1)
    suggestion = ai.generate("local:3b", request, ai.CONSTRAINED_VERSION)
    assert ai.CONSTRAINED_VERSION not in ai.PROMPTS
    assert ai.DEFAULT_PROMPT_VERSION == "local-rewrite-1"
    with pytest.raises(ai.LocalAIError, match="形式"):
        ai.validate_saved_suggestion({"input": "対象"}, suggestion)


@pytest.mark.parametrize("method", ["local-rewrite-1", "local-rewrite-2", "local-rewrite-4", "local-rewrite-5"])
def test_thinking_off_is_only_requested_for_direct_method(monkeypatch, method):
    calls = fake_transport(monkeypatch)
    request = ai.prepare_request("対象", 1)
    ai.generate("local:3b", request, method)
    payload = next(payload for path, payload, timeout in calls if path == "/api/chat")
    assert payload["options"] == {"temperature": .4, "num_predict": 1200}
    if method == ai.DIRECT_VERSION:
        assert payload["format"]["properties"]["line_id"]["enum"] == [request["line_id"]]
        assert payload["format"]["properties"]["original"]["enum"] == ["対象"]
    else:
        assert payload["format"] == ai.SCHEMA
    if method in (ai.DIRECT_VERSION, ai.LEGACY_DIRECT_VERSION):
        assert payload["think"] is False
    else:
        assert "think" not in payload


def test_generation_controls_do_not_leak_between_requests():
    controls = ai.generation_controls(ai.DIRECT_VERSION)
    controls["options"]["num_predict"] = 1
    assert ai.generation_controls(ai.DIRECT_VERSION)["options"]["num_predict"] == 1200
    assert "think" not in ai.generation_controls(ai.DEFAULT_PROMPT_VERSION)


def test_direct_method_can_be_saved_and_restored_with_old_methods(tmp_path, monkeypatch):
    from lyric_analyzer.backup import restore_backup

    fake_transport(monkeypatch)
    library = Library(tmp_path / "library.db")
    store = HistoryStore(library)
    report = build_report("対象", compare("対象", ()), detect("対象", ()), {})
    run = store.save(report, "自作検証")
    for method in ("local-rewrite-1", "local-rewrite-2", ai.LEGACY_DIRECT_VERSION, ai.DIRECT_VERSION):
        store.save_suggestion(run, ai.generate("local:3b", ai.prepare_request("対象", 1), method))
    target = Library(tmp_path / "target.db")
    restore_backup(target, export_backup(library), confirmed=True)
    assert HistoryStore(target).get(run) == store.get(run)
