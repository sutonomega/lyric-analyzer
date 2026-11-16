from pathlib import Path

from streamlit.testing.v1 import AppTest
from lyric_analyzer.storage import Library


def test_save_and_reopen_song(tmp_path, monkeypatch):
    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    assert not app.exception
    app.text_input[1].set_value("テスト曲")
    app.text_area[0].set_value("窓に残る灯り")
    app.button[0].click().run()
    assert not app.exception
    assert app.success
    app.run()
    song_id = app.selectbox[0].options[1]
    app.selectbox[0].select_index(1).run()
    assert song_id == "テスト曲"
    assert app.text_area[0].value == "窓に残る灯り"


def test_tags_preview_and_json_import(tmp_path, monkeypatch):
    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path / "target"))
    source = Library(tmp_path / "source.db")
    source.save("朝の駅", "[Verse]\r\nＡの声。夜の駅", tags=["街"])
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.file_uploader[0].upload("library.json", source.export_json().encode("utf-8")).run()
    assert not app.exception
    assert any("取り込み予定：1曲" in item.value for item in app.caption)
    app.button(key="import").click().run()
    assert not app.exception
    assert app.success
    app.selectbox[0].select_index(1).run()
    assert app.text_input[3].value == "街"
    app.text_input[3].set_value("街, 朝")
    app.form_submit_button[0].click().run()
    assert not app.exception
    app.text_input(key="search").set_value("朝").run()
    assert "朝の駅" in app.selectbox[0].options
    app.form_submit_button[1].click().run()
    assert not app.exception
    assert any("解析区間：2件" in item.value for item in app.caption)
    assert Library(tmp_path / "target" / "library.db").list_songs()[0]["tags"] == ["朝", "街"]


def test_invalid_upload_shows_error_without_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.file_uploader[0].upload("broken.json", b"{}").run()
    assert not app.exception
    assert app.error
    assert Library(tmp_path / "library.db").list_songs() == []


def test_analyze_unsaved_lyrics_and_switch_count_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.text_area[0].set_value("[Verse]\nＡの声\nAの声\n記憶がほどけていく\n記憶がほどけていく")
    app.button(key="analyze").click().run()
    assert not app.exception
    table = app.dataframe[0].value
    assert table.loc[table["語句"] == "ほどける", "回数"].iloc[0] == 2
    app.selectbox(key="frequency_mode").select("フレーズ（全角・半角を統一）").run()
    assert not app.exception
    table = app.dataframe[0].value
    assert table.loc[table["語句"] == "Aの声", "回数"].iloc[0] == 2
    assert Library(tmp_path / "library.db").list_songs() == []
    app.selectbox(key="frequency_mode").select("フレーズ（原文一致）").run()
    assert not app.exception
    table = app.dataframe[0].value
    assert table.loc[table["語句"] == "Ａの声", "回数"].iloc[0] == 1


def test_blank_analysis_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.button(key="analyze").click().run()
    assert not app.exception
    assert any("集計できる語句がありません" in item.value for item in app.info)


def test_compare_unsaved_lyrics_select_sources_and_open_quote(tmp_path, monkeypatch):
    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    library = Library(tmp_path / "library.db")
    first = library.save("過去曲A", "記憶がほどけていく\n記憶がほどけていく")
    second = library.save("過去曲B", "記憶がほどけていく")
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.text_area[0].set_value("記憶がほどけていく")
    app.button(key="compare").click().run()
    assert not app.exception
    table = app.dataframe[0].value
    assert table.iloc[0]["使用曲数"] == 2
    assert table.iloc[0]["過去曲の出現回数"] == 3
    assert table.iloc[0]["比較曲数"] == 2
    assert any("改訂1" in item.value and "行目" in item.value for item in app.text)
    assert any("引用元全文" in item.label for item in app.expander)
    assert len(library.list_songs()) == 2
    app.multiselect(key="comparison-songs-None").set_value([first])
    app.button(key="compare").click().run()
    assert not app.exception
    assert app.dataframe[0].value.iloc[0]["比較曲数"] == 1
    assert all(second != song.song_id for song in app.session_state["comparison_result"][2].corpus)


def test_compare_excludes_current_song_and_rejects_invalid_period(tmp_path, monkeypatch):
    from datetime import date

    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    library = Library(tmp_path / "library.db")
    song_id = library.save("自分の曲", "記憶がほどけていく")
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.selectbox(key="selected_song").select(song_id).run()
    app.button(key="compare").click().run()
    assert not app.exception
    assert any("比較対象の曲がありません" in item.value for item in app.info)
    app.date_input(key="comparison_start").set_value(date(2026, 11, 2))
    app.date_input(key="comparison_end").set_value(date(2026, 11, 1))
    app.button(key="compare").click().run()
    assert not app.exception
    assert any("開始日は終了日以前" in item.value for item in app.error)

