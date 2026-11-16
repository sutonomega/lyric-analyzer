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


def test_report_groups_occurrences_and_can_show_grammatical_references(tmp_path, monkeypatch):
    from lyric_analyzer.history import HistoryStore

    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    library = Library(tmp_path / "library.db")
    for number in range(3):
        library.save(str(number), "夜の駅\nして")
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.text_area[0].set_value("夜の駅\n夜の駅\nして")
    app.button(key="report").click().run()
    assert not app.exception
    run_id = app.session_state["current_report"]
    prefix = f"current-{run_id}"
    frame = app.dataframe[0].value
    assert not any(frame["種類"] == "文法的な表現（参考）")
    phrase = frame[frame["表現"] == "夜の駅"]
    assert len(phrase) == 1
    assert phrase.iloc[0]["行"] == "1, 2"
    assert phrase.iloc[0]["過去出現回数"] == 3
    first_id = app.selectbox(key=f"{prefix}-detail").value
    occurrence = app.selectbox(key=f"{prefix}-occurrence-{first_id}")
    occurrence.select(occurrence.options[1]).run()
    assert not app.exception
    app.checkbox(key=f"{prefix}-show-grammar").check().run()
    assert any(app.dataframe[0].value["種類"] == "文法的な表現（参考）")
    app.checkbox(key=f"{prefix}-review").check().run()
    labels = [widget for widget in app.selectbox if widget.key and widget.key.startswith(f"{prefix}-review-label-")]
    report = HistoryStore(library).get(run_id)
    ids = {widget.key.removeprefix(f"{prefix}-review-label-") for widget in labels}
    candidates = [item for item in report["findings"] if item["id"] in ids]
    assert len({(item["kind"], item["expression_key"]) for item in candidates}) == len(candidates)
    assert all(item["priority"] != 5 for item in candidates)


def test_old_report_reviews_keep_occurrence_based_candidates(tmp_path, monkeypatch):
    from lyric_analyzer.analysis.comparison import compare
    from lyric_analyzer.analysis.expressions import detect
    from lyric_analyzer.analysis.report import build_report
    from lyric_analyzer.history import HistoryStore

    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    library = Library(tmp_path / "library.db")
    raw = "心の奥で\n心の奥で"
    report = build_report(raw, compare(raw, ()), detect(raw, library.list_rules()), {})
    report["versions"]["report"] = "report-1"
    store = HistoryStore(library)
    run_id = store.save(report, "旧版の履歴")
    second_id = report["findings"][1]["id"]
    store.save_reviews(run_id, [{"finding_id": second_id, "label": "useful", "note": "旧版で保存"}])
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.checkbox(key="show_history").check().run()
    prefix = f"history-{run_id}"
    app.checkbox(key=f"{prefix}-review").check().run()
    assert not app.exception
    labels = [widget for widget in app.selectbox if widget.key and widget.key.startswith(f"{prefix}-review-label-")]
    assert len(labels) == 2
    assert app.selectbox(key=f"{prefix}-review-label-{second_id}").value == "useful"
    assert any("旧版の評価" in item.value for item in app.caption)


def test_local_ai_is_opt_in_confirmation_tracks_payload_and_repeated_save_works(tmp_path, monkeypatch):
    from lyric_analyzer import local_ai
    from lyric_analyzer.history import HistoryStore

    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    calls = []
    def models():
        calls.append("models")
        return ("local:3b",)
    def generate(model, request):
        calls.append(request)
        return {"provider": "ollama-local", "model": model, "prompt_version": local_ai.PROMPT_VERSION,
                "created_at": Library.now(), "request": request,
                "result": {"line_id": request["line_id"], "original": request["original"], "reason": "物や動作で描く",
                           "candidates": [{"direction": d, "text": "切符を折って君を待つ"} for d in local_ai.DIRECTIONS]}}
    monkeypatch.setattr(local_ai, "list_models", models)
    monkeypatch.setattr(local_ai, "generate", generate)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.text_area[0].set_value("[Verse]\n心の奥で\n次の行\n送らない行")
    app.button(key="report").click().run()
    run_id = app.session_state["current_report"]
    prefix = f"current-{run_id}"
    assert not app.checkbox(key=f"{prefix}-ai-enabled").value
    assert calls == []
    app.checkbox(key=f"{prefix}-ai-enabled").check().run()
    assert calls == []
    app.button(key=f"{prefix}-ai-connect").click().run()
    assert calls == ["models"]
    assert app.button(key=f"{prefix}-ai-generate").disabled
    consent = next(item for item in app.checkbox if item.key and item.key.startswith(f"{prefix}-ai-confirm-"))
    consent.check().run()
    app.text_input(key=f"{prefix}-ai-theme").set_value("朝の駅").run()
    assert app.button(key=f"{prefix}-ai-generate").disabled
    consent = next(item for item in app.checkbox if item.key and item.key.startswith(f"{prefix}-ai-confirm-"))
    consent.check().run()
    for _ in range(2):
        app.button(key=f"{prefix}-ai-generate").click().run()
        assert not app.exception
    store = HistoryStore(Library(tmp_path / "library.db"))
    saved = store.get(run_id)["ai_suggestions"]
    assert len(saved) == 2
    assert saved[0]["request"]["original"] == "心の奥で"
    assert saved[0]["request"]["context_after"] == ["次の行"]
    assert "送らない行" not in str(saved[0]["request"])
    assert app.checkbox(key=f"{prefix}-ai-saved").value
    assert len(store.list_runs()) == 1
    reading_table = next(frame.value for frame in app.dataframe if "概算モーラ数" in frame.value.columns)
    assert len(reading_table) == 4
    assert reading_table.iloc[0]["方向"] == "原文"
    app.checkbox(key=f"{prefix}-reading-edit-1").check().run()
    app.text_input(key=f"{prefix}-reading-1-original").set_value("こころのおくで")
    app.button(key=f"{prefix}-reading-save-1").click().run()
    assert not app.exception
    assert store.get(run_id)["ai_suggestions"][1]["prosody"]["rows"][0]["mora"] == 7
    assert app.selectbox(key=f"{prefix}-ai-history").value == 1
    app.text_input(key=f"{prefix}-reading-1-original").set_value("ABC")
    app.button(key=f"{prefix}-reading-save-1").click().run()
    assert not app.exception
    assert app.error
    assert store.get(run_id)["ai_suggestions"][1]["prosody"]["rows"][0]["mora"] == 7


def test_local_ai_failure_keeps_basic_analysis(tmp_path, monkeypatch):
    from lyric_analyzer import local_ai
    from lyric_analyzer.history import HistoryStore

    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(local_ai, "list_models", lambda: ("local:3b",))
    def fail(model, request):
        raise local_ai.LocalAIError("応答形式が不正")
    monkeypatch.setattr(local_ai, "generate", fail)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.text_area[0].set_value("心の奥で")
    app.button(key="report").click().run()
    run_id = app.session_state["current_report"]
    prefix = f"current-{run_id}"
    store = HistoryStore(Library(tmp_path / "library.db"))
    before = store.get(run_id)
    app.checkbox(key=f"{prefix}-ai-enabled").check().run()
    app.button(key=f"{prefix}-ai-connect").click().run()
    consent = next(item for item in app.checkbox if item.key and item.key.startswith(f"{prefix}-ai-confirm-"))
    consent.check().run()
    app.button(key=f"{prefix}-ai-generate").click().run()
    assert not app.exception
    assert app.error
    assert app.dataframe
    assert store.get(run_id) == before


def test_import_folder_path_and_styles_editor(tmp_path, monkeypatch, caplog):
    from lyric_analyzer.folder_ui import TEMPLATE

    folder = tmp_path / "lyrics"
    folder.mkdir()
    (folder / "song.txt").write_text(TEMPLATE, encoding="utf-8")
    data = tmp_path / "data"
    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(data))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.checkbox(key="show_folder_import").check().run()
    app.text_input(key="folder_path").set_value(str(folder)).run()
    app.button(key="scan_folder").click().run()
    assert not app.exception
    assert Library(data / "library.db").list_songs() == []
    app.button(key="import_folder").click().run()
    assert not app.exception
    assert app.success
    library = Library(data / "library.db")
    song = library.list_songs()[0]
    assert song["styles"] == "mellow pop, piano, female vocal"
    assert app.multiselect(key="comparison-songs-None").value == [song["id"]]
    assert "was created with a default value" not in caplog.text
    app.selectbox(key="selected_song").select(song["id"]).run()
    assert app.text_area(key=f"styles-{song['id']}").value == song["styles"]
    app.text_area(key=f"styles-{song['id']}").set_value("rock")
    app.form_submit_button[0].click().run()
    assert not app.exception
    assert library.list_songs()[0]["styles"] == "rock"


def test_browser_folder_import_requires_acknowledging_invalid_files(tmp_path, monkeypatch):
    from lyric_analyzer.folder_ui import TEMPLATE

    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.checkbox(key="show_folder_import").check().run()
    app.radio(key="folder_mode").set_value("フォルダーを選択").run()
    app.file_uploader(key="folder_files").set_value([("song.txt", TEMPLATE.encode(), "text/plain"), ("broken.txt", b"invalid", "text/plain")]).run()
    app.button(key="scan_uploaded_folder").click().run()
    assert not app.exception
    assert app.button(key="import_folder").disabled
    acknowledge = next(item for item in app.checkbox if item.key and item.key.endswith("-skip-errors"))
    acknowledge.check().run()
    app.button(key="import_folder").click().run()
    assert not app.exception
    assert len(Library(tmp_path / "library.db").list_songs()) == 1

