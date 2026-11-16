from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import version
import re

from sudachipy import Dictionary, SplitMode

from lyric_analyzer.analysis.parsing import analysis_segments, normalize_with_offsets, parse_lines


ANALYZER_VERSION = "frequency-1"
MAX_LYRICS_LENGTH = 20_000
CONTENT_POS = frozenset({"名詞", "動詞", "形容詞"})
BOUNDARY_POS = frozenset({"空白", "補助記号", "記号"})


@dataclass(frozen=True)
class Token:
    surface: str
    normalized_surface: str
    lemma: str
    normalized: str
    pos: str
    reading: str
    start: int
    end: int
    line_number: int
    section: str | None
    run: int
    boundary: bool


@dataclass(frozen=True)
class Occurrence:
    surface: str
    start: int
    end: int
    line_number: int
    section: str | None
    line_text: str


@dataclass(frozen=True)
class Frequency:
    expression: str
    pos: str
    occurrences: tuple[Occurrence, ...]

    @property
    def count(self):
        return len(self.occurrences)

    @property
    def unique_line_count(self):
        return len({item.line_text for item in self.occurrences})

    @property
    def variants(self):
        return dict(Counter(item.surface for item in self.occurrences))


@dataclass(frozen=True)
class Analysis:
    tokens: tuple[Token, ...]
    words: tuple[Frequency, ...]
    phrases_surface: tuple[Frequency, ...]
    phrases_normalized: tuple[Frequency, ...]
    analyzer_version: str
    tokenizer_version: str
    dictionary_version: str
    split_mode: str


@lru_cache(maxsize=1)
def load_dictionary():
    return Dictionary(dict="core")


def tokenize(raw: str, include_unknown_tags: bool = False) -> tuple[Token, ...]:
    tokenizer = load_dictionary().tokenizer()
    tokens = []
    run = 0
    for segment in analysis_segments(raw, include_unknown_tags):
        run += 1
        normalized = normalize_with_offsets(segment.text)
        for morpheme in tokenizer.tokenize(normalized.text, SplitMode.A):
            start, end = normalized.original_range(morpheme.begin(), morpheme.end())
            surface = raw[segment.start + start:segment.start + end]
            normalized_surface = normalized.text[morpheme.begin():morpheme.end()]
            pos = morpheme.part_of_speech()[0]
            boundary = pos in BOUNDARY_POS or not any(character.isalnum() for character in normalized_surface)
            lemma = morpheme.dictionary_form()
            if re.fullmatch(r"[A-Za-z0-9]+", normalized_surface):
                lemma = normalized_surface
            tokens.append(Token(surface, normalized_surface, lemma, morpheme.normalized_form(), pos,
                                morpheme.reading_form(), segment.start + start, segment.start + end,
                                segment.line_number, segment.section, run, boundary))
            if boundary:
                run += 1
    return tuple(tokens)


def analyze(raw: str, include_unknown_tags: bool = False) -> Analysis:
    if len(raw) > MAX_LYRICS_LENGTH:
        raise ValueError("分析できる歌詞は20,000文字までです。分割して分析してください。")
    tokens = tokenize(raw, include_unknown_tags)
    lines = {line.line_number: line.text for line in parse_lines(raw)}
    words = defaultdict(list)
    surface_phrases = defaultdict(list)
    normalized_phrases = defaultdict(list)

    def occurrence(first, last):
        return Occurrence(raw[first.start:last.end], first.start, last.end,
                          first.line_number, first.section, lines[first.line_number])

    for index, token in enumerate(tokens):
        if not token.boundary and token.pos in CONTENT_POS:
            words[(token.lemma, token.pos)].append(occurrence(token, token))
        if token.boundary:
            continue
        for length in range(2, 6):
            window = tokens[index:index + length]
            if len(window) != length or any(item.boundary or item.run != token.run for item in window):
                break
            if any(before.end > after.start for before, after in zip(window, window[1:])):
                continue
            if not any(item.pos in CONTENT_POS for item in window):
                continue
            found = occurrence(window[0], window[-1])
            surface_phrases[(found.surface, "")].append(found)
            normalized_key = "".join(item.normalized_surface for item in window)
            normalized_phrases[(normalized_key, "")].append(found)

    def frequencies(groups):
        rows = []
        for (expression, pos), occurrences in groups.items():
            unique = {(item.start, item.end): item for item in occurrences}
            rows.append(Frequency(expression, pos, tuple(unique.values())))
        return tuple(sorted(rows, key=lambda row: (-row.count, -len(row.expression), row.expression, row.pos)))

    return Analysis(tokens, frequencies(words), frequencies(surface_phrases), frequencies(normalized_phrases),
                    ANALYZER_VERSION, version("sudachipy"), version("sudachidict_core"), "A")
