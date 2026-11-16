from pathlib import Path

from streamlit.testing.v1 import AppTest

from lyric_analyzer import local_embeddings
from lyric_analyzer.history import HistoryStore
from lyric_analyzer.storage import Library


def test_semantic_is_opt_in_binds_confirmation_and_preserves_results_on_failure(tmp_path, monkeypatch):
    monkeypatch.setenv('LYRIC_ANALYZER_DATA_DIR', str(tmp_path))
    library = Library(tmp_path / 'library.db')
    ids = [library.save('過去A', 'あなたの到着を待つ'), library.save('過去B', '君を待たない')]
    model = {'name': 'embeddinggemma:300m', 'digest': 'a' * 64}
    calls = []
    monkeypatch.setattr(local_embeddings, 'list_models', lambda: (model,))
    monkeypatch.setattr(local_embeddings, 'check_model', lambda value: None)
    def embed(descriptor, texts):
        calls.append(texts)
        return tuple((1., 0.) if text in ('君を待つ', '君を待たない') else (0.9, 0.1) for text in texts)
    monkeypatch.setattr(local_embeddings, 'embed_batch', embed)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / 'app.py').run()
    app.text_area[0].set_value('[Verse]\n君を待つ')
    app.button(key='report').click().run()
    run = app.session_state['current_report']
    prefix = f'current-{run}'
    store = HistoryStore(library)
    before = store.get(run)
    assert not app.checkbox(key=f'{prefix}-semantic-enabled').value
    assert not calls
    app.checkbox(key=f'{prefix}-semantic-enabled').check().run()
    app.button(key=f'{prefix}-semantic-connect').click().run()
    assert not calls
    app.multiselect(key=f'{prefix}-semantic-songs').set_value(ids).run()
    assert app.button(key=f'{prefix}-semantic-run').disabled
    confirm = next(item for item in app.checkbox if item.key and item.key.startswith(f'{prefix}-semantic-confirm-'))
    confirm.check().run()
    app.button(key=f'{prefix}-semantic-run').click().run()
    assert not app.exception
    result = app.session_state[f'{prefix}-semantic-result']
    assert len(result['results']) == 2
    frame = next(item.value for item in app.dataframe if '近さ（参考）' in item.value.columns)
    assert any('否定語' in value for value in frame['語の差（補助）'])
    assert store.get(run) == before
    count = len(calls)
    app.number_input(key=f'{prefix}-semantic-minimum').set_value(0.9).run()
    assert app.button(key=f'{prefix}-semantic-run').disabled
    assert len(calls) == count
    confirm = next(item for item in app.checkbox if item.key and item.key.startswith(f'{prefix}-semantic-confirm-'))
    confirm.check().run()
    def fail(value):
        raise ValueError('model changed')
    monkeypatch.setattr(local_embeddings, 'check_model', fail)
    app.button(key=f'{prefix}-semantic-run').click().run()
    assert not app.exception
    assert app.error
    assert app.session_state[f'{prefix}-semantic-result'] == result
    assert store.get(run) == before
