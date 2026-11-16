import hashlib
import sqlite3

import streamlit as st

from lyric_analyzer.folder_import import import_records, plan_import, preview_files, scan_folder


TEMPLATE = "Title: 夜の改札\nStyles: mellow pop, piano, female vocal\nLyrics:\n[Verse]\n改札の端で切符を折った\n[Chorus]\n最終電車の窓を見送る\n"


def folder_panel(library):
    if not st.checkbox("フォルダーから歌詞を一括取り込み", key="show_folder_import"):
        return
    st.subheader("フォルダーから歌詞を一括取り込み")
    st.caption("1ファイル1曲、Title・Styles・Lyricsの見出しがある.txt／.md／.markdownが対象です。TitleとLyricsの内容は必須、Stylesの内容は空でも登録できます。元のファイルは変更しません。")
    st.download_button("見出し付きテキストのひな形を保存", TEMPLATE, file_name="song-template.txt", mime="text/plain", key="folder_template")
    mode = st.radio("フォルダーの指定方法", ["パスを入力", "フォルダーを選択"], key="folder_mode")
    encoding = st.selectbox("ファイルの文字コード", ["auto", "utf-8-sig", "cp932"],
                            format_func=lambda value: {"auto": "自動（BOM・UTF-8・CP932）", "utf-8-sig": "UTF-8（BOM対応）", "cp932": "CP932（WindowsのShift-JIS）"}[value], key="folder_encoding")
    if mode == "パスを入力":
        folder = st.text_input("歌詞が入ったフォルダーのパス", placeholder="C:\\Music\\Lyrics", key="folder_path")
        recursive = st.checkbox("子フォルダーも含める", key="folder_recursive")
        if st.button("フォルダーを確認", key="scan_folder"):
            st.session_state.pop("folder_preview", None)
            try:
                if not folder.strip():
                    raise ValueError("フォルダーのパスを入力してください。")
                st.session_state["folder_preview"] = scan_folder(folder, recursive, encoding)
            except (ValueError, OSError) as error:
                st.error(f"確認できませんでした：{error}")
    else:
        st.caption("フォルダー選択では、選んだフォルダー内の子フォルダーも対象になります。")
        uploaded = st.file_uploader("取り込むフォルダーを選択", type=["txt", "md", "markdown"],
                                    accept_multiple_files="directory", max_upload_size=2, key="folder_files")
        if st.button("選択したファイルを確認", key="scan_uploaded_folder"):
            st.session_state.pop("folder_preview", None)
            try:
                if not uploaded:
                    raise ValueError("フォルダーを選択してください。")
                st.session_state["folder_preview"] = preview_files(((item.name, item.getvalue()) for item in uploaded), encoding=encoding)
            except ValueError as error:
                st.error(f"確認できませんでした：{error}")
    preview = st.session_state.get("folder_preview")
    if preview is None:
        return
    st.text(f"確認した対象：{preview.label}")
    st.caption("確認ボタンを押した時点の内容です。ファイル・フォルダー・文字コードを変えたら、もう一度確認してください。1回500ファイル・合計20MBまでです。")
    fingerprint = hashlib.sha256(repr((preview.label, tuple(item.id for item in preview.records), preview.errors)).encode("utf-8")).hexdigest()[:16]
    prefix = f"folder-{fingerprint}"
    skip_errors = False
    if preview.errors:
        st.warning(f"読み取れないファイルが{len(preview.errors)}件あります。")
        for name, message in preview.errors:
            st.text(f"{name}：{message}")
        skip_errors = st.checkbox("読み取れないファイルを除き、確認できた曲だけ取り込む", key=f"{prefix}-skip-errors")
    if not preview.records:
        st.info("取り込める曲がありません。対象ファイルと見出しを確認してください。")
        return
    records = {item.id: item for item in preview.records}
    chosen = st.multiselect("取り込む曲", list(records), default=list(records),
                            format_func=lambda value: f"{records[value].source} / {records[value].title}", key=f"{prefix}-selected")
    allow_variants = st.checkbox("同名・同じ歌詞でも、内容が違う曲は別曲として追加", key=f"{prefix}-variants")
    st.caption("既存曲は上書きしません。Title・Styles・Lyricsがすべて同じ曲は、追加設定にかかわらずスキップします。")
    selected = tuple(records[value] for value in chosen)
    planned = plan_import(selected, library.list_songs(), allow_variants)
    st.dataframe([{"ファイル": item.source, "Title": item.title, "Styles": item.styles,
                   "Lyrics文字数": len(item.lyrics), "文字コード": item.encoding, "重複確認": status,
                   "処理": "追加" if add else "スキップ"} for item, status, add in planned],
                 hide_index=True, alt="フォルダー内の曲と登録予定の一覧")
    if selected:
        detail_id = st.selectbox("取り込み内容を確認", chosen, format_func=lambda value: records[value].title, key=f"{prefix}-detail")
        detail = records[detail_id]
        st.text(f"Title：{detail.title}")
        st.text(f"Styles：{detail.styles}")
        st.text(detail.lyrics)
    st.caption(f"選択{len(selected)}曲 / 追加予定{sum(add for _, _, add in planned)}曲 / スキップ予定{sum(not add for _, _, add in planned)}曲")
    if st.button("確認した曲を一括登録", key="import_folder", disabled=not selected or bool(preview.errors and not skip_errors)):
        try:
            added, skipped = import_records(library, selected, allow_variants)
        except (ValueError, sqlite3.Error) as error:
            st.error(f"登録できませんでした。今回の登録は取り消しました：{error}")
        else:
            st.session_state.pop("folder_preview", None)
            if added:
                st.session_state["reset_comparison_targets"] = True
            st.session_state["notice"] = f"{added}曲を追加し、{skipped}曲をスキップしました。"
            st.rerun()
