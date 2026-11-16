from datetime import date
import hashlib

import pytest

from lyric_analyzer.analysis.comparison import SongSnapshot, collapse_contained, compare, match_status, select_corpus
from lyric_analyzer.storage import Library


def snapshot(song_id, lyrics, revision=1, created_at="2026-11-01T00:00:00+00:00"):
    return SongSnapshot(song_id, f"曲{song_id}", f"{song_id}-r{revision}", revision, lyrics,
                        hashlib.sha256(lyrics.encode("utf-8")).hexdigest(), created_at)


def find(rows, expression):
    return next(row for row in rows if row.expression == expression)


def test_counts_songs_separately_from_occurrences_and_excludes_self():
    raw = "記憶がほどけていく"
    corpus = (snapshot("self", raw), snapshot("one", raw + "\n" + raw), snapshot("two", raw))
    result = compare(raw, corpus, target_song_id="self")
    row = find(result.phrases_surface, "ほどけていく")
    assert len(result.corpus) == 2
    assert row.song_count == 2
    assert row.count == 3
    assert len(row.target_occurrences) == 1
    assert row.status == "参考（母数少）"
    assert {item.song_id for item in row.evidence} == {"one", "two"}
    assert result.duplicate_song_ids == ("two",)


def test_uses_latest_revision_even_when_old_revisions_are_supplied():
    result = compare("記憶がほどけていく", (snapshot("one", "記憶がほどけていく"), snapshot("one", "朝の電車", 2)))
    assert len(result.corpus) == 1
    assert result.corpus[0].revision_number == 2
    assert not any(row.expression == "ほどけていく" for row in result.phrases_surface)


def test_storage_returns_latest_snapshots_and_keeps_result_stable(tmp_path):
    library = Library(tmp_path / "library.db")
    song_id = library.save("before", "記憶がほどけていく")
    library.save("before", "朝の電車", song_id=song_id)
    corpus = library.comparison_corpus()
    assert len(corpus) == 1
    assert corpus[0].revision_number == 2
    assert corpus[0].revision_id == library.revisions(song_id)[0]["id"]
    result = compare("朝の電車", corpus)
    library.save("after", "夕焼けの海", song_id=song_id)
    assert result.corpus[0].title == "before"
    assert result.corpus[0].lyrics == "朝の電車"
    fresh = compare("朝の電車", library.comparison_corpus())
    assert fresh.corpus[0].revision_number == 3
    assert not fresh.duplicate_song_ids
    library.delete(song_id)
    assert library.comparison_corpus() == ()


def test_selection_and_inclusive_japan_registration_dates():
    corpus = (snapshot("one", "歌詞", created_at="2026-10-31T15:00:00+00:00"),
              snapshot("two", "歌詞", created_at="2026-10-31T14:59:59+00:00"))
    assert [song.song_id for song in select_corpus(corpus, start_date=date(2026, 11, 1), end_date=date(2026, 11, 1))] == ["one"]
    assert [song.song_id for song in select_corpus(corpus, selected_ids=("two",))] == ["two"]
    assert select_corpus(corpus, selected_ids=()) == ()
    assert select_corpus(corpus, selected_ids=("deleted",)) == ()
    with pytest.raises(ValueError):
        select_corpus(corpus, start_date=date(2026, 11, 2), end_date=date(2026, 11, 1))


@pytest.mark.parametrize("df,total,expected", [(1, 1, "一致"), (2, 9, "参考（母数少）"), (2, 10, "一致"),
                                             (3, 10, "頻出"), (3, 15, "頻出"), (3, 16, "一致")])
def test_frequency_thresholds(df, total, expected):
    assert match_status(df, total) == expected
    assert match_status(df, total, word=True) == "単語参考"


def test_three_of_ten_is_frequent_but_chorus_alone_is_not():
    raw = "記憶がほどけていく"
    corpus = tuple(snapshot(str(index), raw if index < 3 else "朝の電車") for index in range(10))
    result = compare(raw, corpus)
    assert find(result.phrases_surface, "ほどけていく").status == "頻出"
    solo = compare(raw + "\n" + raw, ())
    assert solo.phrases_surface == ()
    assert solo.words == ()


def test_surface_normalized_and_lemma_matches_are_distinct():
    result = compare("Ａの声\n消えた", (snapshot("one", "Aの声\n消える"),))
    assert not any(row.expression == "Ａの声" for row in result.phrases_surface)
    row = find(result.phrases_normalized, "Aの声")
    assert row.evidence[0].occurrence.surface == "Aの声"
    assert row.target_occurrences[0].surface == "Ａの声"
    assert find(result.words, "消える").status == "単語参考"


def test_long_raw_match_and_all_repeated_occurrences():
    raw = "君の記憶がほどけていく"
    result = compare(raw, (snapshot("one", raw + "\n" + raw),))
    row = find(result.phrases_surface, raw)
    assert row.count == 2
    assert len(collapse_contained(result.phrases_surface)) == 1


def test_collapse_keeps_short_phrase_with_extra_occurrences():
    raw = "記憶がほどけていく\nほどけていく"
    result = compare(raw, (snapshot("one", raw),))
    visible = collapse_contained(result.phrases_surface)
    assert any(row.expression == "ほどけていく" for row in visible)


def test_quotes_have_correct_unicode_offsets_and_never_cross_boundaries():
    raw = "[Verse]\r\n😀ｶﾞの声。夜の駅\r\n朝の電車"
    source = snapshot("one", "[Chorus]\r\n😀ｶﾞの声。夜の駅\r\n朝の電車")
    result = compare(raw, (source,))
    for row in (*result.words, *result.phrases_surface, *result.phrases_normalized):
        for item in row.target_occurrences:
            assert raw[item.start:item.end] == item.surface
        for item in row.evidence:
            occurrence = item.occurrence
            assert source.lyrics[occurrence.start:occurrence.end] == occurrence.surface
        assert "[" not in row.expression
        assert "\n" not in row.expression
        assert "。" not in row.expression


def test_overlong_source_does_not_silently_change_denominator():
    with pytest.raises(ValueError, match="曲one"):
        compare("夜の駅", (snapshot("one", "夜" * 20001),))
