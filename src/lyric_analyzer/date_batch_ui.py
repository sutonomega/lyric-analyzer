import hashlib
import json
import sqlite3
from uuid import uuid4

import streamlit as st

from lyric_analyzer.storage import validate_composed_on


def prepare_changes(snapshot, edited):
    if not isinstance(edited, list) or len(edited) != len(snapshot):
        raise ValueError("一覧の行数が変わっています。読み込み直してください。")
    changes = []
    for source, row in zip(snapshot, edited):
        if (not isinstance(row, dict) or row.get("曲ID") != source["id"]
                or row.get("曲名") != source["title"] or row.get("現在の制作日") != source["composed_on"]):
            raise ValueError("曲の対応を確認できません。一覧を読み込み直してください。")
        value = row.get("変更後の制作日")
        if value is None:
            value = ""
        try:
            validate_composed_on(value)
        except ValueError as error:
            raise ValueError(f"「{source['title']}」：{error}") from error
        if value != source["composed_on"]:
            changes.append({"song_id": source["id"], "title": source["title"], "before": source["composed_on"],
                            "after": value, "updated_at": source["updated_at"]})
    return changes


def date_batch_panel(library):
    if not st.checkbox("制作日を一覧で編集", key="show_date_batch"):
        return
    st.subheader("複数曲の制作日をまとめて入力")
    st.caption("曲名などで絞り込み、一覧を読み込んでから日付欄を編集します。YYYY-MM-DDで入力し、不明に戻す場合は空欄にします。登録日からの自動推定は行いません。")
    with st.form("date-batch-filter"):
        query = st.text_input("制作日一覧の検索（曲名・歌詞・タグなど）", key="date-batch-query")
        unknown = st.checkbox("制作日が不明の曲だけ", value=True, key="date-batch-unknown")
        if st.form_submit_button("制作日の一覧を読み込む", key="date-batch-load"):
            songs = [song for song in library.list_songs(query) if not unknown or not song["composed_on"]]
            if len(songs) > 2000:
                st.error("一覧は2,000曲までです。検索条件を絞ってください。前の一覧を保持します。")
            else:
                st.session_state["date-batch-snapshot"] = {
                    "songs": [{key: song[key] for key in ("id", "title", "composed_on", "updated_at")} for song in songs],
                    "query": query, "unknown": unknown, "id": uuid4().hex}
    loaded = st.session_state.get("date-batch-snapshot")
    if loaded is None:
        return
    snapshot = loaded["songs"]
    st.caption(f"読み込み済み：{len(snapshot)}曲 / 検索：{loaded['query'] or 'すべて'} / 不明だけ：{'はい' if loaded['unknown'] else 'いいえ'}。読み込み直すと未保存の一覧編集を破棄します。")
    if not snapshot:
        st.info("条件に合う曲がありません。検索条件を変えて読み込んでください。")
        return
    rows = [{"曲ID": song["id"], "曲名": song["title"], "現在の制作日": song["composed_on"],
             "変更後の制作日": song["composed_on"]} for song in snapshot]
    edited = st.data_editor(rows, hide_index=True, num_rows="fixed", disabled=["曲ID", "曲名", "現在の制作日"],
                            column_config={"曲ID": None, "変更後の制作日": st.column_config.TextColumn(help="YYYY-MM-DD、または空欄")},
                            key=f"date-batch-editor-{loaded['id']}", alt="曲ごとの現在と変更後の制作日を編集する一覧")
    try:
        changes = prepare_changes(snapshot, edited)
    except ValueError as error:
        st.error(str(error))
        return
    if not changes:
        st.info("日付を編集すると変更内容を表示します。まだ保存していません。")
        return
    st.caption(f"変更予定：{len(changes)}曲。歌詞や改訂は変更せず、制作日だけを一括保存します。")
    st.dataframe([{"曲名": change["title"], "変更前": change["before"] or "不明", "変更後": change["after"] or "不明"} for change in changes],
                 hide_index=True, alt="保存前の制作日の変更内容")
    fingerprint = hashlib.sha256(json.dumps([loaded["id"], changes], sort_keys=True).encode()).hexdigest()[:16]
    confirmed = st.checkbox("表示した制作日の変更を保存する", key=f"date-batch-confirm-{fingerprint}")
    if st.button("確認した制作日を一括保存", disabled=not confirmed, key="date-batch-save") and confirmed is True:
        try:
            count = library.update_composition_dates(changes)
        except (ValueError, sqlite3.Error) as error:
            st.error(str(error))
        else:
            st.session_state["pending_date_refresh"] = [change["song_id"] for change in changes]
            st.session_state.pop("date-batch-snapshot", None)
            st.session_state["notice"] = f"{count}曲の制作日を保存しました。期間別の表示には再集計が必要です。"
            st.rerun()
