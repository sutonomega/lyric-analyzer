import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
from statistics import median
from time import perf_counter
from tempfile import TemporaryDirectory

from lyric_analyzer.analysis.comparison import compare
from lyric_analyzer.analysis.expressions import detect
from lyric_analyzer.analysis.report import build_report
from lyric_analyzer.history import HistoryStore
from lyric_analyzer.storage import Library


SCENES = (
    "改札の端に残した切符を折り返す", "雨の商店街で自転車の鍵を握る", "台所の窓に朝の湯気が映る",
    "終電の窓に青い看板が流れる", "机の引き出しに映画の半券をしまう", "夕方の橋で靴ひもを結び直す",
    "郵便受けの奥から白い封筒を抜く", "古い時計の針を指先で戻す", "階段の手すりに冷たい水が残る",
    "パン屋の前で紙袋を抱え直す", "バス停の時刻表をもう一度読む", "洗濯物の隙間を遠い電車が通る",
    "図書館の返却口に本を置く", "港のベンチで缶のふたを開ける", "誰もいない教室で椅子を戻す",
    "公園の蛇口から砂を洗い流す", "玄関の灯りが片方だけ揺れる", "雨上がりの道路に靴音が重なる",
    "青信号の先で傘をたたむ", "食卓の隅に二つの皿を並べる",
)


def synthetic_lyrics(index, length=2000):
    lines = ["[Verse]"]
    number = 0
    while len("\n".join(lines)) < length:
        scene = SCENES[(index + number) % len(SCENES)]
        lines.append(f"{scene}、{index + 1}番目の便りを読む{number + 1}")
        if number % 12 == 0:
            lines.extend(["[Chorus]", "記憶がほどけていく"])
        number += 1
    return "\n".join(lines)[:length]


def benchmark(repeats=3):
    if repeats < 1:
        raise ValueError("repeats must be positive")
    target = synthetic_lyrics(21)
    samples = []
    with TemporaryDirectory(prefix="lyric-evaluation-") as directory:
        library = Library(Path(directory) / "evaluation.db")
        store = HistoryStore(library)
        for index in range(20):
            library.save(f"Synthetic {index + 1}", synthetic_lyrics(index))
        for _ in range(repeats + 1):
            started = perf_counter()
            comparison = compare(target, library.comparison_corpus())
            detection = detect(target, library.list_rules())
            report = build_report(target, comparison, detection, {})
            store.save(report, "Synthetic target")
            samples.append(round(perf_counter() - started, 4))
    return {"created_at": datetime.now(timezone.utc).isoformat(), "dataset": "original-synthetic-v1",
            "real_lyrics_evaluated": False, "corpus_songs": 20, "characters_per_song": 2000, "target_characters": len(target),
            "python": platform.python_version(), "platform": platform.platform(), "processor": platform.processor(),
            "logical_cpus": os.cpu_count(), "cold_seconds": samples[0], "warm_seconds": samples[1:],
            "warm_median_seconds": median(samples[1:]), "warm_max_seconds": max(samples[1:]),
            "under_five_seconds": max(samples[1:]) <= 5, "finding_count": len(report["findings"]),
            "versions": report["versions"], "timing_scope": "read corpus/rules + compare + detect + build_report + JSON/SQLite history save; excludes setup, UI and Streamlit result cache"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    result = benchmark(args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
