import pytest

from lyric_analyzer.analysis.frequency import MAX_LYRICS_LENGTH, analyze


def by_expression(rows):
    return {row.expression: row for row in rows}


def test_inflections_are_grouped_only_for_words():
    result = analyze("消える\n消えた\n美しい\n美しかった")
    words = by_expression(result.words)
    assert words["消える"].count == 2
    assert words["消える"].variants == {"消える": 1, "消え": 1}
    assert words["美しい"].count == 2
    assert "た" not in words
    assert result.split_mode == "A"
    assert result.tokenizer_version == "0.7.0"
    assert result.dictionary_version == "20261015"


def test_particles_and_auxiliaries_remain_in_phrases():
    result = analyze("記憶がほどけていく\n記憶がほどけた\n記憶がほどけていく")
    phrases = by_expression(result.phrases_surface)
    assert phrases["ほどけていく"].count == 2
    assert phrases["ほどけた"].count == 1
    assert phrases["記憶がほどけていく"].count == 2
    assert "記憶ほどけ" not in phrases
    assert "ほどけいく" not in phrases
    assert phrases["ほどけていく"].unique_line_count == 1
    assert [item.line_number for item in phrases["ほどけていく"].occurrences] == [1, 3]


@pytest.mark.parametrize("separator", ["\n", "\r\n", "。", "！", "?", "、", " ", "\t", "😀", "\n[Chorus]\n"])
def test_phrases_do_not_cross_boundaries(separator):
    result = analyze(f"夜の駅{separator}朝の駅")
    for row in result.phrases_surface:
        assert separator not in row.expression
        assert not ("夜" in row.expression and "朝" in row.expression)


def test_original_and_nfkc_phrase_counts_are_distinct():
    result = analyze("Ａの声\nAの声")
    original = by_expression(result.phrases_surface)
    normalized = by_expression(result.phrases_normalized)
    assert original["Ａの声"].count == 1
    assert original["Aの声"].count == 1
    assert normalized["Aの声"].count == 2
    assert normalized["Aの声"].variants == {"Ａの声": 1, "Aの声": 1}


def test_offsets_remain_correct_with_unicode_and_sections():
    raw = "[Verse]\r\n😀ｶﾞの声。か\u3099の声\n[Chorus 2]\n㍿の灯り"
    result = analyze(raw)
    for token in result.tokens:
        assert raw[token.start:token.end] == token.surface
    for row in (*result.words, *result.phrases_surface, *result.phrases_normalized):
        for item in row.occurrences:
            assert raw[item.start:item.end] == item.surface
            assert item.surface in item.line_text
    assert result.tokens[-1].section == "Chorus 2"
    assert all("[" not in token.surface for token in result.tokens)


def test_unknown_tags_are_optional_and_known_tags_stay_excluded():
    raw = "[Chorus]\n[街の灯り]\n夜の駅"
    assert "街" not in by_expression(analyze(raw).words)
    assert "街" in by_expression(analyze(raw, True).words)
    assert all("Chorus" not in row.expression for row in analyze(raw, True).words)


def test_ngram_length_and_content_word_filter():
    result = analyze("君の記憶がほどけていく\nがのて")
    for row in result.phrases_surface:
        for item in row.occurrences:
            contained = [token for token in result.tokens if token.start >= item.start and token.end <= item.end]
            assert 2 <= len(contained) <= 5
            assert any(token.pos in {"名詞", "動詞", "形容詞"} for token in contained)


@pytest.mark.parametrize("raw", ["", "\n\r\n", "[Verse]\n[Chorus 2]", "。！？😀"])
def test_empty_or_metadata_only_input(raw):
    result = analyze(raw)
    assert not result.words
    assert not result.phrases_surface


def test_too_long_input_is_rejected_before_tokenizing():
    with pytest.raises(ValueError, match="20,000"):
        analyze("夜" * (MAX_LYRICS_LENGTH + 1))


def test_repeated_occurrences_on_one_line_are_not_lost():
    result = analyze("夜の駅、夜の駅\n夜の駅")
    row = by_expression(result.phrases_surface)["夜の駅"]
    assert row.count == 3
    assert row.unique_line_count == 2
    assert len({(item.start, item.end) for item in row.occurrences}) == 3


def test_english_is_not_translated_or_stemmed():
    result = analyze("SUMMER\nsummer\nRunning")
    assert set(by_expression(result.words)) == {"SUMMER", "summer", "Running"}
