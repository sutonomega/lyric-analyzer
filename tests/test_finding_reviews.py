import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from lyric_analyzer.analysis.comparison import compare
from lyric_analyzer.analysis.expressions import detect
from lyric_analyzer.analysis.finding_reviews import summarize
from lyric_analyzer.analysis.report import build_report, review_candidates
from lyric_analyzer.backup import export_backup, restore_backup
from lyric_analyzer.history import HistoryStore
from lyric_analyzer.storage import Library


def seed(path):
    library = Library(path)
    library.save("過去曲", "記憶がほどけていく\n記憶がほどけていく")
    song_id = library.save("対象曲", "記憶がほどけていく\n記憶がほどけていく")
    raw = library.list_songs()[0]["lyrics_raw"]
    report = build_report(raw, compare(raw, library.comparison_corpus(), song_id), detect(raw, library.list_rules()), {})
    store = HistoryStore(library)
    run_id = store.save(report, "対象曲", song_id)
    return library, store, run_id, song_id


def test_review_records_match_saved_targets_and_latest_labels(tmp_path):
    library, store, run_id, _ = seed(tmp_path / "library.db")
    report = store.get(run_id)
    candidates = review_candidates(report)
    assert len(candidates) < len(report["findings"])
    eligible = {item["id"] for item in candidates}
    extra = next(item for item in report["findings"] if item["id"] not in eligible)
    store.save_reviews(run_id, [{"finding_id": item["id"], "label": label, "note": "private note"}
                               for item, label in [(candidates[0], "useful"), (extra, "false_positive")]])
    result = summarize(store.finding_review_records())
    assert result["eligible"] == len(candidates)
    assert result["evaluated"] == result["useful"] == 1
    assert result["false_positive"] == 0
    store.save_reviews(run_id, [{"finding_id": candidates[0]["id"], "label": "intentional", "note": "changed"}])
    result = summarize(store.finding_review_records())
    assert result["useful"] == 0 and result["intentional"] == 1
    assert result["useful_ratio"] == 0
    assert "private note" not in json.dumps(store.finding_review_records())
    store.save_reviews(run_id, [{"finding_id": candidates[0]["id"], "label": None, "note": ""}])
    assert summarize(store.finding_review_records())["useful_ratio"] is None


def test_latest_analysis_does_not_reuse_previous_review(tmp_path):
    _, store, run_id, song_id = seed(tmp_path / "library.db")
    report = store.get(run_id)
    store.save_reviews(run_id, [{"finding_id": row["id"], "label": "useful", "note": ""} for row in review_candidates(report)])
    newer = store.save(report, "再分析", song_id)
    result = summarize(store.finding_review_records())
    assert [row["run_id"] for row in result["runs"]] == [newer]
    assert result["evaluated"] == 0 and result["reviewed_saved_songs"] == 0
    all_runs = summarize(store.finding_review_records(), latest_only=False)
    assert len(all_runs["runs"]) == 2 and all_runs["saved_songs"] == 1
    assert all_runs["complete_runs"] == 1


def test_legacy_and_grouped_methods_are_separate():
    rows = [{"id": str(i), "kind": "phrase", "expression_key": "same", "priority": 1} for i in range(12)]
    rows.append({"id": "grammar", "kind": "word", "expression_key": "grammar", "priority": 5})
    assert len(review_candidates({"versions": {"report": "report-1"}, "findings": rows})) == 10
    assert len(review_candidates({"versions": {"report": "report-2"}, "findings": rows})) == 1


def test_empty_unsaved_inputs_weighted_ratio_and_versions():
    def record(key, version="report-2", song_id=None, eligible=2, useful=0, intentional=0):
        return {"run_id": key, "song_id": song_id, "title": "sample", "created_at": key,
                "input_hash": "same", "report_version": version, "eligible": eligible,
                "useful": useful, "intentional": intentional, "false_positive": 0}
    records = [record("a", useful=1), record("b", eligible=0), record("c", song_id="song", useful=1, intentional=1), record("d", "report-1")]
    result = summarize(records)
    assert len(result["runs"]) == 2
    assert result["empty_runs"] == 1
    assert result["complete_runs"] == 1
    assert result["unsaved_inputs"] == result["saved_songs"] == 1
    assert result["useful_ratio"] == .5
    assert len(summarize(records, "report-1")["runs"]) == 1
    assert summarize([])["useful_ratio"] is None


def test_reviews_survive_backup_and_disappear_with_song(tmp_path):
    library, store, run_id, song_id = seed(tmp_path / "library.db")
    first = review_candidates(store.get(run_id))[0]
    store.save_reviews(run_id, [{"finding_id": first["id"], "label": "useful", "note": ""}])
    expected = store.finding_review_records()
    backup = export_backup(library)
    restored = Library(tmp_path / "restored.db")
    restore_backup(restored, backup, confirmed=True)
    assert HistoryStore(restored).finding_review_records() == expected
    restored.delete(song_id)
    assert HistoryStore(restored).finding_review_records() == []


def test_ui_open_unreviewed_history_and_save(tmp_path, monkeypatch):
    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    _, store, run_id, _ = seed(tmp_path / "library.db")
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.checkbox(key="show_finding_reviews").check().run()
    assert not app.exception
    app.button(key="finding-reviews-open").click().run()
    assert app.checkbox(key="show_history").value
    assert app.checkbox(key=f"history-{run_id}-review").value
    candidate = review_candidates(store.get(run_id))[0]
    app.selectbox(key=f"history-{run_id}-review-label-{candidate['id']}").select("useful")
    app.button(key=f"history-{run_id}-save-reviews").click().run()
    assert not app.exception
    assert any("有用 1 /" in element.value for element in app.text)
    assert summarize(store.finding_review_records())["evaluated"] == 1
