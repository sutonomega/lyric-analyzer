from copy import deepcopy
import json

import pytest

from lyric_analyzer import local_ai
from lyric_analyzer.analysis.candidate_quality import CHECKS, DIRECTIONS, inspect_candidates, validate_review
from lyric_analyzer.analysis.comparison import compare
from lyric_analyzer.analysis.expressions import detect
from lyric_analyzer.analysis.report import build_report
from lyric_analyzer.backup import checksum, export_backup, inspect_backup, restore_backup
from lyric_analyzer.history import HistoryStore
from lyric_analyzer.storage import Library


def result(original, meaning, scene="窓辺の封筒", viewpoint="封筒が朝を待つ"):
    return {"line_id": "unused", "original": original, "reason": "具体的な描写",
            "candidates": [{"direction": d, "text": t} for d, t in zip(DIRECTIONS, (meaning, scene, viewpoint))]}


def test_negative_and_question_marks_are_observed_without_semantic_judgement():
    rows = inspect_candidates(result("返事をしない", "返事をしなさい？"))
    assert "否定語の有無が異なる" in rows[0]["checks"]
    assert "文末の疑問符の有無が異なる" in rows[0]["checks"]
    assert "否定語の有無が異なる" in inspect_candidates(result("返事をしない", "返事は控える"))[0]["checks"]
    changed = inspect_candidates(result("昨日、妹が鍵を返した", "明日、兄が鍵を返す"))[0]["checks"]
    assert any("人物を表す語の差" in value for value in changed)
    assert any("時間を表す語の差" in value for value in changed)
    assert not inspect_candidates(result("雨がやんだ", "雨が降っている"))[0]["checks"]
    # No flag is not a guarantee: subject and tense need a person's review.
    assert not inspect_candidates(result("危ない道", "険しい道"))[0]["checks"]


def test_original_duplicate_and_context_copy_are_visible():
    value = result("君を待つ", " 君を待つ。", "窓の前", "窓の前")
    rows = inspect_candidates(value, {"context_before": ["窓の前"], "context_after": []})
    assert "原文と同じ表現" in rows[0]["checks"]
    assert all("別の方向の候補と同じ表現" in row["checks"] for row in rows[1:])
    assert all("前後の本文行をそのまま使用" in row["checks"] for row in rows[1:])


def review():
    return {"version": "candidate-review-1", "updated_at": Library.now(),
            "decisions": {d: {"verdict": "unreviewed", "note": ""} for d in DIRECTIONS},
            "meaning_checks": {key: "unchecked" for key in CHECKS}}


@pytest.mark.parametrize("change", [lambda r: r.update(version="unknown"),
    lambda r: r["meaning_checks"].update(subject="guaranteed"),
    lambda r: r["decisions"]["meaning"].update(verdict="automatic_accept"),
    lambda r: r["decisions"]["scene"].update(note="x" * 1001),
    lambda r: r["decisions"].pop("viewpoint"), lambda r: r.update(updated_at="2026-11-16T12:00:00")])
def test_invalid_reviews_are_rejected(change):
    value = review()
    change(value)
    with pytest.raises(ValueError):
        validate_review(value)


def test_old_prompts_new_reviews_and_backup_remain_compatible(tmp_path):
    library = Library(tmp_path / "library.db")
    store = HistoryStore(library)
    raw = "君を待つ"
    song_id = library.save("自作", raw)
    run = store.save(build_report(raw, compare(raw, ()), detect(raw, ()), {}), "自作", song_id)
    request = local_ai.prepare_request(raw, 1)
    value = result(raw, "君の到着を待つ")
    value["line_id"] = request["line_id"]
    suggestion = {"provider": "ollama-local", "model": "local:3b", "prompt_version": "local-rewrite-1",
                  "created_at": Library.now(), "request": request, "result": value}
    store.save_suggestion(run, suggestion)
    before = library.export_json()
    assessment = review()
    assessment["decisions"]["meaning"] = {"verdict": "edit", "note": "語調を調整する"}
    assessment["meaning_checks"]["tone"] = "changed"
    store.save_candidate_review(run, 0, assessment["decisions"], assessment["meaning_checks"])
    saved = HistoryStore(Library(library.path)).get(run)
    assert saved["ai_suggestions"][0]["candidate_review"]["decisions"]["meaning"]["verdict"] == "edit"
    assert saved["ai_suggestions"][0]["result"] == value
    assert library.export_json() == before
    restored = Library(tmp_path / "restored.db")
    restore_backup(restored, export_backup(library), confirmed=True)
    assert HistoryStore(restored).get(run) == saved
    for index in (-1, True, 4):
        with pytest.raises(ValueError):
            store.save_candidate_review(run, index, assessment["decisions"], assessment["meaning_checks"])
    archive = json.loads(export_backup(library))
    payload = json.loads(archive["tables"]["analysis_runs"][0]["payload_json"])
    payload["ai_suggestions"][0]["candidate_review"]["meaning_checks"]["subject"] = "invalid"
    archive["tables"]["analysis_runs"][0]["payload_json"] = json.dumps(payload, ensure_ascii=False)
    archive["sha256"] = checksum(archive["tables"])
    with pytest.raises(ValueError):
        inspect_backup(json.dumps(archive, ensure_ascii=False).encode())


def test_new_prompt_is_versioned_and_old_prompt_is_retained():
    assert local_ai.PROMPT_VERSION == "local-rewrite-2"
    assert "local-rewrite-1" in local_ai.PROMPTS
    assert "私は手紙を捨てなかった" in local_ai.SYSTEM
    assert "主語" in local_ai.SYSTEM and "肯定否定" in local_ai.SYSTEM
