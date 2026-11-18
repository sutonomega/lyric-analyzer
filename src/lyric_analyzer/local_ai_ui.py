import hashlib
import json
import sqlite3

import streamlit as st

from lyric_analyzer import local_ai, ollama_service
from lyric_analyzer.analysis.parsing import parse_lines
from lyric_analyzer.analysis.prosody import comparison
from lyric_analyzer.analysis.candidate_quality import CHECKS, inspect_candidates


LABELS = {"meaning": "意味を保つ", "scene": "具体的な情景", "viewpoint": "視点を変える"}


def local_ai_panel(store, report, prefix):
    saved = report.get("ai_suggestions", [])
    if st.session_state.pop(f"{prefix}-ai-open-pending", False):
        st.session_state[f"{prefix}-ai-saved"] = True
        if saved:
            st.session_state[f"{prefix}-ai-history"] = len(saved) - 1
    if saved and st.checkbox("保存したAI候補を確認", key=f"{prefix}-ai-saved"):
        index = st.selectbox("生成した候補の履歴", list(range(len(saved) - 1, -1, -1)),
                             format_func=lambda n: f"{n + 1}：{saved[n]['request']['line_number']}行目 / {saved[n]['model']}",
                             key=f"{prefix}-ai-history")
        item = saved[index]
        st.caption(f"Ollama / {item['model']} / {item['created_at']} / {item['prompt_version']}")
        st.text(f"原文：{item['result']['original']}")
        st.text(f"見直しの観点：{item['result']['reason']}")
        for candidate in item["result"]["candidates"]:
            st.text(f"{LABELS[candidate['direction']]}：{candidate['text']}")
        reading_panel(store, report, item, index, prefix)
        candidate_review_panel(store, report, item, index, prefix)
        st.caption("候補は手動で歌詞欄にコピーして編集・保存してください。歌詞は自動変更しません。")
        st.caption("意味や語調が変わる場合があります。原文と比較して、使う表現を選んでください。")
    if not st.checkbox("ローカルAIで言い換え候補を作る", key=f"{prefix}-ai-enabled"):
        return
    st.caption("PC内のOllama（127.0.0.1:11434）を使います。選択行・前後の本文各1行・任意のテーマを生成に使います。モデル一覧の確認では歌詞を送りません。")
    action = None
    if st.button("Ollamaを起動", key=f"{prefix}-ai-start"):
        action = "start"
    if st.button("Ollamaを停止", key=f"{prefix}-ai-stop"):
        action = "stop"
    if st.button("Ollamaを再起動", key=f"{prefix}-ai-restart"):
        action = "restart"
    if action:
        try:
            with st.spinner("Ollamaの状態を変更しています…"):
                if action == "stop":
                    status = ollama_service.stop()
                elif action == "restart":
                    status = ollama_service.restart(store.library.path.parent / "logs")
                else:
                    status = ollama_service.start(store.library.path.parent / "logs")
            if action == "stop":
                st.session_state.pop(f"{prefix}-ai-models", None)
                st.success("Ollamaは停止済みです。" if status == "not_running" else "Ollamaを停止しました。")
            elif status == "starting":
                st.session_state.pop(f"{prefix}-ai-models", None)
                st.info("Ollamaを起動中です。少し待って「Ollamaのモデルを確認」を押してください。")
            else:
                st.session_state[f"{prefix}-ai-models"] = local_ai.list_models()
                st.success("Ollamaは起動済みです。モデル一覧を更新しました。" if status == "already_running" else "Ollamaを起動・接続し、モデル一覧を更新しました。")
        except (local_ai.LocalAIError, OSError) as error:
            st.session_state.pop(f"{prefix}-ai-models", None)
            st.error(str(error))
    st.caption("これらの操作ではモデルの取得・歌詞の送信を行いません。停止・再起動は接続先のOllamaを使う他のアプリにも影響します。")
    st.caption("このアプリが起動・再起動したOllamaは、アプリ本体の終了時にも停止します。ブラウザーのタブを閉じただけではアプリ本体は終了しません。外部で起動済みの場合は、再起動ボタンでこのアプリの管理に切り替えられます。")
    if st.button("Ollamaのモデルを確認", key=f"{prefix}-ai-connect"):
        try:
            st.session_state[f"{prefix}-ai-models"] = local_ai.list_models()
        except local_ai.LocalAIError as error:
            st.session_state.pop(f"{prefix}-ai-models", None)
            st.error(str(error))
    models = st.session_state.get(f"{prefix}-ai-models", ())
    if not models:
        st.info("Ollamaを起動し、インストール済みのローカルモデルを確認してください。モデルは自動ダウンロードしません。")
        return
    model = st.selectbox("使用するローカルモデル", models, key=f"{prefix}-ai-model")
    method_key = f"{prefix}-ai-method"
    if st.session_state.get(method_key) == local_ai.LEGACY_DIRECT_VERSION:
        st.session_state[method_key] = local_ai.DIRECT_VERSION
    prompt_version = st.selectbox("候補の作り方", list(local_ai.GENERATION_METHODS),
                                  format_func=lambda value: local_ai.PROMPT_LABELS[value],
                                  key=method_key)
    st.caption("試用の作り方も、意味を保てるとは限りません。モデルや原文に応じて比較してください。")
    if prompt_version == local_ai.DIRECT_VERSION:
        st.caption("対応モデルに思考モードをオフにするよう要求して、候補を生成します。モデルによって動作は異なります。意味や自然さが改善する保証ではありません。")
        st.caption("返答の対象行IDと原文を入力値に限定します。これは引用元の取り違えを抑えるための制約で、候補の意味の一致を保証するものではありません。")
    lines = {line.line_number: line.text for line in parse_lines(report["input"]) if line.text.strip() and not line.metadata}
    if not lines:
        st.info("候補を作る本文がありません。")
        return
    number = st.selectbox("候補を作る行", list(lines), format_func=lambda n: f"{n}行目：{lines[n]}", key=f"{prefix}-ai-line")
    theme = st.text_input("候補に反映するテーマ（任意）", max_chars=500, key=f"{prefix}-ai-theme")
    try:
        request = local_ai.prepare_request(report["input"], number, theme)
    except local_ai.LocalAIError as error:
        st.error(str(error))
        return
    st.caption("生成に使う内容を確認してください。過去曲の本文・曲名・Stylesは含めません。")
    controls = local_ai.generation_controls(prompt_version)
    schema = local_ai.response_schema(request, prompt_version)
    st.json({"endpoint": local_ai.ENDPOINT, "model": model, "system_prompt": local_ai.PROMPTS[prompt_version], "generation_controls": controls, "lyric_data": request})
    fingerprint = hashlib.sha256(json.dumps([model, request, local_ai.PROMPTS[prompt_version], prompt_version, controls, schema], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    confirmed = st.checkbox("この内容をPC内のOllamaに渡して生成する", key=f"{prefix}-ai-confirm-{fingerprint}")
    if len(saved) >= 20:
        st.info("この分析履歴の生成上限（20回）に達しています。")
    if (st.button("3方向の候補を生成して履歴に保存", key=f"{prefix}-ai-generate", disabled=not confirmed or len(saved) >= 20)
            and confirmed is True and len(saved) < 20):
        try:
            with st.spinner("ローカルAIで候補を生成しています…"):
                suggestion = local_ai.generate(model, request, prompt_version=prompt_version)
                store.save_suggestion(report["id"], suggestion)
        except (ValueError, OSError, sqlite3.Error) as error:
            st.error(f"候補を保存できませんでした：{str(error).rstrip('。.')}。基本の分析結果はそのまま確認できます。")
        else:
            st.session_state[f"{prefix}-ai-open-pending"] = True
            st.rerun()


def reading_panel(store, report, item, index, prefix):
    saved = item.get("prosody")
    data = saved if saved is not None else comparison(item["result"])
    labels = {"original": "原文", **LABELS}
    original_count = data["rows"][0]["mora"]
    st.dataframe([{"方向": labels[row["id"]], "本文": row["text"], "読み": row["reading"],
                   "概算モーラ数": row["mora"],
                   "原文との差": row["mora"] - original_count if row["mora"] is not None and original_count is not None else None,
                   "読みの扱い": row["status"], "確認する語": " / ".join(row["notes"])} for row in data["rows"]],
                 hide_index=True, alt="原文と3方向の候補の読みと概算モーラ数の比較")
    st.caption("拗音・小さい母音は前のかなと合わせ、促音・撥音・長音は各1モーラとして概算します。メロディーの拍数や歌唱時の長さとは異なります。英数字などの音数が不明な場合は空欄です。")
    st.caption(f"{'保存時' if saved is not None else '今回の表示時'}の読み：辞書 {data['dictionary_version']} / {data['algorithm_version']}。固有名詞・未知語は推定を含みます。")
    if st.checkbox("読みを指定して音数を比較", key=f"{prefix}-reading-edit-{index}"):
        with st.form(f"{prefix}-readings-{index}"):
            overrides = {row["id"]: st.text_input(f"{labels[row['id']]}の読み（空欄は自動）",
                                                   value=data["overrides"].get(row["id"], ""), max_chars=2000,
                                                   placeholder=row["reading"] or "かなで入力", key=f"{prefix}-reading-{index}-{row['id']}")
                         for row in data["rows"]}
            if st.form_submit_button("読みを保存して再計算", key=f"{prefix}-reading-save-{index}"):
                try:
                    store.save_readings(report["id"], index, overrides)
                except (ValueError, sqlite3.Error) as error:
                    st.error(str(error))
                else:
                    st.rerun()


def candidate_review_panel(store, report, item, index, prefix):
    if not st.checkbox("候補の品質を確認・評価", key=f"{prefix}-quality-open-{index}"):
        return
    rows = inspect_candidates(item["result"], item["request"])
    st.dataframe([{"方向": LABELS[row["direction"]], "補助確認": " / ".join(row["checks"]) or "自動確認で該当なし"} for row in rows],
                 hide_index=True, alt="原文や別候補との一致と疑問符・否定語の違いの補助確認")
    st.caption("補助確認は表記・疑問符・否定語と、一部の人物語・時間語の差を見ます。該当なしでも主語・時制・意味の保持を示しません。意味を保つ案は次の4項目を原文と比較してください。")
    previous = item.get("candidate_review", {})
    verdicts = {"unreviewed": "未評価", "usable": "使えそう", "edit": "修正したい", "reject": "使わない"}
    statuses = {"unchecked": "未確認", "kept": "保たれている", "changed": "変わっている"}
    labels = {"subject": "誰が・誰に・何をするか", "tense": "時制", "polarity": "肯定・否定", "tone": "語調（疑問・命令・丁寧さ）"}
    with st.form(f"{prefix}-quality-form-{index}"):
        meaning_checks = {key: st.selectbox(f"意味を保つ案：{labels[key]}", list(statuses),
                                           index=list(statuses).index(previous.get("meaning_checks", {}).get(key, "unchecked")),
                                           format_func=lambda value: statuses[value], key=f"{prefix}-quality-check-{index}-{key}") for key in CHECKS}
        decisions = {}
        for direction in LABELS:
            old = previous.get("decisions", {}).get(direction, {})
            verdict = st.selectbox(f"{LABELS[direction]}案の評価", list(verdicts), index=list(verdicts).index(old.get("verdict", "unreviewed")),
                                   format_func=lambda value: verdicts[value], key=f"{prefix}-quality-verdict-{index}-{direction}")
            note = st.text_input(f"{LABELS[direction]}案のメモ（任意）", value=old.get("note", ""), max_chars=1000,
                                 key=f"{prefix}-quality-note-{index}-{direction}")
            decisions[direction] = {"verdict": verdict, "note": note}
        if st.form_submit_button("候補の評価を保存", key=f"{prefix}-quality-save-{index}"):
            try:
                store.save_candidate_review(report["id"], index, decisions, meaning_checks)
            except (ValueError, sqlite3.Error) as error:
                st.error(str(error))
            else:
                st.rerun()
    st.caption("評価を保存しても、歌詞や候補は変更しません。これはAI候補の評価で、重複・定型表現の指摘の有用率とは別の記録です。")
