def summarize(records, version="report-2", latest_only=True):
    if type(latest_only) is not bool:
        raise ValueError("分析履歴の集計方法を確認してください。")
    chosen = sorted((dict(item) for item in records if item["report_version"] == version),
                    key=lambda item: (item["created_at"], item["run_id"]), reverse=True)
    if latest_only:
        seen = set()
        latest = []
        for item in chosen:
            key = ("song", item["song_id"]) if item["song_id"] else ("unsaved", item["input_hash"])
            if key not in seen:
                latest.append(item)
                seen.add(key)
        chosen = latest
    for item in chosen:
        item["evaluated"] = item["useful"] + item["intentional"] + item["false_positive"]
        item["unreviewed"] = item["eligible"] - item["evaluated"]
        item["useful_ratio"] = item["useful"] / item["evaluated"] if item["evaluated"] else None
    counts = {key: sum(item[key] for item in chosen) for key in ("eligible", "evaluated", "unreviewed", "useful", "intentional", "false_positive")}
    return {"format": "finding-review-progress-1", "report_version": version, "latest_only": latest_only,
            "runs": chosen, **counts,
            "saved_songs": len({item["song_id"] for item in chosen if item["song_id"]}),
            "reviewed_saved_songs": len({item["song_id"] for item in chosen if item["song_id"] and item["evaluated"]}),
            "unsaved_inputs": len({item["input_hash"] for item in chosen if not item["song_id"]}),
            "complete_runs": sum(item["eligible"] > 0 and item["unreviewed"] == 0 for item in chosen),
            "empty_runs": sum(item["eligible"] == 0 for item in chosen),
            "useful_ratio": counts["useful"] / counts["evaluated"] if counts["evaluated"] else None}
