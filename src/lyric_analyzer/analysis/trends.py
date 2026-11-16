from collections import Counter, defaultdict

from lyric_analyzer.storage import validate_composed_on


VERSION = "vocabulary-trends-1"


def date_groups(corpus, dates, unit="month"):
    if unit not in ("month", "year"):
        raise ValueError("期間の単位を確認してください。")
    groups = defaultdict(set)
    unknown = []
    for song in corpus:
        value = validate_composed_on(dates.get(song.song_id, ""))
        if value:
            groups[value[:7] if unit == "month" else value[:4]].add(song.song_id)
        else:
            unknown.append(song.song_id)
    return dict(sorted(groups.items())), unknown


def expression_trend(index, row, dates, unit="month"):
    groups, unknown = date_groups(index.corpus, dates, unit)
    counts = Counter(item.song_id for item in row.evidence)
    lines = defaultdict(set)
    for item in row.evidence:
        lines[item.song_id].add(item.occurrence.line_text)
    periods = []
    for period, ids in groups.items():
        used = sorted(ids.intersection(counts))
        periods.append({"period": period, "total_songs": len(ids), "using_songs": len(used),
                        "song_share": len(used) / len(ids), "occurrences": sum(counts[key] for key in ids),
                        "unique_song_lines": sum(len(lines[key]) for key in ids), "song_ids": used})
    return {"format": VERSION, "unit": unit, "date_basis": "composed_on", "versions": index.versions,
            "mode": index.mode, "expression": row.expression, "pos": row.pos,
            "unknown_song_ids": unknown, "periods": periods,
            "sources": [{"song_id": song.song_id, "title": song.title, "revision_id": song.revision_id,
                         "revision_number": song.revision_number, "content_hash": song.content_hash,
                         "composed_on": dates.get(song.song_id, "")} for song in index.corpus],
            "evidence": [{"song_id": item.song_id, "revision_id": item.revision_id,
                          "line_number": item.occurrence.line_number, "start": item.occurrence.start,
                          "end": item.occurrence.end, "surface": item.occurrence.surface,
                          "line_text": item.occurrence.line_text} for item in row.evidence]}
