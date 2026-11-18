from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from lyric_analyzer.analysis.candidate_quality import CHECKS, DIRECTIONS
from lyric_analyzer.analysis.review_summary import condition_id, select_records, summarize
from lyric_analyzer.analysis.comparison import compare
from lyric_analyzer.analysis.expressions import detect
from lyric_analyzer.analysis.report import build_report
from lyric_analyzer.backup import export_backup, restore_backup
from lyric_analyzer.history import HistoryStore
from lyric_analyzer.local_ai import prepare_request
from lyric_analyzer.storage import Library
from pathlib import Path


def assessment(verdict="unreviewed"):
    return {"version": "candidate-review-1", "updated_at": Library.now(),
            "decisions": {d: {"verdict": verdict if d == "meaning" else "unreviewed", "note": "private note"} for d in DIRECTIONS},
            "meaning_checks": {c: "changed" if c == "subject" else "unchecked" for c in CHECKS}}


def record(number, model="A", target="X", verdict=None, prompt="local-rewrite-1", theme=""):
    return {"run_id": str(number), "suggestion_index": 0, "provider": "ollama-local", "model": model,
            "prompt_version": prompt, "line_id": target, "theme": theme,
            "review": assessment(verdict) if verdict else None}


def test_summary_denominators_targets_and_meaning_checks_are_not_tripled():
    rows = [record(1, verdict="usable"), record(2, verdict="edit"), record(3, target="Y"),
            record(4, model="B", verdict="reject")]
    before = deepcopy(rows)
    result = summarize(rows)
    a = next(row for row in result["groups"] if row["model"] == "A" and row["direction"] == "meaning")
    assert a["generations"] == 3 and a["targets"] == 2
    assert a["evaluated"] == 2 and a["unreviewed"] == 1
    assert a["usable_ratio"] == 0.5
    assert a["edit"] == 1 and a["reject"] == 0
    assert result["generation_count"] == 4 and result["candidate_count"] == 12
    assert result["target_count"] == 2
    subject = next(row for row in result["meaning_checks"] if row["model"] == "A" and row["check"] == "subject")
    assert subject["changed"] == 2 and subject["unchecked"] == 1
    assert rows == before


@pytest.mark.parametrize("left,right", [("local-rewrite-2", "local-rewrite-4"), ("local-rewrite-4", "local-rewrite-5")])
def test_direct_method_is_separate_from_identical_text_prompt(left, right):
    rows = [record(1, prompt=left, verdict="usable"), record(2, prompt=right, verdict="reject")]
    result = summarize(rows)
    groups = {row["prompt_version"]: row for row in result["groups"] if row["direction"] == "meaning"}
    assert groups[left]["usable_ratio"] == 1
    assert groups[right]["usable_ratio"] == 0


@pytest.mark.parametrize("verdict,ratio", [(None, None), ("unreviewed", None), ("usable", 1), ("edit", 0), ("reject", 0)])
def test_unreviewed_is_not_zero_percent_and_other_verdicts_are_separate(verdict, ratio):
    summary = summarize([record(1, verdict=verdict)])
    meaning = next(row for row in summary["groups"] if row["direction"] == "meaning")
    assert meaning["usable_ratio"] == ratio
    assert all(row["usable_ratio"] is None for row in summary["groups"] if row["direction"] != "meaning")


def test_common_inputs_respect_conditions_themes_and_repeated_generations():
    records = [record(1), record(2), record(3, target="Y"), record(4, model="B"), record(5, model="B", target="Z"),
               record(6, model="A", prompt="local-rewrite-2")]
    a, b = condition_id(records[0]), condition_id(records[3])
    chosen, common = select_records(records, [a, b], True)
    assert common == 1 and len(chosen) == 3
    assert select_records(records, [a], True) == ([], 0)
    assert select_records(records, [], False) == ([], 0)
    records[3]["theme"] = "different theme"
    assert select_records(records, [a, b], True) == ([], 0)
    assert len(summarize(records)["groups"]) == 9  # Three conditions, three directions each.


def seed(library):
    store = HistoryStore(library)
    raw = "君を待つ"
    song_id = library.save("自作の曲名", raw)
    run_id = store.save(build_report(raw, compare(raw, ()), detect(raw, ()), {}), "自作の曲名", song_id)
    request = prepare_request(raw, 1)
    for model in ("local-a:3b", "local-b:3b"):
        store.save_suggestion(run_id, {"provider": "ollama-local", "model": model, "prompt_version": "local-rewrite-1",
                "created_at": Library.now(), "request": request,
                "result": {"line_id": request["line_id"], "original": raw, "reason": "具体的な情景",
                           "candidates": [{"direction": d, "text": "切符を折って待つ"} for d in DIRECTIONS]}})
    value = assessment("edit")
    store.save_candidate_review(run_id, 0, value["decisions"], value["meaning_checks"])
    return store, run_id, song_id


def test_storage_uses_latest_review_and_survives_restore_without_corpus_text(tmp_path):
    library = Library(tmp_path / "library.db")
    assert HistoryStore(library).candidate_evaluations() == []
    store, run, song = seed(library)
    rows = store.candidate_evaluations()
    assert len(rows) == 2
    assert not any(key in rows[0] for key in ("input", "corpus", "candidates", "result"))
    before = library.export_json()
    value = assessment("usable")
    store.save_candidate_review(run, 0, value["decisions"], value["meaning_checks"])
    reopened = HistoryStore(Library(library.path))
    assert len(reopened.candidate_evaluations()) == 2
    actual = summarize(reopened.candidate_evaluations())
    assert next(row for row in actual["groups"] if row["model"] == "local-a:3b" and row["direction"] == "meaning")["usable"] == 1
    assert library.export_json() == before
    restored = Library(tmp_path / "restored.db")
    restore_backup(restored, export_backup(library), confirmed=True)
    assert summarize(HistoryStore(restored).candidate_evaluations()) == actual
    library.delete(song)
    assert store.candidate_evaluations() == []


def test_summary_ui_empty_and_drilldown_to_saved_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(path).run()
    assert not app.checkbox(key="show_candidate_summary").value
    app.checkbox(key="show_candidate_summary").check().run()
    assert not app.exception
    assert any("まだありません" in item.value for item in app.info)
    store, run, _ = seed(Library(tmp_path / "library.db"))
    app.run()
    assert not app.exception
    assert len(app.dataframe[0].value) == 6
    assert "未評価" in set(app.dataframe[0].value["使えそうの割合"])
    app.checkbox(key="candidate_summary_common").check().run()
    assert not app.exception
    assert len(app.dataframe[0].value) == 6
    app.checkbox(key="candidate_summary_details").check().run()
    app.button(key="candidate_summary_open").click().run()
    assert not app.exception
    assert app.checkbox(key="show_history").value
    assert app.selectbox(key="history_run").value == run
    assert app.checkbox(key=f"history-{run}-ai-saved").value
    index = app.selectbox(key=f"history-{run}-ai-history").value
    assert app.checkbox(key=f"history-{run}-quality-open-{index}").value
    model = store.get(run)["ai_suggestions"][index]["model"]
    app.selectbox(key=f"history-{run}-quality-verdict-{index}-meaning").select("usable")
    app.button(key=f"history-{run}-quality-save-{index}").click().run()
    assert not app.exception
    frame = app.dataframe[0].value
    row = frame[(frame["モデル"] == model) & (frame["方向"] == "意味を保つ")].iloc[0]
    assert row["使えそう"] == 1 and row["評価済み"] == 1
    assert len(store.candidate_evaluations()) == 2


def test_summary_export_only_contains_counts_and_conditions():
    payload = summarize([record(1, verdict="usable")])
    text = str(payload)
    assert "private note" not in text
    assert "line_id" not in text and "theme" not in text and "run_id" not in text
