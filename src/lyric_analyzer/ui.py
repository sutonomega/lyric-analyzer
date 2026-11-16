import sqlite3

import streamlit as st

from lyric_analyzer.storage import Library, default_database_path


def main():
    st.set_page_config(page_title="Lyric Analyzer", page_icon="🎵")
    st.title("Lyric Analyzer")
    st.caption("歌詞を蓄積して、表現を広げる制作ノート")
    try:
        library = Library(default_database_path())
    except (OSError, sqlite3.Error, ValueError) as error:
        st.error(f"保存先を開けませんでした：{error}")
        st.stop()
    st.info("現在は歌詞の保存・編集・改訂履歴に対応しています。重複・定型表現の分析は今後追加します。")
    query = st.text_input("ライブラリを検索")
    songs = library.list_songs(query)
    by_id = {song["id"]: song for song in songs}
    selected = st.selectbox("編集する曲", [None, *by_id], format_func=lambda key: "新しい曲" if key is None else by_id[key]["title"])
    song = by_id.get(selected, {})
    with st.form(f"song-{selected}"):
        title = st.text_input("曲名", value=song.get("title", ""))
        theme = st.text_input("テーマ", value=song.get("theme", ""))
        lyrics = st.text_area("歌詞", value=song.get("lyrics_raw", ""), height=320)
        if st.form_submit_button("保存"):
            try:
                library.save(title, lyrics, theme, selected)
            except (ValueError, sqlite3.Error) as error:
                st.error(str(error))
            else:
                st.success("保存しました。更新した一覧は検索や曲の選択で再表示されます。")
    if selected:
        with st.expander("改訂履歴"):
            for revision in library.revisions(selected):
                st.caption(f"改訂 {revision['revision_number']} / {revision['created_at']}")
                st.text(revision["lyrics_raw"])
        confirmed = st.checkbox("この曲とすべての改訂履歴を削除する")
        if st.button("曲を削除", disabled=not confirmed):
            library.delete(selected)
            st.rerun()
    st.caption(f"保存先：{library.path}")
