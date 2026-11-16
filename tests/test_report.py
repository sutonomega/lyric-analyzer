import json
import sqlite3

import pytest

from lyric_analyzer.analysis.comparison import compare
from lyric_analyzer.analysis.expressions import detect
from lyric_analyzer.analysis.report import build_report, exclusion_for
from lyric_analyzer.history import HistoryStore
from lyric_analyzer.storage import Library


def report_for(library, lyrics="記憶がほどけていく", song_id=None):
    return build_report(lyrics, compare(lyrics, library.comparison_corpus(), song_id),
                        detect(lyrics, library.list_rules()), {"include_unknown_tags": False})


def prepare(tmp_path):
    library = Library(tmp_path / "library.db")
    library.save("過去A", "記憶がほどけていく\n記憶がほどけていく")
    library.save("過去B", "記憶がほどけていく")
    return library, HistoryStore(library)


def test_combined_priority_and_no_double_counting(tmp_path):
    library, store = prepare(tmp_path)
    report = report_for(library, "記憶がほどけていく\n心の奥で")
    assert report["findings"][0]["priority"] == 0
    first = report["findings"][0]
    assert first["rules"]
    assert first["song_count"] == 2
    assert first["past_count"] == 3
    assert report["findings"][-1]["kind"] == "word"
    assert any(item["priority"] == 2 and item["surface"] == "心の奥で" for item in report["findings"])
    assert json.loads(json.dumps(report, ensure_ascii=False))["input"] == report["input"]


def test_unrelated_same_line_does_not_become_combined(tmp_path):
    library = Library(tmp_path / "library.db")
    library.save("A", "夜の駅")
    library.save("B", "夜の駅")
    report = report_for(library, "夜の駅。心の奥で")
    assert not any(item["priority"] == 0 for item in report["findings"])
    assert any(item["priority"] == 1 for item in report["findings"])
    assert any(item["priority"] == 2 for item in report["findings"])


def test_repetition_counts_are_kept_in_report(tmp_path):
    library, _ = prepare(tmp_path)
    report = report_for(library, "記憶がほどけていく\n記憶がほどけていく")
    row = report["findings"][0]
    assert row["input_count"] == 2
    assert row["unique_line_count"] == 1


def test_saved_snapshot_survives_restart_and_changes(tmp_path):
    library, store = prepare(tmp_path)
    report = report_for(library)
    run_id = store.save(report, "未保存の検証")
    saved = store.get(run_id)
    song = library.list_songs()[0]
    library.save("変更後", "朝の電車", song_id=song["id"])
    rule = library.list_rules()[0]
    library.save_rule(rule.kind, json.loads(rule.pattern_json), "新しい理由", rule.review_hint, False, rule.id, rule.version)
    reopened = HistoryStore(Library(library.path))
    assert reopened.get(run_id) == saved
    assert reopened.list_runs()[0]["title"] == "未保存の検証"
    assert saved["input_hash"] == report["input_hash"]
    assert saved["versions"]["report"] == "report-2"


def test_grammatical_phrases_are_reference_but_content_and_rules_remain(tmp_path):
    library = Library(tmp_path / "library.db")
    for index in range(3):
        library.save(str(index), "して\nていく\nないまま\n夜の駅\nほどけていく")
    report = report_for(library, "して\nていく\nないまま\n夜の駅\nほどけていく")
    grammar = [row for row in report["findings"] if row["surface"] in {"して", "ていく", "ないまま"} and row["kind"] == "phrase"]
    assert grammar
    assert all(row["priority"] == 5 for row in grammar)
    assert all(row["priority"] == 5 for row in report["findings"] if row["kind"] == "word" and row["expression_key"] == "する")
    assert any(row["priority"] == 1 and row["surface"] == "夜の駅" for row in report["findings"])
    assert any(row["rules"] and row["priority"] < 5 for row in report["findings"])
    library.save_rule("literal", {"text": "して"}, "意図した監視", "文脈を確認", True)
    updated = report_for(library, "して")
    assert any(row["rules"] and row["priority"] < 5 for row in updated["findings"])


def test_grouping_preserves_occurrences_and_deduplicates_evidence(tmp_path):
    from lyric_analyzer.analysis.report import group_evidence, group_findings

    library, _ = prepare(tmp_path)
    report = report_for(library, "記憶がほどけていく\n記憶がほどけていく")
    original = json.dumps(report, ensure_ascii=False)
    groups = group_findings(report["findings"])
    assert len(groups) < len(report["findings"])
    first = groups[0]
    assert len(first) == 2
    assert {row["line_number"] for row in first} == {1, 2}
    assert group_evidence(report, first) == (2, 3)
    assert json.dumps(report, ensure_ascii=False) == original


@pytest.mark.parametrize("scope", ["run", "song", "global"])
def test_exclusion_scope_and_restore(tmp_path, scope):
    library, store = prepare(tmp_path)
    target = library.save("対象", "記憶がほどけていく")
    run_id = store.save(report_for(library, song_id=target), "対象", target)
    report = store.get(run_id)
    row = report["findings"][0]
    exclusion_id = store.exclude(run_id, row["id"], scope, "意図した反復")
    active = store.list_exclusions()
    assert exclusion_for(row, report, active)["reason"] == "意図した反復"
    assert store.exclude(run_id, row["id"], scope, "意図した反復") == exclusion_id
    assert len(store.list_exclusions()) == 1
    next_id = store.save(report_for(library, song_id=target), "対象", target)
    next_report = store.get(next_id)
    next_row = next_report["findings"][0]
    assert bool(exclusion_for(next_row, next_report, active)) == (scope != "run")
    different_id = store.save(report_for(library), "別の入力")
    different = store.get(different_id)
    assert bool(exclusion_for(different["findings"][0], different, active)) == (scope == "global")
    assert not exclusion_for(row, report, report["exclusions"])
    store.restore(exclusion_id)
    assert not exclusion_for(row, report, store.list_exclusions())
    if scope != "run":
        assert exclusion_for(next_row, next_report, next_report["exclusions"])


def test_word_exclusion_uses_lemma_and_does_not_hide_containing_phrase(tmp_path):
    library = Library(tmp_path / "library.db")
    library.save("過去", "記憶が消える")
    store = HistoryStore(library)
    run_id = store.save(report_for(library, "記憶が消えた"), "入力")
    report = store.get(run_id)
    word = next(item for item in report["findings"] if item["kind"] == "word" and item["surface"] == "消え")
    assert word["expression_key"] == "消える"
    store.exclude(run_id, word["id"], "global", "使う動詞")
    next_id = store.save(report_for(library, "記憶が消える"), "次")
    later = store.get(next_id)
    word_later = next(item for item in later["findings"] if item["kind"] == "word" and item["surface"] == "消える")
    assert exclusion_for(word_later, later, store.list_exclusions())
    phrase = next(item for item in later["findings"] if item["kind"] == "phrase")
    assert not exclusion_for(phrase, later, store.list_exclusions())


def test_unsaved_song_exclusion_rejected_and_invalid_requests_do_not_write(tmp_path):
    library, store = prepare(tmp_path)
    run_id = store.save(report_for(library), "入力")
    row = store.get(run_id)["findings"][0]
    for scope, reason in [("song", "理由"), ("invalid", "理由"), ("global", "")]:
        with pytest.raises(ValueError):
            store.exclude(run_id, row["id"], scope, reason)
    with pytest.raises(ValueError):
        store.exclude(run_id, "missing", "global", "理由")
    assert not store.list_exclusions()


def test_target_revision_only_points_to_saved_input(tmp_path):
    library, store = prepare(tmp_path)
    target = library.save("対象", "記憶がほどけていく")
    saved_id = store.save(report_for(library, song_id=target), "対象", target)
    assert store.get(saved_id)["target_revision_id"] == library.revisions(target)[0]["id"]
    draft_id = store.save(report_for(library, "消えない想い", target), "編集途中", target)
    assert store.get(draft_id)["target_revision_id"] is None


def test_song_delete_removes_dependent_history_and_scoped_exclusions(tmp_path):
    library, store = prepare(tmp_path)
    source = library.list_songs()[0]["id"]
    run_id = store.save(report_for(library), "入力")
    row = store.get(run_id)["findings"][0]
    store.exclude(run_id, row["id"], "run", "今回のみ")
    assert store.related_count(source) == 1
    library.delete(source)
    assert store.get(run_id) is None
    assert not store.list_exclusions()
    assert not store.list_runs()


def test_failed_save_rolls_back_run_and_source_links(tmp_path):
    library, store = prepare(tmp_path)
    report = report_for(library)
    source = library.list_songs()[0]["id"]
    library.delete(source)
    with pytest.raises(sqlite3.IntegrityError):
        store.save(report, "入力")
    assert store.list_runs() == []
    with library.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM analysis_sources").fetchone()[0] == 0
