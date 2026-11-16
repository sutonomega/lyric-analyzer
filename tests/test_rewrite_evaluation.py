import pytest

from lyric_analyzer import local_ai
from lyric_analyzer import rewrite_evaluation as evaluation


def generate(model, request, prompt):
    return {"model": model, "prompt_version": prompt, "result": {
        "line_id": request["line_id"], "original": request["original"], "reason": "表現を確認する",
        "candidates": [{"direction": direction, "text": request["original"] if direction == "meaning" else "窓辺に雨の跡がある"}
                       for direction in local_ai.DIRECTIONS]}}


def test_fixed_cases_context_and_no_human_labels_or_automatic_winner():
    calls = []
    def recorded(model, request, prompt):
        calls.append((model, request, prompt))
        return generate(model, request, prompt)
    result = evaluation.evaluate({"name": "fake", "digest": "a" * 64}, recorded)
    assert len(result["records"]) == len(calls) == 16
    assert all(row["human_review"] == "not_reviewed" for row in result["records"])
    assert all(row["structural_success"] == 8 for row in result["summary"])
    assert len(result["prompt_sha256"]) == 2
    first = result["records"][0]
    assert first["request"]["line_number"] == 3
    assert first["request"]["original"] == "私はその手紙を捨てなかった"
    assert first["automatic_checks"][0]["checks"] == ["原文と同じ表現"]
    assert "fake" in evaluation.markdown(result)


def test_bad_structure_and_connection_error_recorded_without_retry():
    calls = []
    def failures(model, request, prompt):
        calls.append(prompt)
        if len(calls) == 1:
            raise ValueError("no connection")
        result = generate(model, request, prompt)
        if len(calls) == 2:
            result["result"]["original"] = "wrong original"
        return result
    result = evaluation.evaluate({"name": "fake"}, failures)
    assert len(calls) == 16
    assert [row["status"] for row in result["records"][:3]] == ["failed", "failed", "success"]
    assert "no connection" in evaluation.markdown({**result, "model": {"name": "fake", "digest": "b" * 64}})


def test_model_change_aborts_remaining_calls():
    checks = []
    calls = []
    def check(model):
        checks.append(model)
        if len(checks) == 2:
            raise ValueError("model changed")
    def recorded(*args):
        calls.append(args)
        return generate(*args)
    with pytest.raises(ValueError, match="model changed"):
        evaluation.evaluate({"name": "fake"}, recorded, check)
    assert len(calls) == 1


@pytest.mark.parametrize("remote", [False, True])
def test_descriptor_requires_local_completion_model(monkeypatch, remote):
    monkeypatch.setattr(local_ai, "list_models", lambda: ("fake",))
    def request(path, payload=None):
        if path == "/api/tags":
            return {"models": [{"name": "fake", "digest": "a" * 64, "remote_host": "remote" if remote else None}]}
        return {"capabilities": ["completion"]}
    monkeypatch.setattr(local_ai, "_json_request", request)
    if remote:
        with pytest.raises(ValueError):
            evaluation.descriptor("fake")
    else:
        assert evaluation.descriptor("fake") == {"name": "fake", "digest": "a" * 64}
