from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
import sqlite3
from uuid import uuid4

from lyric_analyzer.analysis.expressions import validate_rule
from lyric_analyzer.storage import MIGRATIONS


TABLES = ("schema_migrations", "songs", "song_revisions", "tags", "song_tags", "expression_rules",
          "analysis_runs", "analysis_sources", "exclusions", "finding_reviews")
MAX_BACKUP_BYTES = 100 * 1024 * 1024


def checksum(tables):
    return hashlib.sha256(json.dumps(tables, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def export_backup(library):
    snapshot = sqlite3.connect(":memory:")
    snapshot.row_factory = sqlite3.Row
    try:
        with library.connection() as source:
            source.backup(snapshot)
        tables = {}
        for table in TABLES:
            columns = [row[1] for row in snapshot.execute(f"PRAGMA table_info({table})")]
            tables[table] = [dict(row) for row in snapshot.execute(f"SELECT * FROM {table} ORDER BY {','.join(columns)}")]
        archive = {"format": "lyric-analyzer-backup", "version": 1, "schema_version": len(MIGRATIONS),
                   "created_at": library.now(), "tables": tables, "sha256": checksum(tables)}
        content = json.dumps(archive, ensure_ascii=False, indent=2).encode("utf-8")
        if len(content) > MAX_BACKUP_BYTES:
            raise ValueError("全データが100MBを超えています。JSONバックアップの上限を確認してください。")
        return content
    finally:
        snapshot.close()


def _validate_report(report):
    if not isinstance(report, dict):
        raise ValueError("分析履歴の形式が不正です。")
    for key in ("id", "title", "input", "input_hash", "created_at"):
        if not isinstance(report[key], str):
            raise ValueError("分析履歴の文字項目が不正です。")
    for key in ("settings", "versions", "comparisons"):
        if not isinstance(report[key], dict):
            raise ValueError("分析条件の形式が不正です。")
    for key in ("corpus", "rules", "findings", "exclusions", "duplicate_song_ids"):
        if not isinstance(report[key], list):
            raise ValueError("分析履歴のリスト項目が不正です。")
    for rule in report["rules"]:
        validate_rule(rule["kind"], json.loads(rule["pattern_json"]), rule["reason"], rule["review_hint"], rule["enabled"])
    for finding in report["findings"]:
        for key in ("id", "surface", "expression_key", "kind", "category", "line_text"):
            if not isinstance(finding[key], str):
                raise ValueError("指摘の文字項目が不正です。")
        for key in ("start", "end", "line_number", "song_count", "past_count", "priority", "input_count", "unique_line_count"):
            if type(finding[key]) is not int or finding[key] < 0:
                raise ValueError("指摘の数値項目が不正です。")
        if finding["kind"] not in {"word", "phrase"} or not isinstance(finding["comparisons"], list) or not isinstance(finding["rules"], list):
            raise ValueError("指摘の種類が不正です。")
        for rule_finding in finding["rules"]:
            rule = rule_finding["rule"]
            validate_rule(rule["kind"], json.loads(rule["pattern_json"]), rule["reason"], rule["review_hint"], rule["enabled"])
            if not isinstance(rule_finding["match_kind"], str) or not isinstance(rule["id"], str) or type(rule["version"]) is not int:
                raise ValueError("指摘のルール情報が不正です。")
            occurrence = rule_finding["occurrence"]
            if report["input"][occurrence["start"]:occurrence["end"]] != occurrence["surface"]:
                raise ValueError("ルールの原文位置が不正です。")
    sources = {source["song_id"]: source for source in report["corpus"]}
    for match in report["comparisons"].values():
        for key in ("mode", "expression", "status", "pos"):
            if not isinstance(match[key], str):
                raise ValueError("比較条件が不正です。")
        for evidence in match["evidence"]:
            source = sources[evidence["song_id"]]
            occurrence = evidence["occurrence"]
            if source["revision_id"] != evidence["revision_id"] or source["lyrics"][occurrence["start"]:occurrence["end"]] != occurrence["surface"]:
                raise ValueError("比較の引用位置が不正です。")


def _validate_payloads(connection):
    for rule in connection.execute("SELECT * FROM expression_rules"):
        validate_rule(rule["kind"], json.loads(rule["pattern_json"]), rule["reason"], rule["review_hint"], bool(rule["enabled"]))
    revisions = {row["id"]: dict(row) for row in connection.execute("SELECT * FROM song_revisions")}
    for revision in revisions.values():
        if hashlib.sha256(revision["lyrics_raw"].encode("utf-8")).hexdigest() != revision["content_hash"]:
            raise ValueError("歌詞のハッシュが一致しません。")
    for row in connection.execute("SELECT * FROM analysis_runs"):
        report = json.loads(row["payload_json"])
        _validate_report(report)
        if (report["format_version"] != 1 or report["id"] != row["id"] or report["song_id"] != row["target_song_id"]
                or report["target_revision_id"] != row["target_revision_id"] or report["created_at"] != row["created_at"]
                or hashlib.sha256(report["input"].encode("utf-8")).hexdigest() != report["input_hash"]):
            raise ValueError("分析履歴の整合性を確認できません。")
        if row["target_revision_id"] and revisions[row["target_revision_id"]]["content_hash"] != report["input_hash"]:
            raise ValueError("入力と対象改訂が一致しません。")
        sources = {(source[0], source[1]) for source in connection.execute("SELECT song_id,revision_id FROM analysis_sources WHERE run_id=?", (row["id"],))}
        if sources != {(source["song_id"], source["revision_id"]) for source in report["corpus"]}:
            raise ValueError("比較元の参照が一致しません。")
        for source in report["corpus"]:
            revision = revisions[source["revision_id"]]
            if revision["song_id"] != source["song_id"] or revision["lyrics_raw"] != source["lyrics"] or revision["content_hash"] != source["content_hash"]:
                raise ValueError("比較元の歌詞が一致しません。")
        for finding in report["findings"]:
            if not 0 <= finding["start"] < finding["end"] <= len(report["input"]) or report["input"][finding["start"]:finding["end"]] != finding["surface"]:
                raise ValueError("指摘の原文位置が不正です。")
            for match_id in finding["comparisons"]:
                report["comparisons"][match_id]
    for review in connection.execute("SELECT * FROM finding_reviews"):
        report = json.loads(connection.execute("SELECT payload_json FROM analysis_runs WHERE id=?", (review["run_id"],)).fetchone()[0])
        if review["finding_id"] not in {finding["id"] for finding in report["findings"]}:
            raise ValueError("評価対象の指摘が見つかりません。")


@contextmanager
def validated_backup(content):
    staged = sqlite3.connect(":memory:")
    staged.row_factory = sqlite3.Row
    staged.execute("PRAGMA foreign_keys=ON")
    try:
        if not isinstance(content, bytes) or len(content) > MAX_BACKUP_BYTES:
            raise ValueError("バックアップは100MB以内のJSONファイルを指定してください。")
        archive = json.loads(content)
        if (not isinstance(archive, dict) or archive.get("format") != "lyric-analyzer-backup"
                or type(archive.get("version")) is not int or archive["version"] != 1
                or type(archive.get("schema_version")) is not int or archive["schema_version"] != len(MIGRATIONS)):
            raise ValueError("このアプリで対応していないバックアップ形式です。")
        if datetime.fromisoformat(archive["created_at"]).utcoffset() is None:
            raise ValueError("バックアップの作成日時が不正です。")
        tables = archive["tables"]
        if set(tables) != set(TABLES) or archive["sha256"] != checksum(tables):
            raise ValueError("バックアップのデータや検証値が一致しません。")
        staged.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        for migration in MIGRATIONS:
            for statement in migration:
                staged.execute(statement)
        for table in TABLES:
            info = list(staged.execute(f"PRAGMA table_info({table})"))
            columns = [row[1] for row in info]
            rows = tables[table]
            if not isinstance(rows, list):
                raise ValueError("テーブルの形式が不正です。")
            for row in rows:
                if not isinstance(row, dict) or set(row) != set(columns):
                    raise ValueError("保存項目の形式が不正です。")
                for column in info:
                    value = row[column[1]]
                    if value is None:
                        if column[3] or column[5]:
                            raise ValueError("必須項目が不足しています。")
                    elif column[2] == "INTEGER" and type(value) is not int or column[2] == "TEXT" and value is not None and not isinstance(value, str):
                        raise ValueError("保存項目の型が不正です。")
            placeholders = ",".join("?" for _ in columns)
            staged.executemany(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})", ([row[column] for column in columns] for row in rows))
        if {row[0] for row in staged.execute("SELECT version FROM schema_migrations")} != set(range(1, len(MIGRATIONS) + 1)):
            raise ValueError("データベースの版が一致しません。")
        if staged.execute("PRAGMA foreign_key_check").fetchall() or staged.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("データの参照関係が不正です。")
        _validate_payloads(staged)
        staged.commit()
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError, UnicodeError, RecursionError, sqlite3.Error) as error:
        staged.close()
        raise ValueError(f"バックアップを読み取れません：{error}") from error
    try:
        yield archive, staged
    finally:
        staged.close()


def inspect_backup(content):
    with validated_backup(content) as (archive, _):
        return {"created_at": archive["created_at"], "counts": {table: len(rows) for table, rows in archive["tables"].items()}}


def restore_backup(library, content, confirmed=False):
    if confirmed is not True:
        raise ValueError("現在のデータを置き換えることを確認してください。")
    with validated_backup(content) as (_, staged):
        before = export_backup(library)
        directory = library.path.parent / "backups"
        directory.mkdir(parents=True, exist_ok=True)
        recovery = directory / f"before-restore-{uuid4()}.json"
        with recovery.open("xb") as destination:
            destination.write(before)
            destination.flush()
            os.fsync(destination.fileno())
        with library.connection() as destination:
            staged.backup(destination)
    return recovery
