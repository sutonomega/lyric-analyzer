import sqlite3

import pytest

from lyric_analyzer.storage import Library


def test_revisions_search_and_delete(tmp_path):
    library = Library(tmp_path / "library.db")
    song_id = library.save("夜の歌", "[Verse]\r\n窓に灯る😀", "街")
    library.save("夜の歌", "[Verse]\r\n窓に灯る😀", "夜", song_id)
    assert len(library.revisions(song_id)) == 1
    library.save("新しい夜", "電車の窓に灯る", "街", song_id)
    assert [row["revision_number"] for row in library.revisions(song_id)] == [2, 1]
    assert library.revisions(song_id)[1]["lyrics_raw"] == "[Verse]\r\n窓に灯る😀"
    assert library.list_songs("電車")[0]["id"] == song_id
    assert library.list_songs("存在しない") == []
    library.delete(song_id)
    assert library.list_songs() == []
    assert library.revisions(song_id) == []


def test_migration_is_repeatable_and_foreign_keys_enforced(tmp_path):
    path = tmp_path / "library.db"
    library = Library(path)
    Library(path)
    with library.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO song_revisions VALUES ('r','missing',1,'text','hash','now')")


def test_failed_revision_rolls_back_metadata(tmp_path):
    library = Library(tmp_path / "library.db")
    song_id = library.save("before", "first")
    with library.connection() as connection:
        connection.execute("CREATE TRIGGER fail_revision BEFORE INSERT ON song_revisions BEGIN SELECT RAISE(ABORT, 'failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        library.save("after", "second", song_id=song_id)
    assert library.list_songs()[0]["title"] == "before"
    assert len(library.revisions(song_id)) == 1


def test_invalid_input_and_missing_song(tmp_path):
    library = Library(tmp_path / "library.db")
    with pytest.raises(ValueError):
        library.save(" ", "lyrics")
    with pytest.raises(ValueError):
        library.save("title", "lyrics", song_id="missing")
    assert library.list_songs() == []
