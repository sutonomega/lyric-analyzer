"""Reproducible synthetic comparisons; never reads a user's lyric library."""
import argparse
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import math
from pathlib import Path
from time import perf_counter
import unicodedata

from lyric_analyzer import local_embeddings


# Handwritten development examples, not a held-out or representative lyric corpus.
# id, group, contrast type, query, intended paraphrase, unrelated, contrast
CASES = (
    ("arrival", "initial", "negation", "改札で君の到着を待つ", "駅の入口であなたが来るのを待っている", "台所で夕食の皿を洗う", "改札で君の到着を待たない"),
    ("memory", "initial", "direction", "思い出が薄れてゆく", "昔の記憶が少しずつ遠ざかる", "朝の食卓にパンを並べる", "思い出が鮮明によみがえる"),
    ("letter", "initial", "negation", "返事のない手紙をしまう", "返信が届かない便りを引き出しに入れる", "浜辺で濡れた靴を脱ぐ", "返事のある手紙をしまう"),
    ("key", "initial", "actor_time", "昨日、妹が鍵を返した", "きのう妹から鍵が戻ってきた", "来週の旅行の準備をする", "明日、兄が鍵を返す"),
    ("room", "initial", "negation", "誰もいない部屋で朝を待つ", "無人の室内で夜明けを待ち続ける", "電車の時刻表を書き写す", "大勢いる部屋で朝を待つ"),
    ("umbrella", "initial", "negation", "壊れた傘を捨てられない", "破れた雨傘を手放せずにいる", "新しい靴のひもを結ぶ", "壊れた傘を捨てた"),
    ("train", "additional", "negation", "最終電車には乗らない", "終電を見送ることにする", "台所でリンゴの皮をむく", "最終電車に乗る"),
    ("sender", "additional", "role", "私が君に写真を送った", "あなたへ写真を送ったのは私だ", "公園の池で水鳥を数える", "君が私に写真を送った"),
    ("book", "additional", "role", "兄から借りた本を読む", "兄に貸してもらった書物を開く", "玄関の靴ひもを結び直す", "兄に貸した本を読む"),
    ("rain", "additional", "time", "雨は昨日やんだ", "前の日には雨が上がった", "食卓に温かいスープを置く", "雨は明日やむ"),
    ("seat", "additional", "location", "窓のそばに座る", "窓際の席に腰を下ろす", "畑で大根を引き抜く", "窓から離れた席に座る"),
    ("tickets", "additional", "quantity", "切符を二枚買った", "二人分の乗車券を購入した", "屋上で洗濯物を干す", "切符を一枚買った"),
    ("meeting", "additional", "modality", "明日は君に会えるかもしれない", "翌日あなたと会う可能性がある", "机の下の消しゴムを拾う", "明日は必ず君に会える"),
    ("door", "additional", "completion", "扉を閉めようとした", "ドアを閉じようと試みた", "庭の落ち葉を袋に詰める", "扉を閉め終えた"),
    ("promise", "additional", "negation", "あの約束を忘れていない", "あの日の誓いはまだ覚えている", "駅前の店で電池を買う", "あの約束を忘れてしまった"),
    ("voice", "additional", "actor", "母の声を聞いた", "お母さんの話す声が耳に届いた", "海岸で丸い石を探す", "父の声を聞いた"),
    ("return", "additional", "direction", "故郷へ戻っていく", "生まれた町へ帰っていく", "喫茶店で新聞を広げる", "故郷から離れていく"),
    ("light", "additional", "negation", "廊下の明かりはまだ消えていない", "通路の照明はいまもついている", "朝市で新鮮な野菜を選ぶ", "廊下の明かりはもう消えている"),
)
THRESHOLDS = (.6, .7, .75, .8, .85, .9, .95)


def character_score(left, right):
    return SequenceMatcher(None, unicodedata.normalize("NFKC", left).strip(),
                           unicodedata.normalize("NFKC", right).strip(), autojunk=False).ratio()


def metrics(rows, method):
    values = [row["scores"][method] for row in rows]
    rankings = {}
    for key in ("unrelated", "contrast"):
        rankings[key] = {"wins": sum(row["positive"] > row[key] for row in values),
                         "ties": sum(row["positive"] == row[key] for row in values),
                         "losses": sum(row["positive"] < row[key] for row in values)}
    return {"cases": len(rows), "ranking": rankings,
            "thresholds": [{"minimum_score": threshold,
                            "positive_accepted": sum(row["positive"] >= threshold for row in values),
                            "positive_rejected": sum(row["positive"] < threshold for row in values),
                            "unrelated_accepted": sum(row["unrelated"] >= threshold for row in values),
                            "contrast_accepted": sum(row["contrast"] >= threshold for row in values)} for threshold in THRESHOLDS]}


def evaluate(model=None, embed=None):
    started = perf_counter()
    vectors = {}
    if model is not None:
        if embed is None:
            embed = local_embeddings.embed_batch
        texts = tuple(dict.fromkeys(text for case in CASES for text in case[3:]))
        dimension = None
        for offset in range(0, len(texts), local_embeddings.BATCH_SIZE):
            batch = texts[offset:offset + local_embeddings.BATCH_SIZE]
            rows = embed(model, batch)
            if not isinstance(rows, (list, tuple)) or len(rows) != len(batch):
                raise ValueError("数値化した例の件数が一致しません。")
            for text, values in zip(batch, rows):
                vector = local_embeddings.unit_vector(values)
                if dimension is None:
                    dimension = len(vector)
                if len(vector) != dimension:
                    raise ValueError("評価中に数値表現の次元が変わりました。")
                vectors[text] = vector
    records = []
    for key, group, kind, query, positive, unrelated, contrast in CASES:
        candidates = {"positive": positive, "unrelated": unrelated, "contrast": contrast}
        scores = {"characters": {label: character_score(query, text) for label, text in candidates.items()}}
        if model is not None:
            scores["embedding"] = {label: max(-1., min(1., math.fsum(a * b for a, b in zip(vectors[query], vectors[text]))))
                                   for label, text in candidates.items()}
        records.append({"id": key, "group": group, "contrast_type": kind, "query": query, **candidates, "scores": scores})
    methods = ["characters", "embedding"] if model is not None else ["characters"]
    return {"format": "semantic-evaluation-1", "dataset_version": "synthetic-japanese-18-v1",
            "dataset_sha256": hashlib.sha256(json.dumps(CASES, ensure_ascii=False).encode()).hexdigest(),
            "created_at": datetime.now(timezone.utc).isoformat(), "seconds": round(perf_counter() - started, 3),
            "model": model, "embedding_prefix": local_embeddings.PREFIX if model else None,
            "character_method": "NFKC/strip/SequenceMatcher-autojunk-false",
            "summary": {method: metrics(records, method) for method in methods},
            "groups": {group: {method: metrics([row for row in records if row["group"] == group], method) for method in methods}
                       for group in ("initial", "additional")}, "cases": records}


def markdown(result):
    lines = ["# 自作18例による意味検索の方式比較", "",
             "自作の開発例です。代表性のある歌詞集合や未見の評価用データではなく、実歌詞の精度を示しません。",
             "既存6例と追加12例を区別します。正例は近い言い換えを意図した例で、意味同一の客観的な正解を保証しません。",
             "数値は方式ごとに異なる尺度です。否定・人物・時間等の変更でも高くなります。自動的な閾値変更は行いません。", "",
             f"データ版：{result['dataset_version']} / 実行時間：{result['seconds']}秒",
             f"データSHA256：{result['dataset_sha256']} / 作成日時（UTC）：{result['created_at']}", "",
             "|方式|例数|言い換えが無関係より上|同点|言い換えが対照文より上|同点|",
             "|---|---:|---:|---:|---:|---:|"]
    for method, summary in result["summary"].items():
        rank = summary["ranking"]
        lines.append(f"|{method}|{summary['cases']}|{rank['unrelated']['wins']}|{rank['unrelated']['ties']}|{rank['contrast']['wins']}|{rank['contrast']['ties']}|")
    if result["model"]:
        lines += ["", f"モデル：{result['model']['name']} / digest：{result['model']['digest']}"]
    lines += ["", "## 下限ごとの件数", "", "各分類の母数は18件ずつです。対照文は否定・人物・時間等を変えた難しい負例です。",
              "人工的な分類比率のため実利用の適合率は推定せず、閾値選定にも別の例と利用者の確認が必要です。", "",
              "|方式|下限|言い換えを採用|言い換えを不採用|無関係を採用|対照文を採用|", "|---|---:|---:|---:|---:|---:|"]
    for method, summary in result["summary"].items():
        for row in summary["thresholds"]:
            lines.append(f"|{method}|{row['minimum_score']:.2f}|{row['positive_accepted']}|{row['positive_rejected']}|{row['unrelated_accepted']}|{row['contrast_accepted']}|")
    lines += ["", "## 各例の本文と値"]
    for row in result["cases"]:
        lines += ["", f"### {row['id']}（{row['group']} / {row['contrast_type']}）", "",
                  f"原文：{row['query']}", f"\n言い換え：{row['positive']}", f"\n無関係：{row['unrelated']}", f"\n対照文：{row['contrast']}"]
        for method, values in row["scores"].items():
            lines.append(f"\n{method}：言い換え {values['positive']:.3f} / 無関係 {values['unrelated']:.3f} / 対照文 {values['contrast']:.3f}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="利用者の歌詞を使わず、自作18例で方式を比較")
    parser.add_argument("--local-model", action="store_true", help="自作例をPC内Ollamaへ送り、文字列方式と比較")
    parser.add_argument("--output", type=Path, required=True, help="JSON保存先。同じ名前の.mdも作成")
    args = parser.parse_args()
    if args.output.suffix.lower() != ".json":
        parser.error("保存先は.jsonを指定してください。Markdownも別ファイルで作成します。")
    model = None
    if args.local_model:
        models = local_embeddings.list_models()
        if not models:
            parser.error("PC内のembeddinggemma:300mが見つかりません。自動取得はしません。")
        model = models[0]
        local_embeddings.check_model(model)
    result = evaluate(model)
    if model is not None:
        local_embeddings.check_model(model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output.with_suffix(".md").write_text(markdown(result), encoding="utf-8-sig")
    print(json.dumps({"summary": result["summary"], "seconds": result["seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
