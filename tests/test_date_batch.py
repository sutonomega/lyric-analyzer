from pathlib import Path
import sqlite3

import pytest
from streamlit.testing.v1 import AppTest

from lyric_analyzer.date_batch_ui import prepare_changes
from lyric_analyzer.storage import Library


def changes(library, value="2025-04-01"):
    songs = library.list_songs()
    rows = [{"曲ID": song["id"], "曲名": song["title"], "現在の制作日": song["composed_on"], "変更後の制作日": value} for song in songs]
    return prepare_changes(songs, rows)


def test_batch_changes_only_dates_and_no_revisions(tmp_path):
    library = Library(tmp_path / "library.db")
    library.save("一曲目", "窓の灯り", styles="piano")
    library.save("二曲目", "海の波", composed_on="2024-01-01")
    before = {song["id"]: song for song in library.list_songs()}
    updates = changes(library)
    assert library.update_composition_dates(updates) == 2
    for song in library.list_songs():
        assert song["composed_on"] == "2025-04-01"
        assert {key: value for key, value in song.items() if key not in ("composed_on", "updated_at")} == {key: value for key, value in before[song["id"]].items() if key not in ("composed_on", "updated_at")}
        assert len(library.revisions(song["id"])) == 1
    assert library.update_composition_dates(changes(library, "")) == 2
    assert all(not song["composed_on"] for song in library.list_songs())


@pytest.mark.parametrize("action", ["date", "lyrics", "title", "delete"])
def test_conflicting_changes_do_not_partially_save(tmp_path, action):
    library = Library(tmp_path / "library.db")
    first = library.save("一曲目", "窓の灯り")
    library.save("二曲目", "海の波")
    updates = changes(library)
    if action == "delete":
        library.delete(first)
    else:
        library.save("改名" if action == "title" else "一曲目", "別の歌詞" if action == "lyrics" else "窓の灯り", song_id=first,
                     composed_on="2020-01-01" if action == "date" else None)
    before = library.export_json()
    with pytest.raises(ValueError, match="読み直して"):
        library.update_composition_dates(updates)
    assert library.export_json() == before


def test_failed_sql_update_rolls_back_all(tmp_path):
    library = Library(tmp_path / "library.db")
    library.save("一", "歌詞")
    library.save("二", "歌詞")
    updates = changes(library)
    with library.connection() as connection:
        connection.execute("CREATE TRIGGER reject_second BEFORE UPDATE ON songs WHEN OLD.id='" + updates[1]["song_id"] + "' BEGIN SELECT RAISE(ABORT,'failure'); END")
    before = library.export_json()
    with pytest.raises(sqlite3.Error):
        library.update_composition_dates(updates)
    assert library.export_json() == before


@pytest.mark.parametrize("kind", ["invalid", "duplicate", "empty", "unchanged", "wrong-id"])
def test_invalid_batch_rejected(tmp_path, kind):
    library = Library(tmp_path / "library.db")
    library.save("一", "歌詞")
    updates = changes(library)
    if kind == "invalid":
        updates[0]["after"] = "2025-02-30"
    elif kind == "duplicate":
        updates *= 2
    elif kind == "empty":
        updates = []
    elif kind == "unchanged":
        updates[0]["after"] = ""
    else:
        updates[0]["song_id"] = "missing"
    before = library.export_json()
    with pytest.raises(ValueError):
        library.update_composition_dates(updates)
    assert library.export_json() == before


def test_ui_preview_confirmation_and_save(tmp_path, monkeypatch):
    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    library = Library(tmp_path / "library.db")
    song_id = library.save("一曲目", "窓の灯り")
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.selectbox(key="selected_song").select(song_id).run()
    app.checkbox(key="show_date_batch").check().run()
    app.button(key="date-batch-load").click().run()
    editor = app.data_editor[0]
    app.session_state[editor.key] = {"edited_rows": {0: {"変更後の制作日": "2025-01-02"}}, "added_rows": [], "deleted_rows": []}
    app.run()
    assert app.button(key="date-batch-save").disabled
    assert library.list_songs()[0]["composed_on"] == ""
    confirm = next(item for item in app.checkbox if item.key.startswith("date-batch-confirm-"))
    confirm.check().run()
    # Changing the value must require confirmation again.
    app.session_state[editor.key] = {"edited_rows": {0: {"変更後の制作日": "2025-01-03"}}, "added_rows": [], "deleted_rows": []}
    app.run()
    assert app.button(key="date-batch-save").disabled
    next(item for item in app.checkbox if item.key.startswith("date-batch-confirm-")).check().run()
    app.button(key="date-batch-save").click().run()
    assert not app.exception
    assert library.list_songs()[0]["composed_on"] == "2025-01-03"
    assert app.date_input(key=f"composed-on-{song_id}").value.isoformat() == "2025-01-03"
    assert len(library.revisions(song_id)) == 1
    assert any("1曲の制作日を保存" in item.value for item in app.success)


def test_preview_rejects_altered_identity(tmp_path):
    library = Library(tmp_path / "library.db")
    library.save("一", "歌詞")
    snapshot = library.list_songs()
    with pytest.raises(ValueError):
        prepare_changes(snapshot, [])
    with pytest.raises(ValueError):
        prepare_changes(snapshot, [{"曲ID": "other"}])
