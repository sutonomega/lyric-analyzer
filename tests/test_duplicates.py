import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from lyric_analyzer.backup import export_backup, inspect_backup, restore_backup, checksum
from lyric_analyzer.duplicates import consolidate
from lyric_analyzer.folder_import import ImportRecord, import_records
from lyric_analyzer.storage import Library
from lyric_analyzer.history import HistoryStore
from lyric_analyzer.analysis.comparison import compare
from lyric_analyzer.analysis.expressions import detect
from lyric_analyzer.analysis.report import build_report


def legacy_duplicate(library, source):
    # Simulate pre-fix records. Public writes must never create these now.
    duplicate = str(uuid4())
    with library.connection() as c:
        c.execute('INSERT INTO songs(id,title,theme,created_at,updated_at,styles,composed_on) SELECT ?,title,theme,created_at,updated_at,styles,composed_on FROM songs WHERE id=?', (duplicate,source))
        for row in c.execute('SELECT * FROM song_revisions WHERE song_id=?', (source,)).fetchall():
            c.execute('INSERT INTO song_revisions VALUES(?,?,?,?,?,?)', (str(uuid4()),duplicate,row['revision_number'],row['lyrics_raw'],row['content_hash'],row['created_at']))
    return duplicate


def test_public_creation_and_edit_block_exact_but_keep_variants(tmp_path):
    library=Library(tmp_path/'library.db')
    song=library.save('曲','本文',styles='pop')
    with pytest.raises(ValueError,match='完全一致'):
        library.save(' 曲 ','本文',styles='pop')
    variant=library.save('曲','別の本文',styles='pop')
    with pytest.raises(ValueError,match='完全一致'):
        library.save('曲','本文',song_id=variant)
    assert len(library.list_songs())==2
    assert library.revisions(variant)[0]['lyrics_raw']=='別の本文'
    library.save('曲','本文',song_id=song,tags=['街'])


def test_content_duplicate_json_different_ids_and_copy_are_skipped(tmp_path):
    source=Library(tmp_path/'source.db')
    source.save('曲','本文',tags=['夜'])
    target=Library(tmp_path/'target.db')
    assert target.import_json(source.export_json())==(1,0)
    assert target.import_json(source.export_json(),'copy')==(0,1)
    archive=json.loads(source.export_json())
    song=archive['songs'][0]
    song['id']=str(uuid4())
    for revision in song['revisions']:
        revision['id']=str(uuid4())
        revision['song_id']=song['id']
    assert target.import_json(json.dumps(archive))==(0,1)
    song['composed_on']='2026-01-01'
    with pytest.raises(ValueError,match='制作日'):
        target.import_json(json.dumps(archive))
    assert len(target.list_songs())==1


def test_consolidation_preserves_full_history_and_backup(tmp_path):
    library=Library(tmp_path/'library.db')
    song=library.save('曲','私は切符を買った')
    duplicate=legacy_duplicate(library,song)
    corpus=library.comparison_corpus()
    report=build_report('私は切符を買った',compare('私は切符を買った',corpus),detect('私は切符を買った',()),{})
    store=HistoryStore(library)
    run=store.save(report,'曲',song)
    before=store.get(run)
    result=consolidate(library)
    assert result['removed']==result['archived']==1
    assert len(library.list_songs())==len(library.comparison_corpus())==1
    assert library.list_songs()[0]['id']==song
    assert store.get(run)==before
    assert len(json.loads(library.export_json())['songs'])==1
    assert inspect_backup(export_backup(library))['counts']['analysis_runs']==1
    restored=Library(tmp_path/'restored.db')
    restore_backup(restored,export_backup(library),confirmed=True)
    assert len(restored.list_songs())==1
    assert HistoryStore(restored).get(run)==before
    restore_backup(restored, Path(result['backup']).read_bytes(),confirmed=True)
    assert len(restored.list_songs())==2
    assert consolidate(library)['removed']==0
    assert import_records(library,[ImportRecord('曲','','私は切符を買った','copy.md','utf-8')],True)==(0,1)
    with pytest.raises(ValueError):
        library.save('曲','変更',song_id=duplicate)


def test_different_metadata_or_revisions_are_not_removed(tmp_path):
    library=Library(tmp_path/'library.db')
    song=library.save('曲','本文')
    duplicate=legacy_duplicate(library,song)
    with library.connection() as c:
        c.execute("UPDATE songs SET composed_on='2026-01-01' WHERE id=?",(duplicate,))
    assert consolidate(library)['removed']==0


def test_backup_failure_prevents_cleanup(tmp_path,monkeypatch):
    from lyric_analyzer import backup
    library=Library(tmp_path/'library.db')
    song=library.save('曲','本文')
    legacy_duplicate(library,song)
    monkeypatch.setattr(backup,'export_backup',lambda *a: (_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(OSError):
        consolidate(library)
    assert len(library.list_songs())==2


def test_cleanup_failure_rolls_back_every_record(tmp_path):
    library=Library(tmp_path/'library.db')
    song=library.save('曲','本文')
    legacy_duplicate(library,song)
    legacy_duplicate(library,song)
    with library.connection() as c:
        c.execute('CREATE TRIGGER fail_delete BEFORE DELETE ON songs BEGIN SELECT RAISE(ABORT,"failure"); END')
    with pytest.raises(sqlite3.IntegrityError):
        consolidate(library)
    assert len(library.list_songs())==3


def test_old_schema7_backup_restores_active_records(tmp_path):
    library=Library(tmp_path/'library.db')
    library.save('曲','本文')
    archive=json.loads(export_backup(library))
    archive['schema_version']=7
    archive['tables']['schema_migrations']=[r for r in archive['tables']['schema_migrations'] if r['version']<8]
    for song in archive['tables']['songs']:
        song.pop('duplicate_archived')
    archive['sha256']=checksum(archive['tables'])
    restored=Library(tmp_path/'restored.db')
    restore_backup(restored,json.dumps(archive).encode(),confirmed=True)
    assert restored.list_songs()[0]['duplicate_archived']==0


def test_concurrent_exact_creates_produce_only_one_song(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    library=Library(tmp_path/'library.db')
    def create(_):
        try:
            return library.save('曲','本文')
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(create,range(2)))
    assert results.count(None)==1
    assert len(library.list_songs())==1
