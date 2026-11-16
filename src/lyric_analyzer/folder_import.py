from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from uuid import uuid4


MAX_FILES = 500
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
SUFFIXES = frozenset({".txt", ".md", ".markdown"})
HEADER = re.compile(r"^\s*(?:#{1,6}\s+)?(Title|Styles|Lyrics)(?:\s*[:：]\s*(.*)|\s*)$", re.IGNORECASE)
BOLD_HEADER = re.compile(r"^\s*(?:#{1,6}\s+)?\*\*(Title|Styles|Lyrics)(?:[:：]\*\*|\*\*[:：]?)\s*(.*)$", re.IGNORECASE)
BRACKET_HEADER = re.compile(r"^\s*\[(Title|Styles|Lyrics)\]\s*(?:[:：]\s*)?(.*?)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class ImportRecord:
    title: str
    styles: str
    lyrics: str
    source: str
    encoding: str

    @property
    def id(self):
        return hashlib.sha256(json.dumps([self.source, self.title, self.styles, self.lyrics], ensure_ascii=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FolderPreview:
    label: str
    records: tuple[ImportRecord, ...]
    errors: tuple[tuple[str, str], ...]


def decode_text(content, encoding="auto"):
    if encoding not in {"auto", "utf-8-sig", "cp932"}:
        raise ValueError("対応する文字コードを指定してください。")
    choices = [encoding] if encoding != "auto" else ["utf-16"] if content.startswith((b"\xff\xfe", b"\xfe\xff")) else ["utf-8-sig", "cp932"]
    for candidate in choices:
        try:
            return content.decode(candidate), candidate
        except UnicodeError:
            continue
    raise ValueError("文字コードを読み取れません。UTF-8かCP932を指定し直してください。")


def parse_file(content: bytes, source: str, encoding="auto") -> ImportRecord:
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("1ファイルは2MB以内にしてください。")
    text, decoded_encoding = decode_text(content, encoding)
    sections = {}
    current = None
    for line in text.splitlines(keepends=True):
        if current == "lyrics":
            sections[current].append(line)
            continue
        body = line.rstrip("\r\n")
        match = BOLD_HEADER.fullmatch(body) or HEADER.fullmatch(body) or BRACKET_HEADER.fullmatch(body)
        if match:
            current = match.group(1).casefold()
            if current in sections:
                raise ValueError(f"{current}の見出しが重複しています。1ファイル1曲で保存してください。")
            sections[current] = []
            inline = match.group(2)
            if inline:
                ending = line[len(body):]
                sections[current].append(inline + ending)
        elif current is not None:
            sections[current].append(line)
        elif body.strip():
            raise ValueError("Title・Styles・Lyricsの見出し付きファイルを指定してください。")
    if set(sections) != {"title", "styles", "lyrics"}:
        raise ValueError("Title・Styles・Lyricsの3つの見出しが必要です。Lyricsは最後に置いてください。")
    title = "".join(sections["title"]).strip()
    styles = "".join(sections["styles"]).strip()
    lyrics = "".join(sections["lyrics"])
    if not title or len(title.splitlines()) != 1:
        raise ValueError("Titleは空でない1行にしてください。")
    if not lyrics.strip():
        raise ValueError("Lyricsが空です。")
    if len(lyrics) > 20_000:
        raise ValueError("Lyricsは20,000文字以内にしてください。")
    return ImportRecord(title, styles, lyrics, source, decoded_encoding)


def preview_files(files, label="選択したフォルダー", encoding="auto"):
    records, errors = [], []
    total = 0
    count = 0
    for name, content in files:
        if Path(name).suffix.casefold() not in SUFFIXES:
            continue
        count += 1
        total += len(content)
        if count > MAX_FILES or total > MAX_TOTAL_BYTES:
            raise ValueError("1回の確認は500ファイル・合計20MBまでです。フォルダーを分けてください。")
        try:
            records.append(parse_file(content, name, encoding))
        except ValueError as error:
            errors.append((name, str(error)))
    return FolderPreview(label, tuple(records), tuple(errors))


def scan_folder(folder, recursive=False, encoding="auto"):
    root = Path(folder.strip().strip('"')).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("フォルダーを指定してください。")
    records, errors = [], []
    queue = [root]
    visited = count = total = 0
    while queue:
        directory = queue.pop()
        visited += 1
        if visited > 5000:
            raise ValueError("子フォルダーが多すぎます。対象を絞ってください。")
        for path in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            name = path.relative_to(root).as_posix()
            try:
                if path.is_symlink() or path.is_junction() or not path.resolve(strict=True).is_relative_to(root):
                    continue
                if path.is_dir():
                    if recursive:
                        queue.append(path)
                    continue
                if path.suffix.casefold() not in SUFFIXES or not path.is_file():
                    continue
                count += 1
                if count > MAX_FILES:
                    raise ValueError("1回の確認は500ファイルまでです。フォルダーを分けてください。")
                with path.open("rb") as source:
                    content = source.read(MAX_FILE_BYTES + 1)
                total += len(content)
                if total > MAX_TOTAL_BYTES:
                    raise ValueError("合計20MBを超えています。フォルダーを分けてください。")
                try:
                    records.append(parse_file(content, name, encoding))
                except ValueError as error:
                    errors.append((name, str(error)))
            except OSError as error:
                errors.append((name, f"読み取れません：{error}"))
    return FolderPreview(str(root), tuple(records), tuple(errors))


def plan_import(records, existing, allow_variants=False):
    seen = list(existing)
    planned = []
    for record in records:
        if (not isinstance(record, ImportRecord) or any(not isinstance(value, str) for value in (record.title, record.styles, record.lyrics))
                or not record.title.strip() or not record.lyrics.strip() or len(record.lyrics) > 20_000):
            raise ValueError("取り込みデータを確認してください。")
        exact = any(song["title"] == record.title and song["styles"] == record.styles and song["lyrics_raw"] == record.lyrics for song in seen)
        title_key = unicodedata.normalize("NFKC", record.title).casefold()
        same_title = any(unicodedata.normalize("NFKC", song["title"]).casefold() == title_key for song in seen)
        same_lyrics = any(song["lyrics_raw"] == record.lyrics for song in seen)
        status = "同一内容" if exact else "同名・内容違い" if same_title else "同じ歌詞の別曲" if same_lyrics else "新規"
        add = not exact and (status == "新規" or allow_variants)
        planned.append((record, status, add))
        if add:
            seen.append({"title": record.title, "styles": record.styles, "lyrics_raw": record.lyrics})
    return planned


def import_records(library, records, allow_variants=False):
    if type(allow_variants) is not bool:
        raise ValueError("同名曲の扱いを指定してください。")
    records = tuple(records)
    if not records or len(records) > MAX_FILES:
        raise ValueError("取り込む曲を1〜500件選択してください。")
    with library.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = [dict(row) for row in connection.execute("SELECT title,styles,lyrics_raw FROM songs JOIN song_revisions ON songs.id=song_id WHERE revision_number=(SELECT MAX(revision_number) FROM song_revisions WHERE song_id=songs.id)")]
        planned = plan_import(records, existing, allow_variants)
        added = skipped = 0
        for record, _, add in planned:
            if not add:
                skipped += 1
                continue
            song_id, revision_id, timestamp = str(uuid4()), str(uuid4()), library.now()
            connection.execute("INSERT INTO songs (id,title,theme,created_at,updated_at,styles) VALUES (?,?,'',?,?,?)",
                               (song_id, record.title, timestamp, timestamp, record.styles))
            connection.execute("INSERT INTO song_revisions VALUES (?,?,1,?,?,?)",
                               (revision_id, song_id, record.lyrics, hashlib.sha256(record.lyrics.encode("utf-8")).hexdigest(), timestamp))
            added += 1
    return added, skipped
