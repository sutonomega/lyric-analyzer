import math
import re

from lyric_analyzer import local_ai


MODEL_NAME = "embeddinggemma:300m"
PREFIX = "task: sentence similarity | query: "
BATCH_SIZE = 8


def list_models():
    records = local_ai._json_request("/api/tags").get("models")
    if not isinstance(records, list) or len(records) > 500:
        raise local_ai.LocalAIError("検索モデルの一覧が不正です。")
    return tuple({"name": row["name"], "digest": row["digest"]} for row in records
                 if isinstance(row, dict) and row.get("name") == MODEL_NAME
                 and not row.get("remote_host") and not row.get("remote_model")
                 and isinstance(row.get("digest"), str) and re.fullmatch(r"[0-9a-f]{64}", row["digest"]))


def check_model(model):
    if not isinstance(model, dict) or model not in list_models():
        raise local_ai.LocalAIError("検索モデルがないか、確認後に変更されました。モデルを確認し直してください。")
    info = local_ai._json_request("/api/show", {"model": model["name"]})
    if (info.get("remote_host") or info.get("remote_model") or not info.get("model_info")
            or "embedding" not in info.get("capabilities", [])):
        raise local_ai.LocalAIError("PC内の検索モデルであることを確認できません。")


def unit_vector(values):
    if not isinstance(values, (list, tuple)) or not 2 <= len(values) <= 4096:
        raise local_ai.LocalAIError("検索用の数値の次元が不正です。")
    try:
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
            raise local_ai.LocalAIError("検索用の数値に無効な値があります。")
        norm = math.hypot(*values)
        if not math.isfinite(norm) or norm == 0:
            raise local_ai.LocalAIError("検索用の数値を正規化できません。")
        return tuple(value / norm for value in values)
    except OverflowError as error:
        raise local_ai.LocalAIError("検索用の数値が大きすぎます。") from error


def embed_batch(model, texts):
    if (not isinstance(model, dict) or model.get("name") != MODEL_NAME
            or not isinstance(model.get("digest"), str) or re.fullmatch(r"[0-9a-f]{64}", model["digest"]) is None
            or not isinstance(texts, (tuple, list)) or not 1 <= len(texts) <= BATCH_SIZE
            or any(not isinstance(text, str) or not text.strip() or len(text) > 1000 for text in texts)):
        raise local_ai.LocalAIError("検索する本文とモデルを確認してください。")
    check_model(model)
    response = local_ai._json_request("/api/embed", {"model": model["name"], "input": [PREFIX + text for text in texts],
                                                   "truncate": False}, timeout=45)
    rows = response.get("embeddings")
    if response.get("model") != model["name"] or not isinstance(rows, list) or len(rows) != len(texts):
        raise local_ai.LocalAIError("検索モデルの応答件数が入力と一致しません。")
    result = tuple(unit_vector(row) for row in rows)
    if len({len(row) for row in result}) != 1:
        raise local_ai.LocalAIError("検索用の数値の次元が一致しません。")
    return result
