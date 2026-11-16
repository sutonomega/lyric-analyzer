import sqlite3

import streamlit as st

from lyric_analyzer.storage import Library, default_database_path
from lyric_analyzer.transfer import validate_archive
from lyric_analyzer.analysis.parsing import analysis_segments, normalize_with_offsets, parse_lines
from lyric_analyzer.analysis.frequency import analyze


@st.cache_data(max_entries=20, show_spinner="単語とフレーズを集計しています…")
def cached_analysis(lyrics, include_unknown_tags):
    return analyze(lyrics, include_unknown_tags)


def main():
    st.set_page_config(page_title="Lyric Analyzer", page_icon="🎵")
    st.title("Lyric Analyzer")
    st.caption("歌詞を蓄積して、表現を広げる制作ノート")
    try:
        library = Library(default_database_path())
    except (OSError, sqlite3.Error, ValueError) as error:
        st.error(f"保存先を開けませんでした：{error}")
        st.stop()
    st.info("単語・フレーズの集計に対応しました。過去曲との重複・定型表現の検出は今後追加します。")
    if "pending_song" in st.session_state:
        st.session_state["selected_song"] = st.session_state.pop("pending_song")
        st.session_state["search"] = ""
    if "notice" in st.session_state:
        st.success(st.session_state.pop("notice"))
    query = st.text_input("ライブラリを検索（タグも対象）", key="search")
    songs = library.list_songs(query)
    by_id = {song["id"]: song for song in songs}
    if st.session_state.get("selected_song") not in by_id:
        st.session_state["selected_song"] = None
    selected = st.selectbox("編集する曲", [None, *by_id], format_func=lambda key: "新しい曲" if key is None else by_id[key]["title"], key="selected_song")
    song = by_id.get(selected, {})
    with st.form(f"song-{selected}"):
        title = st.text_input("曲名", value=song.get("title", ""))
        theme = st.text_input("テーマ", value=song.get("theme", ""))
        tags = st.text_input("タグ（カンマ区切り）", value=", ".join(song.get("tags", [])))
        lyrics = st.text_area("歌詞", value=song.get("lyrics_raw", ""), height=320)
        if st.form_submit_button("保存"):
            try:
                saved_id = library.save(title, lyrics, theme, selected, tags=tags.split(","))
            except (ValueError, sqlite3.Error) as error:
                st.error(str(error))
            else:
                st.session_state["pending_song"] = saved_id
                st.session_state["notice"] = "保存しました。"
                st.rerun()
        preview = st.form_submit_button("行・セクションを確認")
        include_unknown_tags = st.checkbox("未知のセクションタグも集計する", key=f"unknown-tags-{selected}")
        submitted_analysis = st.form_submit_button("単語・フレーズを分析", key="analyze")
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
        confirmed = st.checkbox("この曲とすべての改訂履歴を削除する", key=f"delete-confirm-{selected}")
        if st.button("曲を削除", disabled=not confirmed):
            library.delete(selected)
            st.rerun()
    transfer_panel(library)
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


def transfer_panel(library):
    st.subheader("書き出し・取り込み")
    st.caption("全曲の歌詞・タグ・改訂履歴をJSONで保存します。分析設定は今後追加します。")
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
