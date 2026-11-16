from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
import hashlib
from importlib.metadata import version
import json
import unicodedata

from lyric_analyzer.analysis.comparison import Evidence, SongSnapshot, select_corpus
from lyric_analyzer.analysis.frequency import CONTENT_POS, analyze
from lyric_analyzer.analysis.report import GRAMMATICAL_LEMMAS


MODES = {"words": "単語（原形）", "phrases_surface": "フレーズ（原文）", "phrases_normalized": "フレーズ（NFKC）"}


def versions():
    return {"overview": "library-vocabulary-1", "frequency": "frequency-1", "tokenizer": version("sudachipy"),
            "dictionary": version("sudachidict_core"), "split_mode": "A"}


@dataclass(frozen=True)
class Vocabulary:
    id: str
    expression: str
    pos: str
    song_count: int
    count: int
    unique_line_count: int
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class LibraryVocabulary:
    corpus: tuple[SongSnapshot, ...]
    mode: str
    rows: tuple[Vocabulary, ...]
    versions: dict[str, str]


def build_index(corpus, mode="words", include_grammar=False, include_unknown_tags=False):
    if mode not in MODES or type(include_grammar) is not bool or type(include_unknown_tags) is not bool:
        raise ValueError("集計する種類と対象を確認してください。")
    corpus = select_corpus(tuple(corpus))
    groups = defaultdict(list)
    for song in corpus:
        try:
            analysis = analyze(song.lyrics, include_unknown_tags)
        except ValueError as error:
            raise ValueError(f"集計曲「{song.title}」：{error}") from error
        content = [token for token in analysis.tokens if not token.boundary and token.pos in CONTENT_POS
                   and token.lemma not in GRAMMATICAL_LEMMAS]
        starts = [token.start for token in content]
        for row in getattr(analysis, mode):
            if not include_grammar and mode == "words" and row.expression in GRAMMATICAL_LEMMAS:
                continue
            for occurrence in row.occurrences:
                if not include_grammar and mode != "words":
                    index = bisect_left(starts, occurrence.start)
                    if index == len(content) or content[index].end > occurrence.end:
                        continue
                groups[(row.expression, row.pos)].append(Evidence(song.song_id, song.revision_id, occurrence))
    rows = []
    for (expression, pos), evidence in groups.items():
        key = hashlib.sha256(json.dumps([mode, expression, pos], ensure_ascii=False).encode()).hexdigest()
        rows.append(Vocabulary(key, expression, pos, len({item.song_id for item in evidence}), len(evidence),
                               len({(item.song_id, item.occurrence.line_text) for item in evidence}), tuple(evidence)))
    rows.sort(key=lambda row: (-row.song_count, -row.count, -len(row.expression), row.expression, row.pos))
    return LibraryVocabulary(corpus, mode, tuple(rows), versions())


def filter_rows(index, minimum_songs=2, minimum_characters=4, query=""):
    if type(minimum_songs) is not int or minimum_songs < 1 or type(minimum_characters) is not int or minimum_characters < 1:
        raise ValueError("使用曲数と文字数は1以上の整数を指定してください。")
    if not isinstance(query, str):
        raise ValueError("絞り込む表現を入力してください。")
    if index.mode != "phrases_surface":
        query = unicodedata.normalize("NFKC", query)
    return [row for row in index.rows if row.song_count >= minimum_songs
            and (index.mode == "words" or len(row.expression) >= minimum_characters)
            and query in row.expression]


def summaries(index, rows):
    total = len(index.corpus)
    return [{"表現": row.expression, "品詞": row.pos, "使用曲数": row.song_count,
             "使用曲割合": row.song_count / total if total else None, "出現回数": row.count,
             "異なる本文行数": row.unique_line_count} for row in rows]
