import json
from uuid import uuid4


class HistoryStore:
    def __init__(self, library):
        self.library = library

    def save(self, report, title, song_id=None):
        run_id = str(uuid4())
        created_at = self.library.now()
        with self.library.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            revision_id = None
            if song_id:
                latest = connection.execute("SELECT id, content_hash FROM song_revisions WHERE song_id=? ORDER BY revision_number DESC LIMIT 1", (song_id,)).fetchone()
                if latest is None:
                    raise ValueError("分析対象の曲が見つかりません。")
                if latest["content_hash"] == report["input_hash"]:
                    revision_id = latest["id"]
            exclusions = [dict(row) for row in connection.execute("SELECT * FROM exclusions WHERE enabled=1 AND (scope='global' OR (scope='song' AND song_id=?)) ORDER BY id", (song_id,))]
            payload = {**report, "id": run_id, "title": title.strip() or "未保存の歌詞", "song_id": song_id,
                       "target_revision_id": revision_id, "created_at": created_at, "exclusions": exclusions}
            connection.execute("INSERT INTO analysis_runs VALUES (?, ?, ?, ?, ?)",
                               (run_id, song_id, revision_id, json.dumps(payload, ensure_ascii=False), created_at))
            for source in report["corpus"]:
                connection.execute("INSERT INTO analysis_sources VALUES (?, ?, ?)", (run_id, source["song_id"], source["revision_id"]))
        return run_id

    def get(self, run_id):
        with self.library.connection() as connection:
            row = connection.execute("SELECT payload_json FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_suggestion(self, run_id, suggestion):
        from lyric_analyzer.local_ai import validate_saved_suggestion

        with self.library.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload_json FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("分析履歴が見つかりません。")
            report = json.loads(row[0])
            validate_saved_suggestion(report, suggestion)
            suggestions = report.setdefault("ai_suggestions", [])
            if len(suggestions) >= 20:
                raise ValueError("1回の分析履歴に保存できるAI候補は20回までです。")
            suggestions.append(suggestion)
            connection.execute("UPDATE analysis_runs SET payload_json=? WHERE id=?", (json.dumps(report, ensure_ascii=False), run_id))

    def list_runs(self):
        with self.library.connection() as connection:
            return [dict(row) for row in connection.execute("SELECT id, created_at, json_extract(payload_json,'$.title') AS title FROM analysis_runs ORDER BY created_at DESC, id")]

    def related_count(self, song_id):
        with self.library.connection() as connection:
            return connection.execute("SELECT COUNT(*) FROM analysis_runs WHERE target_song_id=? OR id IN (SELECT run_id FROM analysis_sources WHERE song_id=?)", (song_id, song_id)).fetchone()[0]

    def list_exclusions(self):
        with self.library.connection() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM exclusions ORDER BY created_at DESC, id")]

    def exclude(self, run_id, finding_id, scope, reason):
        if scope not in {"run", "song", "global"} or not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValueError("除外範囲と1〜2,000文字の理由を指定してください。")
        with self.library.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            stored = connection.execute("SELECT payload_json FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
            if stored is None:
                raise ValueError("分析履歴が見つかりません。")
            report = json.loads(stored[0])
            finding = next((item for item in report["findings"] if item["id"] == finding_id), None)
            if finding is None or scope == "song" and report["song_id"] is None:
                raise ValueError("この指摘には曲単位の除外を設定できません。曲を保存して再分析してください。")
            scoped_run = run_id if scope == "run" else None
            scoped_song = report["song_id"] if scope == "song" else None
            existing = connection.execute("SELECT id FROM exclusions WHERE scope=? AND run_id IS ? AND song_id IS ? AND expression_key=? AND finding_kind=?",
                                          (scope, scoped_run, scoped_song, finding["expression_key"], finding["kind"])).fetchone()
            if existing:
                connection.execute("UPDATE exclusions SET reason=?, enabled=1 WHERE id=?", (reason.strip(), existing[0]))
                return existing[0]
            exclusion_id = str(uuid4())
            connection.execute("INSERT INTO exclusions VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
                               (exclusion_id, scope, scoped_song, scoped_run, finding["expression_key"], finding["kind"], reason.strip(), self.library.now()))
            return exclusion_id

    def restore(self, exclusion_id):
        with self.library.connection() as connection:
            changed = connection.execute("UPDATE exclusions SET enabled=0 WHERE id=?", (exclusion_id,))
            if not changed.rowcount:
                raise ValueError("除外設定が見つかりません。")

    def reviews(self, run_id):
        with self.library.connection() as connection:
            return {row["finding_id"]: dict(row) for row in connection.execute("SELECT * FROM finding_reviews WHERE run_id=?", (run_id,))}

    def save_reviews(self, run_id, reviews):
        if not isinstance(reviews, list):
            raise ValueError("評価の形式が不正です。")
        with self.library.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload_json FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("分析履歴が見つかりません。")
            finding_ids = {item["id"] for item in json.loads(row[0])["findings"]}
            for review in reviews:
                if (review.get("finding_id") not in finding_ids or review.get("label") not in {None, "useful", "intentional", "false_positive"}
                        or not isinstance(review.get("note"), str) or len(review["note"]) > 2000):
                    raise ValueError("評価対象・分類・メモを確認してください。")
            for review in reviews:
                if review["label"] is None:
                    connection.execute("DELETE FROM finding_reviews WHERE run_id=? AND finding_id=?", (run_id, review["finding_id"]))
                else:
                    connection.execute("INSERT INTO finding_reviews VALUES (?, ?, ?, ?, ?) ON CONFLICT(run_id,finding_id) DO UPDATE SET label=excluded.label,note=excluded.note,updated_at=excluded.updated_at",
                                       (run_id, review["finding_id"], review["label"], review["note"], self.library.now()))
