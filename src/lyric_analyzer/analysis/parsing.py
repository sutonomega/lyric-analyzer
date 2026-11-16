from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class Span:
    text: str
    start: int
    end: int
    line_number: int
    section: str | None
    metadata: bool = False


@dataclass(frozen=True)
class NormalizedText:
    text: str
    source_spans: tuple[tuple[int, int], ...]

    def original_range(self, start, end):
        if not 0 <= start < end <= len(self.text):
            raise ValueError("空でない有効な解析文字範囲を指定してください。")
        spans = self.source_spans[start:end]
        return min(span[0] for span in spans), max(span[1] for span in spans)


def normalize_with_offsets(raw):
    normalized = ""
    spans = []
    prefix = ""
    for offset, character in enumerate(raw):
        prefix += character
        updated = unicodedata.normalize("NFKC", prefix)
        common = 0
        for before, after in zip(normalized, updated):
            if before != after:
                break
            common += 1
        start = min([offset, *(span[0] for span in spans[common:])])
        spans[common:] = [(start, offset + 1)] * (len(updated) - common)
        normalized = updated
    return NormalizedText(normalized, tuple(spans))


def parse_lines(raw):
    section = None
    offset = 0
    lines = []
    for number, line in enumerate(raw.splitlines(keepends=True), start=1):
        body = line.rstrip("\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029")
        label = re.fullmatch(r"\s*\[([^\[\]\r\n]+)\]\s*", body)
        if label:
            section = label.group(1).strip()
        lines.append(Span(body, offset, offset + len(body), number, section, bool(label)))
        offset += len(line)
    return lines


def analysis_segments(raw, include_unknown_tags=False):
    segments = []
    for line in parse_lines(raw):
        known = line.section and re.fullmatch(r"(?:verse|chorus|pre[- ]?chorus|bridge|intro|outro|instrumental|hook|refrain)(?:\s+\d+)?", line.section, re.IGNORECASE)
        if line.metadata and (known or not include_unknown_tags):
            continue
        for match in re.finditer(r"[^。！？!?]+", line.text):
            if match.group().strip():
                segments.append(Span(match.group(), line.start + match.start(), line.start + match.end(), line.line_number, line.section, line.metadata))
    return segments
