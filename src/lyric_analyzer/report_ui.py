from datetime import datetime
import json
import sqlite3
from zoneinfo import ZoneInfo

import streamlit as st

from lyric_analyzer.analysis.report import exclusion_for


def report_panel(store, report, prefix, editable=False):
    st.subheader("統合した分析結果")
    st.caption(f"{report['title']} / 比較 {len(report['corpus'])}曲 / 保存 {datetime.fromisoformat(report['created_at']).astimezone(ZoneInfo('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M:%S')}（日本時間）")
    st.caption("過去曲の頻出／参考と登録ルールの両方 → 過去曲の頻出／参考 → 登録ルール → 単語参考の順で表示します。少数曲の参考情報を含み、AI生成の判定ではありません。")
    st.caption("この結果は分析時点の歌詞・比較元・辞書です。編集・設定変更後は総合分析をやり直してください。")
    current = editable or st.checkbox("現在の除外設定で表示", key=f"{prefix}-current")
    exclusions = store.list_exclusions() if current else report["exclusions"]
    show_excluded = st.checkbox("除外した指摘も表示", key=f"{prefix}-show-excluded")
    rows = [(item, exclusion_for(item, report, exclusions)) for item in report["findings"]]
    visible = [(item, exclusion) for item, exclusion in rows if show_excluded or exclusion is None]
    st.caption(f"全{len(rows)}件 / 除外{sum(exclusion is not None for _, exclusion in rows)}件。履歴の通常表示は保存時の除外設定です。")
    if report["duplicate_song_ids"]:
        st.warning("歌詞全文が同じ別曲が比較対象に含まれています。使用曲数は別々に数えています。")
    if not visible:
        st.info("表示する指摘はありません。除外設定や比較条件を確認できます。")
    else:
        displayed = visible[:50]
        st.caption(f"上位{len(displayed)}件を表示。回数は除外・表示件数によって変えません。入力回数は同じ表現・種類の出現数、異なる本文行数は同じ本文の行をまとめた数です。")
        st.dataframe([{"表現": item["surface"], "種類": item["category"], "行": item["line_number"],
                       "使用曲数": item["song_count"], "過去出現回数": item["past_count"],
                       "入力回数": item["input_count"], "異なる本文行数": item["unique_line_count"],
                       "除外理由": exclusion["reason"] if exclusion else ""} for item, exclusion in displayed],
                     hide_index=True, alt="重複と登録ルールの根拠を統合した指摘一覧")
        finding_id = st.selectbox("統合指摘の詳細", [item["id"] for item, _ in displayed],
                                  format_func=lambda value: next(f"{item['line_number']}行目：{item['surface']} / {item['category']}" for item, _ in displayed if item["id"] == value),
                                  key=f"{prefix}-detail")
        finding, exclusion = next((item, exclusion) for item, exclusion in displayed if item["id"] == finding_id)
        st.text(finding["line_text"])
        st.text(f"該当表記：{finding['surface']}")
        for rule_finding in finding["rules"]:
            rule = rule_finding["rule"]
            st.text(f"{rule_finding['match_kind']}：{rule_finding['occurrence']['surface']}")
            st.text(f"理由：{rule['reason']}")
            st.text(f"見直しの観点：{rule['review_hint']}")
            st.caption(f"ルール {rule['id']} / 版{rule['version']}")
        sources = {source["song_id"]: source for source in report["corpus"]}
        for match_id in finding["comparisons"]:
            match = report["comparisons"][match_id]
            st.text(f"比較の根拠：{match['expression']} / {match['mode']} / {match['status']}")
            for evidence in match["evidence"]:
                source = sources[evidence["song_id"]]
                occurrence = evidence["occurrence"]
                st.text(f"{source['title']} / 改訂{source['revision_number']} / {occurrence['line_number']}行目：{occurrence['line_text']}")
        if editable:
            scopes = ["run", "song", "global"] if report["song_id"] else ["run", "global"]
            with st.form(f"{prefix}-exclude-form-{finding_id}"):
                scope = st.selectbox("除外する範囲", scopes, format_func=lambda value: {"run": "今回の分析だけ", "song": "この曲で許容", "global": "全体で除外"}[value], key=f"{prefix}-scope")
                st.caption(f"同じ種類・表現の全出現に適用します。除外キー：{finding['expression_key']}。含まれる別の語句までは除外しません。")
                reason = st.text_input("除外する理由", key=f"{prefix}-reason")
                if st.form_submit_button("この表現を除外", key=f"{prefix}-exclude"):
                    try:
                        store.exclude(report["id"], finding_id, scope, reason)
                    except (ValueError, sqlite3.Error) as error:
                        st.error(str(error))
                    else:
                        st.rerun()
    if st.checkbox("保存した歌詞・分析条件を確認", key=f"{prefix}-snapshot"):
        st.text(report["input"])
        st.json({"settings": report["settings"], "versions": report["versions"], "rules": report["rules"],
                 "corpus": [{key: source[key] for key in ("song_id", "title", "revision_id", "revision_number", "content_hash")} for source in report["corpus"]]})
    st.download_button("この分析結果をJSONで保存", json.dumps(report, ensure_ascii=False, indent=2),
                       file_name=f"lyric-analysis-{report['id']}.json", mime="application/json", key=f"{prefix}-download")
    review_panel(store, report, prefix)


def review_panel(store, report, prefix):
    if not st.checkbox("上位指摘の有用性を評価", key=f"{prefix}-review"):
        return
    candidates = report["findings"][:10]
    if not candidates:
        st.info("評価する指摘がありません。")
        return
    saved = store.reviews(report["id"])
    labels = {None: "未評価", "useful": "有用", "intentional": "意図的な表現", "false_positive": "誤検出"}
    st.caption("保存時の上位最大10件を評価します。同じ表現の別の出現箇所も1件ずつ数えます。自作サンプルと実歌詞の評価は区別してください。")
    with st.form(f"{prefix}-reviews"):
        reviews = []
        for number, item in enumerate(candidates, start=1):
            previous = saved.get(item["id"], {})
            label = st.selectbox(f"{number}：{item['surface']} / {item['line_number']}行目", list(labels),
                                 index=list(labels).index(previous.get("label")), format_func=lambda value: labels[value],
                                 key=f"{prefix}-review-label-{item['id']}")
            note = st.text_input(f"{number}のメモ（任意）", value=previous.get("note", ""), key=f"{prefix}-review-note-{item['id']}")
            reviews.append({"finding_id": item["id"], "label": label, "note": note})
        if st.form_submit_button("評価を保存", key=f"{prefix}-save-reviews"):
            try:
                store.save_reviews(report["id"], reviews)
            except (ValueError, sqlite3.Error) as error:
                st.error(str(error))
            else:
                st.rerun()
    evaluated = [saved[item["id"]] for item in candidates if item["id"] in saved]
    useful = sum(item["label"] == "useful" for item in evaluated)
    st.caption(f"評価済み {len(evaluated)} / 対象 {len(candidates)}件。未評価は有用率の分母に含めません。")
    if evaluated:
        st.text(f"有用 {useful} / 意図的 {sum(item['label'] == 'intentional' for item in evaluated)} / 誤検出 {sum(item['label'] == 'false_positive' for item in evaluated)} / 有用率 {useful / len(evaluated):.0%}")
    st.download_button("評価記録をJSONで保存", json.dumps({"run_id": report["id"], "title": report["title"], "reviews": evaluated}, ensure_ascii=False, indent=2),
                       file_name=f"lyric-review-{report['id']}.json", mime="application/json", key=f"{prefix}-review-download")


def history_panel(store):
    if not st.checkbox("分析履歴を開く", key="show_history"):
        return
    runs = store.list_runs()
    if not runs:
        st.info("保存した総合分析はまだありません。")
        return
    by_id = {run["id"]: run for run in runs}
    run_id = st.selectbox("保存した分析", list(by_id), format_func=lambda value: f"{by_id[value]['title']} / {datetime.fromisoformat(by_id[value]['created_at']).astimezone(ZoneInfo('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M:%S')}", key="history_run")
    report = store.get(run_id)
    if report:
        report_panel(store, report, f"history-{run_id}")


def exclusion_panel(store):
    if not st.checkbox("除外設定を管理", key="manage_exclusions"):
        return
    rows = store.list_exclusions()
    active = {row["id"]: row for row in rows if row["enabled"]}
    st.caption(f"有効な除外設定：{len(active)}件。解除後は現在の結果と次回の分析に反映します。保存時点の履歴は変更しません。")
    if not active:
        return
    scopes = {"run": "今回の分析", "song": "この曲", "global": "全体"}
    song_titles = {song["id"]: song["title"] for song in store.library.list_songs()}
    selected = st.selectbox("解除する設定", list(active),
                            format_func=lambda value: f"{active[value]['expression_key']} / {scopes[active[value]['scope']]} {song_titles.get(active[value]['song_id'], '')} / {active[value]['reason']}", key="restore_exclusion")
    if st.button("選んだ除外を解除", key="restore"):
        try:
            store.restore(selected)
        except (ValueError, sqlite3.Error) as error:
            st.error(str(error))
        else:
            st.rerun()
