from collections import Counter
import csv
import io
import json
import re

import streamlit as st

from lyric_analyzer.analysis.comparison import select_corpus
from lyric_analyzer.analysis.library_vocabulary import MODES, build_index, filter_rows, summaries, versions


@st.cache_data(max_entries=3, show_spinner="ライブラリ全体の語句を集計しています…")
def cached_index(corpus, mode, grammar, unknown, version_key):
    # Explicit version metadata is part of the cache key.
    return build_index(corpus, mode, grammar, unknown)


def vocabulary_panel(library, corpus):
    if not st.checkbox("ライブラリ全体の語句を集計", key="show_library_vocabulary"):
        return
    st.subheader("曲をまたいだ単語・フレーズの使用傾向")
    st.caption("保存済みの各曲の最新版を対象にします。編集中で未保存の歌詞は含みません。期間は日本時間のライブラリ登録日で、作曲日ではありません。")
    if not corpus:
        st.info("歌詞を保存または一括取り込みすると、全体の語句を集計できます。")
        return
    with st.form("library_vocabulary_options"):
        mode = st.selectbox("全体集計の種類", list(MODES), format_func=lambda value: MODES[value], key="vocabulary_mode")
        minimum = st.number_input("使用曲数の下限", min_value=1, max_value=len(corpus), value=min(2, len(corpus)), step=1, key="vocabulary_minimum")
        characters = st.number_input("フレーズの文字数の下限", min_value=1, max_value=100, value=4, step=1, key="vocabulary_characters")
        start = st.date_input("全体集計の登録日・開始（任意、日本時間）", value=None, key="vocabulary_start")
        end = st.date_input("全体集計の登録日・終了（任意、日本時間）", value=None, key="vocabulary_end")
        grammar = st.checkbox("文法的な表現の参考情報も集計", key="vocabulary_grammar")
        unknown = st.checkbox("未知のセクションタグも集計", key="vocabulary_unknown")
        submitted = st.form_submit_button("全体を集計", key="vocabulary_run")
    if submitted:
        try:
            if type(minimum) is not int or not 1 <= minimum <= len(corpus) or type(characters) is not int or not 1 <= characters <= 100:
                raise ValueError("使用曲数と文字数の範囲を確認してください。")
            selected = select_corpus(corpus, start_date=start, end_date=end)
            index = cached_index(selected, mode, grammar, unknown, versions())
            st.session_state["library_vocabulary_result"] = {
                "index": index, "library_snapshot": corpus, "created_at": library.now(),
                "filters": {"minimum_songs": minimum, "minimum_characters": characters,
                            "start_date": start.isoformat() if start else None, "end_date": end.isoformat() if end else None,
                            "include_grammar": grammar, "include_unknown_tags": unknown}}
        except (ValueError, RuntimeError, OSError) as error:
            st.error(f"全体集計を実行できませんでした：{error}")
    result = st.session_state.get("library_vocabulary_result")
    if result is None:
        return
    index, filters = result["index"], result["filters"]
    if corpus != result["library_snapshot"]:
        st.info("保存データが変わっています。現在の集計は前回実行時のものです。変更を反映するには再集計してください。")
    st.caption(f"最後の集計：{MODES[index.mode]} / 対象{len(index.corpus)}曲 / 使用曲数{filters['minimum_songs']}以上。条件変更後は「全体を集計」で反映します。")
    st.caption("出現回数は同じサビの反復も含みます。異なる本文行数は曲ごとに同じ本文行をまとめた数です。総合分析の除外設定で回数は減らしません。使用頻度は歌詞の良し悪しやAI生成の判定ではありません。")
    duplicates = Counter(song.content_hash for song in index.corpus)
    if any(count > 1 for count in duplicates.values()):
        st.caption("歌詞全文が同じ別曲を含みます。曲IDが異なれば別の曲として数えます。")
    query = st.text_input("集計した表現を絞り込む", key="vocabulary_query")
    rows = filter_rows(index, filters["minimum_songs"], filters["minimum_characters"], query)
    st.caption(f"条件に合う{len(rows)}種類のうち上位{min(len(rows), 50)}種類を表示。使用曲数、出現回数、文字数の順です。フレーズは重なり合う2〜5形態素の候補です。")
    if not rows:
        st.info("条件に合う語句がありません。使用曲数・期間・絞り込み条件を確認してください。")
        return
    st.dataframe(summaries(index, rows[:50]), hide_index=True,
                 column_config={"使用曲割合": st.column_config.NumberColumn(format="percent")},
                 alt="複数曲で使われている単語とフレーズの使用曲数と出現回数")
    displayed = {row.id: row for row in rows[:50]}
    selected_id = st.selectbox("使われている曲と行を確認", list(displayed),
                               format_func=lambda value: f"{displayed[value].expression} / {displayed[value].pos or MODES[index.mode]}", key="vocabulary_detail")
    row = displayed[selected_id]
    sources = {song.song_id: song for song in index.corpus}
    by_song = {}
    for evidence in row.evidence:
        by_song.setdefault(evidence.song_id, []).append(evidence)
    song_id = st.selectbox("この表現を使っている曲", sorted(by_song, key=lambda value: (sources[value].title, value)),
                           format_func=lambda value: f"{sources[value].title}（{value[:8]}） / {len(by_song[value])}回", key="vocabulary_song")
    evidence = by_song[song_id]
    st.caption(f"{sources[song_id].title} / 改訂{sources[song_id].revision_number} / 該当{len(evidence)}箇所のうち最大200箇所を表示。")
    st.dataframe([{"行": item.occurrence.line_number, "セクション": item.occurrence.section or "", "原文の表記": item.occurrence.surface,
                   "本文": item.occurrence.line_text, "開始位置": item.occurrence.start, "終了位置": item.occurrence.end}
                  for item in evidence[:200]], hide_index=True, alt="選択した曲での語句の出現行と原文の表記")
    table = summaries(index, rows)
    content = io.StringIO(newline="")
    writer = csv.DictWriter(content, fieldnames=list(table[0]))
    writer.writeheader()
    writer.writerows({key: "'" + value if isinstance(value, str) and re.match(r"^\s*[=+@-]", value) else value for key, value in item.items()} for item in table)
    st.download_button("全体集計をCSVで保存", content.getvalue().encode("utf-8-sig"), file_name="library-vocabulary.csv", mime="text/csv", key="vocabulary_csv")
    st.download_button("全体集計をJSONで保存", json.dumps({"format": "library-vocabulary-1", "created_at": result["created_at"],
                       "mode": index.mode, "versions": index.versions, "filters": {**filters, "query": query},
                       "song_count": len(index.corpus), "rows": table}, ensure_ascii=False, indent=2),
                       file_name="library-vocabulary.json", mime="application/json", key="vocabulary_json")
    st.caption("保存する集計には、現在の条件に合う全種類の語句と回数を含めます。曲名・歌詞本文・引用行は含めません。")
