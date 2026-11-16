import hashlib
import json
import sqlite3

import streamlit as st

from lyric_analyzer import local_ai
from lyric_analyzer.analysis.parsing import parse_lines


LABELS = {"meaning": "意味を保つ", "scene": "具体的な情景", "viewpoint": "視点を変える"}


def local_ai_panel(store, report, prefix):
    if st.session_state.pop(f"{prefix}-ai-open-pending", False):
        st.session_state[f"{prefix}-ai-saved"] = True
    saved = report.get("ai_suggestions", [])
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
        st.caption("候補は手動で歌詞欄にコピーして編集・保存してください。歌詞は自動変更しません。")
        st.caption("意味や語調が変わる場合があります。原文と比較して、使う表現を選んでください。")
    if not st.checkbox("ローカルAIで言い換え候補を作る", key=f"{prefix}-ai-enabled"):
        return
    st.caption("PC内のOllama（127.0.0.1:11434）を使います。選択行・前後の本文各1行・任意のテーマを生成に使います。モデル一覧の確認では歌詞を送りません。")
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
    st.json({"endpoint": local_ai.ENDPOINT, "model": model, "system_prompt": local_ai.SYSTEM, "lyric_data": request})
    fingerprint = hashlib.sha256(json.dumps([model, request, local_ai.SYSTEM, local_ai.PROMPT_VERSION], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    confirmed = st.checkbox("この内容をPC内のOllamaに渡して生成する", key=f"{prefix}-ai-confirm-{fingerprint}")
    if len(saved) >= 20:
        st.info("この分析履歴の生成上限（20回）に達しています。")
    if (st.button("3方向の候補を生成して履歴に保存", key=f"{prefix}-ai-generate", disabled=not confirmed or len(saved) >= 20)
            and confirmed is True and len(saved) < 20):
        try:
            with st.spinner("ローカルAIで候補を生成しています…"):
                suggestion = local_ai.generate(model, request)
                store.save_suggestion(report["id"], suggestion)
        except (ValueError, OSError, sqlite3.Error) as error:
            st.error(f"候補を保存できませんでした：{error}。基本の分析結果はそのまま確認できます。")
        else:
            st.session_state[f"{prefix}-ai-open-pending"] = True
            st.rerun()
