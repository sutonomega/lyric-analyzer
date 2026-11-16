"""Reading-based mora estimates, independent of melody and AI generation."""
from importlib.metadata import version
import re
import unicodedata

from sudachipy import SplitMode

from lyric_analyzer.analysis.frequency import BOUNDARY_POS, load_dictionary


IDS = ("original", "meaning", "scene", "viewpoint")
SMALL = frozenset("ァィゥェォャュョヮ")
PAIRS = frozenset((" ".join(base + small for base in "キギシジチヂニヒビピミリ" for small in "ャュョ") +
                   " イェ ウァ ウィ ウェ ウォ ヴァ ヴィ ヴェ ヴォ ヴュ キェ ギェ クァ クィ クェ クォ クヮ"
                   " グァ グィ グェ グォ グヮ シェ ジェ スィ ズィ チェ ツァ ツィ ツェ ツォ ティ テュ トゥ"
                   " ディ デュ ドゥ ニェ ヒェ ビェ ピェ ミェ リェ ファ フィ フェ フォ フャ フュ フョ").split())
STATUSES = {"概算", "推定", "不明", "指定した読み"}


def normalize_reading(text):
    if not isinstance(text, str) or len(text) > 2000:
        raise ValueError("読みは2,000文字以内のかなで入力してください。")
    result = []
    for char in unicodedata.normalize("NFKC", text):
        if char.isspace() or unicodedata.category(char).startswith("P"):
            continue
        if "ぁ" <= char <= "ゖ":
            char = chr(ord(char) + 0x60)
        if not ("ァ" <= char <= "ヺ" or char == "ー"):
            raise ValueError("読みはひらがな・カタカナで指定してください。")
        result.append(char)
    if not result:
        raise ValueError("空でない読みを指定してください。")
    return "".join(result)


def mora_count(reading):
    reading = normalize_reading(reading)
    count = 0
    previous = ""
    for char in reading:
        if char in SMALL:
            if previous + char not in PAIRS:
                raise ValueError("小さいかなの前の読みを確認してください。")
        else:
            count += 1
        previous = char
    return count


def estimate(text, override=""):
    if not isinstance(text, str) or not 1 <= len(text) <= 2000 or len(text.splitlines()) != 1:
        raise ValueError("比較する本文は1行・2,000文字以内にしてください。")
    if override:
        reading = normalize_reading(override)
        return {"text": text, "reading": reading, "mora": mora_count(reading), "status": "指定した読み", "notes": []}
    pieces, notes = [], []
    unknown = False
    tokenizer = load_dictionary().tokenizer()
    for word in tokenizer.tokenize(unicodedata.normalize("NFKC", text), SplitMode.A):
        surface, pos = word.surface(), word.part_of_speech()
        if pos[0] in BOUNDARY_POS or not any(char.isalnum() for char in surface):
            continue
        reading = word.reading_form()
        pieces.append(reading)
        if re.search(r"[A-Za-z0-9]", surface):
            notes.append(f"英数字の発音を指定：{surface}")
            unknown = True
        elif word.is_oov():
            notes.append(f"辞書未登録：{surface}")
        elif "固有名詞" in pos:
            notes.append(f"固有名詞の読みを確認：{surface}")
    reading = "".join(pieces)
    try:
        reading = normalize_reading(reading)
        count = mora_count(reading)
    except ValueError:
        unknown = True
        count = None
        notes.append("自動で確定できない読みがあります")
    return {"text": text, "reading": reading, "mora": None if unknown else count,
            "status": "不明" if unknown else "推定" if notes else "概算", "notes": list(dict.fromkeys(notes))}


def comparison(result, overrides=None):
    overrides = {} if overrides is None else overrides
    if not isinstance(overrides, dict) or set(overrides) - set(IDS):
        raise ValueError("読みを指定する対象が不正です。")
    for value in overrides.values():
        if not isinstance(value, str) or len(value) > 2000:
            raise ValueError("読みは2,000文字以内のかなで入力してください。")
        if value.strip():
            mora_count(value)
    overrides = {key: value.strip() for key, value in overrides.items() if value.strip()}
    texts = {"original": result["original"], **{item["direction"]: item["text"] for item in result["candidates"]}}
    return {"algorithm_version": "mora-1", "dictionary_version": version("sudachidict_core"),
            "tokenizer_version": version("sudachipy"), "overrides": overrides,
            "rows": [{"id": key, **estimate(texts[key], overrides.get(key, ""))} for key in IDS]}


def validate_comparison(result, saved):
    if (not isinstance(saved, dict) or set(saved) != {"algorithm_version", "dictionary_version", "tokenizer_version", "overrides", "rows"}
            or saved["algorithm_version"] != "mora-1"
            or any(not isinstance(saved[key], str) or not 1 <= len(saved[key]) <= 100 for key in ("dictionary_version", "tokenizer_version"))
            or not isinstance(saved["overrides"], dict) or set(saved["overrides"]) - set(IDS)
            or not isinstance(saved["rows"], list) or len(saved["rows"]) != 4):
        raise ValueError("読み・音数の保存形式が不正です。")
    texts = {"original": result["original"], **{item["direction"]: item["text"] for item in result["candidates"]}}
    for key, row in zip(IDS, saved["rows"]):
        if (not isinstance(row, dict) or set(row) != {"id", "text", "reading", "mora", "status", "notes"}
                or row["id"] != key or row["text"] != texts[key] or row["status"] not in STATUSES
                or not isinstance(row["reading"], str) or len(row["reading"]) > 12000
                or not isinstance(row["notes"], list) or len(row["notes"]) > 2000
                or any(not isinstance(note, str) or len(note) > 2100 for note in row["notes"])):
            raise ValueError("読み・音数の対象行が不正です。")
        count = row["mora"]
        if row["status"] == "不明":
            if count is not None:
                raise ValueError("不明な読みの音数は確定できません。")
        elif type(count) is not int or count < 1 or count != mora_count(row["reading"]):
            raise ValueError("読みと音数が一致しません。")
        override = saved["overrides"].get(key)
        if override is not None:
            if row["status"] != "指定した読み" or normalize_reading(override) != row["reading"]:
                raise ValueError("指定した読みの保存内容が一致しません。")
        elif row["status"] == "指定した読み":
            raise ValueError("指定した読みが保存されていません。")
