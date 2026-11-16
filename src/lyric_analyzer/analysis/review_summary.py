from collections import defaultdict
import hashlib
import json

from lyric_analyzer.analysis.candidate_quality import CHECKS, DIRECTIONS, validate_review


def condition_id(record):
    return json.dumps([record["provider"], record["model"], record["prompt_version"]], ensure_ascii=False)


def target_key(record):
    return hashlib.sha256(json.dumps([record["line_id"], record["theme"]], ensure_ascii=False).encode()).hexdigest()


def select_records(records, selected=None, common_only=False):
    selected = set(condition_id(record) for record in records) if selected is None else set(selected)
    chosen = [record for record in records if condition_id(record) in selected]
    inputs = {condition: set() for condition in selected}
    for record in chosen:
        inputs[condition_id(record)].add(target_key(record))
    common = set.intersection(*inputs.values()) if len(inputs) >= 2 else set()
    if common_only:
        chosen = [record for record in chosen if target_key(record) in common]
    return chosen, len(common)


def summarize(records):
    decisions = defaultdict(list)
    meaning = defaultdict(list)
    for record in records:
        review = record.get("review")
        if review is not None:
            validate_review(review)
        condition = (record["provider"], record["model"], record["prompt_version"])
        for direction in DIRECTIONS:
            verdict = review["decisions"][direction]["verdict"] if review else "unreviewed"
            decisions[(*condition, direction)].append((record, verdict))
        for check in CHECKS:
            value = review["meaning_checks"][check] if review else "unchecked"
            meaning[(*condition, check)].append(value)
    groups = []
    for key, items in sorted(decisions.items()):
        verdicts = [value for _, value in items]
        evaluated = sum(value != "unreviewed" for value in verdicts)
        usable = verdicts.count("usable")
        groups.append({"provider": key[0], "model": key[1], "prompt_version": key[2], "direction": key[3],
                       "generations": len(items), "targets": len({target_key(record) for record, _ in items}),
                       "evaluated": evaluated, "unreviewed": verdicts.count("unreviewed"),
                       "usable": usable, "edit": verdicts.count("edit"), "reject": verdicts.count("reject"),
                       "usable_ratio": usable / evaluated if evaluated else None})
    checks = [{"provider": key[0], "model": key[1], "prompt_version": key[2], "check": key[3],
               "checked": sum(value != "unchecked" for value in items), "unchecked": items.count("unchecked"),
               "kept": items.count("kept"), "changed": items.count("changed")}
              for key, items in sorted(meaning.items(), key=lambda pair: (*pair[0][:3], CHECKS.index(pair[0][3])))]
    return {"generation_count": len(records), "target_count": len({target_key(record) for record in records}),
            "candidate_count": len(records) * len(DIRECTIONS), "groups": groups, "meaning_checks": checks}
