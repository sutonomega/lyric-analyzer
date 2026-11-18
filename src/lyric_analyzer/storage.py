from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4


MIGRATIONS = (
    (
        "CREATE TABLE songs (id TEXT PRIMARY KEY, title TEXT NOT NULL, theme TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
        "CREATE TABLE song_revisions (id TEXT PRIMARY KEY, song_id TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE, revision_number INTEGER NOT NULL CHECK(revision_number > 0), lyrics_raw TEXT NOT NULL, content_hash TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(song_id, revision_number))",
        "CREATE INDEX revisions_song ON song_revisions(song_id, revision_number)",
    ),
    (
        "CREATE TABLE tags (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE)",
        "CREATE TABLE song_tags (song_id TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE, tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE, PRIMARY KEY(song_id, tag_id))",
        "CREATE INDEX song_tags_tag ON song_tags(tag_id)",
    ),
    (
        "CREATE TABLE expression_rules (id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('literal','abstract_loss')), pattern_json TEXT NOT NULL, reason TEXT NOT NULL, review_hint TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), version INTEGER NOT NULL CHECK(version > 0))",
    ),
    (
        "CREATE TABLE analysis_runs (id TEXT PRIMARY KEY, target_song_id TEXT REFERENCES songs(id) ON DELETE CASCADE, target_revision_id TEXT REFERENCES song_revisions(id) ON DELETE CASCADE, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE analysis_sources (run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE, song_id TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE, revision_id TEXT NOT NULL REFERENCES song_revisions(id) ON DELETE CASCADE, PRIMARY KEY(run_id, song_id))",
        "CREATE INDEX analysis_sources_song ON analysis_sources(song_id)",
        "CREATE TABLE exclusions (id TEXT PRIMARY KEY, scope TEXT NOT NULL CHECK(scope IN ('run','song','global')), song_id TEXT REFERENCES songs(id) ON DELETE CASCADE, run_id TEXT REFERENCES analysis_runs(id) ON DELETE CASCADE, expression_key TEXT NOT NULL, finding_kind TEXT NOT NULL CHECK(finding_kind IN ('phrase','word')), reason TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), created_at TEXT NOT NULL, CHECK((scope='run' AND run_id IS NOT NULL AND song_id IS NULL) OR (scope='song' AND song_id IS NOT NULL AND run_id IS NULL) OR (scope='global' AND song_id IS NULL AND run_id IS NULL)))",
        "CREATE INDEX analysis_runs_target ON analysis_runs(target_song_id)",
        "CREATE TRIGGER delete_related_analysis BEFORE DELETE ON songs BEGIN DELETE FROM analysis_runs WHERE target_song_id=OLD.id OR id IN (SELECT run_id FROM analysis_sources WHERE song_id=OLD.id); END",
    ),
    (
        "CREATE TABLE finding_reviews (run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE, finding_id TEXT NOT NULL, label TEXT NOT NULL CHECK(label IN ('useful','intentional','false_positive')), note TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(run_id,finding_id))",
    ),
    (
        "ALTER TABLE songs ADD COLUMN styles TEXT NOT NULL DEFAULT ''",
    ),
    (
        "ALTER TABLE songs ADD COLUMN composed_on TEXT NOT NULL DEFAULT ''",
    ),
    (
        "ALTER TABLE songs ADD COLUMN duplicate_archived INTEGER NOT NULL DEFAULT 0 CHECK(duplicate_archived IN (0,1))",
    ),
)


def validate_composed_on(value):
    from datetime import date

    if not isinstance(value, str):
        raise ValueError("制作日はYYYY-MM-DD、または不明なら空欄で指定してください。")
    if value:
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError()
        except ValueError:
            raise ValueError("制作日はYYYY-MM-DDの実在する日付で指定してください。") from None
    return value


def default_database_path():
    override = os.environ.get("LYRIC_ANALYZER_DATA_DIR")
    base = Path(override) if override else Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "LyricAnalyzer"
    return base / "library.db"


class Library:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def migrate(self):
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            versions = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
            if versions - set(range(1, len(MIGRATIONS) + 1)):
                raise ValueError("このデータベースは新しいバージョンのアプリで作成されています。")
            for version, statements in enumerate(MIGRATIONS, start=1):
                if version not in versions:
                    for statement in statements:
                        connection.execute(statement)
                    if version == 3:
                        from lyric_analyzer.analysis.expressions import default_rules

                        for rule in default_rules():
                            connection.execute("INSERT INTO expression_rules VALUES (?, ?, ?, ?, ?, ?, ?)",
                                               (rule.id, rule.kind, rule.pattern_json, rule.reason, rule.review_hint, int(rule.enabled), rule.version))
                    connection.execute("INSERT INTO schema_migrations VALUES (?, ?)", (version, self.now()))

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def save(self, title, lyrics, theme="", song_id=None, tags=None, styles=None, composed_on=None):
        if not title.strip() or not lyrics.strip():
            raise ValueError("曲名と歌詞を入力してください。")
        if styles is not None and not isinstance(styles, str):
            raise ValueError("Stylesは文字列で入力してください。")
        if composed_on is not None:
            validate_composed_on(composed_on)
        timestamp = self.now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            from lyric_analyzer.duplicates import duplicate_id
            effective_styles = styles or ""
            if song_id is not None and styles is None:
                current = connection.execute("SELECT styles FROM songs WHERE id=? AND duplicate_archived=0", (song_id,)).fetchone()
                if current is None:
                    raise ValueError("曲が見つかりません。")
                effective_styles = current[0]
            if duplicate_id(connection, title.strip(), effective_styles, lyrics, song_id):
                raise ValueError("曲名・Styles・歌詞が完全一致する曲が登録済みです。既存の曲を選んで編集してください。")
            if song_id is None:
                song_id = str(uuid4())
                connection.execute("INSERT INTO songs (id,title,theme,created_at,updated_at,styles) VALUES (?, ?, ?, ?, ?, ?)", (song_id, title.strip(), theme, timestamp, timestamp, styles or ""))
            else:
                changed = connection.execute("UPDATE songs SET title=?, theme=?, updated_at=?, styles=COALESCE(?,styles) WHERE id=? AND duplicate_archived=0", (title.strip(), theme, timestamp, styles, song_id))
                if changed.rowcount != 1:
                    raise ValueError("曲が見つかりません。")
            latest = connection.execute("SELECT * FROM song_revisions WHERE song_id=? ORDER BY revision_number DESC LIMIT 1", (song_id,)).fetchone()
            if latest is None or latest["lyrics_raw"] != lyrics:
                number = latest["revision_number"] + 1 if latest else 1
                connection.execute("INSERT INTO song_revisions VALUES (?, ?, ?, ?, ?, ?)", (str(uuid4()), song_id, number, lyrics, hashlib.sha256(lyrics.encode("utf-8")).hexdigest(), timestamp))
            if tags is not None:
                self.set_tags(connection, song_id, tags)
            if composed_on is not None:
                connection.execute("UPDATE songs SET composed_on=? WHERE id=?", (composed_on, song_id))
        return song_id

    @staticmethod
    def set_tags(connection, song_id, tags):
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ValueError("タグは文字列のリストで指定してください。")
        connection.execute("DELETE FROM song_tags WHERE song_id=?", (song_id,))
        for name in sorted({tag.strip() for tag in tags if tag.strip()}):
            connection.execute("INSERT OR IGNORE INTO tags VALUES (?, ?)", (str(uuid4()), name))
            connection.execute("INSERT INTO song_tags SELECT ?, id FROM tags WHERE name=?", (song_id, name))

    def list_songs(self, query="", tag=None):
        with self.connection() as connection:
            rows = connection.execute("SELECT songs.*, song_revisions.lyrics_raw, song_revisions.revision_number FROM songs JOIN song_revisions ON songs.id=song_revisions.song_id WHERE songs.duplicate_archived=0 AND song_revisions.revision_number=(SELECT MAX(revision_number) FROM song_revisions WHERE song_id=songs.id) ORDER BY songs.updated_at DESC").fetchall()
            songs = []
            for row in rows:
                song = dict(row)
                song["tags"] = [item[0] for item in connection.execute("SELECT tags.name FROM tags JOIN song_tags ON tags.id=song_tags.tag_id WHERE song_id=? ORDER BY tags.name", (song["id"],))]
                searchable = "\n".join([song["title"], song["lyrics_raw"], song["theme"], song["styles"], *song["tags"]])
                if query.casefold() in searchable.casefold() and (tag is None or tag in song["tags"]):
                    songs.append(song)
            return songs

    def export_json(self):
        with self.connection() as connection:
            connection.execute("BEGIN")
            songs = []
            for row in connection.execute("SELECT * FROM songs WHERE duplicate_archived=0 ORDER BY id"):
                song = dict(row)
                song["tags"] = [item[0] for item in connection.execute("SELECT name FROM tags JOIN song_tags ON tags.id=tag_id WHERE song_id=? ORDER BY name", (song["id"],))]
                song["revisions"] = [dict(item) for item in connection.execute("SELECT * FROM song_revisions WHERE song_id=? ORDER BY revision_number", (song["id"],))]
                songs.append(song)
        return json.dumps({"schema_version": 1, "songs": songs}, ensure_ascii=False, indent=2)

    def comparison_corpus(self):
        from lyric_analyzer.analysis.comparison import SongSnapshot

        with self.connection() as connection:
            rows = connection.execute("SELECT songs.id AS song_id, songs.title, songs.styles, songs.created_at, song_revisions.id AS revision_id, revision_number, lyrics_raw, content_hash FROM songs JOIN song_revisions ON song_revisions.song_id=songs.id WHERE songs.duplicate_archived=0 AND revision_number=(SELECT MAX(revision_number) FROM song_revisions WHERE song_id=songs.id) ORDER BY songs.id").fetchall()
            return tuple(SongSnapshot(row["song_id"], row["title"], row["revision_id"], row["revision_number"], row["lyrics_raw"], row["content_hash"], row["created_at"], row["styles"]) for row in rows)

    def import_json(self, content, conflict="skip"):
        from lyric_analyzer.transfer import validate_archive
        from lyric_analyzer.duplicates import duplicate_id, signature

        archive = validate_archive(content)
        if conflict not in {"skip", "copy"}:
            raise ValueError("重複時の処理を選択してください。")
        imported = skipped = 0
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for song in archive["songs"]:
                exists = connection.execute("SELECT 1 FROM songs WHERE id=?", (song["id"],)).fetchone()
                if exists and conflict == "skip":
                    skipped += 1
                    continue
                duplicate = duplicate_id(connection, song['title'], song['styles'], song['revisions'][-1]['lyrics_raw'])
                if duplicate:
                    stored = connection.execute('SELECT * FROM songs WHERE id=?', (duplicate,)).fetchone()
                    old = signature(connection, stored)
                    tags = tuple(row[0] for row in connection.execute('SELECT name FROM tags JOIN song_tags ON tags.id=tag_id WHERE song_id=? ORDER BY name', (duplicate,)))
                    if (old[2] != song['theme'] or old[3] != song['composed_on'] or tags != tuple(sorted(song['tags']))
                            or old[5] != tuple(r['lyrics_raw'] for r in song['revisions'])):
                        raise ValueError('同じ曲名・Styles・歌詞の曲がありますが、制作日・タグ・改訂などが異なります。既存曲を確認して編集してください。取り込みは取り消しました。')
                    skipped += 1
                    continue
                song_id = str(uuid4()) if exists else song["id"]
                connection.execute("INSERT INTO songs (id,title,theme,created_at,updated_at,styles) VALUES (?, ?, ?, ?, ?, ?)", (song_id, song["title"], song["theme"], song["created_at"], song["updated_at"], song["styles"]))
                connection.execute("UPDATE songs SET composed_on=? WHERE id=?", (song["composed_on"], song_id))
                for revision in song["revisions"]:
                    revision_id = str(uuid4()) if exists else revision["id"]
                    connection.execute("INSERT INTO song_revisions VALUES (?, ?, ?, ?, ?, ?)", (revision_id, song_id, revision["revision_number"], revision["lyrics_raw"], revision["content_hash"], revision["created_at"]))
                self.set_tags(connection, song_id, song["tags"])
                imported += 1
        return imported, skipped

    def revisions(self, song_id):
        with self.connection() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM song_revisions WHERE song_id=? ORDER BY revision_number DESC", (song_id,))]

    def list_rules(self):
        from lyric_analyzer.analysis.expressions import ExpressionRule

        with self.connection() as connection:
            return tuple(ExpressionRule(row["id"], row["kind"], row["pattern_json"], row["reason"], row["review_hint"], bool(row["enabled"]), row["version"])
                         for row in connection.execute("SELECT * FROM expression_rules ORDER BY kind, id"))

    def save_rule(self, kind, pattern, reason, review_hint, enabled=True, rule_id=None, expected_version=None):
        from lyric_analyzer.analysis.expressions import validate_rule

        pattern_json = validate_rule(kind, pattern, reason, review_hint, enabled)
        values = (kind, pattern_json, reason.strip(), review_hint.strip(), int(enabled))
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if rule_id is None:
                rule_id = str(uuid4())
                connection.execute("INSERT INTO expression_rules VALUES (?, ?, ?, ?, ?, ?, 1)", (rule_id, *values))
            else:
                existing = connection.execute("SELECT * FROM expression_rules WHERE id=?", (rule_id,)).fetchone()
                if existing is None or existing["version"] != expected_version:
                    raise ValueError("辞書が変更されたか見つかりません。画面を更新して確認してください。")
                previous = tuple(existing[key] for key in ("kind", "pattern_json", "reason", "review_hint", "enabled"))
                if previous != values:
                    connection.execute("UPDATE expression_rules SET kind=?, pattern_json=?, reason=?, review_hint=?, enabled=?, version=version+1 WHERE id=?", (*values, rule_id))
        return rule_id

    def delete(self, song_id):
        with self.connection() as connection:
            connection.execute("DELETE FROM songs WHERE id=?", (song_id,))

    def update_composition_dates(self, changes):
        if not isinstance(changes, list) or not 1 <= len(changes) <= 2000:
            raise ValueError("変更する曲は1〜2,000曲で指定してください。")
        ids = set()
        for change in changes:
            if (not isinstance(change, dict) or set(change) != {"song_id", "before", "after", "updated_at", "title"}
                    or any(not isinstance(value, str) for value in change.values())
                    or not change["song_id"] or change["song_id"] in ids):
                raise ValueError("変更する曲と日付の形式を確認してください。")
            ids.add(change["song_id"])
            validate_composed_on(change["before"])
            validate_composed_on(change["after"])
            if change["before"] == change["after"]:
                raise ValueError("変更のある曲だけを指定してください。")
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for change in changes:
                row = connection.execute("SELECT title,composed_on,updated_at FROM songs WHERE id=? AND duplicate_archived=0", (change["song_id"],)).fetchone()
                if row is None or tuple(row) != (change["title"], change["before"], change["updated_at"]):
                    raise ValueError("曲が変更または削除されています。一覧を読み直して確認してください。変更は保存していません。")
            timestamp = self.now()
            for change in changes:
                connection.execute("UPDATE songs SET composed_on=?,updated_at=? WHERE id=?", (change["after"], timestamp, change["song_id"]))
        return len(changes)
