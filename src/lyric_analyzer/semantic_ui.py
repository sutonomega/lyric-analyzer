import hashlib
import json

import streamlit as st

from lyric_analyzer import local_ai, local_embeddings
from lyric_analyzer.analysis.parsing import parse_lines
from lyric_analyzer.analysis.semantic import VERSION, prepare, search
from lyric_analyzer.analysis.candidate_quality import meaning_differences


@st.cache_data(max_entries=800, show_spinner=False)
def cached_embeddings(model, texts, algorithm_version):
    return local_embeddings.embed_batch(model, texts)


def semantic_panel(store, report, prefix):
    if st.checkbox("意味が近い過去曲の行を探す（試用）", key=f"{prefix}-semantic-enabled"):
        semantic_controls(store, report, prefix)
    result = st.session_state.get(f"{prefix}-semantic-result")
    if not result:
        return
    st.subheader("意味の近さによる検索結果（試用）")
    st.text(f"検索した行：{result['query']['text']}")
    st.caption(f"モデル {result['model']['name']} / 比較 {len(result['sources'])}曲・{result['unique_reference_lines']}種類の本文行 / 表記を統一して同じ行は{result['exact_excluded']}箇所除外。")
    st.caption("近さは数値表現のコサイン類似度で、確率ではありません。否定・人物・時制が違っても高くなる場合があります。原文を比較して判断してください。比較元は総合分析保存時の歌詞です。編集後の内容を使う場合は総合分析からやり直してください。")
    rows = result["results"]
    if not rows:
        st.info("指定した近さの下限に合う行はありません。条件やモデルの特性を確認できます。")
    else:
        st.dataframe([{"過去曲の行": row["text"], "近さ（参考）": row["score"],
                       "語の差（補助）": " / ".join(meaning_differences(result["query"]["text"], row["text"])) or "該当なし（意味一致の判定ではありません）",
                       "使用曲数": len({item['song_id'] for item in row['evidence']})} for row in rows],
                     column_config={"近さ（参考）": st.column_config.NumberColumn(format="%.3f")},
                     hide_index=True, alt="意味の近さで並べた過去曲の本文行")
        index = st.selectbox("近似行の根拠を確認", list(range(len(rows))), format_func=lambda i: rows[i]["text"], key=f"{prefix}-semantic-detail")
        sources = {source["song_id"]: source for source in result["sources"]}
        evidence = rows[index]["evidence"]
        st.caption(f"同じ本文行の根拠{len(evidence)}箇所のうち最大200箇所を表示。")
        st.dataframe([{"曲名": sources[item["song_id"]]["title"], "改訂": sources[item["song_id"]]["revision_number"],
                       "行": item["occurrence"]["line_number"], "本文": item["occurrence"]["text"]}
                      for item in evidence[:200]], hide_index=True, alt="検索した過去曲の行の曲名と改訂と引用行")
    st.download_button("意味検索の結果をJSONで保存", json.dumps(result, ensure_ascii=False, indent=2),
                       file_name="semantic-lines.json", mime="application/json", key=f"{prefix}-semantic-download")
    st.caption("意味検索の結果はアプリ実行中だけ保持します。このJSONは引用行を含む確認・保管用で、バックアップの取り込み対象ではありません。")


def semantic_controls(store, report, prefix):
    st.caption("PC内のOllamaで、選んだ行と比較対象曲の本文行を数値化して比べます。比較対象に選んだ曲の本文をOllamaに渡すため、実行前に一覧を確認してください。モデルは自動ダウンロードしません。")
    if st.button("検索モデルを確認", key=f"{prefix}-semantic-connect"):
        try:
            models = local_embeddings.list_models()
            for model in models:
                local_embeddings.check_model(model)
            st.session_state[f"{prefix}-semantic-models"] = models
        except ValueError as error:
            st.session_state.pop(f"{prefix}-semantic-models", None)
            st.error(str(error))
    models = st.session_state.get(f"{prefix}-semantic-models", ())
    if not models:
        st.info("Ollamaを起動し、embeddinggemma:300mを用意して「検索モデルを確認」を押してください。")
        return
    selected_model = st.selectbox("意味検索に使うモデル", list(range(len(models))), format_func=lambda i: models[i]["name"], key=f"{prefix}-semantic-model")
    model = models[selected_model]
    lines = {line.line_number: line.text for line in parse_lines(report["input"]) if line.text.strip() and not line.metadata}
    sources = {source["song_id"]: source for source in report["corpus"] if source["song_id"] != report["song_id"]}
    if not lines or not sources:
        st.info("本文行と、自曲以外の比較対象がある総合分析を保存してください。")
        return
    number = st.selectbox("意味を比較する行", list(lines), format_func=lambda n: f"{n}行目：{lines[n]}", key=f"{prefix}-semantic-line")
    selected = st.multiselect("意味検索の比較対象曲", list(sources), default=[],
                              format_func=lambda key: f"{sources[key]['title']}（{key[:8]}）", key=f"{prefix}-semantic-songs")
    exclude = st.checkbox("表記を統一すると同じ行は除く", value=True, key=f"{prefix}-semantic-exact")
    minimum = st.number_input("近さの下限（試用）", min_value=0.0, max_value=1.0, value=0.75, step=0.05, key=f"{prefix}-semantic-minimum")
    limit = st.number_input("近似行の表示件数", min_value=1, max_value=20, value=10, step=1, key=f"{prefix}-semantic-limit")
    if not selected:
        st.info("比較対象曲を選ぶと、渡す本文行のプレビューを表示します。")
        return
    try:
        prepared = prepare(report, number, selected, exclude)
    except ValueError as error:
        st.error(str(error))
        return
    preview = {"model": model, "truncate": False, "query_input": local_embeddings.PREFIX + prepared.query.text.strip(),
               "reference_inputs": [local_embeddings.PREFIX + text for text in prepared.texts]}
    st.text(f"対象行：{prepared.query.text}")
    st.caption(f"Ollamaへ渡す比較本文：{len(prepared.corpus)}曲・{len(prepared.texts)}種類（反復をまとめた数）。画面では先頭200種類を表示し、JSONで全種類を確認できます。")
    st.dataframe([{"比較する本文行": text} for text in prepared.texts[:200]], hide_index=True, alt="Ollamaに渡す比較本文行のプレビュー")
    st.download_button("意味検索の入力を全件確認（JSON）", json.dumps(preview, ensure_ascii=False, indent=2),
                       file_name="semantic-input-preview.json", mime="application/json", key=f"{prefix}-semantic-preview")
    fingerprint = hashlib.sha256(json.dumps([prepared.fingerprint, model, VERSION, local_embeddings.PREFIX, minimum, limit], sort_keys=True).encode()).hexdigest()[:16]
    confirmed = st.checkbox("対象行と選んだ曲の比較本文をPC内のOllamaに渡して検索する", key=f"{prefix}-semantic-confirm-{fingerprint}")
    if st.button("意味の近い行を検索", disabled=not confirmed, key=f"{prefix}-semantic-run") and confirmed is True:
        bar = st.progress(0.0, text="意味の近さを調べています…")
        try:
            local_embeddings.check_model(model)
            result = search(prepared, model, lambda descriptor, texts: cached_embeddings(descriptor, texts, VERSION), minimum, limit,
                            progress=lambda value: bar.progress(value, text=f"比較本文の処理 {value:.0%}"))
            local_embeddings.check_model(model)
            st.session_state[f"{prefix}-semantic-result"] = {**result, "created_at": store.library.now()}
        except (ValueError, OSError) as error:
            st.error(f"意味検索を完了できませんでした：{error}。基本分析と前回の検索結果は保持します。")
        finally:
            bar.empty()
