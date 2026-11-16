import unicodedata

import pytest

from lyric_analyzer.analysis.parsing import analysis_segments, normalize_with_offsets, parse_lines


@pytest.mark.parametrize("raw", ["ＡＢＣ", "ｶﾞ", "か\u3099", "㍿", "😀光", "가", "夜\r\n朝", "", "[Verse]\n君[声]。朝！夜？"])
def test_normalization_maps_back_to_source(raw):
    result = normalize_with_offsets(raw)
    assert result.text == unicodedata.normalize("NFKC", raw)
    assert len(result.source_spans) == len(result.text)
    for index, character in enumerate(result.text):
        start, end = result.original_range(index, index + 1)
        assert character in unicodedata.normalize("NFKC", raw[start:end])


def test_composed_and_expanded_offsets():
    result = normalize_with_offsets("😀ｶﾞ㍿")
    assert result.original_range(1, 2) == (1, 3)
    assert result.original_range(2, 6) == (3, 4)
    with pytest.raises(ValueError):
        result.original_range(0, 0)


def test_sections_boundaries_and_inline_brackets():
    raw = "[Verse]\r\nＡの声。君[光]！\r\n\r\n[My section]\n夜の駅\n[Chorus 2]\n夜の駅"
    lines = parse_lines(raw)
    assert [line.line_number for line in lines] == list(range(1, 8))
    assert [line.text for line in lines if line.metadata] == ["[Verse]", "[My section]", "[Chorus 2]"]
    for line in lines:
        assert raw[line.start:line.end] == line.text
    segments = analysis_segments(raw)
    assert [segment.text for segment in segments] == ["Ａの声", "君[光]", "夜の駅", "夜の駅"]
    assert segments[-1].section == "Chorus 2"
    assert segments[-2].section == "My section"
    for segment in segments:
        assert raw[segment.start:segment.end] == segment.text
    assert "[My section]" in [segment.text for segment in analysis_segments(raw, True)]
