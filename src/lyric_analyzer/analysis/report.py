from dataclasses import asdict
from collections import defaultdict
import hashlib
import unicodedata
from typing import Any

from lyric_analyzer.analysis.comparison import collapse_contained
from lyric_analyzer.analysis.frequency import CONTENT_POS, tokenize


# These common lemmas are a ranking heuristic, not a judgement of originality.
GRAMMATICAL_LEMMAS = frozenset({"する", "いる", "ある", "なる", "いく", "行く", "くる", "来る",
                                "できる", "出来る", "しまう", "ない", "無い", "いい", "良い", "まま", "こと", "もの", "よう"})


def expression_key(expression):
    return unicodedata.normalize("NFKC", expression)


def build_report(raw, comparison, detection, settings):
    content_tokens = [token for token in tokenize(raw, settings.get("include_unknown_tags", False))
                      if not token.boundary and token.pos in CONTENT_POS and token.lemma not in GRAMMATICAL_LEMMAS]
    regions: dict[tuple[str, int, int], dict[str, Any]] = {}
    comparisons: dict[str, dict[str, Any]] = {}
    for kind, mode, matches in (
        ("phrase", "原文", collapse_contained(comparison.phrases_surface)),
        ("phrase", "NFKC", collapse_contained(comparison.phrases_normalized)),
        ("word", "原形", comparison.words),
    ):
        for match in matches:
            match_id = hashlib.sha256(f"{kind}:{mode}:{match.expression}:{match.pos}".encode("utf-8")).hexdigest()
            comparisons[match_id] = {"mode": mode, **asdict(match)}
            for occurrence in match.target_occurrences:
                key = (kind, occurrence.start, occurrence.end)
                row = regions.setdefault(key, {"kind": kind, "start": occurrence.start, "end": occurrence.end,
                                               "line_number": occurrence.line_number, "line_text": occurrence.line_text,
                                               "comparisons": [], "rules": []})
                row["comparisons"].append(match_id)
    for finding in detection.findings:
        occurrence = finding.occurrence
        containing = [row for row in regions.values() if row["kind"] == "phrase" and row["start"] <= occurrence.start and occurrence.end <= row["end"]]
        if containing:
            row = min(containing, key=lambda item: item["end"] - item["start"])
        else:
            key = ("phrase", occurrence.start, occurrence.end)
            row = regions.setdefault(key, {"kind": "phrase", "start": occurrence.start, "end": occurrence.end,
                                           "line_number": occurrence.line_number, "line_text": occurrence.line_text,
                                           "comparisons": [], "rules": []})
        row["rules"].append(asdict(finding))
    findings = []
    for row in regions.values():
        evidence = {(item["song_id"], item["revision_id"], item["occurrence"]["start"], item["occurrence"]["end"]): item
                    for match_id in row["comparisons"] for item in comparisons[match_id]["evidence"]}
        repeated = any(comparisons[match_id]["status"] in {"頻出", "参考（母数少）"} for match_id in row["comparisons"])
        row["surface"] = raw[row["start"]:row["end"]]
        row["expression_key"] = expression_key(comparisons[row["comparisons"][0]]["expression"] if row["kind"] == "word" else row["surface"])
        row["song_count"] = len({item["song_id"] for item in evidence.values()})
        row["past_count"] = len(evidence)
        row["priority"] = 3 if row["kind"] == "word" else 0 if repeated and row["rules"] else 1 if repeated else 2 if row["rules"] else 4
        row["category"] = {0: "過去曲頻出／参考＋登録ルール", 1: "過去曲頻出／参考", 2: "登録ルール", 3: "単語参考", 4: "過去曲一致（閾値未満）"}[row["priority"]]
        if (not row["rules"] and
                (row["kind"] == "word" and row["expression_key"] in GRAMMATICAL_LEMMAS
                 or row["kind"] == "phrase" and not any(row["start"] <= token.start and token.end <= row["end"] for token in content_tokens))):
            row["priority"] = 5
            row["category"] = "文法的な表現（参考）"
        row["id"] = hashlib.sha256(f"{row['kind']}:{row['start']}:{row['end']}:{row['expression_key']}".encode("utf-8")).hexdigest()
        findings.append(row)
    groups = defaultdict(list)
    for row in findings:
        groups[(row["kind"], row["expression_key"])].append(row)
    for row in findings:
        same = groups[(row["kind"], row["expression_key"])]
        row["input_count"] = len(same)
        row["unique_line_count"] = len({item["line_text"] for item in same})
    findings.sort(key=lambda row: (row["priority"], -row["song_count"], -len(row["surface"]), row["start"], row["id"]))
    return {"format_version": 1, "input": raw, "input_hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "settings": settings, "corpus": [asdict(song) for song in comparison.corpus],
            "rules": [asdict(rule) for rule in detection.rules], "duplicate_song_ids": list(comparison.duplicate_song_ids),
            "versions": {"report": "report-2", "comparison": "comparison-1", "frequency": "frequency-1",
                         "detection": detection.analyzer_version, "tokenizer": detection.tokenizer_version,
                         "dictionary": detection.dictionary_version, "split_mode": "A"}, "findings": findings, "comparisons": comparisons}


def group_findings(findings):
    """Preserve saved ordering and occurrence IDs while grouping equal expressions."""
    groups = {}
    for row in findings:
        groups.setdefault((row["kind"], row["expression_key"]), []).append(row)
    return list(groups.values())


def group_evidence(report, group):
    evidence = {(item["song_id"], item["revision_id"], item["occurrence"]["start"], item["occurrence"]["end"])
                for row in group for match_id in row["comparisons"]
                for item in report["comparisons"][match_id]["evidence"]}
    return len({item[0] for item in evidence}), len(evidence)


def exclusion_for(row, report, exclusions):
    for exclusion in exclusions:
        if not exclusion["enabled"] or exclusion["expression_key"] != row["expression_key"] or exclusion["finding_kind"] != row["kind"]:
            continue
        if (exclusion["scope"] == "global" or
                exclusion["scope"] == "song" and exclusion["song_id"] == report["song_id"] or
                exclusion["scope"] == "run" and exclusion["run_id"] == report["id"]):
            return exclusion
    return None
