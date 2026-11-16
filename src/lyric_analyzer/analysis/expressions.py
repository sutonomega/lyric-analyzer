from dataclasses import dataclass
import json
import unicodedata
from uuid import NAMESPACE_URL, uuid5

from lyric_analyzer.analysis.frequency import Occurrence, analyze
from lyric_analyzer.analysis.parsing import analysis_segments, normalize_with_offsets, parse_lines


@dataclass(frozen=True)
class ExpressionRule:
    id: str
    kind: str
    pattern_json: str
    reason: str
    review_hint: str
    enabled: bool
    version: int


@dataclass(frozen=True)
class RuleFinding:
    rule: ExpressionRule
    occurrence: Occurrence
    match_kind: str


@dataclass(frozen=True)
class Detection:
    rules: tuple[ExpressionRule, ...]
    findings: tuple[RuleFinding, ...]
    tokenizer_version: str
    dictionary_version: str
    analyzer_version: str = "expressions-1"


def validate_rule(kind, pattern, reason, review_hint, enabled):
    if kind not in {"literal", "abstract_loss"} or not isinstance(pattern, dict):
        raise ValueError("対応するルールの種類と条件を指定してください。")
    for value in (reason, review_hint):
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError("理由と見直しの観点は1〜2,000文字で入力してください。")
    if type(enabled) is not bool:
        raise ValueError("有効・無効を指定してください。")
    if kind == "literal":
        text = pattern.get("text")
        if set(pattern) != {"text"} or not isinstance(text, str) or not text.strip() or len(text) > 200 or any(character in text for character in "\r\n"):
            raise ValueError("登録表現は改行を含まない1〜200文字で入力してください。")
        pattern = {"text": text.strip()}
    else:
        if set(pattern) != {"nouns", "verbs"}:
            raise ValueError("文型には抽象名詞と動詞の原形を指定してください。")
        cleaned = {}
        for key in ("nouns", "verbs"):
            values = pattern[key]
            if not isinstance(values, list) or not 1 <= len(values) <= 100 or any(not isinstance(value, str) or not value.strip() or len(value) > 100 or any(character.isspace() for character in value.strip()) for value in values):
                raise ValueError("抽象名詞・動詞は各1〜100個、空白を含まない語で入力してください。")
            cleaned[key] = sorted({value.strip() for value in values})
        pattern = cleaned
    return json.dumps(pattern, ensure_ascii=False, sort_keys=True)


def default_rules() -> tuple[ExpressionRule, ...]:
    specifications = [
        ("literal", {"text": "ほどけていく"}, "変化の対象や様子が曖昧になりやすい表現として登録しています。", "何がほどけるのかを、物や動作に置き換えて考えてみる。"),
        ("literal", {"text": "消えない想い"}, "感情を抽象語でまとめた表現として登録しています。", "残している物、消せない記録、繰り返す行動から気持ちを描いてみる。"),
        ("literal", {"text": "心の奥で"}, "気持ちの所在を抽象的に示す表現として登録しています。", "身体の反応や、その場で取った行動を描いてみる。"),
        ("literal", {"text": "光を探して"}, "目指すものが曖昧になりやすい表現として登録しています。", "何を見つけたいのか、場所や物の具体的な手掛かりを考えてみる。"),
        ("abstract_loss", {"nouns": ["記憶", "想い", "約束", "時間", "夢"], "verbs": ["消える", "溶ける", "ほどける"]},
         "登録した抽象名詞が、失われる・形を変える動詞につながる文型の候補です。", "失われたものの痕跡や、変化を感じた場面を描いてみる。文脈によっては元の表現を残す。"),
    ]
    rules = []
    for index, (kind, pattern, reason, hint) in enumerate(specifications):
        pattern_json = validate_rule(kind, pattern, reason, hint, True)
        rules.append(ExpressionRule(str(uuid5(NAMESPACE_URL, f"lyric-analyzer/default-rules/v1/{index}")), kind, pattern_json, reason, hint, True, 1))
    return tuple(rules)


def detect(raw: str, rules: tuple[ExpressionRule, ...], include_unknown_tags: bool = False) -> Detection:
    analysis = analyze(raw, include_unknown_tags)
    lines = {line.line_number: line.text for line in parse_lines(raw)}
    tokens = analysis.tokens
    starts = {token.start for token in tokens if not token.boundary}
    ends = {token.end for token in tokens if not token.boundary}
    segments = [(segment, normalize_with_offsets(segment.text)) for segment in analysis_segments(raw, include_unknown_tags)]
    findings = []
    for rule in rules:
        if not rule.enabled:
            continue
        pattern = json.loads(rule.pattern_json)
        validate_rule(rule.kind, pattern, rule.reason, rule.review_hint, rule.enabled)
        if rule.kind == "literal":
            expression = unicodedata.normalize("NFKC", pattern["text"])
            for segment, normalized in segments:
                offset = normalized.text.find(expression)
                while offset != -1:
                    start, end = normalized.original_range(offset, offset + len(expression))
                    start, end = segment.start + start, segment.start + end
                    if start in starts and end in ends:
                        occurrence = Occurrence(raw[start:end], start, end, segment.line_number, segment.section, lines[segment.line_number])
                        findings.append(RuleFinding(rule, occurrence, "辞書一致（NFKC）"))
                    offset = normalized.text.find(expression, offset + 1)
        else:
            for index in range(len(tokens) - 2):
                noun, particle, verb = tokens[index:index + 3]
                if (noun.pos != "名詞" or noun.lemma not in pattern["nouns"] or particle.pos != "助詞"
                        or particle.normalized_surface not in {"が", "は"} or verb.pos != "動詞"
                        or verb.lemma not in pattern["verbs"] or noun.run != verb.run):
                    continue
                following = tokens[index + 3:index + 6]
                if following and following[0].run == noun.run and following[0].lemma in {"ない", "ぬ", "ず"}:
                    continue
                last = verb
                for extra in following:
                    if extra.run != noun.run or extra.boundary or (extra.pos not in {"助詞", "助動詞"} and extra.lemma not in {"いく", "ゆく", "いる"}):
                        break
                    if extra.pos == "助詞" and extra.normalized_surface != "て":
                        break
                    last = extra
                occurrence = Occurrence(raw[noun.start:last.end], noun.start, last.end, noun.line_number, noun.section, lines[noun.line_number])
                findings.append(RuleFinding(rule, occurrence, "文型候補"))
    unique = {(finding.rule.id, finding.occurrence.start, finding.occurrence.end): finding for finding in findings}
    return Detection(rules, tuple(sorted(unique.values(), key=lambda finding: (finding.occurrence.start, finding.rule.kind, finding.rule.id))),
                     analysis.tokenizer_version, analysis.dictionary_version)
