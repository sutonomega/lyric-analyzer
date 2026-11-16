import sqlite3

import streamlit as st

from lyric_analyzer.storage import Library, default_database_path
from lyric_analyzer.transfer import validate_archive
from lyric_analyzer.analysis.parsing import analysis_segments, normalize_with_offsets, parse_lines
from lyric_analyzer.analysis.frequency import analyze
from lyric_analyzer.analysis.comparison import collapse_contained, compare
from lyric_analyzer.analysis.expressions import detect
from lyric_analyzer.expression_ui import detection_panel, rule_editor
from lyric_analyzer.analysis.report import build_report
from lyric_analyzer.history import HistoryStore
from lyric_analyzer.report_ui import report_panel, history_panel, exclusion_panel
from lyric_analyzer.backup_ui import backup_panel
from lyric_analyzer.folder_ui import folder_panel
from lyric_analyzer.review_summary_ui import summary_panel
from lyric_analyzer.library_vocabulary_ui import vocabulary_panel
from lyric_analyzer.trends_ui import trends_panel
from lyric_analyzer.date_batch_ui import date_batch_panel


@st.cache_data(max_entries=20, show_spinner="単語とフレーズを集計しています…")
def cached_analysis(lyrics, include_unknown_tags):
    return analyze(lyrics, include_unknown_tags)


@st.cache_data(max_entries=20, show_spinner="過去曲との重複を調べています…")
def cached_comparison(lyrics, corpus, target_song_id, selected_ids, start_date, end_date, include_unknown_tags):
    return compare(lyrics, corpus, target_song_id, selected_ids, start_date, end_date, include_unknown_tags)


@st.cache_data(max_entries=20, show_spinner="定型表現と文型を確認しています…")
def cached_detection(lyrics, rules, include_unknown_tags):
    return detect(lyrics, rules, include_unknown_tags)


def main():
    st.set_page_config(page_title="Lyric Analyzer", page_icon="🎵")
    st.title("Lyric Analyzer")
    st.caption("歌詞を蓄積して、表現を広げる制作ノート")
    try:
        library = Library(default_database_path())
    except (OSError, sqlite3.Error, ValueError) as error:
        st.error(f"保存先を開けませんでした：{error}")
        st.stop()
    st.info("総合分析で過去曲との重複と登録ルールを一度に確認できます。指摘の除外と分析履歴にも対応しています。")
    if "pending_song" in st.session_state:
        st.session_state["selected_song"] = st.session_state.pop("pending_song")
        st.session_state["search"] = ""
    if "notice" in st.session_state:
        st.success(st.session_state.pop("notice"))
    for song_id in st.session_state.pop("pending_date_refresh", []):
        st.session_state.pop(f"composed-on-{song_id}", None)
    query = st.text_input("ライブラリを検索（タグも対象）", key="search")
    songs = library.list_songs(query)
    by_id = {song["id"]: song for song in songs}
    if st.session_state.get("selected_song") not in by_id:
        st.session_state["selected_song"] = None
    selected = st.selectbox("編集する曲", [None, *by_id], format_func=lambda key: "新しい曲" if key is None else by_id[key]["title"], key="selected_song")
    song = by_id.get(selected, {})
    corpus = library.comparison_corpus()
    comparison_options = {item.song_id: item for item in corpus if item.song_id != selected}
    if st.session_state.pop("reset_comparison_targets", False):
        for key in list(st.session_state):
            if key.startswith("comparison-songs-"):
                del st.session_state[key]
        st.session_state[f"comparison-songs-{selected}"] = list(comparison_options)
    comparison_key = f"comparison-songs-{selected}"
    if comparison_key not in st.session_state:
        st.session_state[comparison_key] = list(comparison_options)
    history = HistoryStore(library)
    with st.form(f"song-{selected}"):
        title = st.text_input("曲名", value=song.get("title", ""))
        theme = st.text_input("テーマ", value=song.get("theme", ""))
        tags = st.text_input("タグ（カンマ区切り）", value=", ".join(song.get("tags", [])))
        lyrics = st.text_area("歌詞", value=song.get("lyrics_raw", ""), height=320)
        styles = st.text_area("Styles（曲のスタイル）", value=song.get("styles", ""), height=90, key=f"styles-{selected}")
        composed_on = st.date_input("制作日（任意・不明なら空欄）", value=song.get("composed_on") or None, min_value="0001-01-01", max_value="9999-12-31", key=f"composed-on-{selected}")
        if st.form_submit_button("保存"):
            try:
                saved_id = library.save(title, lyrics, theme, selected, tags=tags.split(","), styles=styles,
                                        composed_on=composed_on.isoformat() if composed_on else "")
            except (ValueError, sqlite3.Error) as error:
                st.error(str(error))
            else:
                st.session_state["pending_song"] = saved_id
                st.session_state["notice"] = "保存しました。"
                st.rerun()
        preview = st.form_submit_button("行・セクションを確認")
        include_unknown_tags = st.checkbox("未知のセクションタグも集計する", key=f"unknown-tags-{selected}")
        submitted_analysis = st.form_submit_button("単語・フレーズを分析", key="analyze")
        st.caption("比較する曲は各曲の最新版です。編集中の曲は自動で除外します。")
        comparison_ids = st.multiselect("比較対象の曲", list(comparison_options),
                                        format_func=lambda song_id: f"{comparison_options[song_id].title}（{song_id[:8]}）",
                                        key=comparison_key)
        start_date = st.date_input("登録日・開始（任意、日本時間）", value=None, key="comparison_start")
        end_date = st.date_input("登録日・終了（任意、日本時間）", value=None, key="comparison_end")
        submitted_comparison = st.form_submit_button("過去曲と比較", key="compare")
        submitted_detection = st.form_submit_button("定型表現を確認", key="detect")
        submitted_report = st.form_submit_button("総合分析して履歴に保存", key="report")
        st.caption("総合分析は未保存の歌詞も分析履歴に保存します。単語・比較・定型表現の個別確認は履歴に保存しません。")
    if submitted_report:
        try:
            comparison = cached_comparison(lyrics, corpus, selected, tuple(comparison_ids), start_date, end_date, include_unknown_tags)
            detection = cached_detection(lyrics, library.list_rules(), include_unknown_tags)
            settings = {"selected_ids": comparison_ids, "start_date": start_date.isoformat() if start_date else None,
                        "end_date": end_date.isoformat() if end_date else None, "include_unknown_tags": include_unknown_tags, "styles": styles,
                        "thresholds": {"min_songs": 3, "min_ratio": 0.2, "small_corpus": 10, "reference_songs": 2}}
            report = build_report(lyrics, comparison, detection, settings)
            run_id = history.save(report, title, selected)
        except (ValueError, RuntimeError, OSError, sqlite3.Error) as error:
            st.error(f"総合分析を保存できませんでした：{error}")
        else:
            st.session_state["current_report"] = run_id
            st.success("総合分析を履歴に保存しました。")
    if submitted_detection:
        try:
            detection = cached_detection(lyrics, library.list_rules(), include_unknown_tags)
        except (ValueError, RuntimeError, OSError, sqlite3.Error) as error:
            st.session_state.pop("detection_result", None)
            st.error(f"確認できませんでした：{error}")
        else:
            st.session_state["detection_result"] = (selected, detection)
    if submitted_comparison:
        try:
            comparison = cached_comparison(lyrics, corpus, selected, tuple(comparison_ids), start_date, end_date, include_unknown_tags)
        except (ValueError, RuntimeError, OSError) as error:
            st.session_state.pop("comparison_result", None)
            st.error(f"比較できませんでした：{error}")
        else:
            st.session_state["comparison_result"] = (selected, lyrics, comparison)
    if submitted_analysis:
        try:
            result = cached_analysis(lyrics, include_unknown_tags)
        except (ValueError, RuntimeError, OSError) as error:
            st.session_state.pop("analysis_result", None)
            st.error(f"分析できませんでした：{error}")
        else:
            st.session_state["analysis_result"] = (selected, lyrics, result)
    stored = st.session_state.get("analysis_result")
    if stored is not None and stored[0] == selected:
        frequency_panel(stored[1], stored[2])
    stored_comparison = st.session_state.get("comparison_result")
    if stored_comparison is not None and stored_comparison[0] == selected:
        comparison_panel(stored_comparison[1], stored_comparison[2])
    stored_detection = st.session_state.get("detection_result")
    if stored_detection is not None and stored_detection[0] == selected:
        detection_panel(stored_detection[1])
    if st.session_state.get("current_report"):
        current_report = history.get(st.session_state["current_report"])
        if current_report and current_report["song_id"] == selected:
            report_panel(history, current_report, f"current-{current_report['id']}", editable=True)
    if preview:
        st.subheader("解析の下準備")
        st.caption("セクションタグは集計対象から除外します。本文中のブラケットは保持します。")
        for line in parse_lines(lyrics):
            label = "タグ" if line.metadata else "本文"
            st.text(f"{line.line_number}: [{label}] {line.text}")
        st.caption(f"解析区間：{len(analysis_segments(lyrics))}件 / 正規化後：{len(normalize_with_offsets(lyrics).text)}文字")
    if selected:
        with st.expander("改訂履歴"):
            for revision in library.revisions(selected):
                st.caption(f"改訂 {revision['revision_number']} / {revision['created_at']}")
                st.text(revision["lyrics_raw"])
        st.caption(f"この曲を削除すると、入力・比較元に含む分析履歴{history.related_count(selected)}件と曲単位の除外設定も削除します。")
        confirmed = st.checkbox("この曲とすべての改訂履歴を削除する", key=f"delete-confirm-{selected}")
        if st.button("曲を削除", disabled=not confirmed):
            library.delete(selected)
            st.rerun()
    transfer_panel(library)
    vocabulary_panel(library, corpus)
    trends_panel(library, corpus)
    date_batch_panel(library)
    rule_editor(library)
    exclusion_panel(history)
    summary_panel(history)
    history_panel(history)
    backup_panel(library)
    folder_panel(library)
    st.caption(f"保存先：{library.path}")


def frequency_panel(lyrics, result):
    st.subheader("単語・フレーズの集計")
    st.caption("最後に分析ボタンを押した歌詞の結果です。編集後は再分析してください。分析だけでは歌詞は保存されません。")
    st.caption(f"解析器 {result.tokenizer_version} / core辞書 {result.dictionary_version} / 分割 A / {result.analyzer_version}")
    mode = st.selectbox("集計の種類", ["単語（原形）", "フレーズ（原文一致）", "フレーズ（全角・半角を統一）"], key="frequency_mode")
    rows = {"単語（原形）": result.words, "フレーズ（原文一致）": result.phrases_surface,
            "フレーズ（全角・半角を統一）": result.phrases_normalized}[mode]
    st.caption("単語は名詞・動詞・形容詞、フレーズは助詞・助動詞を含む連続2〜5語を集計します。フレーズの活用形は統合しません。")
    if not rows:
        st.info("集計できる語句がありません。歌詞本文を入力してください。")
        return
    displayed = rows[:50]
    st.caption(f"{len(rows)}種類中、上位{len(displayed)}件を表示。回数は同じ行・サビの反復も含みます。異なる本文行数は同じ本文の行をまとめた数です。")
    st.dataframe([{"語句": row.expression, "品詞": row.pos, "回数": row.count,
                   "異なる本文行数": row.unique_line_count,
                   "表記別回数": " / ".join(f"{surface}：{count}" for surface, count in row.variants.items()),
                   "出現行": ", ".join(str(number) for number in sorted({item.line_number for item in row.occurrences}))}
                  for row in displayed], hide_index=True, alt="語句ごとの回数・表記・出現行", key="frequency_table")
    selected_index = st.selectbox("出現箇所を確認", range(len(displayed)),
                                  format_func=lambda index: f"{displayed[index].expression}（{displayed[index].count}回）",
                                  key=f"frequency-detail-{mode}")
    for item in displayed[selected_index].occurrences:
        st.caption(f"{item.line_number}行目 / {item.section or 'セクションなし'}")
        st.text(item.line_text)
        st.text(f"該当表記：{lyrics[item.start:item.end]}")


def comparison_panel(lyrics, result):
    st.subheader("過去曲との重複")
    st.caption(f"比較対象：{len(result.corpus)}曲。最後に比較した歌詞・対象曲の最新版を表示しています。編集・曲追加・条件変更後は再比較してください。")
    st.caption("使用曲数は曲IDごとに1曲、過去曲の出現回数はサビの反復も含みます。未保存の歌詞でも比較でき、比較だけでは保存されません。")
    if result.duplicate_song_ids:
        titles = [song.title for song in result.corpus if song.song_id in result.duplicate_song_ids]
        st.warning(f"歌詞全文が同じ別曲があります：{'、'.join(titles)}。比較曲数には含めています。必要なら比較対象から外してください。")
    if not result.corpus:
        st.info("比較対象の曲がありません。過去曲を登録するか、対象曲・登録日の条件を確認してください。")
        return
    if len(result.corpus) < 10:
        st.caption("比較曲が10曲未満のため、頻出の判定は参考情報です。2曲以上の一致を「参考（母数少）」と表示します。")
    else:
        st.caption("フレーズは3曲以上かつ比較曲の20%以上で使われている場合に「頻出」と表示します。単語は参考情報です。")
    mode = st.selectbox("比較の種類", ["フレーズ（原文一致）", "フレーズ（全角・半角を統一）", "単語（原形）"], key="comparison_mode")
    rows = {"フレーズ（原文一致）": result.phrases_surface, "フレーズ（全角・半角を統一）": result.phrases_normalized,
            "単語（原形）": result.words}[mode]
    show_nested = st.checkbox("長い一致に含まれる短い一致も表示", key="comparison_nested")
    if not show_nested and mode != "単語（原形）":
        rows = collapse_contained(rows)
    if not rows:
        st.info("この条件で一致する語句はありません。")
        return
    displayed = rows[:50]
    st.caption(f"一致{len(rows)}種類中、上位{len(displayed)}件を表示。短い一致は同じ出現箇所を含む長い一致にまとめます。")
    st.dataframe([{"語句": item.expression, "区分": item.status, "使用曲数": item.song_count,
                   "比較曲数": len(result.corpus), "過去曲の出現回数": item.count,
                   "入力中の出現回数": len(item.target_occurrences)} for item in displayed],
                 hide_index=True, alt="過去曲と一致する表現の使用曲数と出現回数", key="comparison_table")
    index = st.selectbox("重複箇所を確認", range(len(displayed)), format_func=lambda number: displayed[number].expression,
                         key=f"comparison-detail-{mode}-{show_nested}")
    match = displayed[index]
    st.caption("入力中の歌詞")
    for occurrence in match.target_occurrences:
        st.text(f"{occurrence.line_number}行目：{occurrence.line_text}")
        st.text(f"該当表記：{lyrics[occurrence.start:occurrence.end]}")
    snapshots = {song.song_id: song for song in result.corpus}
    st.caption("過去曲の該当箇所（比較した時点の改訂）")
    for evidence in match.evidence:
        song = snapshots[evidence.song_id]
        item = evidence.occurrence
        st.text(f"{song.title} / 改訂{song.revision_number} / {item.line_number}行目：{item.line_text}")
        st.text(f"該当表記：{song.lyrics[item.start:item.end]}")
    source_ids = sorted({item.song_id for item in match.evidence})
    source_id = st.selectbox("引用元の歌詞を開く", source_ids,
                             format_func=lambda song_id: f"{snapshots[song_id].title}（{song_id[:8]}）",
                             key=f"comparison-source-{mode}-{index}-{show_nested}")
    source = snapshots[source_id]
    matching_lines = {item.occurrence.line_number for item in match.evidence if item.song_id == source_id}
    with st.expander(f"引用元全文：{source.title} / 改訂{source.revision_number}"):
        for line in parse_lines(source.lyrics):
            marker = "▶" if line.line_number in matching_lines else " "
            st.text(f"{marker} {line.line_number}：{line.text}")


def transfer_panel(library):
    st.subheader("書き出し・取り込み")
    st.caption("このJSONは全曲の歌詞・タグ・改訂履歴用です。辞書・除外・履歴・評価も保存する場合は、全データのバックアップを使います。")
    st.download_button("全曲をJSONで書き出す", library.export_json(), file_name="lyric-analyzer-library.json", mime="application/json", key="export")
    uploaded = st.file_uploader("書き出したJSONを取り込む（10MBまで）", type=["json"], max_upload_size=10, key="archive")
    if uploaded is None:
        return
    try:
        archive = validate_archive(uploaded.getvalue())
    except ValueError as error:
        st.error(str(error))
        return
    current = {song["id"] for song in library.list_songs()}
    conflicts = sum(song["id"] in current for song in archive["songs"])
    st.caption(f"取り込み予定：{len(archive['songs'])}曲 / 既存IDとの重複：{conflicts}曲")
    for song in archive["songs"]:
        st.text(f"{song['title']} / {len(song['revisions'])}改訂 / {', '.join(song['tags'])}")
    mode = st.radio("IDが重複する曲の扱い", ["skip", "copy"], format_func=lambda value: {"skip": "取り込まない", "copy": "別の曲として追加"}[value], key="import_mode")
    if st.button("表示内容で取り込む", key="import"):
        try:
            imported, skipped = library.import_json(uploaded.getvalue(), mode)
        except (ValueError, sqlite3.Error) as error:
            st.error(f"取り込めませんでした。変更は取り消しました：{error}")
        else:
            st.session_state["notice"] = f"{imported}曲を取り込み、{skipped}曲をスキップしました。"
            st.rerun()
