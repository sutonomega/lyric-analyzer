import sqlite3

import streamlit as st

from lyric_analyzer.storage import Library, default_database_path
from lyric_analyzer.transfer import validate_archive
from lyric_analyzer.analysis.parsing import analysis_segments, normalize_with_offsets, parse_lines


def main():
    st.set_page_config(page_title="Lyric Analyzer", page_icon="🎵")
    st.title("Lyric Analyzer")
    st.caption("歌詞を蓄積して、表現を広げる制作ノート")
    try:
        library = Library(default_database_path())
    except (OSError, sqlite3.Error, ValueError) as error:
        st.error(f"保存先を開けませんでした：{error}")
        st.stop()
    st.info("歌詞・タグ・改訂履歴の管理とJSON入出力に対応しています。行・セクションの確認もできます。重複・定型表現の分析は今後追加します。")
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
