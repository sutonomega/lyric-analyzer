import json

import pytest
from streamlit.testing.v1 import AppTest

from lyric_analyzer.analysis.library_vocabulary import build_index
from lyric_analyzer.analysis.trends import date_groups, expression_trend
from lyric_analyzer.backup import checksum, export_backup, restore_backup
from lyric_analyzer.storage import Library, validate_composed_on


@pytest.mark.parametrize("value", [None, False, 20260101, "2026-02-30", "20260101", "2026-W01-1", "2026-1-01", "2026-01", " 2026-01-01"])
def test_invalid_dates(value):
    with pytest.raises(ValueError):
        validate_composed_on(value)


def test_dates_preserved_cleared_and_roundtripped(tmp_path):
    library = Library(tmp_path / "source.db")
    song_id = library.save("曲", "窓の灯り", composed_on="2024-02-29")
    library.save("改名", "窓の灯り", song_id=song_id)
    assert library.list_songs()[0]["composed_on"] == "2024-02-29"
    assert len(library.revisions(song_id)) == 1
    other = Library(tmp_path / "other.db")
    other.import_json(library.export_json())
    assert other.list_songs()[0]["composed_on"] == "2024-02-29"
    backup = export_backup(library)
    library.save("改名", "窓の灯り", song_id=song_id, composed_on="")
    assert library.list_songs()[0]["composed_on"] == ""
    restore_backup(library, backup, confirmed=True)
    assert library.list_songs()[0]["composed_on"] == "2024-02-29"
    invalid = json.loads(backup)
    invalid["tables"]["songs"][0]["composed_on"] = "2026-02-30"
    invalid["sha256"] = checksum(invalid["tables"])
    with pytest.raises(ValueError):
        restore_backup(library, json.dumps(invalid).encode(), confirmed=True)
    assert library.list_songs()[0]["composed_on"] == "2024-02-29"


def test_legacy_dates_remain_unknown(tmp_path):
    library = Library(tmp_path / "source.db")
    library.save("曲", "窓の灯り")
    archive = json.loads(export_backup(library))
    archive["schema_version"] = 6
    archive["tables"]["schema_migrations"] = [row for row in archive["tables"]["schema_migrations"] if row["version"] <= 6]
    archive["tables"]["songs"][0].pop("composed_on")
    archive["sha256"] = checksum(archive["tables"])
    restore_backup(library, json.dumps(archive).encode(), confirmed=True)
    assert library.list_songs()[0]["composed_on"] == ""
    songs = json.loads(library.export_json())
    songs["songs"][0].pop("composed_on")
    target = Library(tmp_path / "target.db")
    target.import_json(json.dumps(songs))
    assert target.list_songs()[0]["composed_on"] == ""


def seed(library):
    first = library.save("一月", "[Verse]\n窓の灯り\n窓の灯り", composed_on="2025-01-31")
    library.save("一月別曲", "海の波", composed_on="2025-01-01")
    library.save("三月", "海の波", composed_on="2025-03-01")
    library.save("翌年", "窓の灯り", composed_on="2026-01-01")
    unknown = library.save("不明", "窓の灯り")
    return first, unknown


def test_period_denominators_repeats_unknown_dates_and_evidence(tmp_path):
    library = Library(tmp_path / "library.db")
    first, unknown = seed(library)
    corpus = library.comparison_corpus()
    index = build_index(corpus)
    row = next(row for row in index.rows if row.expression == "窓")
    dates = {song["id"]: song["composed_on"] for song in library.list_songs()}
    result = expression_trend(index, row, dates)
    assert result["unknown_song_ids"] == [unknown]
    assert [(r["period"], r["total_songs"], r["using_songs"], r["occurrences"], r["unique_song_lines"], r["song_share"])
            for r in result["periods"]] == [("2025-01", 2, 1, 2, 1, .5), ("2025-03", 1, 0, 0, 0, 0), ("2026-01", 1, 1, 1, 1, 1)]
    assert result["periods"][0]["song_ids"] == [first]
    yearly = expression_trend(index, row, dates, "year")
    assert yearly["periods"][0]["total_songs"] == 3
    assert yearly["periods"][0]["song_share"] == 1 / 3
    sources = {song.song_id: song for song in corpus}
    for evidence in result["evidence"]:
        assert sources[evidence["song_id"]].lyrics[evidence["start"]:evidence["end"]] == evidence["surface"]
    assert date_groups(corpus, {})[0] == {}
    with pytest.raises(ValueError):
        date_groups(corpus, dates, "registration")


def test_trends_ui_and_date_editor(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    library = Library(tmp_path / "library.db")
    first, unknown = seed(library)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.selectbox(key="selected_song").select(first).run()
    app.date_input(key=f"composed-on-{first}").set_value(None)
    app.form_submit_button[0].click().run()
    assert next(song for song in library.list_songs() if song["id"] == first)["composed_on"] == ""
    assert len(library.revisions(first)) == 1
    app.checkbox(key="show_trends").check().run()
    app.button(key="trends-run").click().run()
    app.text_input(key="trends-query").set_value("窓").run()
    assert not app.exception
    assert any("不明：2曲" in caption.value for caption in app.caption)
    result = app.session_state["trends-result"]
    assert result["dates"][first] == ""
    library.save("不明", "窓の灯り", song_id=unknown, composed_on="2020-01-01")
    app.run()
    assert any("保存データが変わりました" in item.value for item in app.info)
