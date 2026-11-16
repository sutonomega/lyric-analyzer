import json
import os
import sqlite3

import pytest

from lyric_analyzer.backup import checksum, export_backup, restore_backup
from lyric_analyzer.folder_import import ImportRecord, import_records, parse_file, plan_import, preview_files, scan_folder
from lyric_analyzer.storage import Library, MIGRATIONS


def song_text(title="夜の駅", styles="piano, mellow pop", lyrics="[Verse]\r\n改札に灯りが残る\r\n"):
    return f"Title: {title}\r\nStyles: {styles}\r\nLyrics:\r\n{lyrics}"


@pytest.mark.parametrize("heading", ["{field}: {value}", "# {field}\n{value}", "## {field}: {value}",
                                     "**{field}:** {value}", "**{field}**: {value}", "[{field}]\n{value}",
                                     "{field}\n{value}", "{field}：{value}"])
def test_heading_formats(heading):
    content = "\n".join(heading.format(field=field, value=value) for field, value in
                        [("Title", "夜の駅"), ("Styles", "piano"), ("Lyrics", "[Verse]\n改札の灯り")])
    record = parse_file(content.encode("utf-8"), "song.md")
    assert record.title == "夜の駅"
    assert record.styles == "piano"
    assert record.lyrics == "[Verse]\n改札の灯り"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "cp932"])
def test_encodings_and_original_lyrics(encoding):
    raw = song_text()
    record = parse_file(raw.encode(encoding), "song.txt")
    assert record.lyrics == "[Verse]\r\n改札に灯りが残る\r\n"
    assert record.styles == "piano, mellow pop"


def test_multiline_styles_and_labels_inside_lyrics_are_preserved():
    raw = "# Title\n夜の駅\n# Styles\npiano\nfemale vocal\n# Lyrics\n[Chorus]\nTitle: 歌詞内の言葉\n## Styles\n心の奥で\n"
    record = parse_file(raw.encode(), "song.md")
    assert record.styles == "piano\nfemale vocal"
    assert record.lyrics == "[Chorus]\nTitle: 歌詞内の言葉\n## Styles\n心の奥で\n"


def test_markdown_inside_field_values_is_not_consumed_as_a_heading():
    raw = "Title: **夜の駅**\nStyles: **soft pop**\nLyrics: **強く**歌う\n[Chorus]\n朝を待つ\n"
    record = parse_file(raw.encode(), "song.md")
    assert record.title == "**夜の駅**"
    assert record.styles == "**soft pop**"
    assert record.lyrics == "**強く**歌う\n[Chorus]\n朝を待つ\n"


def test_empty_styles_is_allowed_but_heading_is_required():
    assert parse_file(song_text(styles="").encode(), "song.txt").styles == ""
    with pytest.raises(ValueError):
        parse_file(b"Title: song\nLyrics:\nlyrics", "song.txt")


@pytest.mark.parametrize("content", ["ただの歌詞", "Title: \nStyles: pop\nLyrics: lyrics", "Title: x\nStyles: pop\nLyrics:\n",
                                     "Title: a\nTitle: b\nStyles: pop\nLyrics: x", "Lyrics: x\nTitle: a\nStyles: pop",
                                     "Title:\n複数\n行\nStyles: pop\nLyrics: x"])
def test_invalid_fields(content):
    with pytest.raises(ValueError):
        parse_file(content.encode(), "bad.txt")


def test_invalid_encoding_and_size_limits(monkeypatch):
    with pytest.raises(ValueError):
        parse_file(b"\xff", "bad.txt", "utf-8-sig")
    with pytest.raises(ValueError):
        parse_file(b"x" * (2 * 1024 * 1024 + 1), "big.txt")
    with pytest.raises(ValueError):
        parse_file(song_text(lyrics="夜" * 20001).encode(), "long.txt")
    monkeypatch.setattr("lyric_analyzer.folder_import.MAX_FILES", 1)
    with pytest.raises(ValueError):
        preview_files([("a.txt", song_text().encode()), ("b.txt", song_text().encode())])


def test_directory_scan_is_read_only_and_recursion_is_optional(tmp_path):
    folder = tmp_path / "songs"
    folder.mkdir()
    (folder / "a.txt").write_bytes(song_text().encode())
    (folder / "b.md").write_bytes(song_text("朝", lyrics="朝の電車").encode("cp932"))
    (folder / "bad.txt").write_text("missing fields", encoding="utf-8")
    (folder / "ignore.json").write_text("{}", encoding="utf-8")
    (folder / "child").mkdir()
    (folder / "child" / "c.txt").write_bytes(song_text("昼", lyrics="昼の駅").encode())
    before = (folder / "a.txt").read_bytes()
    preview = scan_folder(str(folder))
    assert len(preview.records) == 2
    assert len(preview.errors) == 1
    nested = scan_folder(str(folder), recursive=True)
    assert len(nested.records) == 3
    assert any(record.source == "child/c.txt" for record in nested.records)
    assert (folder / "a.txt").read_bytes() == before


def test_symlink_is_not_followed(tmp_path, monkeypatch):
    folder = tmp_path / "songs"
    folder.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(song_text().encode())
    try:
        os.symlink(outside, folder / "link.txt")
    except OSError:
        (folder / "link.txt").write_bytes(song_text().encode())
        original = type(folder).is_symlink
        monkeypatch.setattr(type(folder), "is_symlink", lambda path: path.name == "link.txt" or original(path))
    assert scan_folder(str(folder)).records == ()


def test_idempotent_import_and_explicit_variants(tmp_path):
    library = Library(tmp_path / "library.db")
    first = parse_file(song_text().encode(), "a.txt")
    variant = parse_file(song_text(styles="rock").encode(), "b.txt")
    assert import_records(library, [first]) == (1, 0)
    original = library.list_songs()[0]
    assert original["styles"] == "piano, mellow pop"
    assert original["theme"] == ""
    assert import_records(library, [first, variant]) == (0, 2)
    assert import_records(library, [first, variant], allow_variants=True) == (1, 1)
    assert library.revisions(original["id"])[0]["lyrics_raw"] == first.lyrics
    assert len(library.revisions(original["id"])) == 1
    assert len(library.list_songs()) == 2
    assert len(library.list_songs("rock")) == 1


def test_duplicates_within_batch_and_same_lyrics_different_title(tmp_path):
    library = Library(tmp_path / "library.db")
    first = parse_file(song_text().encode(), "a.txt")
    same = parse_file(song_text().encode(), "copy.txt")
    renamed = parse_file(song_text("別題").encode(), "b.txt")
    planned = plan_import([first, same, renamed], [], False)
    assert [status for _, status, _ in planned] == ["新規", "同一内容", "同じ歌詞の別曲"]
    assert import_records(library, [first, same, renamed]) == (1, 2)
    assert import_records(library, [renamed], allow_variants=True) == (1, 0)


def test_batch_rolls_back_if_any_insert_fails(tmp_path):
    library = Library(tmp_path / "library.db")
    library.save("existing", "before")
    with library.connection() as connection:
        connection.execute("CREATE TRIGGER fail_second BEFORE INSERT ON songs WHEN NEW.title='second' BEGIN SELECT RAISE(ABORT,'failure'); END")
    first = parse_file(song_text("first", lyrics="first lyrics").encode(), "a.txt")
    second = parse_file(song_text("second", lyrics="second lyrics").encode(), "b.txt")
    with pytest.raises(sqlite3.IntegrityError):
        import_records(library, [first, second])
    assert [song["title"] for song in library.list_songs()] == ["existing"]


def test_styles_preserve_on_existing_save_and_json_roundtrip(tmp_path):
    source = Library(tmp_path / "source.db")
    song_id = source.save("曲", "歌詞", styles="synth pop\nfemale vocal")
    source.save("改題", "新歌詞", song_id=song_id)
    assert source.list_songs()[0]["styles"] == "synth pop\nfemale vocal"
    target = Library(tmp_path / "target.db")
    target.import_json(source.export_json())
    assert target.list_songs()[0]["styles"] == "synth pop\nfemale vocal"
    archive = json.loads(source.export_json())
    archive["songs"][0].pop("styles")
    legacy = Library(tmp_path / "legacy-json.db")
    legacy.import_json(json.dumps(archive))
    assert legacy.list_songs()[0]["styles"] == ""


def test_schema_five_backup_can_still_be_restored(tmp_path):
    source = Library(tmp_path / "source.db")
    source.save("旧版", "歌詞")
    archive = json.loads(export_backup(source))
    archive["schema_version"] = 5
    archive["tables"]["schema_migrations"] = [row for row in archive["tables"]["schema_migrations"] if row["version"] <= 5]
    for song in archive["tables"]["songs"]:
        song.pop("styles")
        song.pop("composed_on")
    archive["sha256"] = checksum(archive["tables"])
    target = Library(tmp_path / "target.db")
    restore_backup(target, json.dumps(archive).encode(), confirmed=True)
    assert target.list_songs()[0]["title"] == "旧版"
    assert target.list_songs()[0]["styles"] == ""
    with target.connection() as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 7


def test_schema_five_database_upgrade_preserves_existing_data(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        for number, statements in enumerate(MIGRATIONS[:5], start=1):
            for statement in statements:
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations VALUES (?, 'now')", (number,))
        connection.execute("INSERT INTO songs VALUES ('old','曲','theme','now','now')")
        connection.execute("INSERT INTO song_revisions VALUES ('revision','old',1,'歌詞','hash','now')")
    upgraded = Library(path)
    assert upgraded.list_songs()[0]["theme"] == "theme"
    assert upgraded.list_songs()[0]["lyrics_raw"] == "歌詞"
    assert upgraded.list_songs()[0]["styles"] == ""
