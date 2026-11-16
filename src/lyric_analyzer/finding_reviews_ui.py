import csv
import io
import json

import streamlit as st

from lyric_analyzer.analysis.finding_reviews import summarize


def finding_reviews_panel(store):
    if not st.checkbox("指摘の評価状況を一覧で確認", key="show_finding_reviews"):
        return
    records = store.finding_review_records()
    if not records:
        st.info("総合分析を保存すると、指摘の評価状況を確認できます。")
        return
    st.subheader("重複・定型表現の指摘の評価状況")
    available = sorted({item["report_version"] for item in records}, reverse=True)
    version = st.selectbox("集計する指摘の方式", available, key="finding-reviews-version")
    latest = st.checkbox("同じ曲は最新の分析だけを集計", value=True, key="finding-reviews-latest")
    st.caption("選んだ方式の中で曲ごとの最新分析を使います。未保存の歌詞は本文ハッシュごとの最新分析です。再分析後の未評価は以前の評価で補いません。旧版の出現単位と新版の表現単位は別集計にします。")
    result = summarize(records, version, latest)
    st.caption(f"保存曲 {result['saved_songs']}曲（評価を始めた曲 {result['reviewed_saved_songs']}曲） / 未保存入力 {result['unsaved_inputs']}種類 / 分析 {len(result['runs'])}件")
    st.caption(f"評価済み {result['evaluated']} / 対象 {result['eligible']}件 / 未評価 {result['unreviewed']}件。評価完了の分析 {result['complete_runs']}件、対象指摘なし {result['empty_runs']}件。")
    ratio = f"{result['useful_ratio']:.1%}" if result["useful_ratio"] is not None else "未評価"
    st.text(f"有用 {result['useful']} / 意図的な表現 {result['intentional']} / 誤検出 {result['false_positive']} / 有用率 {ratio}")
    st.caption("有用率は有用／評価済み件数です。未評価を分母から除き、意図的な表現は有用に含めません。分析条件・比較曲・辞書が異なる結果を含み得ます。少数や偏った評価を検索精度・AI生成確率・MVP合格とは扱いません。AI候補への評価とは別の記録です。")
    rows = result["runs"]
    st.dataframe([{"曲名": row["title"], "分析日時（UTC）": row["created_at"], "対象": row["eligible"],
                   "評価済み": row["evaluated"], "未評価": row["unreviewed"], "有用": row["useful"],
                   "意図的": row["intentional"], "誤検出": row["false_positive"]} for row in rows], hide_index=True,
                 alt="分析履歴ごとの指摘の評価済み件数と未評価件数")
    pending_only = st.checkbox("未評価がある分析だけを選ぶ", value=True, key="finding-reviews-pending")
    selectable = {row["run_id"]: row for row in rows if not pending_only or row["unreviewed"] > 0}
    if selectable:
        selected = st.selectbox("評価する分析履歴", list(selectable), format_func=lambda key: f"{selectable[key]['title']} / {selectable[key]['created_at']} / 未評価{selectable[key]['unreviewed']}件", key="finding-reviews-run")
        if st.button("この分析の評価画面を開く", key="finding-reviews-open"):
            st.session_state["show_history"] = True
            st.session_state["history_run"] = selected
            st.session_state[f"history-{selected}-review"] = True
            st.rerun()
    else:
        st.info("選択条件に合う未評価の分析はありません。条件を解除すると評価済みの分析を開けます。")
    exported = [{key: value for key, value in row.items() if key != "title"} for row in rows]
    content = io.StringIO(newline="")
    writer = csv.DictWriter(content, fieldnames=list(exported[0]))
    writer.writeheader()
    writer.writerows(exported)
    st.download_button("指摘の評価集計をCSVで保存", content.getvalue().encode("utf-8-sig"), file_name="finding-review-progress.csv", mime="text/csv", key="finding-reviews-csv")
    st.download_button("指摘の評価集計をJSONで保存", json.dumps({**result, "runs": exported, "created_at": store.library.now()}, ensure_ascii=False, indent=2), file_name="finding-review-progress.json", mime="application/json", key="finding-reviews-json")
    st.caption("集計ファイルに曲名・歌詞・指摘本文・評価メモは含めません。確認用で、取り込みには対応しません。評価記録の復元には全データバックアップを使います。")
