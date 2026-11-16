from copy import deepcopy
import json

import pytest

from lyric_analyzer.analysis.prosody import comparison, estimate, mora_count, validate_comparison
from lyric_analyzer.analysis.comparison import compare
from lyric_analyzer.analysis.report import build_report
from lyric_analyzer.analysis.expressions import detect
from lyric_analyzer.local_ai import DIRECTIONS, PROMPT_VERSION, prepare_request
from lyric_analyzer.storage import Library
from lyric_analyzer.history import HistoryStore
from lyric_analyzer.backup import checksum, export_backup, inspect_backup, restore_backup


@pytest.mark.parametrize("reading,count", [("きょう", 2), ("がっこう", 4), ("しんぶん", 4),
    ("コーヒー", 4), ("ティー", 2), ("ファンタジー", 5), ("ヴァイオリン", 5), ("きゃっ", 2),
    ("おう", 2), ("ｷｬｯﾄ", 3), ("あ い、う", 3), ("ヵヶ", 2)])
def test_mora_conventions(reading, count):
    assert mora_count(reading) == count


@pytest.mark.parametrize("reading", ["", "ゃ", "ンァ", "あぁ", "きゃぁ", "ABC", "123", "漢字", "♡"])
def test_readings_that_cannot_be_counted_are_rejected(reading):
    with pytest.raises(ValueError):
        mora_count(reading)


def test_automatic_reading_flags_names_english_numbers_and_unknown_words():
    assert estimate("君を待つ")["mora"] == 5
    tokyo = estimate("東京")
    assert tokyo["status"] == "推定"
    assert tokyo["mora"] == 4
    assert tokyo["notes"]
    for text in ("ABCを待つ", "123", "𠮷"):
        result = estimate(text)
        assert result["status"] == "不明"
        assert result["mora"] is None
    assert estimate("ネオルミナ")["status"] == "推定"
    specified = estimate("ABC", "えーびーしー")
    assert specified["mora"] == 6
    assert specified["status"] == "指定した読み"


def sample_result():
    return {"line_id": "placeholder", "original": "ABCを待つ", "reason": "情景を具体化",
            "candidates": [{"direction": d, "text": t} for d, t in zip(DIRECTIONS, ("君を待つ", "窓の前", "君が待つ"))]}


def test_comparison_and_validation_preserve_snapshot_without_dictionary_recompute(monkeypatch):
    result = sample_result()
    auto = comparison(result)
    assert comparison(result, {"original": " "}) == auto
    assert auto["rows"][0]["mora"] is None
    saved = comparison(result, {"original": "エービーシーヲマツ"})
    assert saved["rows"][0]["mora"] == 9
    assert [row["id"] for row in saved["rows"]] == ["original", *DIRECTIONS]
    monkeypatch.setattr("lyric_analyzer.analysis.prosody.load_dictionary", lambda: pytest.fail("must not re-tokenize saved snapshots"))
    validate_comparison(result, saved)
    for field, value in (("mora", 99), ("text", "別の本文"), ("status", "不明")):
        bad = deepcopy(saved)
        bad["rows"][0][field] = value
        with pytest.raises(ValueError):
            validate_comparison(result, bad)


def test_reading_changes_persist_and_backup_includes_snapshots(tmp_path):
    library = Library(tmp_path / "library.db")
    store = HistoryStore(library)
    raw = "ABCを待つ"
    song_id = library.save("対象", raw)
    run_id = store.save(build_report(raw, compare(raw, ()), detect(raw, ()), {}), "対象", song_id)
    request = prepare_request(raw, 1)
    result = sample_result()
    result["line_id"] = request["line_id"]
    legacy = {"provider": "ollama-local", "model": "local:3b", "prompt_version": PROMPT_VERSION,
              "created_at": library.now(), "request": request, "result": result}
    store.save_suggestion(run_id, legacy)
    before = library.export_json()
    store.save_readings(run_id, 0, {"original": "エービーシーヲマツ"})
    saved = HistoryStore(Library(library.path)).get(run_id)
    assert saved["ai_suggestions"][0]["prosody"]["rows"][0]["mora"] == 9
    assert library.export_json() == before
    with pytest.raises(ValueError):
        store.save_readings(run_id, 0, {"original": "ABC"})
    assert store.get(run_id) == saved
    for index in (-1, 2, True):
        with pytest.raises(ValueError):
            store.save_readings(run_id, index, {})
    restored = Library(tmp_path / "restored.db")
    restore_backup(restored, export_backup(library), confirmed=True)
    assert HistoryStore(restored).get(run_id) == saved
    archive = json.loads(export_backup(library))
    payload = json.loads(archive["tables"]["analysis_runs"][0]["payload_json"])
    payload["ai_suggestions"][0]["prosody"]["rows"][0]["mora"] = 99
    archive["tables"]["analysis_runs"][0]["payload_json"] = json.dumps(payload, ensure_ascii=False)
    archive["sha256"] = checksum(archive["tables"])
    with pytest.raises(ValueError, match="音数"):
        inspect_backup(json.dumps(archive, ensure_ascii=False).encode())
    store.save_readings(run_id, 0, {})
    assert store.get(run_id)["ai_suggestions"][0]["prosody"]["rows"][0]["mora"] is None
