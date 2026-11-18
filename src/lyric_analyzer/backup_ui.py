import hashlib
import sqlite3

import streamlit as st

from lyric_analyzer.backup import export_backup, inspect_backup, restore_backup


def backup_panel(library):
    if not st.checkbox("全データのバックアップ・復元", key="show_backup"):
        return
    st.subheader("全データのバックアップ・復元")
    st.caption("歌詞・改訂・タグ・辞書・除外・分析履歴・評価をまとめたJSONです。楽曲だけのJSONとは異なります。")
    if st.button("バックアップを作成", key="create_backup"):
        try:
            st.session_state["backup_bytes"] = export_backup(library)
        except (ValueError, OSError, sqlite3.Error) as error:
            st.error(f"バックアップを作成できませんでした：{error}")
    if "backup_bytes" in st.session_state:
        st.caption("作成ボタンを押した時点のデータです。変更後は再作成してください。")
        st.download_button("全データのバックアップを保存", st.session_state["backup_bytes"],
                           file_name="lyric-analyzer-backup.json", mime="application/json", key="download_backup")
    uploaded = st.file_uploader("復元する全データJSON（100MBまで）", type=["json"], max_upload_size=100, key="restore_archive")
    if uploaded is None:
        return
    content = uploaded.getvalue()
    try:
        preview = inspect_backup(content)
    except ValueError as error:
        st.error(str(error))
        return
    counts = preview["counts"]
    st.text(f"作成日時：{preview['created_at']}")
    st.text(f"曲 {counts['songs']} / 改訂 {counts['song_revisions']} / 辞書 {counts['expression_rules']} / 履歴 {counts['analysis_runs']} / 除外 {counts['exclusions']} / 評価 {counts['finding_reviews']}")
    if preview['archived_songs']:
        st.caption(f"曲の件数には、重複整理で履歴参照用に残した{preview['archived_songs']}件を含みます。通常の登録曲は{counts['songs'] - preview['archived_songs']}曲です。")
    st.caption("現在の全データをこの内容に置き換えます。復元直前のデータは保存先のbackupsフォルダーへ自動退避します。")
    digest = hashlib.sha256(content).hexdigest()
    confirmed = st.checkbox("現在のデータを、このバックアップの内容に置き換える", key=f"restore-confirm-{digest}")
    if st.button("確認した内容で復元", disabled=not confirmed, key="restore_backup"):
        try:
            recovery = restore_backup(library, content, confirmed=confirmed)
        except (ValueError, OSError, sqlite3.Error) as error:
            st.error(f"復元できませんでした：{error}")
        else:
            st.session_state.clear()
            st.session_state["notice"] = f"復元しました。復元直前のバックアップ：{recovery}"
            st.rerun()
