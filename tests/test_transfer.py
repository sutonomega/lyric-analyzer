import json
import sqlite3

import pytest

from lyric_analyzer.storage import Library, MIGRATIONS
from lyric_analyzer.transfer import validate_archive


def test_upgrade_preserves_existing_song(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        for statement in MIGRATIONS[0]:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations VALUES (1,'now')")
        connection.execute("INSERT INTO songs VALUES ('old','title','','now','now')")
        connection.execute("INSERT INTO song_revisions VALUES ('revision','old',1,'lyrics','hash','now')")
    library = Library(path)
    assert library.list_songs()[0]["lyrics_raw"] == "lyrics"
    assert library.list_songs()[0]["tags"] == []


def test_round_trip_conflicts_and_tags(tmp_path):
    source = Library(tmp_path / "source.db")
    song_id = source.save("曲", "[Verse]\r\nＡの灯り😀", tags=[" 夜 ", "夜", "街"])
    source.save("改題", "二行目", song_id=song_id)
    archive = source.export_json()
    target = Library(tmp_path / "target.db")
    assert target.import_json(archive) == (1, 0)
    assert json.loads(target.export_json()) == json.loads(archive)
    assert target.list_songs("街")[0]["tags"] == ["夜", "街"]
    assert target.list_songs(tag="夜")
    assert not target.list_songs(tag="朝")
    assert target.import_json(archive) == (0, 1)
    assert target.import_json(archive, "copy") == (1, 0)
    assert len(target.list_songs()) == 2
    assert len(target.revisions(song_id)) == 2
    target.save("改題", "二行目", song_id=song_id, tags=[])
    assert target.list_songs(tag="夜")[0]["id"] != song_id


@pytest.mark.parametrize("change", [
    lambda archive: archive.update(schema_version=999),
    lambda archive: archive["songs"][0].update(tags=[1]),
    lambda archive: archive["songs"][0]["revisions"][0].update(content_hash="bad"),
    lambda archive: archive["songs"][0]["revisions"][0].update(revision_number=True),
    lambda archive: archive["songs"].append(archive["songs"][0]),
    lambda archive: archive["songs"][0].update(created_at="yesterday"),
])
def test_invalid_archive_does_not_write(tmp_path, change):
    source = Library(tmp_path / "source.db")
    source.save("曲", "歌詞")
    archive = json.loads(source.export_json())
    change(archive)
    target = Library(tmp_path / "target.db")
    with pytest.raises(ValueError):
        target.import_json(json.dumps(archive))
    assert target.list_songs() == []


def test_import_failure_rolls_back_entire_batch(tmp_path):
    source = Library(tmp_path / "source.db")
    source.save("first", "one")
    source.save("second", "two")
    target = Library(tmp_path / "target.db")
    with target.connection() as connection:
        connection.execute("CREATE TRIGGER fail_second BEFORE INSERT ON songs WHEN (SELECT COUNT(*) FROM songs) > 0 BEGIN SELECT RAISE(ABORT, 'failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        target.import_json(source.export_json())
    assert target.list_songs() == []


@pytest.mark.parametrize("content", [b"\xff", "{", "[]", '{"schema_version":1,"songs":null}', "x" * (10 * 1024 * 1024 + 1)])
def test_invalid_input(content):
    with pytest.raises(ValueError):
        validate_archive(content)
