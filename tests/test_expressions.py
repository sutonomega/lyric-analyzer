import json
import sqlite3

import pytest

from lyric_analyzer.analysis.expressions import ExpressionRule, default_rules, detect, validate_rule
from lyric_analyzer.storage import Library, MIGRATIONS


def test_literal_and_pattern_have_distinct_evidence():
    raw = "[Verse]\r\n記憶がほどけていく\n消えない想いを持つ"
    result = detect(raw, default_rules())
    assert any(item.match_kind == "辞書一致（NFKC）" and item.occurrence.surface == "ほどけていく" for item in result.findings)
    assert any(item.match_kind == "文型候補" and item.occurrence.surface == "記憶がほどけていく" for item in result.findings)
    assert any(item.occurrence.surface == "消えない想い" for item in result.findings)
    for finding in result.findings:
        assert raw[finding.occurrence.start:finding.occurrence.end] == finding.occurrence.surface
        assert finding.occurrence.section == "Verse"
        assert finding.rule.version == 1
        assert finding.rule.reason
        assert finding.rule.review_hint


@pytest.mark.parametrize("raw", ["記憶が消えた", "約束は溶けていく", "時間が消える", "夢がほどける"])
def test_pattern_positive_examples(raw):
    assert any(item.match_kind == "文型候補" for item in detect(raw, default_rules()).findings)


@pytest.mark.parametrize("raw", ["光が消える", "記憶を消す", "記憶が増える", "記憶が消えない", "記憶が消えぬ", "記憶が消えず", "記憶が\n消える", "記憶が、消える", "記憶が 消える", "記憶が😀消える"])
def test_pattern_negative_examples(raw):
    assert not any(item.match_kind == "文型候補" for item in detect(raw, default_rules()).findings)


def test_literal_does_not_end_in_the_middle_of_a_word():
    assert not any(item.occurrence.surface == "消えない想い" for item in detect("消えない想い出", default_rules()).findings)


def test_repetitions_unknown_tags_and_unicode():
    result = detect("[ほどけていく]\nほどけていく\nほどけていく", default_rules())
    assert len(result.findings) == 2
    assert len(detect("[ほどけていく]", default_rules(), True).findings) == 1
    rule = ExpressionRule("custom", "literal", '{"text":"ガの声"}', "reason", "hint", True, 1)
    raw = "😀ｶﾞの声\r\nガの声"
    found = detect(raw, (rule,)).findings
    assert [item.occurrence.surface for item in found] == ["ｶﾞの声", "ガの声"]
    for item in found:
        assert raw[item.occurrence.start:item.occurrence.end] == item.occurrence.surface


def test_seeds_are_inserted_once_and_edits_survive_restart(tmp_path):
    path = tmp_path / "library.db"
    library = Library(path)
    rules = library.list_rules()
    assert len(rules) == 5
    rule = next(rule for rule in rules if rule.kind == "literal")
    library.save_rule(rule.kind, json.loads(rule.pattern_json), "変更した理由", rule.review_hint, False, rule.id, rule.version)
    reopened = Library(path)
    changed = next(item for item in reopened.list_rules() if item.id == rule.id)
    assert len(reopened.list_rules()) == 5
    assert changed.reason == "変更した理由"
    assert not changed.enabled
    assert changed.version == 2
    assert not detect(json.loads(rule.pattern_json)["text"], (changed,)).findings


def test_new_rule_noop_edit_and_optimistic_version_check(tmp_path):
    library = Library(tmp_path / "library.db")
    rule_id = library.save_rule("literal", {"text": "街の灯り"}, "理由", "観点")
    library.save_rule("literal", {"text": "街の灯り"}, "理由", "観点", rule_id=rule_id, expected_version=1)
    assert next(item for item in library.list_rules() if item.id == rule_id).version == 1
    result = detect("街の灯り", library.list_rules())
    library.save_rule("literal", {"text": "街の灯り"}, "新しい理由", "新しい観点", rule_id=rule_id, expected_version=1)
    assert result.findings[0].rule.reason == "理由"
    assert result.findings[0].rule.version == 1
    with pytest.raises(ValueError, match="辞書が変更"):
        library.save_rule("literal", {"text": "別の表現"}, "古い理由", "観点", rule_id=rule_id, expected_version=1)
    assert next(item for item in library.list_rules() if item.id == rule_id).reason == "新しい理由"


def test_old_database_migration_preserves_songs(tmp_path):
    path = tmp_path / "library.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        for number, migration in enumerate(MIGRATIONS[:2], start=1):
            for statement in migration:
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations VALUES (?, 'now')", (number,))
        connection.execute("INSERT INTO songs VALUES ('existing','title','','now','now')")
        connection.execute("INSERT INTO song_revisions VALUES ('revision','existing',1,'lyrics','hash','now')")
    library = Library(path)
    assert library.list_songs()[0]["lyrics_raw"] == "lyrics"
    assert len(library.list_rules()) == 5


@pytest.mark.parametrize("kind,pattern,reason,hint,enabled", [
    ("regex", {"text": "foo"}, "reason", "hint", True),
    ("literal", {"text": ""}, "reason", "hint", True),
    ("literal", {"text": "foo\nbar"}, "reason", "hint", True),
    ("literal", {"text": "foo", "extra": 1}, "reason", "hint", True),
    ("literal", {"text": "foo"}, "", "hint", True),
    ("literal", {"text": "foo"}, "reason", "", True),
    ("literal", {"text": "foo"}, "reason", "hint", 1),
    ("abstract_loss", {"nouns": [], "verbs": ["消える"]}, "reason", "hint", True),
    ("abstract_loss", {"nouns": ["記憶"], "verbs": "消える"}, "reason", "hint", True),
])
def test_invalid_rule_does_not_write(tmp_path, kind, pattern, reason, hint, enabled):
    library = Library(tmp_path / "library.db")
    with pytest.raises(ValueError):
        library.save_rule(kind, pattern, reason, hint, enabled)
    assert len(library.list_rules()) == 5


def test_custom_pattern_changes_detection_and_empty_input():
    pattern_json = validate_rule("abstract_loss", {"nouns": ["光"], "verbs": ["消える"]}, "reason", "hint", True)
    rule = ExpressionRule("custom", "abstract_loss", pattern_json, "reason", "hint", True, 1)
    assert len(detect("光が消える", (rule,)).findings) == 1
    assert not detect("[Verse]\n。！？", (rule,)).findings
    assert not detect("光が消える", ()).findings
