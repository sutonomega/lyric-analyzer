from datetime import datetime
import re
import unicodedata

from lyric_analyzer.analysis.frequency import tokenize


DIRECTIONS = ("meaning", "scene", "viewpoint")
CHECKS = ("subject", "tense", "polarity", "tone")
PEOPLE = frozenset({"私", "僕", "俺", "君", "あなた", "彼", "彼女", "妹", "姉", "兄", "弟", "父", "母"})
TIMES = frozenset({"昨日", "今日", "明日", "昨夜", "今夜", "今朝"})


def canonical(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).strip("。、.!！?？")


def negative_words(text):
    return [token.surface for token in tokenize(text) if token.pos in {"助動詞", "形容詞"}
            and token.lemma in {"ない", "無い", "ぬ", "ず", "まい"}]


def inspect_candidates(result, request=None):
    """Show observable differences, never a semantic-equivalence score."""
    original = result["original"]
    original_negative = negative_words(original)
    original_lemmas = {token.lemma for token in tokenize(original)}
    candidates = result["candidates"]
    rows = []
    for candidate in candidates:
        text = candidate["text"]
        checks = []
        if canonical(text) == canonical(original):
            checks.append("原文と同じ表現")
        if sum(canonical(text) == canonical(other["text"]) for other in candidates) > 1:
            checks.append("別の方向の候補と同じ表現")
        if request and any(canonical(text) == canonical(line) for line in [*request["context_before"], *request["context_after"]]):
            checks.append("前後の本文行をそのまま使用")
        if candidate["direction"] == "meaning":
            candidate_lemmas = {token.lemma for token in tokenize(text)}
            for terms, label in ((PEOPLE, "人物を表す語"), (TIMES, "時間を表す語")):
                before, after = sorted(original_lemmas & terms), sorted(candidate_lemmas & terms)
                if before != after:
                    checks.append(f"{label}の差：{'・'.join(before) or '該当なし'} → {'・'.join(after) or '該当なし'}")
            if bool(re.search(r"[?？]\s*$", original)) != bool(re.search(r"[?？]\s*$", text)):
                checks.append("文末の疑問符の有無が異なる")
            if bool(original_negative) != bool(negative_words(text)):
                checks.append("否定語の有無が異なる")
        rows.append({"direction": candidate["direction"], "checks": checks})
    return rows


def validate_review(review):
    if (not isinstance(review, dict) or set(review) != {"version", "updated_at", "decisions", "meaning_checks"}
            or review["version"] != "candidate-review-1" or not isinstance(review["updated_at"], str)
            or not isinstance(review["decisions"], dict) or set(review["decisions"]) != set(DIRECTIONS)
            or not isinstance(review["meaning_checks"], dict) or set(review["meaning_checks"]) != set(CHECKS)):
        raise ValueError("候補評価の形式が不正です。")
    if datetime.fromisoformat(review["updated_at"]).utcoffset() is None:
        raise ValueError("候補評価の日時が不正です。")
    for row in review["decisions"].values():
        if (not isinstance(row, dict) or set(row) != {"verdict", "note"}
                or row["verdict"] not in ("unreviewed", "usable", "edit", "reject")
                or not isinstance(row["note"], str) or len(row["note"]) > 1000):
            raise ValueError("候補の評価内容が不正です。")
    if any(value not in ("unchecked", "kept", "changed") for value in review["meaning_checks"].values()):
        raise ValueError("意味保持の確認内容が不正です。")
