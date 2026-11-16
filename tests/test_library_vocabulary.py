from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from lyric_analyzer.analysis.comparison import select_corpus
from lyric_analyzer.analysis.library_vocabulary import build_index, filter_rows, summaries
from lyric_analyzer.storage import Library


def prepare(tmp_path):
    library = Library(tmp_path / "library.db")
    a = library.save("A", "消える記憶")
    old = library.comparison_corpus()[0]
    library.save("A", "[Verse]\n夜の駅で待つ\n[Chorus]\n夜の駅で待つ\nして", song_id=a)
    library.save("B", "夜の港へ向かう")
    return library, old


def test_latest_revisions_song_counts_repetition_and_original_offsets(tmp_path):
    library, old = prepare(tmp_path)
    corpus = library.comparison_corpus()
    before = library.export_json()
    index = build_index((*corpus, old))
    row = next(row for row in index.rows if row.expression == "夜")
    assert len(index.corpus) == 2
    assert row.song_count == 2 and row.count == 3 and row.unique_line_count == 2
    assert not any(row.expression == "記憶" for row in index.rows)
    assert not any(row.expression == "する" for row in index.rows)
    sources = {song.song_id: song for song in corpus}
    for row in index.rows:
        for evidence in row.evidence:
            occurrence = evidence.occurrence
            assert sources[evidence.song_id].lyrics[occurrence.start:occurrence.end] == occurrence.surface
    count = summaries(index, [next(row for row in index.rows if row.expression == "夜")])[0]
    assert count["使用曲割合"] == 1.0
    assert library.export_json() == before


def test_grammar_reference_unknown_tags_and_duplicate_lyrics(tmp_path):
    library, _ = prepare(tmp_path)
    corpus = library.comparison_corpus()
    assert any(row.expression == "する" for row in build_index(corpus, include_grammar=True).rows)
    assert not any(row.expression == "して" for row in build_index(corpus, "phrases_surface").rows)
    assert any(row.expression == "して" for row in build_index(corpus, "phrases_surface", True).rows)
    library.save("タグ", "[夜空]\n港の灯")
    assert not any(row.expression == "夜空" for row in build_index(library.comparison_corpus()).rows)
    assert any(row.expression == "夜空" for row in build_index(library.comparison_corpus(), include_unknown_tags=True).rows)
    library.save("Bの別曲", next(song.lyrics for song in corpus if song.title == "B"))
    row = next(row for row in build_index(library.comparison_corpus()).rows if row.expression == "港")
    assert row.song_count == 3


def test_nfkc_phrase_mode_merges_spellings_but_keeps_raw_evidence(tmp_path):
    library = Library(tmp_path / "library.db")
    library.save("全角", "ＡＢＣの駅")
    library.save("半角", "ABCの駅")
    corpus = library.comparison_corpus()
    raw = build_index(corpus, "phrases_surface")
    assert not any(row.expression == "ABCの駅" for row in filter_rows(raw))
    normalized = build_index(corpus, "phrases_normalized")
    row = next(row for row in filter_rows(normalized) if row.expression == "ABCの駅")
    assert row.song_count == 2
    assert {e.occurrence.surface for e in row.evidence} == {"ＡＢＣの駅", "ABCの駅"}
    assert filter_rows(normalized, query="ＡＢＣ")
    assert filter_rows(raw, minimum_songs=1, query="ＡＢＣ")


def test_registration_dates_use_japan_time_and_empty_corpus_is_valid(tmp_path):
    library, _ = prepare(tmp_path)
    corpus = library.comparison_corpus()
    a = replace(corpus[0], created_at="2026-11-15T15:00:00+00:00")
    b = replace(corpus[1], created_at="2026-11-15T14:59:59+00:00")
    selected = select_corpus((a, b), start_date=date(2026, 11, 16), end_date=date(2026, 11, 16))
    assert [song.song_id for song in selected] == [a.song_id]
    assert build_index(()).rows == ()
    assert summaries(build_index(()), []) == []
    assert filter_rows(build_index(corpus), minimum_songs=3) == []


@pytest.mark.parametrize("mode,grammar,unknown", [("bad", False, False), ("words", 1, False), ("words", False, "true")])
def test_invalid_modes_and_flags_are_rejected(mode, grammar, unknown):
    with pytest.raises(ValueError):
        build_index((), mode, grammar, unknown)


@pytest.mark.parametrize("minimum,characters", [(0, 4), (True, 4), (1, 0), (1, 2.5)])
def test_invalid_thresholds_are_rejected(minimum, characters):
    with pytest.raises(ValueError):
        filter_rows(build_index(()), minimum, characters)


def test_overview_ui_empty_snapshot_filters_and_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("LYRIC_ANALYZER_DATA_DIR", str(tmp_path))
    path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(path).run()
    assert not app.checkbox(key="show_library_vocabulary").value
    app.checkbox(key="show_library_vocabulary").check().run()
    assert not app.exception
    assert any("一括取り込み" in item.value for item in app.info)
    library, _ = prepare(tmp_path)
    app.run()
    app.button(key="vocabulary_run").click().run()
    assert not app.exception
    frame = app.dataframe[0].value
    assert frame[frame["表現"] == "夜"].iloc[0]["使用曲数"] == 2
    app.text_input(key="vocabulary_query").set_value("夜").run()
    assert len(app.dataframe[0].value) == 1
    target = next(song for song in library.list_songs() if song["title"] == "A")
    library.save("Aを編集", "朝の駅で待つ", song_id=target["id"])
    app.run()
    assert any("保存データが変わっています" in item.value for item in app.info)
    assert app.dataframe[0].value.iloc[0]["使用曲数"] == 2
    app.button(key="vocabulary_run").click().run()
    assert not app.exception
    assert any("条件に合う語句がありません" in item.value for item in app.info)
