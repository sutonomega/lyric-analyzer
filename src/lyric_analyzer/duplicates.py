"""Exact duplicate protection and explicit, backed-up consolidation."""
import json
import os
from uuid import uuid4


def duplicate_id(connection, title, styles, lyrics, exclude=None):
    row = connection.execute("""SELECT s.id FROM songs s JOIN song_revisions r ON r.song_id=s.id
        WHERE s.duplicate_archived=0 AND s.title=? AND s.styles=? AND r.lyrics_raw=?
        AND (? IS NULL OR s.id<>?)
        AND r.revision_number=(SELECT MAX(revision_number) FROM song_revisions WHERE song_id=s.id)
        ORDER BY s.created_at,s.id LIMIT 1""", (title, styles, lyrics, exclude, exclude)).fetchone()
    return row[0] if row else None


def signature(connection, song):
    revisions = tuple(row[0] for row in connection.execute("SELECT lyrics_raw FROM song_revisions WHERE song_id=? ORDER BY revision_number", (song['id'],)))
    tags = tuple(row[0] for row in connection.execute("SELECT tag_id FROM song_tags WHERE song_id=? ORDER BY tag_id", (song['id'],)))
    exclusions = tuple(tuple(row) for row in connection.execute("SELECT expression_key,finding_kind,reason,enabled FROM exclusions WHERE scope='song' AND song_id=? ORDER BY expression_key,finding_kind,reason,enabled", (song['id'],)))
    return song['title'], song['styles'], song['theme'], song['composed_on'], tags, revisions, exclusions


def consolidate(library):
    """Remove redundant active entries; keep referenced originals for old history."""
    from lyric_analyzer.backup import export_backup, inspect_backup

    with library.connection() as connection:
        connection.execute('BEGIN IMMEDIATE')
        groups = {}
        # Prefer an existing analysis target so recent candidate history remains easy to find.
        songs = connection.execute('''SELECT s.* FROM songs s WHERE duplicate_archived=0
            ORDER BY (SELECT COUNT(*) FROM analysis_runs WHERE target_song_id=s.id) DESC,s.created_at,s.id''').fetchall()
        redundant = []
        for song in songs:
            key = signature(connection, song)
            if key in groups:
                redundant.append((song['id'], groups[key]))
            else:
                groups[key] = song['id']
        if not redundant:
            return {'removed': 0, 'archived': 0, 'backup': None}
        # No writes have occurred: a separate read connection sees this locked snapshot.
        content = export_backup(library)
        inspect_backup(content)
        directory = library.path.parent / 'backups'
        directory.mkdir(parents=True, exist_ok=True)
        backup = directory / f'before-deduplicate-{uuid4()}.json'
        with backup.open('xb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        archived = 0
        for song_id, _ in redundant:
            references = connection.execute('''SELECT 1 FROM analysis_runs WHERE target_song_id=?
                UNION ALL SELECT 1 FROM analysis_sources WHERE song_id=?
                UNION ALL SELECT 1 FROM exclusions WHERE song_id=? LIMIT 1''', (song_id, song_id, song_id)).fetchone()
            if references:
                connection.execute('UPDATE songs SET duplicate_archived=1 WHERE id=?', (song_id,))
                archived += 1
            else:
                connection.execute('DELETE FROM songs WHERE id=?', (song_id,))
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('重複整理後の参照を確認できません。変更を取り消しました。')
    return {'removed': len(redundant), 'archived': archived, 'backup': str(backup)}
