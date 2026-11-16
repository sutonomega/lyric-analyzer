from datetime import datetime
import hashlib
import json
from uuid import UUID


MAX_ARCHIVE_BYTES = 10 * 1024 * 1024


def validate_archive(content):
    def text(record, key, nonempty=False):
        value = record.get(key)
        if not isinstance(value, str) or (nonempty and not value.strip()):
            raise ValueError(f"{key} は文字列で指定してください。")
        return value

    def identifier(record, key):
        value = text(record, key)
        try:
            if str(UUID(value)) != value:
                raise ValueError()
        except ValueError:
            raise ValueError(f"{key} のUUIDが不正です。") from None
        return value

    def timestamp(record, key):
        try:
            value = datetime.fromisoformat(text(record, key))
            if value.utcoffset() is None:
                raise ValueError()
        except ValueError:
            raise ValueError(f"{key} はタイムゾーン付き日時で指定してください。") from None

    if not isinstance(content, (str, bytes)):
        raise ValueError("JSONファイルを指定してください。")
    size = len(content.encode("utf-8")) if isinstance(content, str) else len(content)
    if size > MAX_ARCHIVE_BYTES:
        raise ValueError("JSONは10MB以内にしてください。")
    try:
        archive = json.loads(content)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("JSONを読み取れません。UTF-8のファイルを指定してください。") from None
    if not isinstance(archive, dict) or type(archive.get("schema_version")) is not int or archive["schema_version"] != 1:
        raise ValueError("対応していない書き出し形式です。")
    if not isinstance(archive.get("songs"), list):
        raise ValueError("songs はリストで指定してください。")
    song_ids = set()
    revision_ids = set()
    for song in archive["songs"]:
        if not isinstance(song, dict):
            raise ValueError("曲データの形式が不正です。")
        song_id = identifier(song, "id")
        if song_id in song_ids:
            raise ValueError("曲IDが重複しています。")
        song_ids.add(song_id)
        text(song, "title", True)
        text(song, "theme")
        timestamp(song, "created_at")
        timestamp(song, "updated_at")
        tags = song.get("tags")
        if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag.strip() or tag != tag.strip() for tag in tags) or len(set(tags)) != len(tags):
            raise ValueError("タグの形式が不正です。")
        revisions = song.get("revisions")
        if not isinstance(revisions, list) or not revisions:
            raise ValueError("改訂履歴が必要です。")
        for number, revision in enumerate(revisions, start=1):
            if not isinstance(revision, dict):
                raise ValueError("改訂データの形式が不正です。")
            revision_id = identifier(revision, "id")
            if revision_id in revision_ids or revision.get("song_id") != song_id:
                raise ValueError("改訂IDの重複または曲IDの不一致があります。")
            revision_ids.add(revision_id)
            if type(revision.get("revision_number")) is not int or revision["revision_number"] != number:
                raise ValueError("改訂番号は1からの連番で指定してください。")
            lyrics = text(revision, "lyrics_raw", True)
            if revision.get("content_hash") != hashlib.sha256(lyrics.encode("utf-8")).hexdigest():
                raise ValueError("歌詞のハッシュが一致しません。")
            timestamp(revision, "created_at")
    return archive
