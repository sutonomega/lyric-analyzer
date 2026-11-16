from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
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
)


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
                    connection.execute("INSERT INTO schema_migrations VALUES (?, ?)", (version, self.now()))

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def save(self, title, lyrics, theme="", song_id=None):
        if not title.strip() or not lyrics.strip():
            raise ValueError("曲名と歌詞を入力してください。")
        timestamp = self.now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if song_id is None:
                song_id = str(uuid4())
                connection.execute("INSERT INTO songs VALUES (?, ?, ?, ?, ?)", (song_id, title.strip(), theme, timestamp, timestamp))
            else:
                changed = connection.execute("UPDATE songs SET title=?, theme=?, updated_at=? WHERE id=?", (title.strip(), theme, timestamp, song_id))
                if changed.rowcount != 1:
                    raise ValueError("曲が見つかりません。")
            latest = connection.execute("SELECT * FROM song_revisions WHERE song_id=? ORDER BY revision_number DESC LIMIT 1", (song_id,)).fetchone()
            if latest is None or latest["lyrics_raw"] != lyrics:
                number = latest["revision_number"] + 1 if latest else 1
                connection.execute("INSERT INTO song_revisions VALUES (?, ?, ?, ?, ?, ?)", (str(uuid4()), song_id, number, lyrics, hashlib.sha256(lyrics.encode("utf-8")).hexdigest(), timestamp))
        return song_id

    def list_songs(self, query=""):
        with self.connection() as connection:
            rows = connection.execute("SELECT songs.*, song_revisions.lyrics_raw, song_revisions.revision_number FROM songs JOIN song_revisions ON songs.id=song_revisions.song_id WHERE song_revisions.revision_number=(SELECT MAX(revision_number) FROM song_revisions WHERE song_id=songs.id) ORDER BY songs.updated_at DESC").fetchall()
            return [dict(row) for row in rows if query.casefold() in (row["title"] + "\n" + row["lyrics_raw"] + "\n" + row["theme"]).casefold()]

    def revisions(self, song_id):
        with self.connection() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM song_revisions WHERE song_id=? ORDER BY revision_number DESC", (song_id,))]

    def delete(self, song_id):
        with self.connection() as connection:
            connection.execute("DELETE FROM songs WHERE id=?", (song_id,))
