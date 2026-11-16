from pathlib import Path
import sqlite3

import pytest
from streamlit.testing.v1 import AppTest

from lyric_analyzer.batch_analysis import run_batch
from lyric_analyzer.history import HistoryStore
from lyric_analyzer.storage import Library


def seed(path):
    library = Library(path)
    first = library.save("一曲目", "記憶がほどけていく")
    second = library.save("二曲目", "記憶がほどけていく\n心の奥で")
    library.save("比較だけ", "夜の駅に灯りが残る")
    return library, HistoryStore(library), [first, second]


def test_batch_preserves_songs_and_creates_self_excluded_reports(tmp_path):
    library, store, ids = seed(tmp_path / "library.db")
    before = library.export_json()
    progress = []
    result = run_batch(store, library.comparison_corpus(), library.list_rules(), ids, progress=progress.append)
    assert len(result) == 2 and progress == [.5, 1.0]
    assert library.export_json() == before
    for item in result:
        report = store.get(item["run_id"])
        assert report["song_id"] == item["song_id"]
        assert report["target_revision_id"] is not None
        assert len(report["corpus"]) == 2
        assert item["song_id"] not in {song["song_id"] for song in report["corpus"]}
        assert report["settings"]["batch_version"] == "batch-analysis-1"
        assert store.reviews(item["run_id"]) == {}


@pytest.mark.parametrize("ids", [[], ["missing"], ["same", "same"], list(map(str, range(21)))])
def test_invalid_selection_does_not_save(tmp_path, ids):
    library, store, _ = seed(tmp_path / "library.db")
    with pytest.raises(ValueError):
        run_batch(store, library.comparison_corpus(), library.list_rules(), ids)
    assert store.list_runs() == []


@pytest.mark.parametrize("changed", ["song", "rule"])
def test_changed_snapshot_does_not_save_partial_reports(tmp_path, changed):
    library, store, ids = seed(tmp_path / "library.db")
    corpus, rules = library.comparison_corpus(), library.list_rules()
    def change(value):
        if value == 1:
            if changed == "song":
                library.save("変更", "別の歌詞", song_id=ids[0])
            else:
                library.save_rule("literal", {"text": "新しい表現"}, "理由", "観点")
    with pytest.raises(ValueError, match="変更されています"):
        run_batch(store, corpus, rules, ids, progress=change)
    assert store.list_runs() == []


def test_sql_failure_rolls_back_every_report(tmp_path):
    library, store, ids = seed(tmp_path / "library.db")
    with library.connection() as connection:
        connection.execute("CREATE TRIGGER reject_batch BEFORE INSERT ON analysis_runs WHEN (SELECT COUNT(*) FROM analysis_runs)>0 BEGIN SELECT RAISE(ABORT,'failure'); END")
    with pytest.raises(sqlite3.Error):
        run_batch(store, library.comparison_corpus(), library.list_rules(), ids)
    assert store.list_runs() == []
    with library.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM analysis_sources").fetchone()[0] == 0


def test_analysis_failure_saves_nothing(tmp_path):
    library, store, ids = seed(tmp_path / "library.db")
    library.save("長すぎる曲", "あ" * 20001, song_id=ids[1])
    with pytest.raises(ValueError):
        run_batch(store, library.comparison_corpus(), library.list_rules(), ids)
    assert store.list_runs() == []


def test_batch_ui_confirmation_duplicate_prevention_and_review(tmp_path, monkeypatch):
    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    _, store, ids = seed(tmp_path / "library.db")
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.checkbox(key="show_batch_analysis").check().run()
    app.multiselect(key="batch-analysis-targets").set_value(ids).run()
    assert app.button(key="batch-analysis-run").disabled
    next(item for item in app.checkbox if item.key.startswith("batch-analysis-confirm-")).check().run()
    app.button(key="batch-analysis-run").click().run()
    assert not app.exception
    assert len(store.list_runs()) == 2
    assert app.button(key="batch-analysis-run").disabled
    app.button(key="batch-analysis-open").click().run()
    assert not app.exception and app.checkbox(key="show_history").value
    app.checkbox(key="batch-analysis-unknown").check().run()
    assert app.button(key="batch-analysis-run").disabled
