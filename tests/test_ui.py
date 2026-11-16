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


def test_detect_and_edit_rule_then_recheck(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.text_area[0].set_value("記憶がほどけていく")
    app.button(key="detect").click().run()
    assert not app.exception
    assert len(app.session_state["detection_result"][1].findings) == 2
    assert any("見直しの観点：" in item.value for item in app.text)
    library = Library(tmp_path / "library.db")
    rule = next(item for item in library.list_rules() if json.loads(item.pattern_json).get("text") == "ほどけていく")
    app.checkbox(key="show_rule_editor").check().run()
    app.selectbox(key="selected_rule").select(rule.id).run()
    app.checkbox(key=f"rule-enabled-{rule.id}-1").uncheck()
    app.button(key="save_rule").click().run()
    assert not app.exception
    assert app.success
    app.button(key="detect").click().run()
    assert not app.exception
    result = app.session_state["detection_result"][1]
    assert len(result.findings) == 1
    assert result.findings[0].match_kind == "文型候補"
    assert len(library.list_songs()) == 0


def test_add_custom_rule_and_reject_empty_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.checkbox(key="show_rule_editor").check().run()
    app.button(key="save_rule").click().run()
    assert not app.exception
    assert app.error
    app.text_input(key="rule-text-None-0").set_value("駅の時計")
    app.text_area(key="rule-reason-None-0").set_value("よく使う情景")
    app.text_area(key="rule-hint-None-0").set_value("別の物から時間を描く")
    app.button(key="save_rule").click().run()
    assert not app.exception
    assert app.success
    assert len(Library(tmp_path / "library.db").list_rules()) == 6


def test_integrated_report_exclude_restore_and_history(tmp_path, monkeypatch):
    from lyric_analyzer.history import HistoryStore

    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    library = Library(tmp_path / "library.db")
    library.save("過去A", "記憶がほどけていく")
    library.save("過去B", "記憶がほどけていく")
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.text_area[0].set_value("記憶がほどけていく")
    app.button(key="report").click().run()
    assert not app.exception
    store = HistoryStore(library)
    run_id = app.session_state["current_report"]
    prefix = f"current-{run_id}"
    assert len(store.list_runs()) == 1
    assert "＋登録ルール" in app.dataframe[0].value.iloc[0]["種類"]
    app.text_input(key=f"{prefix}-reason").set_value("意図した表現")
    app.button(key=f"{prefix}-exclude").click().run()
    assert not app.exception
    assert any(item["enabled"] for item in store.list_exclusions())
    app.checkbox(key="manage_exclusions").check().run()
    app.button(key="restore").click().run()
    assert not app.exception
    assert not any(item["enabled"] for item in store.list_exclusions())
    reopened = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    reopened.checkbox(key="show_history").check().run()
    assert not reopened.exception
    assert any("統合した分析結果" == item.value for item in reopened.subheader)
    assert len(library.list_songs()) == 2


def test_backup_preview_confirmation_and_restore(tmp_path, monkeypatch):
    from lyric_analyzer.backup import export_backup

    source = Library(tmp_path / "source.db")
    source.save("復元する曲", "心の奥で", tags=["夜"])
    content = export_backup(source)
    target_dir = tmp_path / "target"
    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(target_dir))
    target = Library(target_dir / "library.db")
    target.save("置換前の曲", "朝の駅")
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.checkbox(key="show_backup").check().run()
    app.button(key="create_backup").click().run()
    assert not app.exception
    assert app.session_state["backup_bytes"]
    app.file_uploader(key="restore_archive").upload("backup.json", content).run()
    assert not app.exception
    assert app.button(key="restore_backup").disabled
    confirmation = next(item for item in app.checkbox if item.key and item.key.startswith("restore-confirm-"))
    confirmation.check().run()
    app.button(key="restore_backup").click().run()
    assert not app.exception
    assert app.success
    assert target.list_songs()[0]["title"] == "復元する曲"
    assert len(list((target_dir / "backups").glob("before-restore-*.json"))) == 1


def test_review_form_persists_classification(tmp_path, monkeypatch):
    from lyric_analyzer.history import HistoryStore

    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.text_area[0].set_value("心の奥で")
    app.button(key="report").click().run()
    run_id = app.session_state["current_report"]
    store = HistoryStore(Library(tmp_path / "library.db"))
    finding_id = store.get(run_id)["findings"][0]["id"]
    prefix = f"current-{run_id}"
    app.checkbox(key=f"{prefix}-review").check().run()
    app.selectbox(key=f"{prefix}-review-label-{finding_id}").select("intentional")
    app.button(key=f"{prefix}-save-reviews").click().run()
    assert not app.exception
    assert store.reviews(run_id)[finding_id]["label"] == "intentional"
    assert any("有用率 0%" in item.value for item in app.text)

