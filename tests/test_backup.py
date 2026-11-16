import json

import pytest

from lyric_analyzer.analysis.comparison import compare
from lyric_analyzer.analysis.expressions import detect
from lyric_analyzer.analysis.report import build_report
from lyric_analyzer.backup import checksum, export_backup, inspect_backup, restore_backup
from lyric_analyzer.history import HistoryStore
from lyric_analyzer.storage import Library


def populated(path):
    library = Library(path)
    song_id = library.save("検証曲", "記憶がほどけていく", tags=["夜"])
    library.save("検証曲", "記憶がほどけていく\n心の奥で", song_id=song_id)
    rule = library.list_rules()[0]
    library.save_rule(rule.kind, json.loads(rule.pattern_json), "変更済みの理由", rule.review_hint, False, rule.id, rule.version)
    raw = "ほどけていく"
    report = build_report(raw, compare(raw, library.comparison_corpus()), detect(raw, library.list_rules()), {})
    store = HistoryStore(library)
    run_id = store.save(report, "評価用")
    finding = store.get(run_id)["findings"][0]
    store.exclude(run_id, finding["id"], "global", "意図した表現")
    store.save_reviews(run_id, [{"finding_id": finding["id"], "label": "intentional", "note": "自作サンプル"}])
    return library, run_id


def test_full_roundtrip_and_automatic_recovery(tmp_path):
    source, run_id = populated(tmp_path / "source.db")
    content = export_backup(source)
    counts = inspect_backup(content)["counts"]
    assert counts["songs"] == 1
    assert counts["analysis_runs"] == 1
    assert counts["finding_reviews"] == 1
    target = Library(tmp_path / "target.db")
    target.save("置換前の曲", "朝の駅")
    before = json.loads(export_backup(target))["tables"]
    recovery = restore_backup(target, content, confirmed=True)
    assert json.loads(recovery.read_bytes())["tables"] == before
    assert json.loads(export_backup(target))["tables"] == json.loads(content)["tables"]
    reopened = Library(target.path)
    assert HistoryStore(reopened).get(run_id) == HistoryStore(source).get(run_id)
    assert not reopened.list_rules()[0].enabled
    assert HistoryStore(reopened).reviews(run_id)
    restore_backup(reopened, recovery.read_bytes(), confirmed=True)
    assert json.loads(export_backup(reopened))["tables"] == before


def test_restore_requires_confirmation(tmp_path):
    library, _ = populated(tmp_path / "source.db")
    content = export_backup(library)
    with pytest.raises(ValueError, match="確認"):
        restore_backup(library, content)
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("mutation", [
    lambda archive: archive.update(version=99),
    lambda archive: archive.update(schema_version=99),
    lambda archive: archive.update(sha256="bad"),
    lambda archive: archive["tables"].update(unexpected=[]),
    lambda archive: archive["tables"]["songs"][0].update(extra="invalid"),
    lambda archive: archive["tables"]["song_revisions"][0].update(content_hash="bad"),
    lambda archive: archive["tables"]["song_tags"][0].update(song_id="missing"),
    lambda archive: archive["tables"]["expression_rules"][0].update(kind="regex"),
    lambda archive: archive["tables"]["analysis_runs"][0].update(payload_json="{}"),
    lambda archive: archive["tables"]["finding_reviews"][0].update(finding_id="missing"),
])
def test_invalid_backup_keeps_current_database(tmp_path, mutation):
    source, _ = populated(tmp_path / "source.db")
    archive = json.loads(export_backup(source))
    mutation(archive)
    if archive["sha256"] != "bad":
        archive["sha256"] = checksum(archive["tables"])
    target = Library(tmp_path / "target.db")
    target.save("現在の曲", "朝の駅")
    before = target.export_json()
    with pytest.raises(ValueError):
        restore_backup(target, json.dumps(archive).encode("utf-8"), confirmed=True)
    assert target.export_json() == before
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("content", [b"{}", b"not json", b"\xff", b"[]"])
def test_wrong_format_is_rejected(content):
    with pytest.raises(ValueError):
        inspect_backup(content)


def test_recovery_file_failure_prevents_restore(tmp_path, monkeypatch):
    source, _ = populated(tmp_path / "source.db")
    content = export_backup(source)
    target = Library(tmp_path / "target.db")
    target.save("現在の曲", "朝の駅")
    before = target.export_json()
    original = type(tmp_path).open

    def fail_recovery(path, *args, **kwargs):
        if path.name.startswith("before-restore-"):
            raise OSError("disk full")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "open", fail_recovery)
    with pytest.raises(OSError):
        restore_backup(target, content, confirmed=True)
    assert target.export_json() == before
