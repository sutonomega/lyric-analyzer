import csv
from datetime import datetime
import io
import json
import sqlite3
from zoneinfo import ZoneInfo

import streamlit as st

from lyric_analyzer.analysis.review_summary import condition_id, select_records, summarize
from lyric_analyzer.local_ai_ui import LABELS


CHECK_LABELS = {"subject": "人物・動作", "tense": "時制", "polarity": "肯定・否定", "tone": "語調"}


def method_label(version):
    return {"local-rewrite-1": "標準", "local-rewrite-2": "意味保持を詳しく指示（試用）"}.get(version, version)


def summary_panel(store):
    if not st.checkbox("AI候補の評価を集計", key="show_candidate_summary"):
        return
    st.subheader("モデル・作り方ごとの候補評価")
    try:
        records = store.candidate_evaluations()
    except (ValueError, sqlite3.Error) as error:
        st.error(f"候補評価を読み取れませんでした：{error}")
        return
    if not records:
        st.info("生成したAI候補がまだありません。総合分析または分析履歴から候補を作れます。")
        return
    conditions = {condition_id(record): record for record in records}
    selected = st.multiselect("比較するモデル・作り方", list(conditions), default=list(conditions),
                              format_func=lambda key: f"{conditions[key]['model']} / {method_label(conditions[key]['prompt_version'])} / {conditions[key]['provider']}",
                              key="candidate_summary_conditions")
    common_only = st.checkbox("共通の入力だけで比較", key="candidate_summary_common")
    chosen, common_count = select_records(records, selected, common_only)
    st.caption(f"選択した条件に共通する入力：{common_count}種類。同じ保存歌詞・対象行・テーマであることを基準にします。共通比較には2条件以上が必要です。")
    if not chosen:
        st.info("対象の候補がありません。比較条件や共通入力の指定を確認してください。")
        return
    try:
        summary = summarize(chosen)
    except ValueError as error:
        st.error(str(error))
        return
    st.caption(f"生成 {summary['generation_count']}回 / 入力 {summary['target_count']}種類 / 候補 {summary['candidate_count']}件。繰り返し生成は別の候補として数えます。")
    st.caption("割合の分母は評価済みの候補だけです。未評価と修正したいを使えそうに含めません。入力や評価済み件数が偏るため、少数の割合からモデルの優劣を判断しないでください。自動ランキングは行いません。")
    table = [{"提供元": row["provider"], "モデル": row["model"], "作り方": method_label(row["prompt_version"]),
              "方向": LABELS[row["direction"]], "候補数": row["generations"], "入力種類": row["targets"],
              "評価済み": row["evaluated"], "未評価": row["unreviewed"], "使えそう": row["usable"],
              "修正したい": row["edit"], "使わない": row["reject"],
              "使えそうの割合": f"{row['usable_ratio']:.1%}" if row["usable_ratio"] is not None else "未評価"}
             for row in summary["groups"]]
    st.dataframe(table, hide_index=True, alt="モデルと作り方と方向ごとの候補評価と評価済み件数")
    st.dataframe([{"提供元": row["provider"], "モデル": row["model"], "作り方": method_label(row["prompt_version"]), "確認項目": CHECK_LABELS[row["check"]],
                   "確認済み": row["checked"], "未確認": row["unchecked"], "保たれている": row["kept"], "変わっている": row["changed"]}
                  for row in summary["meaning_checks"]], hide_index=True, alt="意味を保つ案の人物と動作と時制と肯定否定と語調の手動確認")
    st.caption("意味保持の確認は1回の生成につき各項目1件です。3方向分として重複計上しません。保存し直した評価は最新の内容で集計します。")
    content = io.StringIO(newline="")
    writer = csv.DictWriter(content, fieldnames=list(table[0]))
    writer.writeheader()
    writer.writerows(table)
    st.download_button("集計をCSVで保存", content.getvalue().encode("utf-8-sig"), file_name="candidate-review-summary.csv",
                       mime="text/csv", key="candidate_summary_csv")
    st.download_button("集計をJSONで保存", json.dumps({"format": "candidate-review-summary-1", "created_at": store.library.now(),
                       "common_inputs_only": common_only, "common_target_count": common_count, **summary}, ensure_ascii=False, indent=2),
                       file_name="candidate-review-summary.json", mime="application/json", key="candidate_summary_json")
    st.caption("集計のダウンロードには歌詞本文・曲名・評価メモを含めません。評価の全文は分析結果のJSONと全データバックアップに含まれます。")
    if st.checkbox("集計元の候補を確認・評価", key="candidate_summary_details"):
        by_id = {f"{item['run_id']}:{item['suggestion_index']}": item for item in chosen}
        key = st.selectbox("確認する生成記録", list(by_id),
                           format_func=lambda value: f"{by_id[value]['title']} / {by_id[value]['model']} / {method_label(by_id[value]['prompt_version'])} / {datetime.fromisoformat(by_id[value]['generated_at']).astimezone(ZoneInfo('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M:%S')}（日本時間） / 候補{by_id[value]['suggestion_index'] + 1}",
                           key="candidate_summary_record")
        if st.button("この候補を分析履歴で開く", key="candidate_summary_open"):
            item = by_id[key]
            st.session_state["show_history"] = True
            st.session_state["history_run"] = item["run_id"]
            st.session_state[f"history-{item['run_id']}-ai-saved"] = True
            st.session_state[f"history-{item['run_id']}-ai-history"] = item["suggestion_index"]
            st.session_state[f"history-{item['run_id']}-quality-open-{item['suggestion_index']}"] = True
            st.rerun()
