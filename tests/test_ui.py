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
from pathlib import Path

