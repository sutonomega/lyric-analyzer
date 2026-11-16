from dataclasses import asdict
import hashlib
import json
import sqlite3

import streamlit as st

from lyric_analyzer.batch_analysis import run_batch


def batch_analysis_panel(store, corpus):
    if not st.checkbox("複数曲をまとめて総合分析", key="show_batch_analysis"):
        return
    st.subheader("評価する曲の総合分析をまとめて作成")
    st.caption("Issue #9の実歌詞レビューの準備用です。保存済み最新版を1〜20曲選び、各曲を自曲以外の保存曲すべてと比較します。評価は自動で付けません。編集中で未保存の内容は含まず、Ollamaや外部AIは呼び出しません。")
    sources = {song.song_id: song for song in corpus}
    if not sources:
        st.info("歌詞を保存または取り込んでください。")
        return
    selected = st.multiselect("一括分析する曲（最大20曲）", list(sources), default=[], format_func=lambda key: f"{sources[key].title}（{key[:8]}）", max_selections=20, key="batch-analysis-targets")
    unknown = st.checkbox("一括分析に未知のセクションタグも含める", key="batch-analysis-unknown")
    rules = store.library.list_rules()
    if selected:
        st.dataframe([{"曲名": sources[key].title, "改訂": sources[key].revision_number,
                       "本文の文字数": len(sources[key].lyrics), "比較曲数": len(corpus) - 1} for key in selected],
                     hide_index=True, alt="一括分析する曲の改訂と比較曲数")
        st.caption(f"辞書：有効{sum(rule.enabled for rule in rules)}件。比較対象は保存曲全体から自曲だけを除き、登録日や制作日で絞りません。現在の有効な全体・曲単位の除外設定を履歴保存時に記録します。既存の分析履歴と評価は残し、新しい未評価の分析履歴を追加します。")
        if st.checkbox("一括分析の比較曲と辞書を確認", key="batch-analysis-details"):
            st.dataframe([{"曲名": song.title, "曲ID": song.song_id, "改訂": song.revision_number} for song in corpus], hide_index=True, alt="一括分析に使用する保存曲の一覧")
            st.dataframe([{"種類": rule.kind, "条件": rule.pattern_json, "有効": rule.enabled, "版": rule.version} for rule in rules], hide_index=True, alt="一括分析に使用する辞書の条件と版")
        fingerprint = hashlib.sha256(json.dumps([[asdict(song) for song in corpus], [asdict(rule) for rule in rules], selected, unknown], sort_keys=True).encode()).hexdigest()
        finished = fingerprint in st.session_state.get("batch-analysis-completed", [])
        confirmed = st.checkbox("表示した曲と条件で分析し、全曲分を履歴に追加する", key=f"batch-analysis-confirm-{fingerprint[:16]}")
        if st.button("確認した曲をまとめて分析・保存", disabled=not confirmed or finished, key="batch-analysis-run") and confirmed is True and not finished:
            bar = st.progress(0.0, text="総合分析を準備しています…")
            try:
                runs = run_batch(store, corpus, rules, selected, unknown, progress=lambda value: bar.progress(value, text="総合分析中（全曲の完了後に履歴へ保存します）"))
            except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
                st.error(f"一括分析を保存できませんでした：{error}")
            else:
                st.session_state["batch-analysis-result"] = {"runs": runs, "titles": {key: sources[key].title for key in selected}}
                st.session_state["batch-analysis-completed"] = [*st.session_state.get("batch-analysis-completed", []), fingerprint][-50:]
                st.rerun()
            finally:
                bar.empty()
        if finished:
            st.info("この曲と条件の一括分析は今回のアプリ実行中に保存済みです。重複追加を避けるため再実行できません。個別の再分析は曲の編集画面から行えます。")
    result = st.session_state.get("batch-analysis-result")
    if result:
        existing = {row["id"] for row in store.list_runs()}
        runs = {row["run_id"]: row for row in result["runs"] if row["run_id"] in existing}
        st.success(f"直近の一括分析：{len(result['runs'])}曲分を保存しました。評価は未実施です。")
        if runs:
            chosen = st.selectbox("一括分析から評価する履歴", list(runs), format_func=lambda key: result["titles"][runs[key]["song_id"]], key="batch-analysis-history")
            if st.button("一括分析した履歴の評価を開く", key="batch-analysis-open"):
                st.session_state["show_history"] = True
                st.session_state["history_run"] = chosen
                st.session_state[f"history-{chosen}-review"] = True
                st.rerun()
