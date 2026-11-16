import csv
import io
import json

import streamlit as st

from lyric_analyzer.analysis.library_vocabulary import MODES, filter_rows, versions
from lyric_analyzer.analysis.trends import date_groups, expression_trend
from lyric_analyzer.library_vocabulary_ui import cached_index


def trends_panel(library, corpus):
    if not st.checkbox("制作時期ごとの語句の傾向を見る", key="show_trends"):
        return
    st.subheader("制作時期ごとの語句の使い方")
    st.caption("各曲の最新版と、曲の編集画面で指定した制作日を使います。登録日からは推定しません。制作日が不明な曲は期間別集計から除き、件数を別に表示します。")
    dates = {song["id"]: song["composed_on"] for song in library.list_songs()}
    groups, unknown = date_groups(corpus, dates)
    st.caption(f"制作日あり：{sum(len(ids) for ids in groups.values())}曲 / 不明：{len(unknown)}曲")
    if not groups:
        st.info("曲の制作日を入力して保存すると、期間別に比較できます。")
        return
    with st.form("trends-options"):
        mode = st.selectbox("期間別に集計する種類", list(MODES), format_func=lambda key: MODES[key], key="trends-mode")
        unit = st.selectbox("期間の単位", ["month", "year"], format_func=lambda key: {"month": "月", "year": "年"}[key], key="trends-unit")
        grammar = st.checkbox("期間別集計に文法的な参考情報も含める", key="trends-grammar")
        if st.form_submit_button("制作時期別に集計", key="trends-run"):
            try:
                # Keep undated sources in the snapshot to account for missing dates.
                index = cached_index(corpus, mode, grammar, False, versions())
                st.session_state["trends-result"] = {"index": index, "dates": dates, "unit": unit,
                                                    "created_at": library.now()}
            except (ValueError, OSError, RuntimeError) as error:
                st.error(f"期間別集計を完了できませんでした：{error}")
    result = st.session_state.get("trends-result")
    if not result:
        return
    index = result["index"]
    if corpus != index.corpus or dates != result["dates"]:
        st.info("保存データが変わりました。表示は前回集計時のものです。変更を反映するには再集計してください。")
    st.caption(f"集計日時：{result['created_at']} / {MODES[index.mode]}。条件変更後は集計ボタンで反映します。")
    query = st.text_input("期間別に確認する表現を絞り込む", key="trends-query")
    rows = filter_rows(index, 1, 4, query)
    if not rows:
        st.info("該当する表現がありません。フレーズは4文字以上が対象です。")
        return
    st.caption(f"該当{len(rows)}種類のうち使用曲数順の先頭200種類から選べます。絞り込みで対象を変更できます。")
    by_id = {row.id: row for row in rows[:200]}
    selected = st.selectbox("時期別に比較する表現", list(by_id), format_func=lambda key: f"{by_id[key].expression} / {by_id[key].pos}", key="trends-expression")
    trend = expression_trend(index, by_id[selected], result["dates"], result["unit"])
    st.caption(f"制作日不明のため期間別集計から除外：{len(trend['unknown_song_ids'])}曲。制作日のある曲が存在する期間だけを表示します。語句未使用は0件です。歌詞の良し悪しやAI生成の判定ではありません。")
    table = [{"期間": item["period"], "対象曲数": item["total_songs"], "使用曲数": item["using_songs"],
              "使用曲割合": item["song_share"], "出現回数": item["occurrences"],
              "異なる本文行数": item["unique_song_lines"]} for item in trend["periods"]]
    st.dataframe(table, hide_index=True, column_config={"使用曲割合": st.column_config.NumberColumn(format="percent")}, alt="制作期間ごとの対象曲数と表現の使用曲割合")
    sources = {song["song_id"]: song for song in trend["sources"]}
    evidence = [{"曲名": sources[item["song_id"]]["title"], "制作日": sources[item["song_id"]]["composed_on"] or "不明（期間別から除外）",
                 "改訂": sources[item["song_id"]]["revision_number"], "行": item["line_number"], "原文": item["line_text"]} for item in trend["evidence"]]
    st.dataframe(evidence[:200], hide_index=True, alt="選んだ表現の使用曲と制作日と引用行")
    st.caption("根拠は先頭200箇所を表示。JSONには制作日不明の曲を含む全引用を記録します。結果は実行中だけ保持し、確認用ファイルの取り込みには対応しません。")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(table[0]))
    writer.writeheader()
    writer.writerows(table)
    st.download_button("選んだ表現の期間別CSVを保存", stream.getvalue().encode("utf-8-sig"), file_name="vocabulary-trend.csv", mime="text/csv", key="trends-csv")
    st.download_button("引用付きの期間別JSONを保存", json.dumps({**trend, "created_at": result["created_at"]}, ensure_ascii=False, indent=2), file_name="vocabulary-trend.json", mime="application/json", key="trends-json")
