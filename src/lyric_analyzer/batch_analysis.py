from lyric_analyzer.analysis.comparison import compare
from lyric_analyzer.analysis.expressions import detect
from lyric_analyzer.analysis.report import build_report


def run_batch(store, corpus, rules, target_ids, include_unknown_tags=False, progress=None):
    if (not isinstance(target_ids, (list, tuple)) or not 1 <= len(target_ids) <= 20
            or len(set(target_ids)) != len(target_ids) or type(include_unknown_tags) is not bool):
        raise ValueError("重複しない対象曲を1〜20曲選んでください。")
    sources = {song.song_id: song for song in corpus}
    if set(target_ids) - set(sources):
        raise ValueError("保存済みの対象曲を選んでください。")
    reports = []
    for index, song_id in enumerate(target_ids):
        song = sources[song_id]
        settings = {"selected_ids": [key for key in sources if key != song_id], "start_date": None, "end_date": None,
                    "include_unknown_tags": include_unknown_tags, "styles": song.styles,
                    "thresholds": {"min_songs": 3, "min_ratio": .2, "small_corpus": 10, "reference_songs": 2},
                    "batch_version": "batch-analysis-1"}
        try:
            comparison = compare(song.lyrics, corpus, song_id, include_unknown_tags=include_unknown_tags)
            detection = detect(song.lyrics, rules, include_unknown_tags)
            reports.append((song_id, build_report(song.lyrics, comparison, detection, settings)))
        except (ValueError, RuntimeError, OSError) as error:
            raise ValueError(f"「{song.title}」の分析を完了できませんでした：{error}。履歴は保存していません。") from error
        if progress:
            progress((index + 1) / len(target_ids))
    return store.save_batch(reports, corpus, rules)
