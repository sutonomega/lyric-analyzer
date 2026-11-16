from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
import hashlib
from zoneinfo import ZoneInfo

from lyric_analyzer.analysis.frequency import Analysis, Frequency, Occurrence, analyze
from lyric_analyzer.analysis.parsing import parse_lines


@dataclass(frozen=True)
class SongSnapshot:
    song_id: str
    title: str
    revision_id: str
    revision_number: int
    lyrics: str
    content_hash: str
    created_at: str


@dataclass(frozen=True)
class Evidence:
    song_id: str
    revision_id: str
    occurrence: Occurrence


@dataclass(frozen=True)
class Match:
    expression: str
    pos: str
    target_occurrences: tuple[Occurrence, ...]
    evidence: tuple[Evidence, ...]
    status: str

    @property
    def song_count(self):
        return len({item.song_id for item in self.evidence})

    @property
    def count(self):
        return len(self.evidence)


@dataclass(frozen=True)
class Comparison:
    corpus: tuple[SongSnapshot, ...]
    words: tuple[Match, ...]
    phrases_surface: tuple[Match, ...]
    phrases_normalized: tuple[Match, ...]
    duplicate_song_ids: tuple[str, ...]


def select_corpus(corpus: tuple[SongSnapshot, ...], target_song_id: str | None = None,
                  selected_ids: tuple[str, ...] | None = None, start_date: date | None = None,
                  end_date: date | None = None) -> tuple[SongSnapshot, ...]:
    if start_date and end_date and start_date > end_date:
        raise ValueError("登録日の開始日は終了日以前にしてください。")
    latest = {}
    for song in corpus:
        if song.song_id == target_song_id or (selected_ids is not None and song.song_id not in selected_ids):
            continue
        if song.song_id not in latest or latest[song.song_id].revision_number < song.revision_number:
            latest[song.song_id] = song
    selected = []
    for song in latest.values():
        registered = datetime.fromisoformat(song.created_at).astimezone(ZoneInfo("Asia/Tokyo")).date()
        if (start_date is None or registered >= start_date) and (end_date is None or registered <= end_date):
            selected.append(song)
    return tuple(sorted(selected, key=lambda song: song.song_id))


def match_status(song_count: int, corpus_count: int, word: bool = False) -> str:
    if word:
        return "単語参考"
    if corpus_count < 10:
        return "参考（母数少）" if song_count >= 2 else "一致"
    return "頻出" if song_count >= 3 and song_count / corpus_count >= 0.2 else "一致"


def raw_runs(analysis: Analysis, raw: str) -> tuple[Occurrence, ...]:
    groups = defaultdict(list)
    for token in analysis.tokens:
        if not token.boundary:
            groups[token.run].append(token)
    result = []
    lines = {line.line_number: line.text for line in parse_lines(raw)}
    for tokens in groups.values():
        first, last = tokens[0], tokens[-1]
        result.append(Occurrence(raw[first.start:last.end], first.start, last.end, first.line_number, first.section, lines[first.line_number]))
    return tuple(result)


def raw_occurrences(expression: str, runs: tuple[Occurrence, ...]) -> tuple[Occurrence, ...]:
    occurrences = []
    for run in runs:
        offset = run.surface.find(expression)
        while offset != -1:
            occurrences.append(Occurrence(expression, run.start + offset, run.start + offset + len(expression),
                                          run.line_number, run.section, run.line_text))
            offset = run.surface.find(expression, offset + 1)
    return tuple(occurrences)


def compare(raw: str, corpus: tuple[SongSnapshot, ...], target_song_id: str | None = None,
            selected_ids: tuple[str, ...] | None = None, start_date: date | None = None,
            end_date: date | None = None, include_unknown_tags: bool = False) -> Comparison:
    corpus = select_corpus(corpus, target_song_id, selected_ids, start_date, end_date)
    target = analyze(raw, include_unknown_tags)
    analyses = {}
    for song in corpus:
        try:
            analyses[song.song_id] = analyze(song.lyrics, include_unknown_tags)
        except ValueError as error:
            raise ValueError(f"比較曲「{song.title}」：{error}") from error

    def matches(target_rows: tuple[Frequency, ...], attribute: str, word: bool = False):
        source = defaultdict(list)
        for song in corpus:
            for row in getattr(analyses[song.song_id], attribute):
                source[(row.expression, row.pos)].extend(Evidence(song.song_id, song.revision_id, item) for item in row.occurrences)
        result = []
        for row in target_rows:
            evidence = source.get((row.expression, row.pos))
            if evidence:
                count = len({item.song_id for item in evidence})
                result.append(Match(row.expression, row.pos, row.occurrences, tuple(evidence), match_status(count, len(corpus), word)))
        return result

    surface = {item.expression: item for item in matches(target.phrases_surface, "phrases_surface")}
    target_runs = raw_runs(target, raw)
    source_runs = {song.song_id: raw_runs(analyses[song.song_id], song.lyrics) for song in corpus}
    candidates = set()
    target_texts = {run.surface for run in target_runs}
    source_texts = {run.surface for runs in source_runs.values() for run in runs}
    for target_text in target_texts:
        for source_text in source_texts:
            for block in SequenceMatcher(None, target_text, source_text, autojunk=False).get_matching_blocks():
                if block.size >= 6:
                    candidates.add(target_text[block.a:block.a + block.size])
    for expression in candidates:
        if expression in surface:
            continue
        evidence = tuple(Evidence(song.song_id, song.revision_id, item) for song in corpus
                         for item in raw_occurrences(expression, source_runs[song.song_id]))
        count = len({item.song_id for item in evidence})
        surface[expression] = Match(expression, "", raw_occurrences(expression, target_runs), evidence, match_status(count, len(corpus)))

    def ordered(rows):
        return tuple(sorted(rows, key=lambda item: (-item.song_count, -len(item.expression), item.target_occurrences[0].start, item.expression, item.pos)))

    content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return Comparison(corpus, ordered(matches(target.words, "words", True)), ordered(surface.values()),
                      ordered(matches(target.phrases_normalized, "phrases_normalized")),
                      tuple(song.song_id for song in corpus if song.content_hash == content_hash))


def collapse_contained(matches: tuple[Match, ...]) -> tuple[Match, ...]:
    kept = []
    for candidate in sorted(matches, key=lambda item: -len(item.expression)):
        def covered_by(longer):
            return (all(any(outer.start <= inner.start and inner.end <= outer.end for outer in longer.target_occurrences)
                        for inner in candidate.target_occurrences)
                    and all(any(outer.song_id == inner.song_id and outer.occurrence.start <= inner.occurrence.start
                                and inner.occurrence.end <= outer.occurrence.end for outer in longer.evidence)
                            for inner in candidate.evidence))
        if not any(len(longer.expression) > len(candidate.expression) and covered_by(longer) for longer in kept):
            kept.append(candidate)
    return tuple(item for item in matches if item in kept)
