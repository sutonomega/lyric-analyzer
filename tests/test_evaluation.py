import pytest

from lyric_analyzer.analysis.expressions import default_rules, detect
from lyric_analyzer.evaluation import synthetic_lyrics
from lyric_analyzer.history import HistoryStore
from lyric_analyzer.storage import Library
from lyric_analyzer.analysis.comparison import compare
from lyric_analyzer.analysis.report import build_report


CASES = [
    ("ほどけていく", {"辞書一致（NFKC）"}), ("糸がほどけていく", {"辞書一致（NFKC）"}),
    ("心の奥で", {"辞書一致（NFKC）"}), ("心の奥で音がする", {"辞書一致（NFKC）"}),
    ("消えない想い", {"辞書一致（NFKC）"}), ("消えない想いを持つ", {"辞書一致（NFKC）"}),
    ("光を探して", {"辞書一致（NFKC）"}), ("光を探して歩く", {"辞書一致（NFKC）"}),
    ("[Verse]\n心の奥で", {"辞書一致（NFKC）"}), ("😀ほどけていく", {"辞書一致（NFKC）"}),
    ("心の奥で\r\n光を探して", {"辞書一致（NFKC）"}), ("ほどけていく\nほどけていく", {"辞書一致（NFKC）"}),
    ("記憶が消える", {"文型候補"}), ("記憶が消えた", {"文型候補"}),
    ("時間は溶ける", {"文型候補"}), ("約束が消える", {"文型候補"}),
    ("夢が消えた", {"文型候補"}), ("時間がほどけた", {"文型候補"}),
    ("記憶がほどけていく", {"文型候補", "辞書一致（NFKC）"}),
    ("夢はほどけていく", {"文型候補", "辞書一致（NFKC）"}),
    ("朝の駅で切符を買う", set()), ("記憶が増える", set()), ("光が消える", set()),
    ("記憶を消す", set()), ("記憶が消えない", set()), ("記憶が\n消える", set()),
    ("記憶が、消える", set()), ("消えない想い出", set()), ("[ほどけていく]", set()), ("[Chorus]\n。！？", set()),
]


@pytest.mark.parametrize("lyrics,expected", CASES)
def test_original_golden_cases(lyrics, expected):
    assert {finding.match_kind for finding in detect(lyrics, default_rules()).findings} == expected


def test_benchmark_corpus_is_twenty_distinct_2000_character_texts():
    texts = [synthetic_lyrics(index) for index in range(20)]
    assert len(set(texts)) == 20
    assert all(len(text) == 2000 for text in texts)


def test_reviews_persist_and_invalid_batch_is_atomic(tmp_path):
    library = Library(tmp_path / "library.db")
    raw = "ほどけていく\n心の奥で"
    store = HistoryStore(library)
    run_id = store.save(build_report(raw, compare(raw, ()), detect(raw, library.list_rules()), {}), "自作検証")
    findings = store.get(run_id)["findings"]
    store.save_reviews(run_id, [{"finding_id": findings[0]["id"], "label": "useful", "note": "自作サンプル"}])
    assert HistoryStore(Library(library.path)).reviews(run_id)[findings[0]["id"]]["label"] == "useful"
    with pytest.raises(ValueError):
        store.save_reviews(run_id, [{"finding_id": findings[0]["id"], "label": "false_positive", "note": "変更"},
                                   {"finding_id": "missing", "label": "useful", "note": ""}])
    assert store.reviews(run_id)[findings[0]["id"]]["label"] == "useful"
    store.save_reviews(run_id, [{"finding_id": findings[0]["id"], "label": None, "note": ""}])
    assert store.reviews(run_id) == {}
