"""Compare existing prompts on fixed handwritten examples, without a lyric DB."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from time import perf_counter

from lyric_analyzer import local_ai
from lyric_analyzer.analysis.candidate_quality import inspect_candidates


# id, category, before, original, after, points for manual review
CASES = (
    ("letter", "否定", "古い封筒を机に置く", "私はその手紙を捨てなかった", "引き出しには空きがある", "私が手紙を捨てなかったこと。肯定への反転や捨てられなかったへの変更に注意。"),
    ("sender", "人物と動作の相手", "夕方の窓に雨が映る", "私が君に写真を送った", "机には空の封筒が残る", "送ったのは私、受け手は君、過去の送付。役割の入れ替えに注意。"),
    ("time", "時制", "雨の跡が歩道に残る", "昨日、妹が鍵を返した", "玄関に靴音が響く", "昨日・妹・鍵の返却が過去に起きたこと。人物と時制の変更に注意。"),
    ("question", "疑問の語調", "終電の表示が消えかける", "君はまだ駅にいるの？", "私は改札の外で待っている", "君が駅にまだいるかという疑問。平叙文や命令への変更に注意。"),
    ("possibility", "可能性", "予定表の余白をなぞる", "明日は君に会えるかもしれない", "まだ返事は届いていない", "明日会える可能性であって確定ではない。断定への変更に注意。"),
    ("number", "数量", "窓口の列が短くなる", "私は切符を二枚買った", "財布に小銭が残る", "私が切符二枚を買った過去。数量や購入者の変更に注意。"),
    ("attempt", "試行と完了", "冷たい風が部屋に入る", "君は扉を閉めようとした", "廊下から足音が聞こえる", "君が扉を閉めようと試みた。閉め終えたと断定しない。"),
    ("borrow", "貸し借りの方向", "ページの端を指で押さえる", "兄から借りた本を読む", "栞の糸が揺れている", "兄が貸し手で、省略された話者が借り手。逆向きの貸し借りに注意。"),
)


def descriptor(name):
    if not local_ai.valid_model(name) or name not in local_ai.list_models():
        raise ValueError("インストール済みのPC内の生成モデルを指定してください。")
    rows = local_ai._json_request("/api/tags")["models"]
    row = next((row for row in rows if isinstance(row, dict) and row.get("name") == name), {})
    digest = row.get("digest")
    info = local_ai._json_request("/api/show", {"model": name})
    if (not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or row.get("remote_host") or row.get("remote_model") or info.get("remote_host") or info.get("remote_model")
            or "completion" not in info.get("capabilities", [])):
        raise ValueError("PC内の生成モデルの種類と識別値を確認できません。")
    return {"name": name, "digest": digest}


def evaluate(model, generate=local_ai.generate, check=None, progress=None):
    records = []
    for case in CASES:
        key, category, before, original, after, points = case
        request = local_ai.prepare_request(f"[Verse]\n{before}\n{original}\n{after}", 3)
        for prompt in local_ai.PROMPTS:
            if check:
                check(model)
            started = perf_counter()
            record = {"case_id": key, "category": category, "review_points": points,
                      "prompt_version": prompt, "request": request, "human_review": "not_reviewed"}
            try:
                suggestion = generate(model["name"], request, prompt)
                local_ai.validate_result(suggestion["result"], request)
                record.update(status="success", suggestion=suggestion,
                              automatic_checks=inspect_candidates(suggestion["result"], request))
            except (ValueError, OSError) as error:
                record.update(status="failed", error=str(error))
            if check:
                check(model)
            record["seconds"] = round(perf_counter() - started, 3)
            records.append(record)
            if progress:
                progress(record)
    return {"format": "rewrite-evaluation-1", "dataset_version": "synthetic-rewrite-8-v1",
            "dataset_sha256": hashlib.sha256(json.dumps(CASES, ensure_ascii=False).encode()).hexdigest(),
            "created_at": datetime.now(timezone.utc).isoformat(), "model": model,
            "prompt_sha256": {key: hashlib.sha256(value.encode()).hexdigest() for key, value in local_ai.PROMPTS.items()},
            "summary": [{"prompt_version": prompt, "attempts": sum(row["prompt_version"] == prompt for row in records),
                         "structural_success": sum(row["prompt_version"] == prompt and row["status"] == "success" for row in records),
                         "failed": sum(row["prompt_version"] == prompt and row["status"] == "failed" for row in records)} for prompt in local_ai.PROMPTS],
            "records": records}


def markdown(result):
    lines = ["# 自作8例の候補生成比較", "", "Issue #11の開発時確認です。各例・各指示で1回だけ生成し、自動再試行はしません。",
             "利用者の歌詞やDBは読みません。構造検証・語の差の補助表示と、意味・自然さ・具体性の評価は別です。",
             "全例の人による評価は未実施です。モデル順位や標準指示の改善率は結論しません。", "",
             f"モデル：{result['model']['name']} / digest：{result['model']['digest']}",
             f"データ：{result['dataset_version']} / SHA256：{result['dataset_sha256']}",
             f"作成日時（UTC）：{result['created_at']}", "",
             "|指示|試行|構造検証に成功|失敗|", "|---|---:|---:|---:|"]
    for row in result["summary"]:
        lines.append(f"|{row['prompt_version']}|{row['attempts']}|{row['structural_success']}|{row['failed']}|")
    for row in result["records"]:
        lines += ["", f"## {row['case_id']} / {row['prompt_version']}（{row['category']}）", "",
                  f"前：{row['request']['context_before'][0]}", f"\n原文：{row['request']['original']}",
                  f"\n後：{row['request']['context_after'][0]}", f"\n確認点：{row['review_points']}"]
        if row["status"] != "success":
            lines.append(f"\n生成失敗：{row['error']}")
            continue
        for candidate, checks in zip(row["suggestion"]["result"]["candidates"], row["automatic_checks"]):
            # Preserve literal quotation marks and any escaped line separators.
            lines.append(f"\n{candidate['direction']}：{json.dumps(candidate['text'], ensure_ascii=False)}")
            lines.append("\n語の差などの補助確認：" + (" / ".join(checks["checks"]) or "該当なし（意味保持の判定ではありません）"))
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="自作8例をPC内モデルで各指示1回ずつ比較")
    parser.add_argument("--local-model", required=True, help="インストール済みのローカル生成モデル名")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.suffix.lower() != ".json":
        parser.error("保存先は.jsonを指定してください。Markdownも作成します。")
    model = descriptor(args.local_model)
    def check(expected):
        if descriptor(expected["name"]) != expected:
            raise ValueError("評価中にモデルが変更されました。結果を保存しません。")
    result = evaluate(model, check=check, progress=lambda row: print(json.dumps({key: row[key] for key in ("case_id", "prompt_version", "status", "seconds")}), flush=True))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output.with_suffix(".md").write_text(markdown(result), encoding="utf-8-sig")
    print(json.dumps(result["summary"]), flush=True)


if __name__ == "__main__":
    main()
