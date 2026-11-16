import json
import sys

import pytest

from lyric_analyzer import semantic_evaluation as evaluation


def test_dataset_and_offline_evaluation_do_not_call_ai(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Offline evaluation must not access Ollama")
    monkeypatch.setattr(evaluation.local_embeddings, "embed_batch", unexpected)
    monkeypatch.setattr(evaluation.local_embeddings, "list_models", unexpected)
    result = evaluation.evaluate()
    assert len(result["cases"]) == len({row["id"] for row in result["cases"]}) == 18
    assert len([row for row in result["cases"] if row["group"] == "additional"]) == 12
    assert result["model"] is None and set(result["summary"]) == {"characters"}
    assert result["groups"]["initial"]["characters"]["cases"] == 6
    assert all(len(set(case[3:])) == 4 for case in evaluation.CASES)


def test_threshold_counts_ties_and_boundary_inclusion():
    rows = [{"scores": {"method": {"positive": .75, "unrelated": .1, "contrast": .8}}},
            {"scores": {"method": {"positive": .5, "unrelated": .5, "contrast": .2}}}]
    summary = evaluation.metrics(rows, "method")
    assert summary["ranking"]["unrelated"] == {"wins": 1, "ties": 1, "losses": 0}
    assert summary["ranking"]["contrast"] == {"wins": 1, "ties": 0, "losses": 1}
    threshold = next(row for row in summary["thresholds"] if row["minimum_score"] == .75)
    assert threshold == {"minimum_score": .75, "positive_accepted": 1, "positive_rejected": 1, "unrelated_accepted": 0, "contrast_accepted": 1}


def test_embeddings_are_batched_normalized_and_ties_preserved():
    calls = []
    def embed(model, texts):
        calls.append(texts)
        return [[3, 4] for _ in texts]
    result = evaluation.evaluate({"name": "fake", "digest": "a" * 64}, embed)
    assert all(1 <= len(batch) <= 8 for batch in calls)
    assert len({text for batch in calls for text in batch}) == sum(len(batch) for batch in calls)
    assert result["summary"]["embedding"]["ranking"]["contrast"]["ties"] == 18
    assert all(row["scores"]["embedding"]["positive"] == pytest.approx(1) for row in result["cases"])
    report = evaluation.markdown(result)
    assert "人工的" in report and "fake" in report and "sender" in report


@pytest.mark.parametrize("failure", ["count", "dimension", "nan"])
def test_invalid_embedding_batches_rejected(failure):
    counter = 0
    def embed(model, texts):
        nonlocal counter
        counter += 1
        if failure == "count":
            return []
        if failure == "nan":
            return [[float("nan"), 1] for _ in texts]
        return [[1, 0] if counter == 1 else [1, 0, 0] for _ in texts]
    with pytest.raises(ValueError):
        evaluation.evaluate({"name": "fake"}, embed)


def test_cli_writes_json_and_markdown_without_user_library(tmp_path, monkeypatch):
    output = tmp_path / "evaluation.json"
    monkeypatch.setattr(sys, "argv", ["evaluation", "--output", str(output)])
    evaluation.main()
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["format"] == "semantic-evaluation-1"
    assert output.with_suffix(".md").is_file()
    assert evaluation.character_score("Ａ", "A") == 1


def test_model_changed_during_evaluation_preserves_existing_output(tmp_path, monkeypatch):
    output = tmp_path / "evaluation.json"
    output.write_text("previous result", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["evaluation", "--local-model", "--output", str(output)])
    monkeypatch.setattr(evaluation.local_embeddings, "list_models", lambda: ({"name": "fake"},))
    checks = []
    def check(model):
        checks.append(model)
        if len(checks) == 2:
            raise ValueError("model changed")
    monkeypatch.setattr(evaluation.local_embeddings, "check_model", check)
    monkeypatch.setattr(evaluation, "evaluate", lambda model: {})
    with pytest.raises(ValueError, match="model changed"):
        evaluation.main()
    assert output.read_text(encoding="utf-8") == "previous result"
    assert not output.with_suffix(".md").exists()
