import json
import sqlite3

import streamlit as st


def detection_panel(result):
    st.subheader("定型表現・文型の確認")
    st.caption("登録ルールへの一致と文型候補です。AI生成の判定や、表現の良し悪しの採点ではありません。意図的に使う表現はそのまま残せます。")
    st.caption("最後に確認した歌詞・辞書の結果です。歌詞や辞書の編集後は再確認してください。")
    st.caption(f"有効ルール {sum(rule.enabled for rule in result.rules)}件 / 指摘 {len(result.findings)}件 / {result.analyzer_version} / 解析器 {result.tokenizer_version} / 辞書 {result.dictionary_version}")
    if not result.findings:
        st.info("有効なルールに一致する表現はありませんでした。")
        return
    st.dataframe([{"表現": item.occurrence.surface, "種類": item.match_kind, "行": item.occurrence.line_number,
                   "理由": item.rule.reason, "見直しの観点": item.rule.review_hint, "ルール版": item.rule.version}
                  for item in result.findings], hide_index=True, alt="定型表現と文型候補の根拠・見直しの観点")
    index = st.selectbox("指摘の詳細", range(len(result.findings)),
                         format_func=lambda number: f"{number + 1}：{result.findings[number].occurrence.surface}（{result.findings[number].match_kind}）", key="expression_detail")
    finding = result.findings[index]
    st.text(f"{finding.occurrence.line_number}行目 / {finding.occurrence.section or 'セクションなし'}")
    st.text(finding.occurrence.line_text)
    st.text(f"該当表記：{finding.occurrence.surface}")
    st.text(f"理由：{finding.rule.reason}")
    st.text(f"見直しの観点：{finding.rule.review_hint}")
    st.caption(f"根拠ルール：{finding.rule.id} / 版 {finding.rule.version}")


def rule_editor(library):
    if not st.checkbox("定型表現辞書を編集", key="show_rule_editor"):
        return
    st.subheader("定型表現辞書")
    st.caption("初期ルールは手作業の例です。一般的なAI歌詞全体の頻度を示す辞書ではありません。無効化したルールは次回の確認から除外します。")
    rules = {rule.id: rule for rule in library.list_rules()}
    if "pending_rule" in st.session_state:
        st.session_state["selected_rule"] = st.session_state.pop("pending_rule")

    def label(rule_id):
        if rule_id is None:
            return "新しいルール"
        rule = rules[rule_id]
        pattern = json.loads(rule.pattern_json)
        title = pattern.get("text", "抽象名詞＋が／は＋消失動詞")
        return f"{title} / {'有効' if rule.enabled else '無効'} / 版{rule.version}（{rule_id[:8]}）"

    selected = st.selectbox("編集するルール", [None, *rules], format_func=label, key="selected_rule")
    rule = rules.get(selected)
    editor_key = f"{selected}-{rule.version if rule else 0}"
    kind = st.selectbox("ルールの種類", ["literal", "abstract_loss"],
                        index=0 if rule is None or rule.kind == "literal" else 1,
                        format_func=lambda value: {"literal": "登録表現の一致", "abstract_loss": "抽象名詞＋が／は＋消失動詞の文型"}[value],
                        key=f"rule-kind-{editor_key}")
    pattern = json.loads(rule.pattern_json) if rule and rule.kind == kind else {}
    with st.form(f"rule-form-{editor_key}-{kind}"):
        if kind == "literal":
            expression = st.text_input("登録する表現", value=pattern.get("text", ""), key=f"rule-text-{editor_key}")
            pattern = {"text": expression}
            st.caption("全角・半角を揃えた表記で、単語の境界に一致する箇所を探します。改行や句点等をまたぎません。")
        else:
            nouns = st.text_input("抽象名詞の原形（カンマ区切り）", value=", ".join(pattern.get("nouns", [])), key=f"rule-nouns-{editor_key}")
            verbs = st.text_input("消失動詞の原形（カンマ区切り）", value=", ".join(pattern.get("verbs", [])), key=f"rule-verbs-{editor_key}")
            pattern = {"nouns": [value.strip() for value in nouns.split(",") if value.strip()],
                       "verbs": [value.strip() for value in verbs.split(",") if value.strip()]}
            st.caption("登録名詞→が／は→登録動詞の3語の連続を確認します。活用形は原形で照合し、直後の「ない・ぬ・ず」は除外します。意味全体を解釈するものではありません。")
        reason = st.text_area("指摘する理由", value=rule.reason if rule else "", key=f"rule-reason-{editor_key}")
        hint = st.text_area("見直しの観点", value=rule.review_hint if rule else "", key=f"rule-hint-{editor_key}")
        enabled = st.checkbox("このルールを有効にする", value=rule.enabled if rule else True, key=f"rule-enabled-{editor_key}")
        if st.form_submit_button("ルールを保存", key="save_rule"):
            try:
                saved_id = library.save_rule(kind, pattern, reason, hint, enabled, selected, rule.version if rule else None)
            except (ValueError, sqlite3.Error) as error:
                st.error(f"辞書を保存できませんでした：{error}")
            else:
                st.session_state["pending_rule"] = saved_id
                st.session_state["notice"] = "ルールを保存しました。次回の確認から反映されます。"
                st.rerun()
