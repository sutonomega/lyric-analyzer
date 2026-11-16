from dataclasses import asdict
import hashlib
import math

import pytest

from lyric_analyzer import local_ai, local_embeddings as embeddings
from lyric_analyzer.analysis.comparison import SongSnapshot
from lyric_analyzer.analysis.semantic import prepare, search


MODEL = {"name": "embeddinggemma:300m", "digest": "a" * 64}


def snapshot(song_id, raw):
    return SongSnapshot(song_id, song_id, song_id + '-r1', 1, raw, hashlib.sha256(raw.encode()).hexdigest(), '2026-11-16T00:00:00+00:00')


def report():
    raw = '[Verse]\n君を待つ\n空を見上げる'
    corpus = [snapshot('self', raw), snapshot('a', '[Verse]\n君を待つ\nあなたの到着を待つ\nあなたの到着を待つ'),
              snapshot('b', '食器を洗う\nあなたの到着を待つ')]
    return {"song_id": 'self', "input": raw, "input_hash": hashlib.sha256(raw.encode()).hexdigest(),
            "corpus": [asdict(song) for song in corpus]}


def test_prepare_excludes_self_tags_exact_matches_and_groups_repeated_source_lines():
    value = report()
    result = prepare(value, 2, ['self', 'a', 'b'])
    assert {song.song_id for song in result.corpus} == {'a', 'b'}
    assert result.texts == ('あなたの到着を待つ', '食器を洗う')
    assert result.exact_excluded == 1
    sources = {song.song_id: song for song in result.corpus}
    assert len(result.evidence[0]) == 3
    for items in result.evidence:
        for item in items:
            span = item['occurrence']
            assert sources[item['song_id']].lyrics[span['start']:span['end']] == span['text']
    assert prepare(value, 3, ['a', 'b']).fingerprint != result.fingerprint
    assert prepare(value, 2, ['a'], False).fingerprint != result.fingerprint


@pytest.mark.parametrize('line,ids', [(True, ['a']), (1, ['a']), (999, ['a']), (2, []), (2, ['unknown']), (2, ['self'])])
def test_invalid_selection_never_prepares_a_request(line, ids):
    with pytest.raises(ValueError):
        prepare(report(), line, ids)


def test_ranking_threshold_citations_and_dimensions():
    result = prepare(report(), 2, ['a', 'b'])
    calls = []
    def embed(model, texts):
        calls.append(texts)
        return [([1, 0] if text == '君を待つ' else [0.9, 0.1] if '待つ' in text else [0, 1]) for text in texts]
    answer = search(result, MODEL, embed, minimum_score=0.5, limit=1)
    assert len(calls) == 2
    assert answer['results'][0]['text'] == 'あなたの到着を待つ'
    assert len(answer['results'][0]['evidence']) == 3
    assert answer['results'][0]['score'] > 0.9
    assert answer['reference_occurrences'] == 4
    assert len(answer['sources']) == 2
    assert not search(result, MODEL, embed, minimum_score=1)['results']
    with pytest.raises(ValueError, match='次元'):
        search(result, MODEL, lambda m, texts: [[1, 0]] if texts == ('君を待つ',) else [[1, 0, 0] for _ in texts])
    with pytest.raises(ValueError):
        search(result, MODEL, lambda m, texts: [])


@pytest.mark.parametrize('score,limit', [(float('nan'), 10), (1.1, 10), (True, 10), (0.5, True), (0.5, 21)])
def test_invalid_rank_options(score, limit):
    with pytest.raises(ValueError):
        search(prepare(report(), 2, ['a']), MODEL, lambda m, t: [], score, limit)


@pytest.mark.parametrize('values', [[], [1], [0, 0], [float('nan'), 1], [float('inf'), 1], [True, 1], ['1', 1]])
def test_invalid_embedding_values(values):
    with pytest.raises(ValueError):
        embeddings.unit_vector(values)


def transport(monkeypatch, transform=None):
    calls = []
    def request(path, payload=None, timeout=5):
        calls.append((path, payload))
        if path == '/api/tags':
            return {'models': [dict(MODEL), {'name': 'chat:3b', 'digest': 'b' * 64}]}
        if path == '/api/show':
            return {'capabilities': ['embedding'], 'model_info': {'architecture': 'gemma3'}}
        value = {'model': MODEL['name'], 'embeddings': [[3, 4] for _ in payload['input']]}
        return transform(value) if transform else value
    monkeypatch.setattr(local_ai, '_json_request', request)
    return calls


def test_local_embedding_protocol_and_truncation(monkeypatch):
    calls = transport(monkeypatch)
    assert embeddings.list_models() == (MODEL,)
    vectors = embeddings.embed_batch(MODEL, ('君を待つ', 'あなたを待つ'))
    assert vectors[0] == (0.6, 0.8)
    assert math.isclose(math.hypot(*vectors[0]), 1)
    path, payload = calls[-1]
    assert path == '/api/embed' and payload['truncate'] is False
    assert payload['input'] == [embeddings.PREFIX + text for text in ('君を待つ', 'あなたを待つ')]


@pytest.mark.parametrize('transform', [lambda r: {**r, 'embeddings': []}, lambda r: {**r, 'model': 'other'},
                                      lambda r: {**r, 'embeddings': [[1, 0], [1, 0, 0]]}])
def test_invalid_model_responses(monkeypatch, transform):
    transport(monkeypatch, transform)
    with pytest.raises(ValueError):
        embeddings.embed_batch(MODEL, ('one', 'two'))


def test_changed_or_remote_model_is_rejected_before_text_is_sent(monkeypatch):
    calls = []
    def request(path, payload=None, timeout=5):
        calls.append(path)
        return {'models': [dict(MODEL)]} if path == '/api/tags' else {'remote_host': 'https://example.com', 'model_info': {'x': 1}, 'capabilities': ['embedding']}
    monkeypatch.setattr(local_ai, '_json_request', request)
    with pytest.raises(ValueError):
        embeddings.embed_batch(MODEL, ('private lyrics',))
    assert '/api/embed' not in calls
    with pytest.raises(ValueError):
        embeddings.embed_batch({**MODEL, 'digest': 'b' * 64}, ('private lyrics',))
    assert '/api/embed' not in calls


def test_line_limits_and_reference_count_limits_are_checked_before_embedding():
    value = report()
    value['input'] = 'x' * 1001
    with pytest.raises(ValueError, match='1,000'):
        prepare(value, 1, ['a'])
    value = report()
    value['corpus'] = [asdict(snapshot('long', 'x' * 1001))]
    with pytest.raises(ValueError, match='1,000'):
        prepare(value, 2, ['long'])
    value['corpus'] = [asdict(snapshot(str(i), '\n'.join(f'line{i}-{n}' for n in range(1000)))) for i in range(7)]
    with pytest.raises(ValueError, match='6,000'):
        prepare(value, 2, [str(i) for i in range(7)])
    value['corpus'] = [asdict(snapshot(str(i), '\n'.join(['同じ本文'] * 1000))) for i in range(21)]
    with pytest.raises(ValueError, match='20,000'):
        prepare(value, 2, [str(i) for i in range(21)])
