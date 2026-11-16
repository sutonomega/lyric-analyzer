from dataclasses import asdict, dataclass
import hashlib
import json
import math
import unicodedata
from typing import Any

from lyric_analyzer.analysis.comparison import SongSnapshot, select_corpus
from lyric_analyzer.analysis.parsing import Span, parse_lines
from lyric_analyzer.local_embeddings import BATCH_SIZE, PREFIX, unit_vector


VERSION = "semantic-lines-1"
MAX_LINES = 6000


def canonical(text):
    return unicodedata.normalize("NFKC", text).strip()


@dataclass(frozen=True)
class SemanticRequest:
    input_hash: str
    query: Span
    corpus: tuple[SongSnapshot, ...]
    texts: tuple[str, ...]
    evidence: tuple[tuple[dict, ...], ...]
    exact_excluded: int
    fingerprint: str


def prepare(report, line_number, selected_ids, exclude_exact=True):
    if type(line_number) is not int or type(exclude_exact) is not bool:
        raise ValueError("対象行と同一行の扱いを確認してください。")
    query = next((line for line in parse_lines(report["input"]) if line.line_number == line_number
                  and line.text.strip() and not line.metadata), None)
    if query is None or len(query.text) > 1000:
        raise ValueError("検索する本文行を1,000文字以内で選んでください。")
    allowed = {source["song_id"] for source in report["corpus"]}
    if not selected_ids or set(selected_ids) - allowed:
        raise ValueError("分析時の比較対象から1曲以上選んでください。")
    corpus = select_corpus(tuple(SongSnapshot(**source) for source in report["corpus"]),
                           target_song_id=report["song_id"], selected_ids=tuple(selected_ids))
    if not corpus:
        raise ValueError("自曲以外の比較対象を選んでください。")
    groups = {}
    excluded = 0
    total = 0
    for song in corpus:
        for line in parse_lines(song.lyrics):
            if not line.text.strip() or line.metadata:
                continue
            total += 1
            if total > 20000:
                raise ValueError("比較対象の本文行は反復も含め20,000行までです。対象曲を減らしてください。")
            if len(line.text) > 1000:
                raise ValueError(f"比較曲「{song.title}」に1,000文字を超える行があります。対象曲を変更してください。")
            if exclude_exact and canonical(line.text) == canonical(query.text):
                excluded += 1
                continue
            groups.setdefault(line.text.strip(), []).append({"song_id": song.song_id, "revision_id": song.revision_id,
                                                           "occurrence": asdict(line)})
            if len(groups) > MAX_LINES:
                raise ValueError("比較対象は異なる本文行6,000種類までです。対象曲を減らしてください。")
    if not groups:
        raise ValueError("比較する本文行がありません。同一行の除外や対象曲を確認してください。")
    texts = tuple(sorted(groups))
    evidence = tuple(tuple(groups[text]) for text in texts)
    fingerprint = hashlib.sha256(json.dumps([VERSION, report["input_hash"], asdict(query),
                                             [(song.song_id, song.revision_id, song.content_hash) for song in corpus],
                                             texts, evidence, exclude_exact], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return SemanticRequest(report["input_hash"], query, corpus, texts, evidence, excluded, fingerprint)


def search(prepared, model, embed, minimum_score=0.75, limit=10, progress=None):
    if (type(minimum_score) not in (int, float) or not math.isfinite(minimum_score) or not -1 <= minimum_score <= 1
            or type(limit) is not int or not 1 <= limit <= 20):
        raise ValueError("近さの下限と表示件数を確認してください。")
    query_rows = embed(model, (prepared.query.text.strip(),))
    if not isinstance(query_rows, (tuple, list)) or len(query_rows) != 1:
        raise ValueError("対象行の検索用の数値を取得できませんでした。")
    query = unit_vector(query_rows[0])
    candidates: list[dict[str, Any]] = []
    for offset in range(0, len(prepared.texts), BATCH_SIZE):
        texts = prepared.texts[offset:offset + BATCH_SIZE]
        vectors = embed(model, texts)
        if len(vectors) != len(texts):
            raise ValueError("検索結果の件数が一致しません。")
        for index, vector in enumerate(vectors, offset):
            vector = unit_vector(vector)
            if len(vector) != len(query):
                raise ValueError("検索モデルの次元が途中で変わりました。結果を採用しません。")
            score = max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(query, vector))))
            if score >= minimum_score:
                candidates.append({"text": prepared.texts[index], "score": score, "evidence": list(prepared.evidence[index])})
        if progress:
            progress(min(offset + BATCH_SIZE, len(prepared.texts)) / len(prepared.texts))
    candidates.sort(key=lambda item: (-item["score"], item["text"]))
    return {"format": VERSION, "input_hash": prepared.input_hash, "query": asdict(prepared.query), "model": model,
            "embedding_prefix": PREFIX, "minimum_score": minimum_score, "limit": limit,
            "unique_reference_lines": len(prepared.texts), "exact_excluded": prepared.exact_excluded,
            "reference_occurrences": sum(len(items) for items in prepared.evidence),
            "sources": [{key: asdict(song)[key] for key in ("song_id", "title", "revision_id", "revision_number", "content_hash")} for song in prepared.corpus],
            "matched_lines": len(candidates), "results": candidates[:limit]}
